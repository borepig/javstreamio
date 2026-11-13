"""Best-effort magnet hunt for 欲望都市之3P诱惑 (2006) — SearXNG variant queries."""
import json
import urllib.parse
import urllib.request

SEARXNG = "http://192.168.68.83:8888"
QUERIES = [
    "欲望都市 3P",
    "3P诱惑",
    "欲望都市 诱惑",
    "Lust City 2006",
    "City of Desire 3P",
    "Metropolis 3P 2006",
    "欲望都市之3P诱惑 2006",
]
KEYS = ["欲望都市", "3P", "lust", "city", "metropolis", "temptation", "诱惑", "都市"]

for q in QUERIES:
    url = f"{SEARXNG}/search?q={urllib.parse.quote(q, safe='')}&format=json&pageno=1&engines=bt4g,solidtorrents,piratebay"
    req = urllib.request.Request(url, headers={"User-Agent": "javstreamio-hkcat3/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    res = data.get("results") or []
    hits = []
    for x in res:
        t = (x.get("title") or "").lower()
        if any(k.lower() in t for k in KEYS):
            hits.append((x.get("title", "")[:100], (x.get("magnetlink") or "")[:80], x.get("engine")))
    print(f"== {q}: {len(res)} results, {len(hits)} keyword-matched")
    for h in hits[:6]:
        print("   ", h[0], "|", h[1], "|", h[2])
    print()
