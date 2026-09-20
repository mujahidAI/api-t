"""Reconstruct a Codeshare editor's initial text from public Next.js HTML.

This module never executes page JavaScript or opens a network connection. The
schema and Firepad operation encoding were checked against Codeshare's public
application bundles; see research/render/FINDINGS.md for provenance and limits.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any


@dataclass(frozen=True)
class Extraction:
    status: str
    content: str = ""
    detail: str = ""


class _InvalidState(ValueError):
    pass


class _NextData(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.found = 0
        self.active = False
        self.closed = False
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self.in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self.in_title = True
        if tag == "script" and dict(attrs).get("id") == "__NEXT_DATA__":
            self.found += 1
            self.active = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.in_title = False
        if tag == "script" and self.active:
            self.active = False
            self.closed = True

    def handle_data(self, data: str) -> None:
        if self.active:
            self.parts.append(data)
        elif self.in_title:
            self.title_parts.append(data)


_BASE62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def _history_index(value: Any) -> int:
    if not isinstance(value, str) or not 2 <= len(value) <= 53:
        raise _InvalidState("Invalid history revision ID.")
    if value[0] != _BASE62[len(value) + 8]:
        raise _InvalidState("Invalid history revision prefix.")
    if len(value) > 2 and value[1] == "0":
        raise _InvalidState("History revision has an unexpected leading zero.")
    index = 0
    for char in value[1:]:
        digit = _BASE62.find(char)
        if digit < 0:
            raise _InvalidState("Invalid history revision digit.")
        index = index * 62 + digit
    if index > 2**53 - 1:
        raise _InvalidState("History revision exceeds JavaScript's integer range.")
    return index


def _apply_operation(document: bytes, operations: Any) -> bytes:
    """Apply Firepad's JSON op format using JavaScript UTF-16 code unit counts."""
    if not isinstance(operations, list) or not operations:
        raise _InvalidState("Missing or unsupported text operation.")
    result: list[bytes] = []
    position = 0
    pending_attributes = False
    for operation in operations:
        if isinstance(operation, dict):
            if pending_attributes:
                raise _InvalidState("Consecutive text-operation attribute objects.")
            pending_attributes = True
            continue
        pending_attributes = False
        if isinstance(operation, str):
            result.append(operation.encode("utf-16-le", errors="surrogatepass"))
        elif type(operation) is int:
            length = abs(operation) * 2
            end = position + length
            if end > len(document):
                raise _InvalidState("Text operation exceeds the preceding document length.")
            if operation > 0:
                result.append(document[position:end])
            position = end
        else:
            raise _InvalidState("Unsupported value in text operation.")
    if pending_attributes:
        raise _InvalidState("Text-operation attributes have no following operation.")
    if position != len(document):
        raise _InvalidState("Text operation does not consume the preceding document.")
    return b"".join(result)


def _record(records: dict[str, Any], link: Any, typename: str) -> dict[str, Any]:
    if not isinstance(link, dict) or not isinstance(link.get("__ref"), str):
        raise _InvalidState(f"Missing {typename} record reference.")
    value = records.get(link["__ref"])
    if not isinstance(value, dict) or value.get("__typename") != typename:
        raise _InvalidState(f"Missing or unsupported {typename} record.")
    return value


def _room_text(props: dict[str, Any], requested_name: str) -> Extraction:
    expected_id = base64.b64encode(f"Codeshare:{requested_name}".encode()).decode()
    if props.get("codeshareId") != expected_id:
        raise _InvalidState("The returned room ID does not match the requested address.")
    query = props.get("queryProps")
    records = props.get("queryRecords")
    if not isinstance(query, dict) or not isinstance(records, dict):
        raise _InvalidState("Missing server-rendered Relay state.")
    selected_room = query.get("codeshare")
    if not isinstance(selected_room, dict) or selected_room.get("id") != expected_id:
        raise _InvalidState("Room data is unavailable; this does not establish an empty room.")
    room = _record(records, {"__ref": expected_id}, "Codeshare")
    if room.get("id") != expected_id:
        raise _InvalidState("The returned room record has an inconsistent ID.")
    history_keys = [key for key in room if re.fullmatch(r"codeHistory\([^)]*\)", key)]
    if len(history_keys) != 1:
        raise _InvalidState("Missing or ambiguous initial history connection.")
    # A non-null 'after' would omit an unknown prefix. Initial Next.js state uses
    # codeHistory(first:1000); Relay omits null arguments from storage keys.
    if not re.fullmatch(r"codeHistory\(first:[1-9][0-9]*\)", history_keys[0]):
        raise _InvalidState("Unsupported initial history connection arguments.")
    connection = _record(records, room[history_keys[0]], "CodeHistoryConnection")
    page_info = _record(records, connection.get("pageInfo"), "PageInfo")
    if page_info.get("hasNextPage") is not False:
        raise _InvalidState("History is incomplete or requires another page; text was not saved.")
    edge_links = connection.get("edges")
    if not isinstance(edge_links, dict) or not isinstance(edge_links.get("__refs"), list):
        raise _InvalidState("Missing initial history edge list.")

    document = b""
    checkpoint_index = -1
    if "codeCheckpoint" not in room:
        raise _InvalidState("Missing checkpoint state.")
    if room["codeCheckpoint"] is not None:
        checkpoint = _record(records, room["codeCheckpoint"], "CodeCheckpoint")
        checkpoint_edge = _record(records, checkpoint.get("codeHistoryEdge"), "CodeHistoryEdge")
        checkpoint_node = _record(records, checkpoint_edge.get("node"), "CodeHistory")
        if checkpoint.get("codeHistoryId") != checkpoint_node.get("id"):
            raise _InvalidState("Checkpoint history ID does not match its revision.")
        checkpoint_index = _history_index(checkpoint_node.get("historyId"))
        document = _apply_operation(b"", checkpoint.get("value"))

    revisions: dict[int, Any] = {}
    for edge_id in edge_links["__refs"]:
        edge = _record(records, {"__ref": edge_id}, "CodeHistoryConnectionEdge")
        node = _record(records, edge.get("node"), "CodeHistory")
        index = _history_index(node.get("historyId"))
        # The application prepends the checkpoint and ignores old revisions.
        if index <= checkpoint_index:
            continue
        if index in revisions:
            if revisions[index] != node.get("value"):
                raise _InvalidState("Conflicting operations for one history revision.")
            continue
        revisions[index] = node.get("value")

    expected_revision = checkpoint_index + 1
    for index in sorted(revisions):
        if index != expected_revision:
            raise _InvalidState("History contains a gap; text was not saved.")
        document = _apply_operation(document, revisions[index])
        expected_revision += 1
    try:
        content = document.decode("utf-16-le")
    except UnicodeDecodeError as exc:
        raise _InvalidState("Reconstructed text contains unpaired UTF-16 surrogates.") from exc
    detail = "Reconstructed the complete text snapshot embedded in the initial HTML."
    if room.get("migratedAt"):
        detail += " This legacy room is marked as moved to new.codeshare.io; the new site was not queried."
    return Extraction("ok" if content else "empty", content, detail)


def parse_codeshare_html(html: str, requested_name: str) -> Extraction:
    """Return text only after validating the full initial room history.

    HTTP status and redirects belong to the caller. Unknown HTML, challenges,
    null/missing data, truncated history, and invalid operations are never
    silently classified as empty. An empty result requires a valid room state.
    """
    try:
        parser = _NextData()
        parser.feed(html)
        parser.close()
        if not parser.found:
            title = " ".join(parser.title_parts).lower()
            if (
                any(marker in title for marker in ("just a moment", "access denied", "attention required"))
                or "cf-chl-" in html
                or "challenges.cloudflare.com" in html
                or "Please update your browser." in html
            ):
                return Extraction("blocked", detail="The site returned an access or browser-compatibility page.")
            return Extraction("error", detail="No supported server-rendered editor state was found.")
        if parser.found != 1 or not parser.closed or parser.active:
            raise _InvalidState("The page has duplicate or incomplete Next.js data.")
        data = json.loads("".join(parser.parts))
        if not isinstance(data, dict):
            raise _InvalidState("Unsupported Next.js data structure.")
        props = data.get("props")
        if not isinstance(props, dict) or not isinstance(props.get("pageProps"), dict):
            raise _InvalidState("Missing Next.js page properties.")
        page_props = props["pageProps"]
        if data.get("page") in ("/login", "/register"):
            return Extraction("blocked", detail="The site returned an authentication page.")
        if data.get("page") == "/_error":
            status = page_props.get("statusCode", page_props.get("status"))
            if status in (404, 410):
                return Extraction("not_found", detail=f"The page reports HTTP {status}.")
            if status in (401, 403, 429):
                return Extraction("blocked", detail=f"The page reports HTTP {status}.")
            raise _InvalidState("The site returned an error page.")
        if data.get("page") != "/[codeshareId]":
            raise _InvalidState("The response is not the supported Codeshare editor page.")
        return _room_text(page_props, requested_name)
    except (_InvalidState, json.JSONDecodeError, TypeError, ValueError, OverflowError, RecursionError) as exc:
        # Our own diagnostics contain no room text, tokens, or personal data.
        detail = str(exc) if isinstance(exc, _InvalidState) else "Malformed or unsupported embedded editor state."
        return Extraction("error", detail=detail)
