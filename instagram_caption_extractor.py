"""
Instagram Caption Extractor — uses existing TokenManager
Strategies:
  1. Instagram Graph API via existing token_mgr (your own posts)
  2. Facebook oEmbed API (public posts)
  3. Enhanced scraping with browser-like headers (fallback)
"""

import re
import os
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

FB_APP_ID = os.getenv("FB_APP_ID", "")
FB_APP_SECRET = os.getenv("FB_APP_SECRET", "")


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


# ── Strategy 1: Graph API via existing TokenManager ──────────
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
                permalink = post.get("permalink", "")
                if shortcode in permalink:
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


# ── Strategy 2: Facebook oEmbed API ──────────────────────────
async def try_oembed(url: str) -> str | None:
    if not FB_APP_ID or not FB_APP_SECRET:
        return None

    try:
        app_token = f"{FB_APP_ID}|{FB_APP_SECRET}"
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://graph.facebook.com/v21.0/instagram_oembed",
                params={
                    "url": url,
                    "access_token": app_token,
                    "maxwidth": 320,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                html = data.get("html", "")
                caption_match = re.search(
                    r'<p[^>]*>(.*?)</p>', html, re.DOTALL
                )
                if caption_match:
                    caption = caption_match.group(1)
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


# ── Strategy 3: Enhanced scraping ────────────────────────────
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
        "Connection": "keep-alive",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    }

    try:
        async with httpx.AsyncClient(
            timeout=15, follow_redirects=True
        ) as client:
            resp = await client.get(url, headers=headers)
            html = resp.text

            og_match = re.search(
                r'<meta\s+(?:property|name)=["\']og:description["\']\s+content=["\'](.+?)["\']',
                html,
                re.DOTALL,
            )
            if og_match:
                caption = og_match.group(1)
                caption = (
                    caption.replace("&amp;", "&")
                    .replace("&lt;", "<")
                    .replace("&gt;", ">")
                    .replace("&#39;", "'")
                    .replace("&quot;", '"')
                    .replace("\\n", "\n")
                )
                colon_split = caption.split(": ", 1)
                if len(colon_split) > 1:
                    caption = colon_split[1].strip().strip('"').strip("\u201c").strip("\u201d")
                if caption and len(caption) > 10:
                    return caption

            json_match = re.search(
                r'"caption"\s*:\s*\{[^}]*"text"\s*:\s*"(.*?)"',
                html,
                re.DOTALL,
            )
            if json_match:
                caption = json_match.group(1)
                caption = caption.encode().decode("unicode_escape")
                if caption:
                    return caption

            tw_match = re.search(
                r'<meta\s+(?:property|name)=["\']twitter:description["\']\s+content=["\'](.+?)["\']',
                html,
                re.DOTALL,
            )
            if tw_match:
                caption = tw_match.group(1)
                if caption and len(caption) > 10:
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

    caption = await try_graph_api(shortcode)
    if caption:
        return LoadPostResponse(caption=caption, method="graph_api")

    caption = await try_oembed(url)
    if caption:
        return LoadPostResponse(caption=caption, method="oembed")

    caption = await try_scrape(url)
    if caption:
        return LoadPostResponse(caption=caption, method="scrape")

    raise HTTPException(
        404,
        "Could not extract caption. Instagram may be blocking the request. "
        "Try pasting the caption text manually.",
    )
