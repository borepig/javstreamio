#!/usr/bin/env python3
"""Fill missing posters for hk:cat3 rows, in the user's priority order.

Priority (per user instruction):
  0. KEEP rows that already carry a proper poster/fanart — only rows with
     an empty `poster` are touched (poster != '' => skip).
  1. Web-search posters (SearXNG -> Firecrawl fallback), reusing
     addon/hk_cat3_posters.py `find_poster()`.
  2. Video frame grab: resolve the row's magnet through the same PikPak
     flow the live app uses (lib/debrids.js `pikpak`/`resolvePikpakPlay`):
     POST /drive/v1/files (UPLOAD_TYPE_URL + magnet) -> list the folder,
     pick the largest video file -> /drive/v1/files/<id> (phase complete)
     -> CDN link (upgraded to https) -> download first 8 MB -> ffmpeg
     frame at ~3s -> 512x768 (2:3) JPG.
  3. Local AI poster: ComfyUI (http://127.0.0.1:8188) with
     Qwen-Rapid-AIO-NSFW-v19 (18+/NSFW), graph copied from story2video's
     g_rapid (TextEncodeQwenImageEditPlus + EmptySD3LatentImage +
     KSampler euler_ancestral/beta 6 steps). vLLM is parked (pkill) before
     the AI batch and resumed after — same pattern as s2v.py vllm_pause /
     vllm_resume.

Images are written to debrid-app/public/posters/hkcat3/<row_id>.jpg (served
live at https://javstremio.ddns.net/posters/hkcat3/<row_id>.jpg — the host
is on the app's poster allow-list), and the DB row gets
poster=background=that URL.

Resume-safe: rows that already have a poster are skipped; rows whose JPG
already exists on disk are not regenerated (only the DB write is re-applied
if missing).

Usage:
    PIKPAK_TOKEN='<jwt>' python3 addon/hk_cat3_poster_fill.py \
        [--limit N] [--no-pikpak] [--no-ai] [--dry-run] [--ids id1,id2]
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
POSTER_DIR = os.path.join(ROOT, "debrid-app", "public", "posters", "hkcat3")
LIVE_POSTER_BASE = "https://javstremio.ddns.net/posters/hkcat3"

# --- web-search stage (reuses the existing job) ---
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import hk_cat3_posters  # noqa: F401 (find_poster, clean_query)
    HAS_WEB_SEARCH = True
except Exception as e:
    HAS_WEB_SEARCH = False
    print(f"[poster_fill] web-search stage disabled: {e}")

# --- PikPak (mirrors debrid-app/lib/debrids.js) ---
PP_BASE = "https://api-drive.mypikpak.com"
VIDEO_EXTS = (".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv", ".webm")
PP_POLL_TRIES = 12
PP_POLL_DELAY = 2.0
PP_RANGE_BYTES = 8 * 1024 * 1024  # 8 MB head is enough for a 3s keyframe


def pp_headers(token):
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }


def pp_json(url, body=None, token=None, method=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=pp_headers(token), method=method or ("POST" if data is not None else "GET"))
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def pikpak_add(token, magnet):
    """POST the magnet to PikPak offline download. Returns (task_id, file_id, phase)."""
    try:
        d = pp_json(PP_BASE + "/drive/v1/files",
                     {"kind": "drive#file", "upload_type": "UPLOAD_TYPE_URL",
                      "url": {"url": magnet}, "folder_type": "DOWNLOAD"},
                     token=token)
    except urllib.error.HTTPError as e:
        print(f"    pikpak add failed: {e.code} {e.reason}")
        return None, None, None
    task = d.get("task") or {}
    return task.get("id"), task.get("file_id"), task.get("phase")


def pikpak_video_url(token, file_id, info_hash):
    """List the offline folder, pick the largest video file, fetch its CDN
    link (upgraded to https). Returns the URL or None if not complete."""
    params = urllib.parse.urlencode({"parent_id": str(file_id), "limit": "50"})
    try:
        d = pp_json(f"{PP_BASE}/drive/v1/files?{params}", token=token)
    except urllib.error.HTTPError as e:
        # 404: the offline-download folder does not exist yet (task still in
        # flight) — return None so the polling loop retries; other codes raise.
        if e.code != 404:
            raise
        return None
    files = d.get("files") or []
    videos = [f for f in files
               if str(f.get("name", "")).lower().endswith(VIDEO_EXTS)]
    if not videos:
        return None
    video = max(videos, key=lambda f: int(f.get("size") or 0))
    fid = str(video.get("id"))
    params = urllib.parse.urlencode({"thumbnail_size": "SIZE_LARGE"})
    try:
        d = pp_json(f"{PP_BASE}/drive/v1/files/{urllib.parse.quote(fid)}?{params}", token=token)
    except urllib.error.HTTPError as e:
        print(f"    pikpak file detail failed: {e.code} {e.reason}")
        return None
    if str(d.get("phase") or "") != "PHASE_TYPE_COMPLETE":
        return None
    link = ""
    medias = d.get("medias") or []
    if medias and medias[0].get("link"):
        link = str(medias[0]["link"].get("url") or "")
    link = link or str(d.get("web_content_link") or "")
    if link.startswith("http"):
        return link.replace("http://", "https://", 1)
    return None


def pikpak_grab_frame(token, magnet, info_hash, out_jpg):
    """Full frame-grab: add magnet -> poll file completion -> download 8 MB
    head -> ffmpeg 512x768 JPG. Returns True on success."""
    task_id, file_id, phase = pikpak_add(token, magnet)
    if not file_id:
        return False
    url = None
    if str(phase or "") == "PHASE_TYPE_COMPLETE":
        url = pikpak_video_url(token, file_id, info_hash)
    else:
        for _ in range(PP_POLL_TRIES):
            time.sleep(PP_POLL_DELAY)
            url = pikpak_video_url(token, file_id, info_hash)
            if url:
                break
    if not url:
        return False
    return grab_video_frame(url, out_jpg)


def grab_video_frame(url, out_jpg):
    """Download the first 8 MB of the video and cut a 2:3 frame at ~3s."""
    os.makedirs(os.path.dirname(out_jpg), exist_ok=True)
    part = out_jpg + ".part"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Range": f"bytes=0-{PP_RANGE_BYTES - 1}",
        })
        with urllib.request.urlopen(req, timeout=120) as r, open(part, "wb") as f:
            shutil.copyfileobj(r, f)
        vf = "scale=512:768:force_original_aspect_ratio=increase,crop=512:768,format=yuv420p"
        cmd = ["ffmpeg", "-y", "-ss", "3", "-i", part,
               "-frames:v", "1", "-vf", vf, "-q:v", "3", out_jpg]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return os.path.exists(out_jpg) and os.path.getsize(out_jpg) > 1024
    except Exception as e:
        print(f"    frame-grab failed: {e}")
        return False
    finally:
        if os.path.exists(part):
            os.remove(part)


# --- ComfyUI AI stage (graph from story2video s2v.py g_rapid) ---
COMFY = os.environ.get("COMFY_URL", "http://127.0.0.1:8188")
COMFY_DIR = os.environ.get("COMFY_DIR", "/home/joe/Projects/ComfyUI")
CKPT = os.environ.get("S2V_RAPID", "Qwen-Rapid-AIO-NSFW-v19.safetensors")
NEG = "lowres, blurry, watermark, text, logo, deformed"
# Must match story2video's LLM_API_KEY default (s2v.py). The env var
# VLLM_API_KEY (set by the shell) carries the same 44-char key.
VLLM_API_KEY = os.environ.get("VLLM_API_KEY", "64d094dffaac09510cfb0b7be37be1f0bd3a93138248d5a4")
VLLM_SERVE_CMD = (
    "/home/joe/miniconda3/bin/vllm serve /home/joe/models/Qwen3.8-27B-Uncensored-NVFP4 "
    "--host 0.0.0.0 --port 8000 "
    f"--api-key {VLLM_API_KEY} --served-model-name qwen-code --gpu-memory-utilization 0.87 "
    "--max-model-len 163840 --kv-cache-dtype fp8 --max-num-seqs 8 --enable-chunked-prefill "
    "--max-num-batched-tokens 8192 --trust-remote-code --reasoning-parser qwen3 "
    "--enable-auto-tool-choice --tool-call-parser qwen3_coder --attention-backend TRITON_ATTN "
    "--linear-backend cutlass --gdn-prefill-backend triton --skip-mm-profiling "
    '--limit-mm-per-prompt \'{"image":2,"video":0}\''
)


def _http_json(url, body=None, method=None, headers=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method=method or ("POST" if data is not None else "GET"))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def comfy_generate(prompt, out_jpg, seed):
    """One 512x768 NSFW poster via the Qwen-Rapid-AIO-NSFW-v19 graph."""
    graph = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": CKPT}},
        "3": {"class_type": "TextEncodeQwenImageEditPlus",
               "inputs": {"clip": ["1", 1], "prompt": prompt, "vae": ["1", 2]}},
        "4": {"class_type": "TextEncodeQwenImageEditPlus",
               "inputs": {"clip": ["1", 1], "prompt": NEG}},
        "5": {"class_type": "EmptySD3LatentImage",
               "inputs": {"width": 512, "height": 768, "batch_size": 1}},
        "6": {"class_type": "KSampler", "inputs": {
            "model": ["1", 0], "seed": seed, "steps": 6, "cfg": 1.0,
            "sampler_name": "euler_ancestral", "scheduler": "beta",
            "positive": ["3", 0], "negative": ["4", 0],
            "latent_image": ["5", 0], "denoise": 1.0}},
        "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
        "8": {"class_type": "SaveImage", "inputs": {"images": ["7", 0], "filename_prefix": "javcat3"}},
    }
    d = _http_json(f"{COMFY}/prompt", {"prompt": graph, "client_id": "javcat3poster"})
    pid = d["prompt_id"]
    deadline = time.time() + 180
    while time.time() < deadline:
        h = _http_json(f"{COMFY}/history/{pid}", timeout=10)
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
    """Stop the resident vLLM (frees its ~87% VRAM for ComfyUI) — same
    pattern as s2v.py vllm_pause."""
    subprocess.run(["pkill", "-f", "vllm serve"], stderr=subprocess.DEVNULL)
    subprocess.run(["pkill", "-f", "VLLM::EngineCore"], stderr=subprocess.DEVNULL)
    for _ in range(30):
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader"],
                             capture_output=True, text=True).stdout.strip()
        m = re.search(r"\d+", out)
        used = int(m.group()) if m else 99999
        if used <= 4096:
            print(f"[poster_fill] vLLM parked ({used} MiB VRAM left)")
            return
        time.sleep(3)
    print("[poster_fill] vLLM parked (VRAM not fully freed; proceeding)")


def resume_vllm():
    """Restart vLLM so the story2video LLM stages work again (s2v.py vllm_resume)."""
    log = os.path.join(COMFY_DIR, "vllm_restart.log")
    try:
        with open(log, "a") as lf:
            subprocess.Popen(VLLM_SERVE_CMD, shell=True, stdout=lf,
                             stderr=subprocess.STDOUT, start_new_session=True)
        print("[poster_fill] vLLM restarting (qwen-code on :8000)")
    except Exception as e:
        print(f"[poster_fill] vLLM resume failed: {e}")


# --- DB / orchestration ---
def clean_title(name):
    """Prompt text for the AI stage: strip forum noise from the title."""
    t = re.sub(r"\(.*?\)", "", name or "")
    t = re.sub(r"\[.*?\]", "", t)
    return " ".join(t.split())


_NOISE_LINE = re.compile(r"^(出演女优|容量|有码/无码|演员|片商|导演|发行)\s*[:：]\s*$")


def ai_prompt(name, description, web_note):
    parts = [f"Photorealistic cinematic adult film poster for '{clean_title(name)}'"]
    # Drop forum-metadata lines with empty values ("出演女优: ", "容量: ", ...);
    # keep only actual prose from the description.
    desc = (description or "").strip()
    if desc and len(desc) < 200:
        lines = [l.strip() for l in desc.splitlines()
                 if l.strip() and not _NOISE_LINE.match(l.strip())]
        if lines:
            parts.append(" " + " ".join(lines)[:180])
    if web_note:
        parts.append(" " + web_note[:120])
    parts.append(" dramatic lighting, 2:3 movie poster composition, high detail, 8k")
    return " ".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--ids", default="", help="comma-separated row ids to process")
    ap.add_argument("--no-pikpak", action="store_true", help="skip the video frame-grab stage")
    ap.add_argument("--no-ai", action="store_true", help="skip the local-AI stage")
    ap.add_argument("--delay", type=float, default=0.3)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--pikpak-token", default=os.environ.get("PIKPAK_TOKEN", ""))
    args = ap.parse_args()

    db = sqlite3.connect(DB)
    if args.ids:
        ids = [x for x in args.ids.split(",") if x]
        ph = ",".join("?" for _ in ids)
        rows = db.execute(
            f"SELECT id, name, description, magnet, info_hash FROM titles "
            f"WHERE id IN ({ph}) AND (poster IS NULL OR poster='')", ids).fetchall()
    else:
        rows = db.execute(
            "SELECT id, name, description, magnet, info_hash FROM titles "
            "WHERE id LIKE 'hk:cat3-%' AND (poster IS NULL OR poster='') "
            "ORDER BY posted_at DESC").fetchall()
    total = len(rows)
    if args.limit:
        rows = rows[: args.limit]

    web = web_search = frame = ai = kept = failed = 0
    ai_pending = []
    ai_vllm_parked = False
    for i, (row_id, name, description, magnet, info_hash) in enumerate(rows):
        out_jpg = os.path.join(POSTER_DIR, f"{row_id}.jpg")
        url = f"{LIVE_POSTER_BASE}/{row_id}.jpg"
        if os.path.exists(out_jpg):
            # Resume case: image already generated — just re-apply the DB write.
            if not args.dry_run:
                db.execute("UPDATE titles SET poster=?, background=? WHERE id=?", (url, url, row_id))
                db.commit()
            kept += 1
            print(f"[poster_fill] {i+1}/{len(rows)} {row_id}: re-applied DB write (image on disk)")
            continue
        # Stage 1: web search
        note = ""
        if HAS_WEB_SEARCH and magnet:
            poster, status = hk_cat3_posters.find_poster(hk_cat3_posters.clean_query(name))
            if status == "found":
                if not args.dry_run:
                    db.execute("UPDATE titles SET poster=?, background=? WHERE id=?", (poster, poster, row_id))
                    db.commit()
                web_search += 1
                print(f"[poster_fill] {i+1}/{len(rows)} {row_id}: web-search poster {poster[:80]}")
                continue
            if status == "no_usable" or status == "no_result":
                note = f"web: {status}"
        # Stage 2: video frame grab via PikPak
        if not args.no_pikpak and magnet and args.pikpak_token:
            print(f"[poster_fill] {i+1}/{len(rows)} {row_id}: pikpak frame-grab ...")
            if grab_result := pikpak_grab_frame(args.pikpak_token, magnet, info_hash, out_jpg):
                if not args.dry_run:
                    db.execute("UPDATE titles SET poster=?, background=? WHERE id=?", (url, url, row_id))
                    db.commit()
                frame += 1
                print(f"[poster_fill] {i+1}/{len(rows)} {row_id}: frame poster from video")
                continue
        # Stage 3: queue for local AI
        ai_pending.append((row_id, name, description, note))
        if args.delay:
            time.sleep(args.delay)

    # Stage 3: local AI (park vLLM once for the whole batch)
    if ai_pending and not args.no_ai:
        print(f"[poster_fill] AI stage: {len(ai_pending)} titles (parking vLLM)")
        if not args.dry_run:
            park_vllm()
            ai_vllm_parked = True
        t0 = time.time()
        for j, (row_id, name, description, note) in enumerate(ai_pending):
            out_jpg = os.path.join(POSTER_DIR, f"{row_id}.jpg")
            url = f"{LIVE_POSTER_BASE}/{row_id}.jpg"
            prompt = ai_prompt(name, description, note)
            seed = int(time.time()) % (2**31)
            ok = args.dry_run or comfy_generate(prompt, out_jpg, seed)
            if ok:
                if not args.dry_run:
                    db.execute("UPDATE titles SET poster=?, background=? WHERE id=?", (url, url, row_id))
                    db.commit()
                ai += 1
                print(f"[poster_fill] AI {j+1}/{len(ai_pending)} {row_id}: generated ({time.time()-t0:.0f}s)")
            else:
                failed += 1
                print(f"[poster_fill] AI {j+1}/{len(ai_pending)} {row_id}: generation failed")
            time.sleep(0.5)
        if ai_vllm_parked and not args.dry_run:
            resume_vllm()

    db.close()
    print(f"[poster_fill] done: web={web_search} frame={frame} ai={ai} "
          f"reapplied={kept} failed={failed} (processed {len(rows)} of {total} poster-less cat3 rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
