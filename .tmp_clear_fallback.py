#!/usr/bin/env python3
"""Clear poster/background on the 170 SVG-card rows so the re-run re-processes them."""
import re
import sqlite3

LOG = open(".hk_cat3_poster_run.log", encoding="utf-8").read()
slugs = [f"hk:cat3-{int(m.group(1)) - 1}"
          for m in re.finditer(r"^\[hkcat3-poster\] (\d+)/172 .* -> SVG card", LOG, re.M)]
print(f"fallback slugs: {len(slugs)}")

conn = sqlite3.connect("debrid-app/data/titles.sqlite")
conn.execute("PRAGMA journal_mode=WAL")
for s in slugs:
    conn.execute("UPDATE titles SET poster = NULL, background = NULL WHERE id = ?", (s,))
conn.commit()
print("cleared", conn.total_changes, "rows")
conn.close()
