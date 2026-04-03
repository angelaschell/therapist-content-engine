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
    execute(SCHEMA_SQL)
except Exception:
    pass

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
    if not APIFY_TOKEN:
        return JSONResponse({"error": "APIFY_TOKEN not configured. Set the environment variable to enable scanning."}, status_code=400)

    accounts = query("SELECT * FROM monitored_accounts WHERE is_active = TRUE")
    if not accounts:
        return JSONResponse({"error": "No accounts to monitor"}, status_code=400)

    usernames = [a["username"] for a in accounts]
    all_posts = []

    # Use Apify Instagram Profile Scraper
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            run_resp = await client.post(
                f"https://api.apify.com/v2/acts/apify~instagram-profile-scraper/runs",
                params={"token": APIFY_TOKEN},
                json={
                    "usernames": usernames,
                    "resultsLimit": 10,
                    "resultsType": "posts",
                }
            )
            run_resp.raise_for_status()
            run_data = run_resp.json()
            run_id = run_data["data"]["id"]
            dataset_id = run_data["data"]["defaultDatasetId"]

            # Poll for completion
            for _ in range(30):
                import asyncio
                await asyncio.sleep(5)
                status_resp = await client.get(
                    f"https://api.apify.com/v2/actor-runs/{run_id}",
                    params={"token": APIFY_TOKEN}
                )
                status = status_resp.json().get("data", {}).get("status", "")
                if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
                    break

            if status == "SUCCEEDED":
                data_resp = await client.get(
                    f"https://api.apify.com/v2/datasets/{dataset_id}/items",
                    params={"token": APIFY_TOKEN}
                )
                items = data_resp.json()
                for item in items:
                    username = item.get("ownerUsername", "")
                    caption = item.get("caption", "") or ""
                    likes = item.get("likesCount", 0) or 0
                    comments = item.get("commentsCount", 0) or 0
                    post_url = item.get("url", "")
                    media_type = item.get("type", "Image").upper()
                    posted_at = item.get("timestamp", "")

                    # Store post
                    execute("""
                        INSERT INTO monitored_posts (account_username, post_url, caption, media_type, like_count, comment_count, posted_at, engagement_score)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT DO NOTHING
                    """, (username, post_url, caption[:2000], media_type, likes, comments, posted_at or None, likes + comments * 3))

                    all_posts.append({
                        "username": username,
                        "caption": caption[:500],
                        "likes": likes,
                        "comments": comments,
                        "url": post_url,
                    })

                # Update last_checked
                for acct in accounts:
                    execute("UPDATE monitored_accounts SET last_checked = NOW() WHERE id = %s", (acct["id"],))

    except Exception as e:
        return JSONResponse({"error": f"Scan failed: {str(e)[:300]}", "partial_posts": len(all_posts)}, status_code=500)

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
    })


async def analyze_trends(posts):
    """Use Claude to identify trending topics from scraped posts."""
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
