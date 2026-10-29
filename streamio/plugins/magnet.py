"""Magnet plugin: writes one magnet link per line, with an optional push to a torrent client."""
from __future__ import annotations

import http.cookiejar
import json
import sys
import urllib.parse
import urllib.request
from base64 import b64encode
from pathlib import Path

from ..core import Plugin, register


def _magnet_lines(record: dict) -> list[str]:
    raw = record.get("magnet") or ""
    return [line for line in raw.splitlines() if "urn:btih" in line]


@register
class MagnetPlugin(Plugin):
    name = "magnet"

    def default_filename(self) -> str:
        return "magnets.txt"

    def consume(self, records, out_path: Path) -> None:
        lines: list[str] = []
        for record in records:
            lines.extend(_magnet_lines(record))
        with out_path.open("w", encoding="utf-8") as handle:
            for line in lines:
                handle.write(line + "\n")


def push_magnets(
    records,
    base_url: str = "http://localhost:8080",
    user: str = "",
    password: str = "",
    client: str = "qbit",
    timeout: int = 15,
) -> int:
    """Push magnet links to a torrent client.

    Supports qBittorrent (cookie-authenticated Web UI RPC) and Transmission
    (JSON-RPC). Returns the number of magnets successfully pushed.
    """
    if client == "transmission":
        return _push_transmission(records, base_url, user, password, timeout)
    return _push_qbit(records, base_url, user, password, timeout)


def _push_qbit(records, base_url, user, password, timeout) -> int:
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    base_url = base_url.rstrip("/")
    if user:
        data = urllib.parse.urlencode({"username": user, "password": password}).encode()
        req = urllib.request.Request(f"{base_url}/api/login", data=data, method="POST")
        try:
            opener.open(req, timeout=timeout).read()
        except Exception as exc:
            print(f"[streamio] qBittorrent login failed: {exc}", file=sys.stderr)
            return 0
    pushed = 0
    for record in records:
        for line in _magnet_lines(record):
            url = f"{base_url}/api/torrents/add?magnet={urllib.parse.quote_plus(line)}"
            try:
                resp = opener.open(urllib.request.Request(url, data=b"", method="POST"), timeout=timeout)
                body = resp.read().decode(errors="replace")
                if resp.getcode() in (200, 201):
                    pushed += 1
                elif "Duplicate" not in body:
                    print(f"[streamio] qBittorrent rejected {line[:48]}: {body[:120]}", file=sys.stderr)
            except Exception as exc:
                print(f"[streamio] push failed {line[:48]}: {exc}", file=sys.stderr)
    return pushed


def _push_transmission(records, base_url, user, password, timeout) -> int:
    base_url = base_url.rstrip("/")
    auth: dict[str, str] = {}
    if user:
        auth = {"Authorization": "Basic " + b64encode(f"{user}:{password}".encode()).decode()}
    pushed = 0
    for record in records:
        name = f"{record.get('titleno', '')} {record.get('title', '')}".strip()
        for line in _magnet_lines(record):
            payload = {
                "method": "torrent-add",
                "params": {"magnetLink": line, "filename": name},
                "id": 1,
            }
            req = urllib.request.Request(
                f"{base_url}/transmission/rpc",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json-rpc", **auth},
                method="POST",
            )
            try:
                resp = urllib.request.urlopen(req, timeout=timeout)
                resp.read()
                pushed += 1
            except Exception as exc:
                print(f"[streamio] transmission push failed {line[:48]}: {exc}", file=sys.stderr)
    return pushed
