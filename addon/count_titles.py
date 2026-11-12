#!/usr/bin/env python3
"""Print the titles row count from the live SQLite (used by the daily refresh).

Kept as a file so the shell's command substitution `$( )` sees no parens —
an inline `python3 -c "..."` with nested `print(...)` gets truncated by bash's
`$( )` parser ("'(' was never closed").
"""
import sqlite3
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "debrid-app/data/titles.sqlite"
print(sqlite3.connect(path).execute("SELECT COUNT(*) FROM titles").fetchone()[0])
