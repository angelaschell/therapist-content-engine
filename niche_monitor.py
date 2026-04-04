# Niche & Competitor Monitoring - Track IG accounts and surface trending topics
import os
import json
import psycopg2
import psycopg2.extras
import httpx
from datetime import datetime, timedelta
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()
DATABASE_URL = os.environ.get("DATABASE_URL", "")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "")


# ── DB Helpers ─────────────────────────────────────────────────
def get_conn():
    if not DATABASE_URL:
        raise Exception("DATABASE_URL not configured")
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


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS monitored_accounts (
    id BIGSERIAL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    display_name TEXT DEFAULT '',
    category TEXT DEFAULT 'competitor',
    notes TEXT DEFAULT '',
    is_active BOOLEAN DEFAULT TRUE,
    last_checked TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS monitored_posts (
    id BIGSERIAL PRIMARY KEY,
    account_username TEXT NOT NULL,
    post_url TEXT DEFAULT '',
    caption TEXT DEFAULT '',
    media_type TEXT DEFAULT 'IMAGE',
    like_count INT DEFAULT 0,
    comment_count INT DEFAULT 0,
    posted_at TIMESTAMPTZ,
    topics JSONB DEFAULT '[]',
    engagement_score FLOAT DEFAULT 0,
    fetched_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS niche_trends (
    id BIGSERIAL PRIMARY KEY,
    topic TEXT NOT NULL,
    frequency INT DEFAULT 1,
    avg_engagement FLOAT DEFAULT 0,
    example_posts JSONB DEFAULT '[]',
    detected_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_monitored_posts_username ON monitored_posts(account_username);
CREATE INDEX IF NOT EXISTS idx_monitored_posts_engagement ON monitored_posts(engagement_score DESC);
CREATE INDEX IF NOT EXISTS idx_niche_trends_detected ON niche_trends(detected_at DESC);
"""

try:
    if DATABASE_URL:
        execute(SCHEMA_SQL)
except Exception as e:
    print(f"[niche_monitor] Schema creation warning: {e}")

# Default accounts to monitor (therapists in the grief/trauma niche)
DEFAULT_ACCOUNTS = [
    {"username": "the.holistic.psychologist", "display_name": "Dr. Nicole LePera", "category": "niche_leader"},
    {"username": "nedratawwab", "display_name": "Nedra Tawwab", "category": "niche_leader"},
    {"username": "lisaoliveratherapy", "display_name": "Lisa Olivera", "category": "competitor"},
    {"username": "sitwithwhit", "display_name": "Whitney Goodman", "category": "competitor"},
    {"username": "hopeedelman", "display_name": "Hope Edelman", "category": "niche_leader"},
    {"username": "the.grief.therapist", "display_name": "Grief Therapist", "category": "competitor"},
]


@router.get("/api/monitor/accounts")
async def list_accounts():
    if not DATABASE_URL:
        return JSONResponse({"accounts": [], "error": "DATABASE_URL not configured"})
    try:
        accounts = query("SELECT * FROM monitored_accounts WHERE is_active = TRUE ORDER BY category, username")
        if not accounts:
            # Seed defaults
            for acct in DEFAULT_ACCOUNTS:
                try:
                    execute(
                        "INSERT INTO monitored_accounts (username, display_name, category) VALUES (%s, %s, %s) ON CONFLICT (username) DO NOTHING",
                        (acct["username"], acct["display_name"], acct["category"])
                    )
                except Exception:
                    pass
            accounts = query("SELECT * FROM monitored_accounts WHERE is_active = TRUE ORDER BY category, username")
        return JSONResponse({"accounts": accounts})
    except Exception as e:
        return JSONResponse({"accounts": [], "error": str(e)[:300]})


@router.post("/api/monitor/accounts")
async def add_account(req: Request):
    data = await req.json()
    username = data.get("username", "").strip().lstrip("@")
    if not username:
        return JSONResponse({"error": "Username required"}, status_code=400)
    row = insert_returning(
        "INSERT INTO monitored_accounts (username, display_name, category, notes) VALUES (%s, %s, %s, %s) ON CONFLICT (username) DO UPDATE SET display_name=EXCLUDED.display_name, category=EXCLUDED.category, notes=EXCLUDED.notes, is_active=TRUE RETURNING *",
        (username, data.get("display_name", ""), data.get("category", "competitor"), data.get("notes", ""))
    )
    return JSONResponse(row)


@router.delete("/api/monitor/accounts/{account_id}")
async def remove_account(account_id: int):
    execute("UPDATE monitored_accounts SET is_active = FALSE WHERE id = %s", (account_id,))
    return JSONResponse({"ok": True})


@router.post("/api/monitor/scan")
async def scan_accounts(req: Request):
    """Fetch recent posts from monitored accounts via Apify and analyze trends."""
    import asyncio

    if not APIFY_TOKEN:
        return JSONResponse({"error": "APIFY_TOKEN not configured. Set the environment variable to enable scanning."}, status_code=400)

    accounts = query("SELECT * FROM monitored_accounts WHERE is_active = TRUE")
    if not accounts:
        return JSONResponse({"error": "No accounts to monitor"}, status_code=400)

    usernames = [a["username"] for a in accounts]
    all_posts = []
    debug_info = {"actor_used": "", "raw_item_keys": [], "items_count": 0, "db_errors": []}

    # Use Apify Instagram Scraper
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            # Try the current actor name first, fall back to legacy
            run_resp = await client.post(
                "https://api.apify.com/v2/acts/apify~instagram-scraper/runs",
                params={"token": APIFY_TOKEN},
                json={
                    "directUrls": [f"https://www.instagram.com/{u}/" for u in usernames],
                    "resultsLimit": 10,
                    "resultsType": "posts",
                    "searchType": "user",
                }
            )
            debug_info["actor_used"] = "apify~instagram-scraper"

            # If the new actor doesn't work, try the legacy name
            if run_resp.status_code in (400, 404):
                run_resp = await client.post(
                    "https://api.apify.com/v2/acts/apify~instagram-profile-scraper/runs",
                    params={"token": APIFY_TOKEN},
                    json={
                        "usernames": usernames,
                        "resultsLimit": 10,
                        "resultsType": "posts",
                    }
                )
                debug_info["actor_used"] = "apify~instagram-profile-scraper"

            if run_resp.status_code != 201:
                return JSONResponse({
                    "error": f"Apify returned status {run_resp.status_code}: {run_resp.text[:500]}"
                }, status_code=500)

            run_data = run_resp.json()
            run_id = run_data["data"]["id"]
            dataset_id = run_data["data"]["defaultDatasetId"]

            # Poll for completion (up to ~4 minutes)
            status = "RUNNING"
            for _ in range(50):
                await asyncio.sleep(5)
                status_resp = await client.get(
                    f"https://api.apify.com/v2/actor-runs/{run_id}",
                    params={"token": APIFY_TOKEN}
                )
                status = status_resp.json().get("data", {}).get("status", "")
                if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
                    break

            if status != "SUCCEEDED":
                return JSONResponse({
                    "error": f"Apify scan ended with status: {status}. Check your Apify dashboard for details."
                }, status_code=500)

            data_resp = await client.get(
                f"https://api.apify.com/v2/datasets/{dataset_id}/items",
                params={"token": APIFY_TOKEN}
            )
            items = data_resp.json()
            debug_info["items_count"] = len(items) if isinstance(items, list) else 0

            if not items or not isinstance(items, list):
                return JSONResponse({
                    "error": "Apify returned 0 posts. The accounts may be private or the scraper needs a different configuration.",
                    "posts_found": 0,
                    "accounts_scanned": len(usernames),
                    "debug": debug_info
                })

            # Log the keys from the first item so we know the field names
            if items:
                debug_info["raw_item_keys"] = list(items[0].keys())[:30]
                debug_info["sample_item"] = {k: str(v)[:100] for k, v in list(items[0].items())[:15]}

            for item in items:
                # Handle multiple possible field names from different Apify actor versions
                username = (
                    item.get("ownerUsername")
                    or item.get("profileUsername")
                    or item.get("username")
                    or (item.get("owner", {}).get("username", "") if isinstance(item.get("owner"), dict) else "")
                ) or ""
                caption = item.get("caption", "") or ""
                likes = int(item.get("likesCount") or item.get("likes") or item.get("digg_count") or 0)
                comments = int(item.get("commentsCount") or item.get("comments") or item.get("comment_count") or 0)
                post_url = item.get("url") or item.get("webLink") or item.get("permalink") or ""
                shortcode = item.get("shortCode") or item.get("shortcode") or ""
                if not post_url and shortcode:
                    post_url = f"https://www.instagram.com/p/{shortcode}/"
                media_type = (item.get("type") or item.get("mediaType") or "Image").upper()
                if isinstance(media_type, int):
                    media_type = {1: "IMAGE", 2: "VIDEO", 8: "CAROUSEL"}.get(media_type, "IMAGE")

                # Handle posted_at — could be ISO string, unix timestamp, or empty
                posted_at_raw = item.get("timestamp") or item.get("takenAtTimestamp") or item.get("taken_at_timestamp") or None
                posted_at = None
                if posted_at_raw:
                    if isinstance(posted_at_raw, (int, float)):
                        # Unix timestamp
                        try:
                            posted_at = datetime.utcfromtimestamp(posted_at_raw).isoformat()
                        except Exception:
                            posted_at = None
                    elif isinstance(posted_at_raw, str) and posted_at_raw.strip():
                        posted_at = posted_at_raw

                # Store post in DB
                try:
                    execute("""
                        INSERT INTO monitored_posts (account_username, post_url, caption, media_type, like_count, comment_count, posted_at, engagement_score)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """, (username, post_url, caption[:2000], media_type, int(likes), int(comments), posted_at, int(likes) + int(comments) * 3))
                except Exception as e:
                    debug_info["db_errors"].append(str(e)[:150])

                all_posts.append({
                    "username": username,
                    "caption": caption[:500],
                    "likes": int(likes),
                    "comments": int(comments),
                    "url": post_url,
                })

            # Update last_checked
            for acct in accounts:
                execute("UPDATE monitored_accounts SET last_checked = NOW() WHERE id = %s", (acct["id"],))

    except Exception as e:
        return JSONResponse({"error": f"Scan failed: {str(e)[:300]}", "partial_posts": len(all_posts), "debug": debug_info}, status_code=500)

    # Analyze trends with Claude
    trends = []
    if all_posts:
        try:
            trends = await analyze_trends(all_posts)
        except Exception:
            pass

    return JSONResponse({
        "posts_found": len(all_posts),
        "accounts_scanned": len(usernames),
        "trends": trends,
        "debug": debug_info,
    })


async def analyze_trends(posts):
    """Use Claude to identify trending topics from scraped posts."""
    if not ANTHROPIC_KEY:
        print("[niche_monitor] ANTHROPIC_API_KEY not configured, skipping trend analysis")
        return []
    post_summaries = "\n".join([
        f"@{p['username']} ({p['likes']}L, {p['comments']}C): {p['caption'][:200]}"
        for p in posts[:50]
    ])

    prompt = f"""Analyze these recent Instagram posts from therapists/grief accounts and identify trending topics.

POSTS:
{post_summaries}

Return ONLY a JSON array of trending topics. No backticks:
[{{"topic": "topic name", "frequency": number_of_posts_about_it, "avg_engagement": average_likes_plus_comments, "description": "1 sentence about why this is trending", "content_angle": "How Angela Schellenberg could create content about this"}}]

Find 5-8 distinct topics. Focus on emotional themes, not generic categories."""

    async with httpx.AsyncClient(timeout=45) as client:
        resp = await client.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 2000,
                "messages": [{"role": "user", "content": prompt}]
            })
        resp.raise_for_status()
        text = "".join(b["text"] for b in resp.json().get("content", []) if b.get("type") == "text").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        trends = json.loads(text)

        # Store trends
        for t in trends:
            execute(
                "INSERT INTO niche_trends (topic, frequency, avg_engagement, example_posts) VALUES (%s, %s, %s, %s)",
                (t.get("topic", ""), t.get("frequency", 1), t.get("avg_engagement", 0), json.dumps([]))
            )

        return trends


@router.get("/api/monitor/status")
async def monitor_status():
    """Quick health check for monitor dependencies."""
    result = {"database": False, "apify": False, "apify_token_set": bool(APIFY_TOKEN)}
    if DATABASE_URL:
        try:
            query("SELECT 1")
            result["database"] = True
            accts = query("SELECT COUNT(*) as cnt FROM monitored_accounts WHERE is_active = TRUE")
            result["account_count"] = accts[0]["cnt"] if accts else 0
        except Exception as e:
            result["database_error"] = str(e)[:200]
    if APIFY_TOKEN:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get("https://api.apify.com/v2/users/me", params={"token": APIFY_TOKEN})
                if r.status_code == 200:
                    result["apify"] = True
                    result["apify_user"] = r.json().get("data", {}).get("username", "")
                else:
                    result["apify_error"] = f"Status {r.status_code}: {r.text[:200]}"
        except Exception as e:
            result["apify_error"] = str(e)[:200]
    return JSONResponse(result)


@router.get("/api/monitor/trends")
async def get_trends():
    """Get recent trends detected from monitoring."""
    trends = query("SELECT * FROM niche_trends ORDER BY detected_at DESC LIMIT 30")
    return JSONResponse({"trends": trends})


@router.get("/api/monitor/posts")
async def get_monitored_posts(req: Request):
    """Get recent posts from monitored accounts."""
    username = req.query_params.get("username", "")
    limit = min(int(req.query_params.get("limit", "50")), 200)

    if username:
        posts = query(
            "SELECT * FROM monitored_posts WHERE account_username = %s ORDER BY engagement_score DESC LIMIT %s",
            (username, limit)
        )
    else:
        posts = query(
            "SELECT * FROM monitored_posts ORDER BY fetched_at DESC, engagement_score DESC LIMIT %s",
            (limit,)
        )
    return JSONResponse({"posts": posts})
