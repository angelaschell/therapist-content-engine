"""
Instagram Caption Extractor — 5 strategies
  1. Graph API via token_mgr (your own posts, instant)
  2. Explore DB lookup (already-scraped posts, instant)
  3. oEmbed API (public posts, fast but flaky)
  4. Apify Instagram Scraper (any public post, 15-30s, reliable)
  5. Enhanced scraping (fast but Instagram often blocks)
"""

import re
import os
import json
import httpx
import psycopg2
import psycopg2.extras
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

FB_APP_ID = os.getenv("FB_APP_ID", "")
FB_APP_SECRET = os.getenv("FB_APP_SECRET", "")
APIFY_TOKEN = os.getenv("APIFY_TOKEN", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")


class LoadPostRequest(BaseModel):
    url: str


class LoadPostResponse(BaseModel):
    caption: str
    method: str


def extract_shortcode(url: str) -> str | None:
    patterns = [
        r"instagram\.com/p/([A-Za-z0-9_-]+)",
        r"instagram\.com/reel/([A-Za-z0-9_-]+)",
        r"instagram\.com/reels/([A-Za-z0-9_-]+)",
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None


# ── Strategy 1: Graph API (your own posts, instant) ──────────
async def try_graph_api(shortcode: str) -> str | None:
    try:
        from instagram_analytics import token_mgr

        page_token = await token_mgr.get_page_token()
        ig_id = token_mgr.ig_account_id
        if not page_token or not ig_id:
            return None

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"https://graph.facebook.com/v21.0/{ig_id}/media",
                params={
                    "fields": "caption,permalink,timestamp",
                    "limit": 100,
                    "access_token": page_token,
                },
            )
            if resp.status_code != 200:
                return None

            data = resp.json()
            for post in data.get("data", []):
                if shortcode in post.get("permalink", ""):
                    return post.get("caption")

            next_url = data.get("paging", {}).get("next")
            for _ in range(2):
                if not next_url:
                    break
                resp = await client.get(next_url)
                if resp.status_code != 200:
                    break
                data = resp.json()
                for post in data.get("data", []):
                    if shortcode in post.get("permalink", ""):
                        return post.get("caption")
                next_url = data.get("paging", {}).get("next")

    except Exception as e:
        print(f"[Graph API] Error: {e}")
    return None


# ── Strategy 2: Explore DB (already-scraped posts, instant) ──
async def try_explore_db(url: str, shortcode: str) -> str | None:
    if not DATABASE_URL:
        return None
    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT caption FROM explore_posts WHERE permalink LIKE %s AND caption != '' LIMIT 1",
            (f"%{shortcode}%",),
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row and row.get("caption"):
            return row["caption"]
    except Exception as e:
        print(f"[Explore DB] Error: {e}")
    return None


# ── Strategy 3: oEmbed API (fast but flaky) ──────────────────
async def try_oembed(url: str) -> str | None:
    if not FB_APP_ID or not FB_APP_SECRET:
        return None
    try:
        app_token = f"{FB_APP_ID}|{FB_APP_SECRET}"
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://graph.facebook.com/v21.0/instagram_oembed",
                params={"url": url, "access_token": app_token, "maxwidth": 320},
            )
            if resp.status_code == 200:
                html = resp.json().get("html", "")
                match = re.search(r"<p[^>]*>(.*?)</p>", html, re.DOTALL)
                if match:
                    caption = match.group(1)
                    caption = (
                        caption.replace("&amp;", "&")
                        .replace("&lt;", "<")
                        .replace("&gt;", ">")
                        .replace("&#39;", "'")
                        .replace("&quot;", '"')
                        .replace("<br>", "\n")
                        .replace("<br/>", "\n")
                    )
                    caption = re.sub(r"<[^>]+>", "", caption).strip()
                    if caption:
                        return caption
    except Exception as e:
        print(f"[oEmbed] Error: {e}")
    return None


# ── Strategy 4: Apify Instagram Scraper (reliable, 15-30s) ───
async def try_apify(url: str) -> str | None:
    if not APIFY_TOKEN:
        return None
    try:
        print(f"[Apify] Scraping single post: {url}")
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"https://api.apify.com/v2/acts/apify~instagram-scraper/run-sync-get-dataset-items?token={APIFY_TOKEN}",
                json={
                    "directUrls": [url],
                    "resultsLimit": 1,
                    "resultsType": "posts",
                },
            )
            if resp.status_code in (200, 201):
                items = resp.json()
                if isinstance(items, list) and items:
                    item = items[0]
                    # Try multiple caption field names
                    caption = (
                        item.get("caption", "")
                        or item.get("text", "")
                        or item.get("alt", "")
                        or ""
                    )
                    # Fallback: nested caption object
                    if not caption and isinstance(
                        item.get("edge_media_to_caption"), dict
                    ):
                        edges = item["edge_media_to_caption"].get("edges", [])
                        if edges:
                            caption = (
                                edges[0].get("node", {}).get("text", "")
                            )
                    if caption and len(caption.strip()) > 10:
                        print(f"[Apify] Got caption: {len(caption)} chars")
                        return caption.strip()
            else:
                print(f"[Apify] Error status: {resp.status_code}")
    except Exception as e:
        print(f"[Apify] Error: {e}")
    return None


# ── Strategy 5: Enhanced scraping (fast but often blocked) ───
async def try_scrape(url: str) -> str | None:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    }
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            html = resp.text

            # og:description
            og = re.search(
                r'<meta\s+(?:property|name)=["\']og:description["\']\s+content=["\'](.+?)["\']',
                html,
                re.DOTALL,
            )
            if og:
                caption = og.group(1)
                caption = (
                    caption.replace("&amp;", "&")
                    .replace("&lt;", "<")
                    .replace("&gt;", ">")
                    .replace("&#39;", "'")
                    .replace("&quot;", '"')
                    .replace("\\n", "\n")
                )
                parts = caption.split(": ", 1)
                if len(parts) > 1:
                    caption = parts[1].strip().strip('"').strip("\u201c").strip("\u201d")
                if caption and len(caption) > 10:
                    return caption

            # Embedded JSON
            jm = re.search(
                r'"caption"\s*:\s*\{[^}]*"text"\s*:\s*"(.*?)"', html, re.DOTALL
            )
            if jm:
                caption = jm.group(1).encode().decode("unicode_escape")
                if caption:
                    return caption

    except Exception as e:
        print(f"[Scrape] Error: {e}")
    return None


# ── Main endpoint ────────────────────────────────────────────
@router.post("/api/load-instagram-post", response_model=LoadPostResponse)
async def load_instagram_post(req: LoadPostRequest):
    url = req.url.strip()
    if "instagram.com" not in url:
        raise HTTPException(400, "Not a valid Instagram URL")

    shortcode = extract_shortcode(url)
    if not shortcode:
        raise HTTPException(400, "Could not extract shortcode from URL")

    # 1. Graph API (own posts, instant)
    caption = await try_graph_api(shortcode)
    if caption:
        return LoadPostResponse(caption=caption, method="graph_api")

    # 2. Explore DB (already scraped, instant)
    caption = await try_explore_db(url, shortcode)
    if caption:
        return LoadPostResponse(caption=caption, method="explore_db")

    # 3. oEmbed (fast, sometimes works)
    caption = await try_oembed(url)
    if caption:
        return LoadPostResponse(caption=caption, method="oembed")

    # 4. Apify (reliable, takes 15-30 seconds)
    caption = await try_apify(url)
    if caption:
        return LoadPostResponse(caption=caption, method="apify")

    # 5. Scraping (fast, often blocked)
    caption = await try_scrape(url)
    if caption:
        return LoadPostResponse(caption=caption, method="scrape")

    raise HTTPException(
        404,
        "Could not extract caption from this post. The post may be private. "
        "Try pasting the caption text manually.",
    )
