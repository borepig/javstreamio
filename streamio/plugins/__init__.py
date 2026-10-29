"""Built-in StreamIO plugins. Importing each module registers its plugin."""
from . import csv
from . import jsonl
from . import magnet
from . import rss

__all__ = ["csv", "jsonl", "magnet", "rss"]
