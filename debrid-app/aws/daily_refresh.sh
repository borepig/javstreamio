#!/usr/bin/env bash
# Daily metadata refresh for JAVSTREAMIO.
#
# Runs every category's incremental scraper (hentai.tv, sehuatang forum 103,
# Sukebei), and when new titles land in the live SQLite it bumps the addon
# version (so Stremio refetches), commits, pushes, and redeploys to EC2.
# Meant to be run from the dev machine's crontab (07:00 daily).
set -uo pipefail

ROOT=/home/joe/Projects/JAVSTREAMIO
LOG="$ROOT/.daily_refresh.log"
SSH_KEY=/home/joe/.ssh/javstremio-key.pem
EC2="ssh -i $SSH_KEY ubuntu@184.196.196.42"

cd "$ROOT"
{
  echo "=== daily refresh $(date '+%F %T') ==="

  BEFORE=$(python3 addon/count_titles.py)

  echo "--- hnt: new series + new episodes (hentai.tv -> 'hentai') ---"
  python3 addon/scrape_hentaitv.py --incremental

  echo "--- jav-censored / jav-uncensored / western / others (sehuatang forum 103) ---"
  python3 addon/build_addon.py --scrape-sqlite

  echo "--- hentai-censored / hentai-uncensored (Sukebei) ---"
  python3 addon/build_addon.py --scrape-hentai

  AFTER=$(python3 addon/count_titles.py)
  ADDED=$((AFTER - BEFORE))
  echo "titles.sqlite rows: $BEFORE -> $AFTER ($ADDED new)"

  if [ "$ADDED" -le 0 ]; then
    echo "no new titles; skipping commit/deploy"
    exit 0
  fi

  # Bump the addon version so Stremio refetches catalogs.
  CUR=$(grep -oE 'ADDON_VERSION = "[0-9]+\.[0-9]+\.[0-9]+"' debrid-app/lib/version.js | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')
  MAJ=${CUR%%.*}
  REST=${CUR#*.}
  MIN=${REST%%.*}
  PAT=${REST#*.}
  NEXT="$MAJ.$MIN.$((PAT + 1))"
  echo "bumping $CUR -> $NEXT"
  sed -i "s/ADDON_VERSION = \"$CUR\"/ADDON_VERSION = \"$NEXT\"/" debrid-app/lib/version.js
  sed -i "s/^VERSION = \"$CUR\"/VERSION = \"$NEXT\"/" addon/build_addon.py
  python3 - "$CUR" "$NEXT" <<'PY'
import json, sys
cur, nxt = sys.argv[1], sys.argv[2]
p = "addon/manifest.json"
m = json.load(open(p))
m["version"] = nxt
m["description"] = f"Stremio JAV Database Addon {nxt}. This is only a metadata scraper."
json.dump(m, open(p, "w"), indent=2, ensure_ascii=False)
PY

  git add debrid-app/data/titles.sqlite data/forum-103-all.jsonl \
          addon/build_addon.py addon/manifest.json debrid-app/lib/version.js
  git commit -m "daily: +$ADDED titles (hnt + JAV + Sukebei); bump $NEXT"
  git push origin main
  echo "--- EC2 deploy ---"
  $EC2 "cd /opt/javstreamio && git fetch origin && git reset --hard origin/main && DOMAIN=javstremio.ddns.net sudo bash ./debrid-app/aws/deploy.sh"
  echo "=== done: $ADDED new titles live at https://javstremio.ddns.net (version $NEXT) ==="
} 2>&1 | tee -a "$LOG"
