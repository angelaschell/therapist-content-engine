"""
Explore Feed Backend
────────────────────
Pulls top carousel posts from Instagram hashtags using the Graph API.
Free. Uses Angela's existing IG Business Account token.

Limitations:
- 30 unique hashtags per 7-day rolling window
- Up to 50 top media per hashtag
- Carousel children include media_url for each slide

Add to main.py:
  from explore_backend import router as explore_router
  app.include_router(explore_router)
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
import httpx
import os
import json
import psycopg2
import psycopg2.extras
from datetime import datetime, timedelta

router = APIRouter()

DATABASE_URL = os.environ.get("DATABASE_URL", "")
GRAPH_API_BASE = "https://graph.facebook.com/v19.0"

# Hashtags to pull from (9 hashtags, well under the 30/week limit)
DEFAULT_HASHTAGS = [
    "therapistsofinstagram",
    "grieftherapist",
    "attachmenttheory",
    "healingtrauma",
    "innerchildhealing",
    "nervousystemregulation",
    "personalgrowth",
    "selfawareness",
    "psychologyfacts",
]


# ── DB Helpers ─────────────────────────────────────────────────
def get_conn():
    return psycopg2.connect(DATABASE_URL)

def clean(row):
    if not row:
        return None
    d = dict(row)
    for k, v in d.items():
        if isinstance(v, datetime):
            d[k] = v.isoformat()
        elif hasattr(v, 'isoformat'):
            d[k] = v.isoformat()
    return d

def query(sql, params=None):
    conn = get_conn()
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(sql, params or ())
    result = [clean(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return result

def execute(sql, params=None):
    conn = get_conn()
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(sql, params or ())
    cur.close()
    conn.close()

def insert_returning(sql, params=None):
    conn = get_conn()
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(sql, params or ())
    row = clean(cur.fetchone())
    cur.close()
    conn.close()
    return row


# ── Auto-setup table ──────────────────────────────────────────
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS explore_posts (
    id BIGSERIAL PRIMARY KEY,
    ig_media_id TEXT UNIQUE,
    hashtag TEXT DEFAULT '',
    media_type TEXT DEFAULT '',
    caption TEXT DEFAULT '',
    permalink TEXT DEFAULT '',
    thumbnail_url TEXT DEFAULT '',
    slide_urls JSONB DEFAULT '[]',
    like_count INT DEFAULT 0,
    comments_count INT DEFAULT 0,
    timestamp TIMESTAMP,
    created_at TIMESTAMP DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_explore_hashtag ON explore_posts(hashtag);
CREATE INDEX IF NOT EXISTS idx_explore_likes ON explore_posts(like_count DESC);
"""

try:
    execute(SCHEMA_SQL)
except Exception as e:
    print(f"Explore schema setup: {e}")


# ── Token helper (reuse existing TokenManager) ────────────────
async def get_ig_credentials():
    """Get the IG account ID and token from the existing TokenManager."""
    from instagram_analytics import token_mgr
    token = await token_mgr.get_page_token()
    ig_id = token_mgr.ig_account_id
    return token, ig_id


# ── Hashtag search ────────────────────────────────────────────
async def search_hashtag_id(hashtag, token, ig_user_id):
    """Get the hashtag ID from Instagram."""
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            f"{GRAPH_API_BASE}/ig_hashtag_search",
            params={
                "q": hashtag,
                "user_id": ig_user_id,
                "access_token": token
            }
        )
    data = r.json()
    if data.get("data") and len(data["data"]) > 0:
        return data["data"][0]["id"]
    return None


async def get_top_media(hashtag_id, token, ig_user_id, limit=30):
    """Get top media for a hashtag."""
    fields = "id,caption,media_type,media_url,permalink,timestamp,like_count,comments_count,children{media_url,media_type}"
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(
            f"{GRAPH_API_BASE}/{hashtag_id}/top_media",
            params={
                "user_id": ig_user_id,
                "fields": fields,
                "limit": limit,
                "access_token": token
            }
        )
    data = r.json()
    return data.get("data", [])


# ── Pull hashtag posts ────────────────────────────────────────
@router.post("/api/explore/refresh")
async def refresh_explore(req: Request):
    """Pull top posts from configured hashtags. Call this to refresh the feed."""
    try:
        data = await req.json() if req.headers.get("content-type") == "application/json" else {}
        hashtags = data.get("hashtags", DEFAULT_HASHTAGS)

        token, ig_id = await get_ig_credentials()
        if not token or not ig_id:
            return JSONResponse({"success": False, "error": "No Instagram token. Connect in Analytics tab first."})

        total_saved = 0
        errors = []

        for tag in hashtags:
            try:
                # Get hashtag ID
                h_id = await search_hashtag_id(tag, token, ig_id)
                if not h_id:
                    errors.append(f"{tag}: not found")
                    continue

                # Get top media
                media = await get_top_media(h_id, token, ig_id, limit=30)

                for post in media:
                    media_type = post.get("media_type", "")

                    # Get slide URLs for carousels
                    slide_urls = []
                    if media_type == "CAROUSEL_ALBUM" and post.get("children"):
                        for child in post["children"].get("data", []):
                            if child.get("media_url"):
                                slide_urls.append({
                                    "url": child["media_url"],
                                    "type": child.get("media_type", "IMAGE")
                                })

                    thumbnail = ""
                    if slide_urls:
                        thumbnail = slide_urls[0]["url"]
                    elif post.get("media_url"):
                        thumbnail = post["media_url"]

                    try:
                        execute(
                            """INSERT INTO explore_posts 
                               (ig_media_id, hashtag, media_type, caption, permalink, thumbnail_url, slide_urls, like_count, comments_count, timestamp)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                               ON CONFLICT (ig_media_id) DO UPDATE SET
                               like_count=EXCLUDED.like_count, comments_count=EXCLUDED.comments_count""",
                            (post.get("id", ""), tag, media_type,
                             post.get("caption", "")[:5000], post.get("permalink", ""),
                             thumbnail, json.dumps(slide_urls),
                             post.get("like_count", 0), post.get("comments_count", 0),
                             post.get("timestamp", None))
                        )
                        total_saved += 1
                    except Exception:
                        pass

            except Exception as e:
                errors.append(f"{tag}: {str(e)[:100]}")

        return JSONResponse({
            "success": True,
            "saved": total_saved,
            "hashtags_searched": len(hashtags),
            "errors": errors if errors else None
        })

    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# ── Get explore feed ──────────────────────────────────────────
@router.get("/api/explore/feed")
async def get_feed(request: Request):
    """Get the explore feed, optionally filtered."""
    try:
        filter_type = request.query_params.get("type", "all")  # all, carousel, image, video
        hashtag = request.query_params.get("hashtag", "")
        sort = request.query_params.get("sort", "likes")  # likes, recent, comments
        limit = min(int(request.query_params.get("limit", "60")), 200)

        where = "WHERE 1=1"
        params = []

        if filter_type == "carousel":
            where += " AND media_type = 'CAROUSEL_ALBUM'"
        elif filter_type == "image":
            where += " AND media_type = 'IMAGE'"
        elif filter_type == "video":
            where += " AND media_type = 'VIDEO'"

        if hashtag:
            where += " AND hashtag = %s"
            params.append(hashtag)

        order = "like_count DESC"
        if sort == "recent":
            order = "timestamp DESC NULLS LAST"
        elif sort == "comments":
            order = "comments_count DESC"

        params.append(limit)
        posts = query(
            f"SELECT * FROM explore_posts {where} ORDER BY {order} LIMIT %s",
            tuple(params)
        )

        return JSONResponse({"success": True, "posts": posts, "count": len(posts)})

    except Exception as e:
        return JSONResponse({"success": False, "error": str(e), "posts": []})


# ── Get available hashtags ────────────────────────────────────
@router.get("/api/explore/hashtags")
async def get_hashtags():
    """Get list of hashtags that have been scraped."""
    try:
        rows = query(
            "SELECT hashtag, COUNT(*) as count, MAX(like_count) as top_likes FROM explore_posts GROUP BY hashtag ORDER BY count DESC"
        )
        return JSONResponse({"success": True, "hashtags": rows, "defaults": DEFAULT_HASHTAGS})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e), "hashtags": []})


# ── Save a post to personal inspo library ─────────────────────
@router.post("/api/explore/save")
async def save_to_inspo(req: Request):
    """Manually save a post (pasted text + optional screenshot) to inspo library."""
    try:
        data = await req.json()
        caption = data.get("caption", "")
        source = data.get("source", "manual")
        permalink = data.get("permalink", "")

        if not caption:
            return JSONResponse({"success": False, "error": "No caption text provided"})

        row = insert_returning(
            """INSERT INTO explore_posts (ig_media_id, hashtag, media_type, caption, permalink, thumbnail_url, slide_urls, like_count)
               VALUES (%s, %s, 'MANUAL', %s, %s, '', '[]', 0) RETURNING *""",
            (f"manual_{datetime.now().timestamp()}", source, caption, permalink)
        )

        return JSONResponse({"success": True, "post": row})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


# ── Scrape a single Instagram post caption ────────────────────
@router.post("/api/explore/scrape-post")
async def scrape_post(req: Request):
    """Extract caption text from an Instagram post URL.
    Tries: 1) oEmbed API with app token, 2) Page meta tags."""
    try:
        data = await req.json()
        url = data.get("url", "").strip()

        if not url or "instagram.com" not in url:
            return JSONResponse({"success": False, "error": "Invalid Instagram URL"})

        caption = ""

        # Method 1: Facebook oEmbed API (most reliable, needs app credentials)
        app_id = os.environ.get("FB_APP_ID", "")
        app_secret = os.environ.get("FB_APP_SECRET", "")

        if app_id and app_secret:
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    r = await client.get(
                        f"{GRAPH_API_BASE}/instagram_oembed",
                        params={
                            "url": url,
                            "access_token": f"{app_id}|{app_secret}",
                            "fields": "author_name,html"
                        }
                    )
                if r.status_code == 200:
                    oembed = r.json()
                    html = oembed.get("html", "")
                    # Extract caption from embed HTML
                    # The caption sits inside a <p> tag in the embed
                    import re
                    p_match = re.search(r'<p[^>]*>(.*?)</p>', html, re.DOTALL)
                    if p_match:
                        raw = p_match.group(1)
                        # Clean HTML entities and tags
                        raw = re.sub(r'<[^>]+>', '', raw)
                        raw = raw.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&#39;', "'").replace('&quot;', '"')
                        caption = raw.strip()
                        if caption:
                            return JSONResponse({"success": True, "caption": caption, "method": "oembed", "author": oembed.get("author_name", "")})
            except Exception as e:
                pass  # Fall through to method 2

        # Method 2: Fetch page and parse og:description meta tag
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
            }
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                r = await client.get(url, headers=headers)

            if r.status_code == 200:
                text = r.text
                import re
                # Try og:description
                og_match = re.search(r'<meta\s+(?:property|name)="og:description"\s+content="([^"]*)"', text)
                if not og_match:
                    og_match = re.search(r'content="([^"]*)"\s+(?:property|name)="og:description"', text)

                if og_match:
                    raw = og_match.group(1)
                    raw = raw.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&#39;', "'").replace('&quot;', '"').replace('\\n', '\n')
                    # og:description often has format: "LIKES likes, COMMENTS comments - Author (@handle) on Instagram: "CAPTION""
                    caption_match = re.search(r'on Instagram:\s*["\u201c](.+)["\u201d]?$', raw, re.DOTALL)
                    if caption_match:
                        caption = caption_match.group(1).rstrip('"').rstrip('\u201d').strip()
                    else:
                        caption = raw.strip()

                    if caption:
                        return JSONResponse({"success": True, "caption": caption, "method": "meta"})

                # Try JSON-LD or shared data
                json_match = re.search(r'"caption":\s*\{[^}]*"text":\s*"([^"]+)"', text)
                if json_match:
                    caption = json_match.group(1).replace('\\n', '\n').replace('\\"', '"')
                    if caption:
                        return JSONResponse({"success": True, "caption": caption, "method": "json"})

        except Exception as e:
            pass

        if caption:
            return JSONResponse({"success": True, "caption": caption, "method": "fallback"})

        return JSONResponse({
            "success": False,
            "error": "Could not extract caption. The post may be private, or Instagram blocked the request. Try pasting the text manually."
        })

    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})
