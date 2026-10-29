"""JSONL pass-through plugin: rewrites the stream in the same record shape."""
from __future__ import annotations

import json
from pathlib import Path

from ..core import Plugin, register


@register
class JsonlPlugin(Plugin):
    name = "jsonl"

    def default_filename(self) -> str:
        return "streamio.jsonl"

    def consume(self, records, out_path: Path) -> None:
        with out_path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
