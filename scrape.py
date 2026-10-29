#!/usr/bin/env python3
"""Read public sehuatang.org thread and forum pages into JSON.

The site shows an 18+ interstitial before the real Discuz HTML. This script
opens that page in Chrome, follows the interstitial, and parses what a
browser would show. It does not download attachments or media files.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag
from playwright.sync_api import Browser, Page, Playwright, sync_playwright

BASE = "https://sehuatang.org/"
MAX_PAGES = 20
BATCH_SIZE = 6
LABELS = ("影片名称", "出演女优", "影片容量", "是否有码", "种子期限", "下载工具", "影片预览")
LABEL_RE = re.compile(r"^【([^】]+)】\s*[:：]\s*(.*)$")
MAGNET_RE = re.compile(r"magnet:\?xt=urn:btih:[A-Za-z0-9]+(?:&[^\s<\"']+)?", re.I)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def absolute(href: str | None, page_url: str) -> str:
    if not href:
        return ""
    return urljoin(page_url, href)


def clean_text(value: str) -> str:
    value = value.replace("\xa0", " ").replace("\u200b", "")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines()]
    collapsed: list[str] = []
    blank = False
    for line in lines:
        if not line:
            if not blank and collapsed:
                collapsed.append("")
            blank = True
            continue
        collapsed.append(line)
        blank = False
    return "\n".join(collapsed).strip()


def chrome_path() -> str | None:
    for candidate in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = shutil.which(candidate)
        if found:
            return found
    return None


def launch_browser(playwright: Playwright) -> Browser:
    executable = chrome_path()
    options: dict = {
        "headless": True,
        "args": ["--disable-blink-features=AutomationControlled"],
        "ignore_default_args": ["--enable-automation"],
    }
    if executable:
        options["executable_path"] = executable
    return playwright.chromium.launch(**options)


def looks_blocked(html: str) -> bool:
    title = ""
    match = re.search(r"<title>(.*?)</title>", html, re.I | re.S)
    if match:
        title = match.group(1)
    return "Just a moment" in title or "请稍候" in title or "满18岁" in html[:4000]


def read_html(page: Page, url: str) -> tuple[str, str]:
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    gate = page.locator("text=满18岁")
    if gate.count():
        gate.first.click()
        page.wait_for_load_state("domcontentloaded", timeout=45000)
    page.wait_for_selector("#thread_subject, a.s.xst, #postlist", timeout=20000)
    return page.content(), page.url


def image_sources(node: Tag, page_url: str) -> list[str]:
    found: list[str] = []
    for img in node.select("img"):
        chosen = ""
        for attr in ("zoomfile", "file", "src"):
            raw = img.get(attr) or ""
            if not raw or raw.endswith("none.gif") or "static/image/" in raw:
                continue
            chosen = absolute(raw, page_url)
            break
        if chosen and chosen not in found:
            found.append(chosen)
    return found


def post_links(node: Tag, page_url: str) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for anchor in node.select("a[href]"):
        href = absolute(anchor.get("href"), page_url)
        if not href or href.startswith("javascript:"):
            continue
        host = urlparse(href).netloc
        if host.endswith("sehuatang.org") and any(
            part in href for part in ("mod=spacecp", "do=profile", "from=album", "authorid=")
        ):
            continue
        if href in seen:
            continue
        seen.add(href)
        links.append({"text": clean_text(anchor.get_text(" ", strip=True)), "url": href})
    return links


def attachments(post: Tag, page_url: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for block in post.select("dl.tattl"):
        anchor = block.select_one("p.attnm a[href]")
        if anchor is None:
            continue
        meta = clean_text(block.get_text("\n", strip=True))
        name = clean_text(anchor.get_text(" ", strip=True))
        meta = "\n".join(line for line in meta.splitlines() if line and line != name)
        items.append(
            {
                "name": name,
                "url": absolute(anchor.get("href"), page_url),
                "meta": meta,
            }
        )
    return items


def parse_post(post: Tag, page_url: str) -> dict:
    author_link = post.select_one(".authi a.xw1")
    posted = post.select_one("em[id^=authorposton]")
    posted_label = ""
    posted_at = ""
    if posted is not None:
        posted_label = clean_text(posted.get_text(" ", strip=True)).removeprefix("发表于").strip()
        stamp = posted.select_one("span[title]")
        if stamp is not None:
            posted_at = stamp.get("title") or ""
    floor = post.select_one("a[id^=postnum]")
    body = post.select_one(".t_f")
    code_blocks = []
    if body is not None:
        for block in body.select(".blockcode"):
            text = clean_text(block.get_text("\n", strip=True)).replace("复制代码", "").strip()
            if text:
                code_blocks.append(text)
    return {
        "id": (post.get("id") or "").removeprefix("post_"),
        "floor": clean_text(floor.get_text(" ", strip=True)) if floor else "",
        "author": clean_text(author_link.get_text(" ", strip=True)) if author_link else "",
        "author_url": absolute(author_link.get("href"), page_url) if author_link else "",
        "posted_at": posted_at,
        "posted_label": posted_label,
        "text": clean_text(body.get_text("\n", strip=True)) if body else "",
        "images": image_sources(body, page_url) if body else [],
        "links": post_links(body, page_url) if body else [],
        "code_blocks": code_blocks,
        "attachments": attachments(post, page_url),
    }


def parse_thread(html: str, page_url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    subject = soup.select_one("#thread_subject")
    crumbs = []
    trail = soup.select_one("#pt")
    if trail is not None:
        crumbs = [clean_text(node.get_text(" ", strip=True)) for node in trail.select("a")]
    posts = [
        parse_post(node, page_url)
        for node in soup.select("#postlist > div[id^=post_]")
    ]
    nxt = soup.select_one("a.nxt")
    return {
        "type": "thread",
        "url": page_url,
        "title": clean_text(subject.get_text(" ", strip=True)) if subject else "",
        "breadcrumb": crumbs,
        "posts": posts,
        "next_url": absolute(nxt.get("href"), page_url) if nxt else "",
    }


def parse_forum(html: str, page_url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    threads = []
    seen: set[str] = set()
    for anchor in soup.select("a.s.xst"):
        url = absolute(anchor.get("href"), page_url)
        if not url or url in seen:
            continue
        seen.add(url)
        row = anchor.find_parent("tbody") or anchor.find_parent("tr")
        heading = anchor.find_parent("th")
        tag = ""
        if heading is not None:
            tag_link = heading.select_one("em a")
            if tag_link is not None:
                tag = clean_text(tag_link.get_text(" ", strip=True))
        author = ""
        posted_at = ""
        if row is not None:
            cite = row.select_one("td.by cite a")
            if cite is not None:
                author = clean_text(cite.get_text(" ", strip=True))
            stamp = row.select_one("td.by em span[title]")
            if stamp is not None:
                posted_at = stamp.get("title") or clean_text(stamp.get_text(" ", strip=True))
        threads.append(
            {
                "title": clean_text(anchor.get_text(" ", strip=True)),
                "url": url,
                "tag": tag,
                "author": author,
                "posted_at": posted_at,
            }
        )
    nxt = soup.select_one("a.nxt")
    heading = soup.select_one("#pt") or soup.select_one("title")
    title = clean_text(heading.get_text(" ", strip=True)) if heading else ""
    return {
        "type": "forum",
        "url": page_url,
        "title": title,
        "threads": threads,
        "next_url": absolute(nxt.get("href"), page_url) if nxt else "",
    }


def parse_labels(text: str) -> dict[str, str]:
    found = {label: "" for label in LABELS}
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        match = LABEL_RE.match(lines[index].strip())
        if match and match.group(1) in found:
            value = match.group(2).strip()
            if not value:
                follow = index + 1
                while follow < len(lines) and not lines[follow].strip():
                    follow += 1
                if follow < len(lines) and not lines[follow].strip().startswith("【"):
                    value = lines[follow].strip()
            found[match.group(1)] = value
        index += 1
    return found


def split_heading(category: str, subject: str) -> tuple[str, str, str]:
    """Split a forum subject into category tag, product code, and title.

    Bracket labels with no digits, such as 自译征用 and 自提征用, are markers
    rather than part of the name. The category itself comes from the link
    in front of the subject, for example 无码高清.
    """
    text = subject or ""
    text = re.sub(r"\[([^\]]*)\]", lambda match: match.group(0) if re.search(r"\d", match.group(1)) else " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if category:
        text = re.sub(rf"^\[\s*{re.escape(category)}\s*\]\s*", "", text)
    token, _, rest = text.partition(" ")
    if token and re.search(r"\d", token):
        return category, token, rest.strip()
    return category, "", text


def find_magnet(*chunks: str) -> str:
    found: list[str] = []
    for chunk in chunks:
        for magnet in MAGNET_RE.findall(chunk or ""):
            if magnet not in found:
                found.append(magnet)
    return "\n".join(found)


def movie_record(html: str, thread: dict) -> dict:
    if looks_blocked(html):
        raise RuntimeError("blocked")
    parsed = parse_thread(html, thread["url"])
    post = parsed["posts"][0] if parsed["posts"] else None
    text = post["text"] if post else ""
    labels = parse_labels(text)
    code = "\n".join(post["code_blocks"]) if post else ""
    magnet = find_magnet(text, code)
    if not labels["影片名称"] and not magnet:
        return {}
    tag, titleno, title = split_heading(thread.get("tag", ""), thread.get("title") or parsed["title"])
    return {
        "url": thread["url"],
        "tag": tag,
        "titleno": titleno,
        "title": title,
        "出演女优": labels["出演女优"],
        "影片容量": labels["影片容量"],
        "是否有码": labels["是否有码"],
        "posted_at": (post or {}).get("posted_at") or thread.get("posted_at", ""),
        "preview_images": post["images"] if post else [],
        "magnet": magnet,
    }


def forum_identity(url: str, html: str) -> tuple[int, int, int]:
    match = re.search(r"forum-(\d+)-(\d+)", url)
    fid = int(match.group(1)) if match else 0
    current = int(match.group(2)) if match else 1
    last = current
    soup = BeautifulSoup(html, "html.parser")
    tail = soup.select_one("a.last")
    if tail and tail.get("href"):
        found = re.search(r"forum-(\d+)-(\d+)", tail["href"])
        if found:
            fid = fid or int(found.group(1))
            last = int(found.group(2))
    if not fid:
        raise RuntimeError(f"Could not find a forum id in {url}")
    return fid, current, last


def load_done(path: Path) -> set[str]:
    done: set[str] = set()
    if not path.exists():
        return done
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            done.add(json.loads(line)["url"])
        except (json.JSONDecodeError, KeyError):
            continue
    return done


class Site:
    """One Chrome session that has already passed the 18+ gate."""

    def __init__(self) -> None:
        self._playwright = sync_playwright().start()
        self.browser = launch_browser(self._playwright)
        self.context = self.browser.new_context(
            user_agent=USER_AGENT,
            locale="zh-CN",
            viewport={"width": 1366, "height": 900},
        )
        self.context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        self.page = self.context.new_page()
        self.page.set_default_timeout(60000)

    def close(self) -> None:
        self.browser.close()
        self._playwright.stop()

    def open(self, url: str) -> str:
        html, _final = read_html(self.page, url)
        if looks_blocked(html):
            self.page.wait_for_timeout(3000)
            html = self.page.content()
        return html

    def fetch_many(self, urls: list[str]) -> list[dict[str, str | int]]:
        return self.page.evaluate(
            """async (urls) => {
                return Promise.all(urls.map(async (url) => {
                    const response = await fetch(url, {credentials: 'include'});
                    return {url, status: response.status, html: await response.text()};
                }));
            }""",
            urls,
        )


def reopen(site: Site | None, url: str) -> tuple[Site, str]:
    if site is not None:
        site.close()
    fresh = Site()
    try:
        html = fresh.open(url)
    except Exception:
        fresh.close()
        raise
    if looks_blocked(html):
        fresh.close()
        raise RuntimeError("Age gate or Cloudflare check did not clear.")
    return fresh, html


def scrape_forum_details(url: str, pages: int, out_path: Path, batch: int, delay: float) -> dict:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    done = load_done(out_path)
    site, first_html = reopen(None, url)
    if "normalthread_" not in first_html and 'class="s xst"' not in first_html:
        site.close()
        raise RuntimeError("Forum page did not load.")
    fid, _current, last = forum_identity(url, first_html)
    end_page = last if pages <= 0 else min(last, pages)
    print(f"Forum {fid}: pages 1-{end_page} of {last}. {len(done)} threads already saved.", flush=True)
    page_no = 1
    written = 0
    pages_on_browser = 0
    page_failures = 0
    try:
        while page_no <= end_page:
            try:
                if pages_on_browser >= 40:
                    site, _html = reopen(site, f"{BASE}forum-{fid}-1.html")
                    pages_on_browser = 0
                page_url = f"{BASE}forum-{fid}-{page_no}.html"
                listing_html = first_html if page_no == 1 and first_html else ""
                if not listing_html:
                    fetched = site.fetch_many([page_url])[0]
                    listing_html = str(fetched["html"])
                    if int(fetched["status"]) != 200 or looks_blocked(listing_html):
                        raise RuntimeError(f"Blocked while reading {page_url}")
                listing = parse_forum(listing_html, page_url)
                pending = [thread for thread in listing["threads"] if thread["url"] not in done]
                for start in range(0, len(pending), batch):
                    chunk = pending[start : start + batch]
                    payloads = site.fetch_many([thread["url"] for thread in chunk])
                    by_url = {str(item["url"]): item for item in payloads}
                    for thread in chunk:
                        item = by_url.get(thread["url"])
                        html = str(item["html"]) if item else ""
                        status = int(item["status"]) if item else 0
                        if status != 200 or looks_blocked(html):
                            raise RuntimeError(f"Blocked while reading {thread['url']}")
                        record = movie_record(html, thread)
                        if not record:
                            done.add(thread["url"])
                            continue
                        with out_path.open("a", encoding="utf-8") as handle:
                            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                        done.add(thread["url"])
                        written += 1
                    if delay:
                        time.sleep(delay)
                print(
                    f"page {page_no}/{end_page}: {len(listing['threads'])} listed, {written} saved",
                    flush=True,
                )
                page_no += 1
                pages_on_browser += 1
                page_failures = 0
                first_html = ""
            except Exception as exc:
                page_failures += 1
                print(f"page {page_no} failed ({exc}); reopening browser", file=sys.stderr, flush=True)
                if page_failures >= 4:
                    print(f"skipping page {page_no} after repeated failures", file=sys.stderr, flush=True)
                    page_no += 1
                    page_failures = 0
                time.sleep(5)
                site, _html = reopen(site, f"{BASE}forum-{fid}-1.html")
                pages_on_browser = 0
                first_html = ""
    finally:
        site.close()
    return {"type": "forum-details", "url": url, "written": written, "saved": len(done), "out": str(out_path)}


def parse_page(html: str, page_url: str) -> dict:
    if 'id="thread_subject"' in html or "id='thread_subject'" in html:
        return parse_thread(html, page_url)
    if "normalthread_" in html or 'class="s xst"' in html:
        return parse_forum(html, page_url)
    soup = BeautifulSoup(html, "html.parser")
    title = clean_text(soup.title.get_text(" ", strip=True)) if soup.title else page_url
    raise RuntimeError(f"Unrecognized page ({title}). Pass a thread or forum URL.")


def merge_pages(pages: list[dict]) -> dict:
    first = pages[0]
    if first["type"] == "thread":
        posts: list[dict] = []
        seen: set[str] = set()
        for page in pages:
            for post in page["posts"]:
                if post["id"] in seen:
                    continue
                seen.add(post["id"])
                posts.append(post)
        return {
            "type": "thread",
            "url": first["url"],
            "title": first["title"],
            "breadcrumb": first["breadcrumb"],
            "pages_fetched": len(pages),
            "posts": posts,
        }
    threads: list[dict] = []
    seen_urls: set[str] = set()
    for page in pages:
        for thread in page["threads"]:
            if thread["url"] in seen_urls:
                continue
            seen_urls.add(thread["url"])
            threads.append(thread)
    return {
        "type": "forum",
        "url": first["url"],
        "title": first["title"],
        "pages_fetched": len(pages),
        "threads": threads,
    }


def scrape(url: str, pages: int, delay: float) -> dict:
    pages = max(1, min(pages, MAX_PAGES))
    collected: list[dict] = []
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        context = browser.new_context(user_agent=USER_AGENT, locale="zh-CN")
        page = context.new_page()
        current = url
        try:
            for index in range(pages):
                if index:
                    time.sleep(delay)
                html, final_url = read_html(page, current)
                parsed = parse_page(html, final_url)
                collected.append(parsed)
                current = parsed.get("next_url") or ""
                if not current:
                    break
        finally:
            browser.close()
    if not collected:
        raise RuntimeError(f"No page content returned for {url}")
    return merge_pages(collected)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scrape a sehuatang.org thread or forum page.")
    parser.add_argument("url", help="Thread URL (thread-ID-1-1.html) or forum URL (forum-ID-1.html)")
    parser.add_argument("--pages", type=int, default=1, help="Pages to follow. 0 means every forum page (details mode).")
    parser.add_argument("--delay", type=float, default=0.2, help="Seconds to wait between batches (default: 0.2)")
    parser.add_argument("--batch", type=int, default=BATCH_SIZE, help=f"Threads fetched together (default: {BATCH_SIZE})")
    parser.add_argument("--details", action="store_true", help="Open each forum thread and extract the movie fields, preview images, and magnet")
    parser.add_argument("--out", help="Write JSON (or JSONL, if the name ends in .jsonl) to this file")
    args = parser.parse_args(argv)

    if args.details:
        out_path = Path(args.out or "forum-details.jsonl")
        attempts = 0
        while True:
            try:
                result = scrape_forum_details(args.url, args.pages, out_path, max(1, args.batch), args.delay)
                print(
                    f"Saved {result['written']} threads to {result['out']}",
                    flush=True,
                )
                return 0
            except Exception as exc:
                attempts += 1
                print(f"retry {attempts} after error: {exc}", file=sys.stderr, flush=True)
                if attempts >= 8:
                    raise
                time.sleep(min(30, 3 * attempts))

    result = scrape(args.url, args.pages if args.pages > 0 else MAX_PAGES, max(args.delay, 0.0))
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.write("\n")
        kind = result["type"]
        count = len(result["posts"]) if kind == "thread" else len(result["threads"])
        print(f"Wrote {kind} '{result['title']}' ({count} items, {result['pages_fetched']} pages) to {args.out}")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
