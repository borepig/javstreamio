"""StreamIO pipeline: optionally scrape a forum, then stream records through a plugin."""
from __future__ import annotations

import json
from pathlib import Path

from . import plugins  # noqa: F401  (ensure built-ins are registered)
from .core import run_plugin

SCRAPE_OUT_NAME = "streamio-scrape.jsonl"


def read_records(path: str | Path) -> list[dict]:
    """Read a scrape.py JSONL file into a list of record dicts."""
    records: list[dict] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def stream_from_file(input_path: str | Path, plugin_name: str, out_dir: str | Path = "data") -> dict:
    """Consume an existing JSONL data file through the named plugin."""
    records = read_records(input_path)
    if not records:
        raise FileNotFoundError(f"No valid records in {input_path}")
    result = run_plugin(plugin_name, records, out_dir)
    result["source"] = str(Path(input_path).resolve())
    return result


def stream_from_scrape(
    url: str,
    pages: int,
    plugin_name: str,
    out_dir: str | Path = "data",
    batch: int = 6,
    delay: float = 0.2,
) -> dict:
    """Full pipeline: run scrape.py's details mode, then stream the records."""
    # Lazy import: file-only mode must not require playwright/bs4.
    from scrape import scrape_forum_details

    out_path = Path(out_dir) / SCRAPE_OUT_NAME
    out_path.parent.mkdir(parents=True, exist_ok=True)
    scrape_forum_details(url, pages, out_path, batch, delay)
    records = read_records(out_path)
    if not records:
        raise RuntimeError(f"Scrape produced no records at {out_path}")
    result = run_plugin(plugin_name, records, out_dir)
    result["source"] = str(out_path)
    return result
