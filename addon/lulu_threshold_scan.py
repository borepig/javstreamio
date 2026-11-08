#!/usr/bin/env python3
"""One-off: compute the best fuzzy match score for every hnt row and
bucket the distribution by threshold bands, to decide how far the threshold
can be lowered for more lulu_token coverage."""
import sys, os, sqlite3, difflib
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from enrich_lulu import load_catalog, open_db, series_from_hnt_name, norm, tokenize, match_title

def main():
    con = open_db()
    rows = con.execute("SELECT id, name FROM titles WHERE id LIKE 'hnt:%'").fetchall()
    catalog = load_catalog()
    cdata = [(item["slug"], norm(item["title"]), tokenize(item["title"])) for item in catalog]
    dist = Counter()
    no_series = 0
    for tid, name in rows:
        series = series_from_hnt_name(name)
        if not series:
            no_series += 1
            continue
        s_norm = norm(series)
        st = tokenize(series)
        best = 0.0
        best_slug = ""
        for slug, c_norm, ct in cdata:
            ratio = difflib.SequenceMatcher(None, s_norm, c_norm).ratio()
            inter = set(st) & set(ct)
            union = set(st) | set(ct)
            tok = len(inter) / len(union) if union else 0.0
            score = max(ratio, tok)
            if score > best:
                best = score
                best_slug = slug
        if best >= 0.40:
            dist[">=0.40"] += 1
        elif best >= 0.35:
            dist["0.35-0.40"] += 1
        elif best >= 0.30:
            dist["0.30-0.35"] += 1
        elif best >= 0.25:
            dist["0.25-0.30"] += 1
        else:
            dist["<0.25"] += 1
    print("rows:", len(rows), "no-series:", no_series)
    for band in [">=0.40", "0.35-0.40", "0.30-0.35", "0.25-0.30", "<0.25"]:
        print(f"  {band}: {dist.get(band, 0)}")
    con.close()

if __name__ == "__main__":
    main()
