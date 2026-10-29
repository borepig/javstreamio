"""StreamIO core: plugin base, registry, and runner.

A record is the dict shape scrape.py writes: url, tag, titleno, title,
出演女优, 影片容量, 是否有码, posted_at, preview_images, magnet.
"""
from __future__ import annotations

from abc import ABC
from pathlib import Path
from typing import Iterable, Sequence

_PLUGINS: dict[str, type[Plugin]] = {}


class Plugin(ABC):
    """Turns a stream of records into an output file."""

    name: str = "base"

    def default_filename(self) -> str:
        raise NotImplementedError

    def consume(self, records: Iterable[dict], out_path: Path) -> None:
        raise NotImplementedError


def register(cls: type[Plugin]) -> type[Plugin]:
    _PLUGINS[cls.name] = cls
    return cls


def available_plugins() -> list[str]:
    return sorted(_PLUGINS)


def run_plugin(name: str, records: Sequence[dict] | Iterable[dict], out_dir: str | Path = "data") -> dict:
    """Run the named plugin over the record stream and return its result."""
    records = list(records)
    cls = _PLUGINS.get(name)
    if cls is None:
        raise KeyError(f"Unknown plugin '{name}'. Available: {', '.join(available_plugins())}")
    plugin = cls()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    out_path = out / plugin.default_filename()
    plugin.consume(records, out_path)
    return {
        "plugin": name,
        "out": str(out_path),
        "records": records,
        "record_count": len(records),
    }
