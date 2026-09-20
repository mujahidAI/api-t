"""Fetch Codeshare snapshots for common name or generic slug lists.

Usage: python fetch_codeshares_common_names.py --workers 4 --interval 1

This runner reuses fetch_codeshares.py for the actual HTTP, retry, resume, and
output behavior. The only difference is input validation: entries may be ordinary
name or project-style slugs instead of exactly three letters.
"""
from __future__ import annotations

from pathlib import Path
import re
import sys

import fetch_codeshares as base


ROOT = Path(__file__).resolve().parent
DEFAULT_INPUT = ROOT / "most_common_english_names_1000_lowercase.txt"
DEFAULT_OUTPUT = ROOT / "codeshare_common_names_results.txt"
ROOM_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")


def load_name_urls(path: Path, casing: str, limit: int | None) -> list[str]:
    urls = []
    seen = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        name = line.strip()
        if not name:
            continue
        if not ROOM_SLUG.fullmatch(name):
            raise ValueError(
                f"{path.name}:{line_number}: expected 1-64 letters, digits, underscores, or hyphens for a room slug."
            )
        slug = name.lower() if casing == "lower" else name
        url = base.ORIGIN + "/" + slug
        if url not in seen:
            seen.add(url)
            urls.append(url)
    if not urls:
        raise ValueError("The input file has no valid names.")
    return urls[:limit] if limit else urls


def with_common_defaults(argv: list[str]) -> list[str]:
    args = list(argv)
    if "--input" not in args:
        args.extend(["--input", str(DEFAULT_INPUT)])
    if "--output" not in args:
        args.extend(["--output", str(DEFAULT_OUTPUT)])
    return args


def main(argv: list[str] | None = None) -> int:
    base.load_urls = load_name_urls
    return base.main(with_common_defaults(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
