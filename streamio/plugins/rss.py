"""RSS 2.0 plugin: a syndication feed of the scraped catalog."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.etree import ElementTree as ET

from ..core import Plugin, register

TZ_CN = timezone(timedelta(hours=8))


def _to_rfc822(value: str) -> str:
    try:
        dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ_CN)
        return format_datetime(dt)
    except (TypeError, ValueError):
        return format_datetime(datetime.now(TZ_CN))


@register
class RssPlugin(Plugin):
    name = "rss"

    def default_filename(self) -> str:
        return "feed.xml"

    def consume(self, records, out_path: Path) -> None:
        rss = ET.Element("rss", {"version": "2.0"})
        channel = ET.SubElement(rss, "channel")
        ET.SubElement(channel, "title").text = "StreamIO — JAV catalog"
        ET.SubElement(channel, "link").text = "https://sehuatang.org/forum-103-1.html"
        ET.SubElement(channel, "description").text = "JAV titles streamed from sehuatang.org (forum 103)"
        ET.SubElement(channel, "lastBuildDate").text = format_datetime(datetime.now(TZ_CN))

        for record in records:
            item = ET.SubElement(channel, "item")
            titleno = record.get("titleno", "")
            title = record.get("title", "")
            ET.SubElement(item, "title").text = f"{titleno} | {title}" if titleno else title
            ET.SubElement(item, "link").text = record.get("url", "")
            magnet_lines = [l for l in (record.get("magnet") or "").splitlines() if l.strip()]
            magnet = magnet_lines[0] if magnet_lines else ""
            ET.SubElement(item, "description").text = "\n".join(
                [
                    f"出演女优: {record.get('出演女优', '')}",
                    f"容量: {record.get('影片容量', '')}",
                    f"有码/无码: {record.get('是否有码', '')}",
                    f"种子: {magnet}",
                ]
            )
            ET.SubElement(item, "pubDate").text = _to_rfc822(record.get("posted_at", ""))
            ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = record.get("url", "")

        tree = ET.ElementTree(rss)
        ET.indent(tree)
        tree.write(out_path, encoding="utf-8", xml_declaration=True)
