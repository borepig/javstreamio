"""Attach the Sukebei "Sister Breeder" eng-subbed magnets to the 6 hnt episode rows.

The r2.1hanime.com "Online" mp4 is Cloudflare-protected (403), so playback
must go through a debrid service using these magnets (English Softsubs).
Mapping (paired torrents cover 2 episodes each):
  ep1,ep2 -> 01-02  hash 040c00f65a878973c507145f8579ad98abeddba5
  ep3,ep4 -> 03-04  hash 80d5dc816b715a6d3adc1fb0152abd99192439da
  ep5,ep6 -> 05-06  hash 6ea922404f20238b58a9e01d5d488d078c96970c
"""
import sqlite3

DB = "debrid-app/data/titles.sqlite"
TRACKERS = (
    "udp://tracker.nyaa.net:6969/announce"
    "&tr=http://sukebei.tracker.wf:8888/announce"
    "&tr=udp://open.stealth.si:80/announce"
    "&tr=udp://tracker.opentrackr.org:1337/announce"
    "&tr=udp://exodus.desync.com:6969/announce"
    "&tr=udp://tracker.torrent.eu.org:451/announce"
)
HASH_01_02 = "040c00f65a878973c507145f8579ad98abeddba5"
HASH_03_04 = "80d5dc816b715a6d3adc1fb0152abd99192439da"
HASH_05_06 = "6ea922404f20238b58a9e01d5d488d078c96970c"

def magnet_for(hash):
    return f"magnet:?xt=urn:btih:{hash}{TRACKERS}"

# episode_num -> info_hash
EP_HASH = {
    1: HASH_01_02,
    2: HASH_01_02,
    3: HASH_03_04,
    4: HASH_03_04,
    5: HASH_05_06,
    6: HASH_05_06,
}

db = sqlite3.connect(DB)
cur = db.cursor()
updated = 0
for ep, h in EP_HASH.items():
    mag = magnet_for(h)
    cur.execute(
        "UPDATE titles SET magnet = ?, info_hash = ? WHERE series_key = ? AND episode_num = ?",
        (mag, h, "sister-breeder", ep),
    )
    updated += cur.rowcount
db.commit()
print(f"updated {updated} sister-breeder episode rows with eng-subbed magnets")
for row in cur.execute(
    "SELECT episode_num, info_hash, length(magnet) FROM titles WHERE series_key='sister-breeder' AND episode_num > 0 ORDER BY episode_num"
):
    print(row)
db.close()
