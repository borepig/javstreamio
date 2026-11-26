#!/usr/bin/env python3
"""Generate local AI posters for cat3 titles whose poster is missing.

Affected rows per category (2026-11-18 audit):
  - poster is empty
  - poster is a local SVG title card (javstremio.ddns.net/posters/hkcat3/*.svg —
    "the poster just displays the actual title")
  - poster host is NOT in the debrid-app allow-list (isPublicArtUrl), so
    withPublicPosters strips it and Stremio shows a title-only fallback card

The AI stage uses ComfyUI (http://127.0.0.1:8188) with the
Qwen-Rapid-AIO-NSFW-v19 checkpoint (same graph as hk_cat3_poster_fill.py
and story2video's g_rapid). vLLM is parked (pkill) before the batch and
resumed after — same pattern as s2v.py vllm_pause / vllm_resume.

Prompts are country-specific (per user instruction):
  hongkong  -> character set in Hong Kong (Victoria Harbour skyline)
  japan      -> character set in Japan (neon-lit Tokyo alley)
  france     -> character set in France (Paris streets)
  cat3-others-> character set in an East Asian city

JPGs are written to debrid-app/public/posters/<catdir>/<row_id>.jpg and the
DB row gets poster=background=https://javstremio.ddns.net/posters/<catdir>/<row_id>.jpg.
`javstremio.ddns.net` is on the allow-list, so the new posters survive
withPublicPosters. The files are git-tracked, so the EC2 deploy (git pull +
rebuild) publishes them live.

Resume-safe: rows whose JPG already exists on disk are not regenerated —
only the DB write is re-applied if missing.

Usage:
    python3 addon/cat3_ai_posters.py [--category japan] [--limit N]
        [--start N] [--ids id1,id2] [--dry-run] [--no-vllm]
"""

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "debrid-app", "data", "titles.sqlite")
LIVE_BASE = "https://javstremio.ddns.net"

# category -> (poster dir under debrid-app/public/posters, country-setting clause)
CATEGORY_CONF = {
    "japan": ("japancat3", "set in Japan, Japanese city backdrop"),
    "france": ("francecat3", "set in France, Paris streets backdrop"),
    "cat3-others": ("cat3others", "set in an East Asian city"),
    "hongkong": ("hkcat3", "set in Hong Kong, Victoria Harbour skyline"),
}

# Mirror of isPublicArtUrl() in debrid-app/lib/addon-data.js — only these
# poster hosts survive withPublicPosters; anything else gets stripped.
ALLOWED_EXACT = {
    "pics.dmm.co.jp", "hanime-cdn.com", "fourhoi.com", "image.mgstage.com",
    "jdbstatic.com", "avgood.com", "images.ijavtorrent.com",
    "raw.githubusercontent.com", "javstremio.ddns.net", "image.oceanveil.net",
    "hentai.tv", "img.douban.com", "upload.wikimedia.org",
}
ALLOWED_SUFFIX = {"fourhoi.com", "jdbstatic.com", "doubanio.com"}
BANNED_SUBSTR = ("vercel.app", "djhdhs.us", "w4kmzkm.com")


def poster_host(poster):
    try:
        return (urllib.parse.urlsplit(poster or "").hostname or "").lower()
    except Exception:
        return ""


def is_allowed(poster):
    host = poster_host(poster)
    if not host:
        return False
    if any(b in host for b in BANNED_SUBSTR):
        return False
    if host in ALLOWED_EXACT:
        return True
    return any(host == a or host.endswith("." + a) for a in ALLOWED_SUFFIX)


def is_affected(poster):
    """True when the poster would show as a title-only / default card:
    empty, a local SVG title card, or a non-allowlisted host."""
    if not poster:
        return True
    if poster_host(poster) == "javstremio.ddns.net":
        return True  # SVG title card ("default" poster)
    return not is_allowed(poster)


# --- ComfyUI AI stage (graph from story2video s2v.py g_rapid) ---
COMFY = os.environ.get("COMFY_URL", "http://127.0.0.1:8188")
COMFY_DIR = os.environ.get("COMFY_DIR", "/home/joe/Projects/ComfyUI")
CKPT = os.environ.get("S2V_RAPID", "Qwen-Rapid-AIO-NSFW-v19.safetensors")
NEG = "lowres, blurry, watermark, text, logo, deformed"
# vLLM is managed by systemd. Park/resume must use systemctl, not pkill +
# a raw `vllm serve` command: the Huihui MTP unit has Restart=on-failure, so
# pkill would just bring the server back and fight ComfyUI for VRAM.
VLLM_UNITS = ("vllm-qwen-mtp.service", "vllm-qwen.service")
VLLM_UNIT = "vllm-qwen-mtp.service"


def _http_json(url, body=None, method=None, headers=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method=method or ("POST" if data is not None else "GET"))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


# Same launch command as the resident ComfyUI server (pid 2690).
COMFY_LAUNCH_CMD = (
    "/home/joe/miniconda3/bin/python /home/joe/Projects/ComfyUI/main.py "
    "--enable-manager --enable-manager-legacy-ui --disable-pinned-memory --cache-none"
)


def comfy_alive():
    try:
        _http_json(f"{COMFY}/queue", timeout=5)
        return True
    except Exception:
        return False


def comfy_relaunch():
    """Restart a hung ComfyUI server and wait for it to come back."""
    if comfy_alive():
        print("[cat3_ai_posters] ComfyUI responsive (no restart needed)")
        return True
    # Kill the hung server (match the exact launch flags so nothing else dies).
    subprocess.run(["pkill", "-f", "main.py --enable-manager --enable-manager-legacy-ui"],
                   stderr=subprocess.DEVNULL)
    time.sleep(3)
    log = os.path.join(COMFY_DIR, "comfyui_restart.log")
    try:
        with open(log, "a") as lf:
            subprocess.Popen(COMFY_LAUNCH_CMD, shell=True, stdout=lf,
                             stderr=subprocess.STDOUT, start_new_session=True)
        for _ in range(60):
            if comfy_alive():
                print("[cat3_ai_posters] ComfyUI restarted")
                return True
            time.sleep(2)
        print("[cat3_ai_posters] ComfyUI restart timed out")
        return False
    except Exception as e:
        print(f"[cat3_ai_posters] ComfyUI relaunch failed: {e}")
        return False


def comfy_generate(prompt, out_jpg, seed):
    """One 512x768 NSFW poster via the Qwen-Rapid-AIO-NSFW-v19 graph.
    Self-healing: if the generation fails or ComfyUI hangs, restart the
    server once and retry so a single stuck image does not stop the batch."""
    for attempt in (1, 2):
        try:
            if _comfy_generate_once(prompt, out_jpg, seed):
                return True
        except Exception as e:
            # Any per-image exception (ComfyUI hang, timeout, IO error) must
            # not stop the batch: log it, restart ComfyUI, and retry once.
            print(f"    comfyui exception ({e.__class__.__name__}): {e}")
        if attempt == 1:
            if not comfy_relaunch():
                return False
            time.sleep(3)
    print("    comfyui: generation failed after restart+retry")
    return False


def _comfy_generate_once(prompt, out_jpg, seed):
    graph = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": CKPT}},
        "3": {"class_type": "TextEncodeQwenImageEditPlus",
               "inputs": {"clip": ["1", 1], "prompt": prompt, "vae": ["1", 2]}},
        "4": {"class_type": "TextEncodeQwenImageEditPlus",
               "inputs": {"clip": ["1", 1], "prompt": NEG, "vae": ["1", 2]}},
        "5": {"class_type": "EmptySD3LatentImage",
               "inputs": {"width": 512, "height": 768, "batch_size": 1}},
        "6": {"class_type": "KSampler", "inputs": {
            "model": ["1", 0], "seed": seed, "steps": 6, "cfg": 1.0,
            "sampler_name": "euler_ancestral", "scheduler": "beta",
            "positive": ["3", 0], "negative": ["4", 0],
            "latent_image": ["5", 0], "denoise": 1.0}},
        "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
        "8": {"class_type": "SaveImage", "inputs": {"images": ["7", 0], "filename_prefix": "cat3ai"}},
    }
    # The /prompt POST can block while ComfyUI's event loop is busy; the
    # 60s default timed out mid-batch, so allow 300s and retry on timeout.
    d = None
    for attempt in range(3):
        try:
            d = _http_json(f"{COMFY}/prompt", {"prompt": graph, "client_id": "cat3aiposter"}, timeout=300)
            break
        except (TimeoutError, urllib.error.URLError) as e:
            if attempt == 2:
                print(f"    comfyui /prompt failed after 3 tries: {e}")
                return False
            time.sleep(2)
    pid = d["prompt_id"]
    deadline = time.time() + 180
    while time.time() < deadline:
        try:
            h = _http_json(f"{COMFY}/history/{pid}", timeout=10)
        except Exception as e:
            print(f"    comfyui history poll error ({e.__class__.__name__}); retrying")
            time.sleep(2)
            continue
        if pid in h:
            for node, o in h[pid].get("outputs", {}).items():
                for img in o.get("images", []):
                    sub = img.get("subfolder") or ""
                    src = os.path.join(COMFY_DIR, "output", sub, img["filename"])
                    if os.path.exists(src):
                        os.makedirs(os.path.dirname(out_jpg), exist_ok=True)
                        # SaveImage writes PNG to ComfyUI/output/; convert to JPEG
                        subprocess.run(["ffmpeg", "-y", "-i", src, "-q:v", "3", out_jpg],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                        return os.path.exists(out_jpg) and os.path.getsize(out_jpg) > 1024
            st = (h[pid].get("status") or {})
            if st.get("status_str") == "error":
                print(f"    comfyui error: {json.dumps(st.get('messages', [])[-1:])[:300]}")
            return False
        time.sleep(1)
    print("    comfyui: generation did not finish in 180s")
    return False


def park_vllm():
    """Stop the resident vLLM (frees its VRAM for ComfyUI)."""
    for unit in VLLM_UNITS:
        subprocess.run(["systemctl", "--user", "stop", unit],
                       stderr=subprocess.DEVNULL)
    for _ in range(30):
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader"],
                             capture_output=True, text=True).stdout.strip()
        m = re.search(r"\d+", out)
        used = int(m.group()) if m else 99999
        if used <= 4096:
            print(f"[cat3_ai_posters] vLLM parked ({used} MiB VRAM left)")
            return
        time.sleep(3)
    print("[cat3_ai_posters] vLLM parked (VRAM not fully freed; proceeding)")


def resume_vllm():
    """Restart the Huihui MTP vLLM unit so qwen-code on :8000 is available again."""
    try:
        subprocess.run(["systemctl", "--user", "start", VLLM_UNIT], check=False)
        print("[cat3_ai_posters] vLLM restarting (vllm-qwen-mtp, qwen-code on :8000)")
    except Exception as e:
        print(f"[cat3_ai_posters] vLLM resume failed: {e}")


# --- prompts ---
def clean_title(name):
    t = re.sub(r"\(.*?\)", "", name or "")
    t = re.sub(r"\[.*?\]", "", t)
    return " ".join(t.split())


_NOISE_LINE = re.compile(r"^(出演女优|容量|有码/无码|演员|片商|导演|发行)\s*[:：]\s*$")


def ai_prompt(name, description, country_clause):
    parts = [f"Photorealistic cinematic adult film poster for '{clean_title(name)}'"]
    desc = (description or "").strip()
    if desc and len(desc) < 200:
        lines = [l.strip() for l in desc.splitlines()
                 if l.strip() and not _NOISE_LINE.match(l.strip())]
        if lines:
            parts.append(" " + " ".join(lines)[:180])
    parts.append(" " + country_clause)
    parts.append(" dramatic lighting, 2:3 movie poster composition, high detail, 8k")
    return " ".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--category", choices=sorted(CATEGORY_CONF), default=None,
                    help="process a single category (default: all four)")
    ap.add_argument("--limit", type=int, default=0, help="max rows to process per category")
    ap.add_argument("--start", type=int, default=0, help="skip the first N selected rows (resume long runs)")
    ap.add_argument("--ids", default="", help="comma-separated row ids to process")
    ap.add_argument("--no-vllm", action="store_true", help="do not park/resume vLLM")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cats = [args.category] if args.category else list(CATEGORY_CONF)

    db = sqlite3.connect(DB)
    selected = []
    for cat in cats:
        cat_dir, country_clause = CATEGORY_CONF[cat]
        if args.ids:
            id_list = [x for x in args.ids.split(",") if x]
            ph = ",".join("?" for _ in id_list)
            cur = db.execute(
                f"SELECT id, name, description, poster FROM titles "
                f"WHERE category='{cat}' AND id IN ({ph})", id_list)
        else:
            cur = db.execute(
                "SELECT id, name, description, poster FROM titles "
                "WHERE category=?", (cat,))
        for row_id, name, description, poster in cur.fetchall():
            if is_affected(poster):
                selected.append((cat, row_id, name, description))

    total = len(selected)
    if args.start:
        selected = selected[args.start:]
    if args.limit:
        selected = selected[:args.limit]

    print(f"[cat3_ai_posters] {len(selected)} affected rows to process "
          f"({total} total affected; --start {args.start}, --limit {args.limit})")
    if not selected:
        db.close()
        return 0
    if args.dry_run:
        for cat, row_id, name, _ in selected[:5]:
            print(f"  would generate: {row_id} ({cat}) {clean_title(name)[:40]}")
        db.close()
        return 0

    vllm_parked = False
    if not args.no_vllm:
        park_vllm()
        vllm_parked = True

    ok = failed = 0
    t0 = time.time()
    for i, (cat, row_id, name, description) in enumerate(selected):
        cat_dir, country_clause = CATEGORY_CONF[cat]
        out_jpg = os.path.join(ROOT, "debrid-app", "public", "posters", cat_dir, f"{row_id}.jpg")
        url = f"{LIVE_BASE}/posters/{cat_dir}/{row_id}.jpg"
        if os.path.exists(out_jpg):
            db.execute("UPDATE titles SET poster=?, background=? WHERE id=?", (url, url, row_id))
            db.commit()
            ok += 1
            print(f"[cat3_ai_posters] {i+1}/{len(selected)} {row_id}: re-applied DB write (image on disk)")
            continue
        prompt = ai_prompt(name, description, country_clause)
        seed = int(time.time()) % (2**31)
        if comfy_generate(prompt, out_jpg, seed):
            db.execute("UPDATE titles SET poster=?, background=? WHERE id=?", (url, url, row_id))
            db.commit()
            ok += 1
            print(f"[cat3_ai_posters] {i+1}/{len(selected)} {cat} {row_id}: generated ({time.time()-t0:.0f}s)")
        else:
            failed += 1
            print(f"[cat3_ai_posters] {i+1}/{len(selected)} {cat} {row_id}: generation failed")
        time.sleep(0.5)

    if vllm_parked:
        resume_vllm()

    db.close()
    print(f"[cat3_ai_posters] done: {ok} ok, {failed} failed "
          f"(total {len(selected)}, {time.time()-t0:.0f}s)")
    # Completion marker: the supervisor loop uses this file to know the
    # whole batch finished and stop restarting.
    with open(os.path.join(ROOT, ".cat3_ai_posters.done"), "w") as f:
        f.write(time.ctime())
    return 0


if __name__ == "__main__":
    sys.exit(main())
