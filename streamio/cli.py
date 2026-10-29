"""Command line interface for the StreamIO plugin runner."""
from __future__ import annotations

import argparse
import os

from . import available_plugins
from .pipeline import stream_from_file, stream_from_scrape
from .plugins.magnet import push_magnets


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="streamio",
        description="StreamIO: stream scraped JAV records through pluggable output plugins.",
    )
    source = parser.add_argument_group("source (choose one)")
    source.add_argument("--input", help="JSONL data file produced by scrape.py")
    source.add_argument("--scrape", metavar="URL", help="Forum URL to scrape first (full pipeline: scrape then stream)")
    opts = parser.add_argument_group("options")
    opts.add_argument("--pages", type=int, default=1, help="Forum pages to follow (0 = every page)")
    opts.add_argument("--batch", type=int, default=6, help="Threads fetched together (default: 6)")
    opts.add_argument("--delay", type=float, default=0.2, help="Seconds between batches (default: 0.2)")
    opts.add_argument("--plugin", default="jsonl", choices=available_plugins(), help="Output plugin to stream records through")
    opts.add_argument("--out-dir", default="data", help="Directory for plugin output (default: data/)")
    push = parser.add_argument_group("torrent client push (optional)")
    push.add_argument("--push", action="store_true", help="After writing, push magnet links to a torrent client")
    push.add_argument("--torrent-client", choices=["qbit", "transmission"], default="qbit", help="Which torrent client RPC to push to")
    push.add_argument("--torrent-base", default=os.environ.get("QBIT_URL", "http://localhost:8080"))
    push.add_argument("--torrent-user", default=os.environ.get("QBIT_USER", ""))
    push.add_argument("--torrent-pass", default=os.environ.get("QBIT_PASS", ""))
    parser.add_argument("--list", action="store_true", help="List available plugins and exit")
    args = parser.parse_args(argv)

    if args.list:
        print("Available StreamIO plugins:")
        for name in available_plugins():
            print(f"  {name}")
        return 0

    if args.scrape:
        result = stream_from_scrape(args.scrape, args.pages, args.plugin, args.out_dir, args.batch, args.delay)
    elif args.input:
        result = stream_from_file(args.input, args.plugin, args.out_dir)
    else:
        parser.error("Provide --input FILE or --scrape URL")

    print(f"[streamio] {result['plugin']} wrote {result['record_count']} records -> {result['out']}")
    if args.push:
        pushed = push_magnets(
            result["records"],
            base_url=args.torrent_base,
            user=args.torrent_user,
            password=args.torrent_pass,
            client=args.torrent_client,
        )
        print(f"[streamio] pushed {pushed} magnets to {args.torrent_client}")
    return 0
