"""Mark DMM NOW PRINTING stubs in titles.sqlite so the addon can fall back."""
from __future__ import annotations

import argparse
import re
import sqlite3
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SQLITE = ROOT / "debrid-app" / "data" / "titles.sqlite"
CODE_RE = re.compile(r"^jav:([A-Za-z]{2,})[-_](\d+)$")
STUB_PS = 4000
UA = {"User-Agent": "javstreamio-poster-probe"}


def digital_ps(title_id: str) -> str:
    raw = (title_id or "").replace("jav:", "")
    if raw.lower().startswith("thread") or raw.lower().startswith("fc2"):
        return ""
    match = CODE_RE.match(title_id or "")
    if not match:
        return ""
    code = match.group(1).lower() + match.group(2).zfill(5)
    return f"https://pics.dmm.co.jp/digital/video/{code}/{code}ps.jpg"


def is_stub(url: str) -> bool:
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = resp.read()
            final = resp.geturl()
    except Exception:
        return True
    if "now_printing" in final or "noimage" in final:
        return True
    return len(data) < STUB_PS


def probe_ids(ids: list[str], workers: int) -> list[str]:
    stubs: list[str] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(is_stub, digital_ps(title_id)): title_id for title_id in ids if digital_ps(title_id)}
        done = 0
        for future in as_completed(futures):
            title_id = futures[future]
            done += 1
            if future.result():
                stubs.append(title_id)
            if done % 500 == 0:
                print(f"probe: {done}/{len(futures)} checked, {len(stubs)} stubs", flush=True)
    return stubs


def main() -> None:
    parser = argparse.ArgumentParser(description="Flag DMM NOW PRINTING stubs in titles.sqlite")
    parser.add_argument("--db", default=str(SQLITE))
    parser.add_argument("--workers", type=int, default=20)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    conn = sqlite3.connect(args.db)
    sql = "SELECT id FROM titles WHERE dmm_missing=0"
    ids = [row[0] for row in conn.execute(sql)]
    if args.limit:
        ids = ids[: args.limit]
    print(f"probe: {len(ids)} unmarked titles", flush=True)
    stubs = probe_ids(ids, args.workers)
    conn.executemany("UPDATE titles SET dmm_missing=1 WHERE id=?", [(title_id,) for title_id in stubs])
    conn.commit()
    flagged = conn.execute("SELECT count(*) FROM titles WHERE dmm_missing=1").fetchone()[0]
    conn.close()
    print(f"probe: marked {len(stubs)} new stubs; dmm_missing now {flagged}")


if __name__ == "__main__":
    main()
