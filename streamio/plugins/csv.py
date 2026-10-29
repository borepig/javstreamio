"""CSV plugin: tabular export of the record stream for spreadsheet review."""
from __future__ import annotations

import csv as csv_mod
from pathlib import Path

from ..core import Plugin, register

COLUMNS = ["url", "tag", "titleno", "title", "出演女优", "影片容量", "是否有码", "posted_at", "magnet"]


@register
class CsvPlugin(Plugin):
    name = "csv"

    def default_filename(self) -> str:
        return "catalog.csv"

    def consume(self, records, out_path: Path) -> None:
        # utf-8-sig so CJK cells render correctly in Excel
        with out_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv_mod.writer(handle)
            writer.writerow(COLUMNS)
            for record in records:
                writer.writerow([
                    "" if record.get(col) is None else str(record.get(col, ""))
                    for col in COLUMNS
                ])
