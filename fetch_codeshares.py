"""Fetch public Codeshare room snapshots with bounded concurrency and durable results.

Usage: python fetch_codeshares.py --workers 4 --interval 1
Only room-page GETs are used. No browser, collaboration session, API or proxy rotation.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import math
import os
from pathlib import Path
import random
import re
import sqlite3
import sys
import time
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from codeshare_fetcher.extract import parse_codeshare_html

ROOT = Path(__file__).resolve().parent
ORIGIN = 'https://codeshare.io'
ROBOT_AGENT = 'CodeshareSnapshot'
USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
              'AppleWebKit/537.36 (KHTML, like Gecko) '
              'Chrome/130.0.0.0 Safari/537.36 CodeshareSnapshot/1.0')
DONE = {'ok', 'empty', 'not_found', 'skipped'}
RESERVED = {'new', 'api', 'tos'}
RETRYABLE = {408, 425, 429, 500, 502, 503, 504}


@dataclass
class Result:
    url: str
    status: str
    content: str = ''
    detail: str = ''
    http_status: int | None = None
    attempts: int = 0
    final_url: str = ''
    fetched_at: str = ''


@dataclass
class Options:
    workers: int = 4
    interval: float = 1.0
    attempts: int = 4
    timeout: float = 30.0
    max_bytes: int = 8 * 1024 * 1024
    max_errors: int = 5


class BatchStopped(Exception):
    pass


class ResponseTooLarge(Exception):
    pass


class RequestGate:
    """One request-start budget and cooldown shared by ALL workers and retries."""
    def __init__(self, interval: float, pause_callback=None):
        self.interval = interval
        self.next_start = 0.0
        self.cooldown_until = 0.0
        self.lock = asyncio.Lock()
        self.stopped = asyncio.Event()
        self.reason = ''
        self.pause_callback = pause_callback

    def stop(self, reason: str):
        self.reason = self.reason or reason
        self.stopped.set()

    async def wait(self):
        while True:
            async with self.lock:
                if self.stopped.is_set():
                    raise BatchStopped(self.reason)
                now = time.monotonic()
                delay = max(self.next_start, self.cooldown_until) - now
                if delay <= 0:
                    self.next_start = now + self.interval
                    return
            try:
                await asyncio.wait_for(self.stopped.wait(), timeout=delay)
            except TimeoutError:
                pass

    async def pause(self, seconds: float, slow_down=False):
        seconds = max(0.0, seconds)
        async with self.lock:
            self.cooldown_until = max(self.cooldown_until, time.monotonic() + seconds)
            if slow_down:
                self.interval = max(self.interval, min(max(self.interval * 2, 1.0), 60.0))
            if self.pause_callback:
                self.pause_callback(time.time() + seconds)


def retry_delay(value: str | None, attempt: int, status: int | None) -> float:
    delay = min(60.0, 2.0 ** attempt) + random.uniform(0, 1)
    if status == 429:
        delay = max(delay, 10.0)
    if value:
        try:
            if re.fullmatch(r'\d+', value.strip()):
                requested = float(value)
            else:
                stamp = parsedate_to_datetime(value)
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                requested = stamp.timestamp() - time.time()
            if math.isfinite(requested):
                delay = max(delay, requested)
        except (ValueError, TypeError, OverflowError):
            pass
    return delay


def format_result(result: Result) -> str:
    if result.status == 'ok':
        body = result.content
        if result.detail and ('legacy' in result.detail.lower() or 'migrat' in result.detail.lower()):
            body = f'[note: {result.detail}]\n\n' + body
    elif result.status in {'empty', 'not_found'}:
        body = 'empty'
        if result.detail and 'migrat' in result.detail.lower():
            body += f'\n[note: {result.detail}]'
    else:
        body = f'[{result.status}: {result.detail}]'
    return f'{result.url}\n{body}\n\n' + '=' * 72 + '\n\n'


class ResultStore:
    """Commit each completed result; reconstruct TXT after a crash or interruption."""
    def __init__(self, output: Path):
        self.output = output
        self.state_path = output.with_suffix('.sqlite3')
        if output.exists() and not self.state_path.exists():
            raise ValueError(f'{output} exists without a checkpoint database. Choose a different --output.')
        output.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.state_path)
        self.db.execute('CREATE TABLE IF NOT EXISTS results (url TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        self.db.commit()
        self.writer = None

    def get(self, url: str) -> Result | None:
        row = self.db.execute('SELECT payload FROM results WHERE url=?', (url,)).fetchone()
        return Result(**json.loads(row[0])) if row else None

    def pause_until(self, stamp: float):
        prior = self.saved_cooldown()
        self.db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)', ('not_before', str(max(prior, stamp))))
        self.db.commit()

    def saved_cooldown(self) -> float:
        row = self.db.execute('SELECT value FROM metadata WHERE key=?', ('not_before',)).fetchone()
        return float(row[0]) if row else 0.0

    def rebuild(self, urls: list[str], completed_only=False):
        if self.writer:
            self.writer.close()
            self.writer = None
        temporary = self.output.with_name(self.output.name + '.tmp')
        with temporary.open('w', encoding='utf-8', newline='\n') as f:
            for url in urls:
                result = self.get(url)
                if result and (not completed_only or result.status in DONE):
                    f.write(format_result(result))
        temporary.replace(self.output)
        self.writer = self.output.open('a', encoding='utf-8', newline='\n')

    def save(self, result: Result):
        result.fetched_at = datetime.now(timezone.utc).isoformat()
        self.db.execute('INSERT OR REPLACE INTO results VALUES (?,?)',
                        (result.url, json.dumps(asdict(result), ensure_ascii=True)))
        self.db.commit()
        if self.writer:
            self.writer.write(format_result(result))
            self.writer.flush()

    def close(self):
        if self.writer:
            self.writer.close()
        self.db.close()


async def request_page(client: httpx.AsyncClient, url: str, gate: RequestGate, max_bytes: int):
    await gate.wait()
    async with client.stream('GET', url) as response:
        status, headers = response.status_code, response.headers
        # Do not consume irrelevant error/redirect bodies or their embedded data.
        if status != 200:
            return status, headers, ''
        if headers.get('content-length', '').isdigit() and int(headers['content-length']) > max_bytes:
            raise ResponseTooLarge(f'Response exceeds the {max_bytes}-byte limit')
        data = bytearray()
        async for chunk in response.aiter_bytes():
            data.extend(chunk)
            if len(data) > max_bytes:
                raise ResponseTooLarge(f'Response exceeds the {max_bytes}-byte limit')
        return status, headers, data.decode('utf-8-sig')


async def fetch_one(client: httpx.AsyncClient, url: str, gate: RequestGate, options: Options,
                    robots: RobotFileParser) -> Result:
    slug = urlparse(url).path.strip('/')
    if slug.lower() in RESERVED or not robots.can_fetch(ROBOT_AGENT, url):
        return Result(url, 'skipped', detail='Reserved route or disallowed by robots.txt; no room request sent.')
    current = url
    attempts = 0
    redirects = 0
    last_status = None
    while attempts < options.attempts:
        try:
            status, headers, html = await request_page(client, current, gate, options.max_bytes)
            attempts += 1
            last_status = status
            if status in RETRYABLE:
                delay = retry_delay(headers.get('retry-after'), attempts, status)
                await gate.pause(delay, slow_down=status == 429)
                print(f'HTTP {status}: shared pause {delay:.1f}s; {url}', file=sys.stderr)
                if attempts < options.attempts:
                    continue
                if status == 429:
                    gate.stop('Repeated HTTP 429 rate limits. Resume later; server cooldown is saved.')
                return Result(url, 'error', detail=f'HTTP {status}; retry budget exhausted.', http_status=status, attempts=attempts, final_url=current)
            if status in {401, 403}:
                if status == 403:
                    gate.stop('HTTP 403 access block; no further requests will start.')
                return Result(url, 'blocked', detail=f'HTTP {status}; authentication/access restrictions were not bypassed.', http_status=status, attempts=attempts, final_url=current)
            if status in {404, 410}:
                return Result(url, 'not_found', http_status=status, attempts=attempts, final_url=current)
            if status in {301, 302, 303, 307, 308}:
                target = urljoin(current, headers.get('location', ''))
                parsed = urlparse(target)
                # Never follow /new, login, an API, another room, or another origin.
                allowed = (parsed.scheme == 'https' and parsed.netloc == 'codeshare.io'
                           and parsed.path in {f'/{slug}', f'/{slug}/'}
                           and not parsed.query and not parsed.fragment
                           and target != current and redirects < 2
                           and robots.can_fetch(ROBOT_AGENT, target))
                if not allowed:
                    return Result(url, 'blocked', detail='Redirect leaves the requested room or is unsupported; not followed.', http_status=status, attempts=attempts, final_url=current)
                current = target
                redirects += 1
                continue
            if status != 200:
                return Result(url, 'error', detail=f'HTTP {status}', http_status=status, attempts=attempts, final_url=current)
            if 'html' not in headers.get('content-type', '').lower():
                return Result(url, 'error', detail='Expected an HTML room page; received another content type.', http_status=status, attempts=attempts, final_url=current)
            extracted = parse_codeshare_html(html, urlparse(current).path.strip('/'))
            if extracted.status == 'blocked':
                gate.stop('The site returned an access challenge; no further requests will start.')
            return Result(url, extracted.status, extracted.content, extracted.detail, status, attempts, current)
        except BatchStopped as exc:
            return Result(url, 'not_attempted' if attempts == 0 else 'error', detail=str(exc),
                          http_status=last_status, attempts=attempts, final_url=current)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            attempts += 1
            if attempts < options.attempts:
                await gate.pause(retry_delay(None, attempts, None))
                continue
            return Result(url, 'error', detail=f'{type(exc).__name__}; retry budget exhausted.', attempts=attempts, final_url=current)
        except (ResponseTooLarge, UnicodeError, ValueError) as exc:
            return Result(url, 'error', detail=str(exc), http_status=last_status, attempts=max(1, attempts), final_url=current)
    return Result(url, 'error', detail='Redirect/retry budget exhausted.', attempts=attempts, final_url=current)


async def load_robots(client: httpx.AsyncClient, gate: RequestGate, options: Options) -> RobotFileParser:
    url = ORIGIN + '/robots.txt'
    for attempt in range(1, options.attempts + 1):
        try:
            status, headers, text = await request_page(client, url, gate, 128 * 1024)
        except (httpx.TimeoutException, httpx.TransportError):
            if attempt == options.attempts:
                raise ValueError('Could not read robots.txt; no room requests were made.')
            await gate.pause(retry_delay(None, attempt, None))
            continue
        if status in RETRYABLE:
            await gate.pause(retry_delay(headers.get('retry-after'), attempt, status), slow_down=status == 429)
            continue
        if status == 404:
            text = ''
        elif status != 200 or 'text/plain' not in headers.get('content-type', '').lower():
            raise ValueError(f'robots.txt returned HTTP {status} or unexpected content; no room requests were made.')
        robots = RobotFileParser(url)
        robots.parse(text.splitlines())
        crawl_delay = robots.crawl_delay(ROBOT_AGENT)
        request_rate = robots.request_rate(ROBOT_AGENT)
        if crawl_delay:
            gate.interval = max(gate.interval, float(crawl_delay))
        if request_rate and request_rate.requests > 0 and request_rate.seconds > 0:
            gate.interval = max(gate.interval, request_rate.seconds / request_rate.requests)
        return robots
    raise ValueError('robots.txt retries exhausted; no room requests were made. Cooldown saved.')


def load_urls(path: Path, casing: str, limit: int | None) -> list[str]:
    urls = []
    seen = set()
    for line_number, line in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
        name = line.strip()
        if not name:
            continue
        if not re.fullmatch('[A-Za-z]{3}', name):
            raise ValueError(f'{path.name}:{line_number}: expected exactly three A-Z letters.')
        slug = name.lower() if casing == 'lower' else name
        url = ORIGIN + '/' + slug
        if url not in seen:
            seen.add(url)
            urls.append(url)
    if not urls:
        raise ValueError('The input file has no valid names.')
    return urls[:limit] if limit else urls


class RunLock:
    """OS-held lock: released even after a crash; prevents duplicate concurrent runs."""
    def __init__(self, path: Path):
        self.path = path
        self.file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open('a+b')
        if self.path.stat().st_size == 0:
            self.file.write(b'0')
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            raise ValueError('Another process is using this output/checkpoint. Wait for it to finish.') from exc
        return self

    def __exit__(self, *_):
        self.file.close()


async def run_batch(urls: list[str], store: ResultStore, options: Options, refresh=False,
                    transport: httpx.AsyncBaseTransport | None = None) -> Counter:
    pending = [url for url in urls if refresh or not (old := store.get(url)) or old.status not in DONE]
    store.rebuild(urls, completed_only=not refresh)
    if not pending:
        print(f'All {len(urls)} URLs already recorded; no requests needed.')
        return Counter(store.get(url).status for url in urls)
    gate = RequestGate(options.interval, store.pause_until)
    wait_remaining = store.saved_cooldown() - time.time()
    if wait_remaining > 0:
        print(f'Respecting saved server cooldown: {wait_remaining:.1f}s.', file=sys.stderr)
        await gate.pause(wait_remaining)
    queue = asyncio.Queue()
    for url in pending:
        queue.put_nowait(url)
    finished = 0
    consecutive_errors = 0
    limits = httpx.Limits(max_connections=options.workers, max_keepalive_connections=options.workers)
    timeout = httpx.Timeout(options.timeout)
    try:
        async with httpx.AsyncClient(headers={'User-Agent': USER_AGENT, 'Accept': 'text/html,application/xhtml+xml'},
                                     timeout=timeout, limits=limits, follow_redirects=False,
                                     trust_env=False, transport=transport) as client:
            robots = await load_robots(client, gate, options)
            print(f'{len(pending)} URLs pending; {options.workers} workers, one shared request start every {gate.interval:g}s.')

            async def worker():
                nonlocal finished, consecutive_errors
                while True:
                    try:
                        url = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        return
                    try:
                        result = await fetch_one(client, url, gate, options, robots)
                    except Exception as exc:
                        result = Result(url, 'error', detail=f'Unexpected {type(exc).__name__}: {exc}')
                    # Store writes have a single event-loop writer; no interleaved TXT blocks.
                    store.save(result)
                    finished += 1
                    if result.status == 'error':
                        consecutive_errors += 1
                        if consecutive_errors >= options.max_errors:
                            gate.stop('Repeated failures; stopped instead of continuing with an unsupported response format or outage.')
                    elif result.status in DONE:
                        consecutive_errors = 0
                    if result.status != 'not_attempted' or finished % 100 == 0:
                        print(f'[{finished}/{len(pending)}] {result.status}: {url}', flush=True)

            async with asyncio.TaskGroup() as group:
                for _ in range(options.workers):
                    group.create_task(worker())
    finally:
        # This restores input order and removes older/retried copies from the live log.
        store.rebuild(urls)
    return Counter(store.get(url).status for url in urls if store.get(url))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--input', type=Path, default=ROOT / 'all_three_letter_names.txt')
    parser.add_argument('--output', type=Path, default=ROOT / 'codeshare_results.txt')
    parser.add_argument('--workers', type=int, default=4, help='Concurrent requests, 1-16 (default: 4).')
    parser.add_argument('--interval', type=float, default=1.0, help='Minimum seconds between starts across ALL workers (default: 1).')
    parser.add_argument('--attempts', type=int, default=4, help='Maximum attempts per URL, including redirects (default: 4).')
    parser.add_argument('--timeout', type=float, default=30.0, help='Network inactivity timeout in seconds.')
    parser.add_argument('--max-bytes', type=int, default=8 * 1024 * 1024, help='Maximum decompressed HTML bytes per room.')
    parser.add_argument('--max-errors', type=int, default=5, help='Stop after this many consecutive errors.')
    parser.add_argument('--case', choices=['lower', 'preserve'], default='lower', help='URL path casing. Codeshare IDs may be case-sensitive.')
    parser.add_argument('--limit', type=int, help='Process only the first N distinct input URLs.')
    parser.add_argument('--refresh', action='store_true', help='Fetch successful saved URLs again; default resumes unfinished URLs.')
    parser.add_argument('--dry-run', action='store_true', help='Validate input and print settings without network requests or output writes.')
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= 16 or not 1 <= args.attempts <= 10:
        parser.error('--workers must be 1-16 and --attempts must be 1-10.')
    if not math.isfinite(args.interval) or args.interval < 0.25:
        parser.error('--interval must be finite and at least 0.25 seconds.')
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('--timeout must be finite and positive.')
    if args.max_bytes < 1024 or args.max_errors < 1 or (args.limit is not None and args.limit < 1):
        parser.error('--max-bytes must be >=1024, --max-errors and --limit must be positive.')
    try:
        urls = load_urls(args.input, args.case, args.limit)
        options = Options(args.workers, args.interval, args.attempts, args.timeout, args.max_bytes, args.max_errors)
        if args.dry_run:
            print(json.dumps({'urls': len(urls), 'input': str(args.input), 'output': str(args.output),
                              'case': args.case, **asdict(options)}, indent=2))
            return 0
        if args.output.resolve() == args.input.resolve():
            raise ValueError('--output cannot overwrite the input name list.')
        if args.output.suffix.lower() != '.txt':
            raise ValueError('--output must use a .txt extension; the checkpoint uses .sqlite3.')
        with RunLock(args.output.with_suffix('.lock')):
            store = ResultStore(args.output)
            try:
                counts = asyncio.run(run_batch(urls, store, options, args.refresh))
            finally:
                store.close()
        print(json.dumps({'output': str(args.output), 'counts': dict(counts)}, indent=2))
        return 2 if any(status not in DONE for status in counts) else 0
    except KeyboardInterrupt:
        print('Interrupted. Completed results are saved; run the same command to resume.', file=sys.stderr)
        return 130
    except ExceptionGroup as exc:
        print(f'Worker failure: {exc}. Completed results remain in the checkpoint.', file=sys.stderr)
        return 2
    except (OSError, ValueError, sqlite3.Error, ResponseTooLarge, UnicodeError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
