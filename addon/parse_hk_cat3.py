#!/usr/bin/env python3
"""Parse the Wikipedia Cat III markdown dumps into clean (title, year) lists.

Reads the two persisted firecrawl markdown files (EN + ZH), extracts film
titles with their release years, and writes structured JSON for downstream
AV-vs-horror classification.
"""
import json
import re
import sys

EN_FILE = "/home/joe/.qwen/tmp/73b93af1d3b8a3804339991ec95dbfbdafc46f6a31cca585d32cb178d6a08954/tool-results/chatcmpl-tool-a62c9a080a6d2298.txt"
ZH_FILE = "/home/joe/.qwen/tmp/73b93af1d3b8a3804339991ec95dbfbdafc46f6a31cca585d32cb178d6a08954/tool-results/chatcmpl-tool-b3c9dddc328297c2.txt"

YEAR_RE = re.compile(r"（(\d{4})）|\((\d{4})")
# A film entry line looks like:  [Title](url)（1993）  or  * _Title_ (1993)
ENTRY_RE = re.compile(
    r"\*?\s*(?:_)?\[(?P<title>[^\]]+)\]\([^)]+\)\s*(?:_)?\s*[（(](?P<year>\d{4})[)）]",
)
PLAIN_RE = re.compile(r"^\*?\s*_?([^\(\[\n]+?)_?\s*[（(](\d{4})[)）]")


def load_markdown(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("markdown", "")


def extract_entries(md):
    """Return list of (title, year) from a wikipedia markdown list.

    Uses findall over the whole string (the ZH dump is a single line).
    """
    seen = set()
    entries = []
    for m in re.finditer(ENTRY_RE, md):
        title = m.group("title").strip()
        year = m.group("year")
        key = (title, year)
        if key not in seen:
            seen.add(key)
            entries.append((title, year))
    return entries


def main():
    en_md = load_markdown(EN_FILE)
    zh_md = load_markdown(ZH_FILE)

    en = extract_entries(en_md)
    zh = extract_entries(zh_md)

    out = {
        "en_count": len(en),
        "zh_count": len(zh),
        "en": en,
        "zh": zh,
    }
    out_path = "/home/joe/Projects/JAVSTREAMIO/addon/hk_cat3_parsed.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"EN entries: {len(en)}")
    print(f"ZH entries: {len(zh)}")
    print(f"Wrote {out_path}")
    # Also dump the ZH list as plain lines for quick review.
    zh_path = "/home/joe/Projects/JAVSTREAMIO/addon/hk_cat3_zh_list.txt"
    with open(zh_path, "w", encoding="utf-8") as f:
        for title, year in zh:
            f.write(f"{year}\t{title}\n")
    print(f"Wrote {zh_path}")


if __name__ == "__main__":
    main()
