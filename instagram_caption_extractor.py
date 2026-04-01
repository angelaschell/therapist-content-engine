"""
Instagram Caption Extractor — drop-in replacement for Content Engine
Uses 3 strategies in order:
  1. Instagram Graph API (your own posts, most reliable)
  2. Facebook oEmbed API (public posts, no scraping)
  3. Enhanced scraping with browser-like headers (fallback)
"""

import re
import os
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

# ── Pull these from your existing env / Supabase config ──────────────
IG_USER_ID = os.getenv("IG_USER_ID")           # Your Instagram Business account ID
IG_ACCESS_TOKEN = os.getenv("IG_ACCESS_TOKEN")  # Your long-lived Page token (already in your app)
FB_APP_ID = os.getenv("FB_APP_ID", "")          # Optional: Facebook App ID
FB_APP_SECRET = os.getenv("FB_APP_SECRET", "")  # Optional: Facebook App Secret


class LoadPostRequest(BaseModel):
    url: str


class LoadPostResponse(BaseModel):
    caption: str
    method: str  # which strategy worked


def extract_shortcode(url: str) -> str | None:
    """Pull the shortcode from any Instagram post/reel URL."""
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


# ── Strategy 1: Instagram Graph API (your own posts) ─────────────────
async def try_graph_api(shortcode: str) -> str | None:
    """
    Search your own media by permalink to find the caption.
    This is the most reliable method for your own content.
    """
    if not IG_USER_ID or not IG_ACCESS_TOKEN:
        return None

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # Fetch recent media (up to 100) and match by shortcode in permalink
            resp = await client.get(
                f"https://graph.facebook.com/v21.0/{IG_USER_ID}/media",
                params={
                    "fields": "caption,permalink,shortcode,timestamp",
                    "limit": 100,
                    "access_token": IG_ACCESS_TOKEN,
                },
            )
            if resp.status_code != 200:
                return None

            data = resp.json()
            for post in data.get("data", []):
                permalink = post.get("permalink", "")
                # Match shortcode in the permalink
                if shortcode in permalink:
                    return post.get("caption")

            # If not in first 100, try pagination (up to 2 more pages)
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


# ── Strategy 2: Facebook oEmbed API ──────────────────────────────────
async def try_oembed(url: str) -> str | None:
    """
    Use Facebook's oEmbed endpoint. Works for public posts.
    Requires FB_APP_ID + FB_APP_SECRET (app token).
    """
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
                # oEmbed returns HTML; extract caption from it
                html = data.get("html", "")
                # The caption is in the <p> tag of the embed
                caption_match = re.search(
                    r'<p[^>]*>(.*?)</p>', html, re.DOTALL
                )
                if caption_match:
                    caption = caption_match.group(1)
                    # Clean HTML entities
                    caption = (
                        caption.replace("&amp;", "&")
                        .replace("&lt;", "<")
                        .replace("&gt;", ">")
                        .replace("&#39;", "'")
                        .replace("&quot;", '"')
                        .replace("<br>", "\n")
                        .replace("<br/>", "\n")
                    )
                    # Strip remaining HTML tags
                    caption = re.sub(r"<[^>]+>", "", caption).strip()
                    if caption:
                        return caption
    except Exception as e:
        print(f"[oEmbed] Error: {e}")

    return None


# ── Strategy 3: Enhanced scraping with browser headers ───────────────
async def try_scrape(url: str) -> str | None:
    """
    Fetch the Instagram page with browser-like headers and extract
    the caption from meta tags or embedded JSON.
    """
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

            # Method A: og:description meta tag (most common)
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
                # og:description often has format: "N Likes, N Comments - @user on ...: "caption""
                # Try to extract just the caption part after the colon
                colon_split = caption.split(": ", 1)
                if len(colon_split) > 1:
                    caption = colon_split[1].strip().strip('"').strip(""").strip(""")
                if caption and len(caption) > 10:
                    return caption

            # Method B: Look for caption in the JSON data embedded in the page
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

            # Method C: twitter:description meta tag
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


# ── Main endpoint ────────────────────────────────────────────────────
@router.post("/api/load-instagram-post", response_model=LoadPostResponse)
async def load_instagram_post(req: LoadPostRequest):
    url = req.url.strip()

    if "instagram.com" not in url:
        raise HTTPException(400, "Not a valid Instagram URL")

    shortcode = extract_shortcode(url)
    if not shortcode:
        raise HTTPException(400, "Could not extract shortcode from URL")

    # Strategy 1: Graph API (your own posts)
    caption = await try_graph_api(shortcode)
    if caption:
        return LoadPostResponse(caption=caption, method="graph_api")

    # Strategy 2: oEmbed
    caption = await try_oembed(url)
    if caption:
        return LoadPostResponse(caption=caption, method="oembed")

    # Strategy 3: Enhanced scraping
    caption = await try_scrape(url)
    if caption:
        return LoadPostResponse(caption=caption, method="scrape")

    raise HTTPException(
        404,
        "Could not extract caption. Instagram may be blocking the request. "
        "Try pasting the caption text manually.",
    )
