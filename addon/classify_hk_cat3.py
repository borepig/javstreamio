#!/usr/bin/env python3
"""Classify HK Cat III (三级片) titles into AV/erotic vs horror vs other,
and pair English + Chinese names from the two Wikipedia lists.

Outputs a JSON: av list (with en+zh names), counts per bucket.
"""
import json
import re

PARSED = "/home/joe/Projects/JAVSTREAMIO/addon/hk_cat3_parsed.json"
OUT = "/home/joe/Projects/JAVSTREAMIO/addon/hk_cat3_av.json"

with open(PARSED, encoding="utf-8") as f:
    data = json.load(f)

# (title, year) pairs
zh = [(t, y) for (t, y) in data["zh"]]
en = data["en"]

# --- keyword buckets -------------------------------------------------------
# Erotic / AV (keep). These are the 情色/成人片 signals.
EROTIC = [
    "色", "淫", "欲", "性", "奸", "桃色", "赤裸", "裸", "妓", "鸭",
    "宝鉴", "蒲团", "金瓶梅", "聊斋", "蜜桃", "情色", "情欲", "偷情",
    "情妇", "情圣", "情火", "情海", "情奴", "情迷", "春宫", "嬉春", "叫春",
    "禁春", "成人", "春光", "风月", "花街柳巷", "应召", "舞男", "应召女郎",
    "情敌", "情难自制", "情迷香江", "欲海", "欲焰", "欲兽", "兽性",
    "兽欲", "淫魔", "淫妖", "淫欲", "色欲", "色狼", "色鬼", "情欲城市",
    # soft-erotic tokens (from the OTHER-bucket scan)
    "三级", "云雨", "豪情", "情人", "激情", "春", "艳", "媚", "恋", "情爱",
]
# Horror (exclude).
HORROR = [
    "鬼", "僵尸", "尸", "蛊", "咒", "妖", "魔", "邪", "阴", "魂", "惊",
    "怪谈", "奇幻", "灵异", "凶宅", "鬼同你住", "尸城", "李碧华",
]
# NOTE: "血" and "情" alone are too ambiguous (血玫瑰 is erotic, not horror;
# 情仇 is a revenge drama). We treat them via more specific tokens above.

def has_kw(title, kws):
    return any(kw in title for kw in kws)

def classify(title):
    erotic = has_kw(title, EROTIC)
    horror = has_kw(title, HORROR)
    if erotic and not horror:
        return "av"
    if horror and not erotic:
        return "horror"
    if erotic and horror:
        # hybrid: erotic signal wins for AV (e.g. 艳鬼情未子, 色鬼投胎).
        return "av"
    return "other"

buckets = {"av": [], "horror": [], "other": []}
for title, year in zh:
    buckets[classify(title)].append((title, year))

# --- English name pairing -------------------------------------------------
# The EN list is a subset of the same films. Pair ZH titles to EN titles
# by matching year + normalized-similarity of the romanized/English name.
# Simple approach: for each AV zh title, look for an EN entry with the same
# year and a title that shares characters/tokens. Since EN titles are roman
# and ZH are ideographs, exact string matching won't work; instead we pair by
# year + positional proximity in the list order (both lists are chronological).

def norm(s):
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", s).lower()

# Build EN lookup keyed by year.
en_by_year = {}
for etitle, eyear in en:
    en_by_year.setdefault(eyear, []).append(etitle)

# Curated ZH->EN correspondences for films known to appear in the EN subset.
# Only pairs with matching years / high confidence. The EN list is a small
# curated subset with its own (sometimes inconsistent) year tags, so we
# avoid guessing where the years disagree.
KNOWN_MAP = {
    ("像我这样的爱情", "2025"): "Someone Like Me",
}

def pair_en(zh_title, year):
    key = (zh_title, str(year))
    if key in KNOWN_MAP:
        return KNOWN_MAP[key]
    cands = en_by_year.get(year, [])
    # Only pair when the year has a single EN film (unambiguous); otherwise
    # leave empty rather than guess among several candidates.
    if len(cands) == 1:
        return cands[0]
    return ""

av_out = []
for ztitle, year in buckets["av"]:
    en_name = pair_en(ztitle, year)
    av_out.append({
        "zh": ztitle,
        "year": year,
        "en": en_name,  # empty when the film is not in the EN subset
    })

result = {
    "counts": {
        "av": len(buckets["av"]),
        "horror": len(buckets["horror"]),
        "other": len(buckets["other"]),
        "total_zh": len(zh),
    },
    "av": av_out,
    "horror_sample": buckets["horror"][:10],
    "other_sample": buckets["other"][:10],
}
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

print("counts:", result["counts"])
print("AV (erotic) movies:", result["counts"]["av"])
print("Horror excluded:", result["counts"]["horror"])
print("Other excluded:", result["counts"]["other"])
print("Wrote", OUT)
