# Smart Hashtag Optimizer - Dynamic hashtag scoring based on performance data
import os
import json
import re
import psycopg2
import psycopg2.extras
import httpx
from datetime import datetime
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()
DATABASE_URL = os.environ.get("DATABASE_URL", "")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")


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


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS hashtag_performance (
    id BIGSERIAL PRIMARY KEY,
    hashtag TEXT NOT NULL,
    times_used INT DEFAULT 0,
    avg_engagement FLOAT DEFAULT 0,
    best_engagement INT DEFAULT 0,
    category TEXT DEFAULT 'general',
    competition_level TEXT DEFAULT 'medium',
    last_used TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_hashtag_unique ON hashtag_performance(hashtag);
"""

try:
    if DATABASE_URL:
        execute(SCHEMA_SQL)
except Exception as e:
    print(f"[hashtag_optimizer] Schema setup: {e}")


def extract_hashtags(text):
    if not text:
        return []
    return [tag.lower() for tag in re.findall(r'#(\w+)', text)]


@router.post("/api/hashtags/analyze")
async def analyze_hashtag_performance(req: Request):
    """Scan saved carousels and analytics posts to score hashtag performance."""
    # Get posts from explore_posts that have captions with hashtags
    try:
        explore_posts = query(
            "SELECT caption, like_count, comments_count FROM explore_posts WHERE caption IS NOT NULL AND caption != '' LIMIT 500"
        )
    except Exception:
        explore_posts = []

    hashtag_stats = {}
    for post in explore_posts:
        tags = extract_hashtags(post.get("caption", ""))
        eng = (post.get("like_count", 0) or 0) + (post.get("comments_count", 0) or 0)
        for tag in tags:
            if tag not in hashtag_stats:
                hashtag_stats[tag] = {"total_eng": 0, "count": 0, "best": 0}
            hashtag_stats[tag]["total_eng"] += eng
            hashtag_stats[tag]["count"] += 1
            if eng > hashtag_stats[tag]["best"]:
                hashtag_stats[tag]["best"] = eng

    # Store/update
    updated = 0
    for tag, stats in hashtag_stats.items():
        avg = round(stats["total_eng"] / stats["count"], 1) if stats["count"] > 0 else 0
        execute("""
            INSERT INTO hashtag_performance (hashtag, times_used, avg_engagement, best_engagement, updated_at)
            VALUES (%s, %s, %s, %s, NOW())
            ON CONFLICT (hashtag) DO UPDATE SET
                times_used = EXCLUDED.times_used,
                avg_engagement = EXCLUDED.avg_engagement,
                best_engagement = EXCLUDED.best_engagement,
                updated_at = NOW()
        """, (tag, stats["count"], avg, stats["best"]))
        updated += 1

    return JSONResponse({"analyzed": updated, "total_posts_scanned": len(explore_posts)})


@router.get("/api/hashtags/top")
async def get_top_hashtags(req: Request):
    """Get top performing hashtags sorted by avg engagement."""
    limit = min(int(req.query_params.get("limit", "30")), 100)
    category = req.query_params.get("category", "")

    where = "WHERE times_used >= 2"
    params = []
    if category:
        where += " AND category = %s"
        params.append(category)
    params.append(limit)

    tags = query(
        f"SELECT * FROM hashtag_performance {where} ORDER BY avg_engagement DESC LIMIT %s",
        tuple(params)
    )
    return JSONResponse({"hashtags": tags})


@router.post("/api/hashtags/recommend")
async def recommend_hashtags(req: Request):
    """Get AI-recommended hashtags for a specific topic, combining performance data with research."""
    data = await req.json()
    topic = data.get("topic", "grief")

    # Get top performing hashtags from our data
    top = query(
        "SELECT hashtag, avg_engagement, times_used FROM hashtag_performance WHERE times_used >= 2 ORDER BY avg_engagement DESC LIMIT 30"
    )
    top_list = ", ".join([f"#{h['hashtag']} (avg eng: {h['avg_engagement']})" for h in top[:20]])

    prompt = f"""Recommend the best 15 Instagram hashtags for this topic: {topic}

Angela Schellenberg is a grief/trauma therapist with 171K followers.

TOP PERFORMING HASHTAGS FROM HER DATA:
{top_list or 'No performance data yet'}

Return ONLY valid JSON:
{{"hashtags": [
  {{"tag": "hashtag_without_hash", "category": "niche|community|broad|trending", "competition": "low|medium|high", "reason": "1 sentence why"}},
  ...
]}}

Mix:
- 5 niche hashtags (specific to grief/trauma therapy, lower competition)
- 5 community hashtags (where her audience hangs out)
- 3 broad reach hashtags (higher volume, casting a wider net)
- 2 trending/timely hashtags (seasonal or currently popular)

Prioritize hashtags that appear in the performance data with high engagement."""

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post("https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": "claude-sonnet-4-20250514", "max_tokens": 1500,
                    "messages": [{"role": "user", "content": prompt}]})
            resp.raise_for_status()
            text = "".join(b["text"] for b in resp.json().get("content", []) if b.get("type") == "text").strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            result = json.loads(text)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    return JSONResponse(result)
