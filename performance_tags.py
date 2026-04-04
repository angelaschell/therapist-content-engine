# Content Performance Tags - Auto-tag published posts and correlate what works best
import os
import json
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
    try:
        conn.autocommit = True
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params or ())
        result = [clean(r) for r in cur.fetchall()]
        cur.close()
        return result
    finally:
        conn.close()

def execute(sql, params=None):
    conn = get_conn()
    try:
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(sql, params or ())
        cur.close()
    finally:
        conn.close()

def insert_returning(sql, params=None):
    conn = get_conn()
    try:
        conn.autocommit = True
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params or ())
        row = clean(cur.fetchone())
        cur.close()
        return row
    finally:
        conn.close()


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS content_tags (
    id BIGSERIAL PRIMARY KEY,
    ig_media_id TEXT,
    carousel_id BIGINT,
    topic TEXT DEFAULT '',
    template_type TEXT DEFAULT '',
    viral_mode TEXT DEFAULT '',
    trigger_keyword TEXT DEFAULT '',
    pillar TEXT DEFAULT '',
    themes JSONB DEFAULT '[]',
    likes INT DEFAULT 0,
    comments INT DEFAULT 0,
    saves INT DEFAULT 0,
    shares INT DEFAULT 0,
    reach INT DEFAULT 0,
    engagement_rate FLOAT DEFAULT 0,
    posted_at TIMESTAMPTZ,
    tagged_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_content_tags_template ON content_tags(template_type);
CREATE INDEX IF NOT EXISTS idx_content_tags_mode ON content_tags(viral_mode);
CREATE INDEX IF NOT EXISTS idx_content_tags_trigger ON content_tags(trigger_keyword);
"""

try:
    if DATABASE_URL:
        execute(SCHEMA_SQL)
except Exception as e:
    print(f"[performance_tags] Schema setup: {e}")


@router.post("/api/tags/auto-tag")
async def auto_tag_posts(req: Request):
    """Auto-tag posts by analyzing their captions with Claude."""
    data = await req.json()
    posts = data.get("posts", [])

    if not posts:
        return JSONResponse({"error": "No posts provided"}, status_code=400)

    post_lines = "\n".join([
        f"[{p.get('id', i)}] Caption: {(p.get('caption', '') or '')[:300]} | Likes: {p.get('like_count', 0)} | Comments: {p.get('comments_count', 0)}"
        for i, p in enumerate(posts[:20])
    ])

    prompt = f"""Analyze these Instagram posts and tag each one.

POSTS:
{post_lines}

For each post, return a JSON array:
[{{"post_id": "id", "topic": "main topic", "template_type": "naming|redefine|tribal|framework|pullquote|editorial|conversational|covercontext", "viral_mode": "emotional_recognition|authority_redefine|tribal_identity", "pillar": "Grief Education|Attachment & Relationships|Nervous System|Clinical Authority|Community", "themes": ["theme1", "theme2"]}}]

Rules:
- template_type: detect the writing pattern used
- viral_mode: which of Angela's 3 modes does this most match
- pillar: which content pillar it falls under
- themes: 2-3 specific emotional themes (e.g. "motherless daughters", "milestone grief", "nervous system regulation")
- Return ONLY valid JSON"""

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post("https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": "claude-sonnet-4-20250514", "max_tokens": 2000,
                    "messages": [{"role": "user", "content": prompt}]})
            resp.raise_for_status()
            text = "".join(b["text"] for b in resp.json().get("content", []) if b.get("type") == "text").strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            tags = json.loads(text)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    saved = 0
    for tag in tags:
        post = next((p for p in posts if str(p.get("id", "")) == str(tag.get("post_id", ""))), None)
        execute("""
            INSERT INTO content_tags (ig_media_id, topic, template_type, viral_mode, trigger_keyword, pillar, themes, likes, comments, posted_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            post.get("id") if post else tag.get("post_id"),
            tag.get("topic", ""),
            tag.get("template_type", ""),
            tag.get("viral_mode", ""),
            tag.get("trigger_keyword", ""),
            tag.get("pillar", ""),
            json.dumps(tag.get("themes", [])),
            post.get("like_count", 0) if post else 0,
            post.get("comments_count", 0) if post else 0,
            post.get("timestamp") if post else None,
        ))
        saved += 1

    return JSONResponse({"tagged": saved})


@router.get("/api/tags/correlations")
async def get_correlations():
    """Calculate which combinations of template + mode + trigger perform best."""
    all_tags = query("SELECT * FROM content_tags WHERE likes > 0 OR comments > 0")
    if not all_tags:
        return JSONResponse({"correlations": [], "message": "No tagged posts with engagement data yet"})

    # Correlate by template type
    by_template = {}
    for t in all_tags:
        key = t.get("template_type", "unknown")
        if key not in by_template:
            by_template[key] = {"count": 0, "total_eng": 0}
        by_template[key]["count"] += 1
        by_template[key]["total_eng"] += (t.get("likes", 0) or 0) + (t.get("comments", 0) or 0)

    template_perf = [{"template": k, "posts": v["count"], "avg_engagement": round(v["total_eng"] / v["count"], 1)}
                     for k, v in by_template.items() if v["count"] > 0]
    template_perf.sort(key=lambda x: x["avg_engagement"], reverse=True)

    # Correlate by viral mode
    by_mode = {}
    for t in all_tags:
        key = t.get("viral_mode", "unknown")
        if key not in by_mode:
            by_mode[key] = {"count": 0, "total_eng": 0}
        by_mode[key]["count"] += 1
        by_mode[key]["total_eng"] += (t.get("likes", 0) or 0) + (t.get("comments", 0) or 0)

    mode_perf = [{"mode": k, "posts": v["count"], "avg_engagement": round(v["total_eng"] / v["count"], 1)}
                 for k, v in by_mode.items() if v["count"] > 0]
    mode_perf.sort(key=lambda x: x["avg_engagement"], reverse=True)

    # Correlate by pillar
    by_pillar = {}
    for t in all_tags:
        key = t.get("pillar", "unknown")
        if key not in by_pillar:
            by_pillar[key] = {"count": 0, "total_eng": 0}
        by_pillar[key]["count"] += 1
        by_pillar[key]["total_eng"] += (t.get("likes", 0) or 0) + (t.get("comments", 0) or 0)

    pillar_perf = [{"pillar": k, "posts": v["count"], "avg_engagement": round(v["total_eng"] / v["count"], 1)}
                   for k, v in by_pillar.items() if v["count"] > 0]
    pillar_perf.sort(key=lambda x: x["avg_engagement"], reverse=True)

    # Top themes
    theme_eng = {}
    for t in all_tags:
        themes = t.get("themes", [])
        if isinstance(themes, str):
            try: themes = json.loads(themes)
            except (json.JSONDecodeError, Exception): themes = []
        eng = (t.get("likes", 0) or 0) + (t.get("comments", 0) or 0)
        for theme in themes:
            if theme not in theme_eng:
                theme_eng[theme] = {"count": 0, "total_eng": 0}
            theme_eng[theme]["count"] += 1
            theme_eng[theme]["total_eng"] += eng

    top_themes = [{"theme": k, "posts": v["count"], "avg_engagement": round(v["total_eng"] / v["count"], 1)}
                  for k, v in theme_eng.items() if v["count"] > 0]
    top_themes.sort(key=lambda x: x["avg_engagement"], reverse=True)

    # Best combo (template + mode)
    combos = {}
    for t in all_tags:
        key = f"{t.get('template_type', '?')} + {t.get('viral_mode', '?')}"
        if key not in combos:
            combos[key] = {"count": 0, "total_eng": 0}
        combos[key]["count"] += 1
        combos[key]["total_eng"] += (t.get("likes", 0) or 0) + (t.get("comments", 0) or 0)

    best_combos = [{"combo": k, "posts": v["count"], "avg_engagement": round(v["total_eng"] / v["count"], 1)}
                   for k, v in combos.items() if v["count"] >= 2]
    best_combos.sort(key=lambda x: x["avg_engagement"], reverse=True)

    return JSONResponse({
        "by_template": template_perf,
        "by_mode": mode_perf,
        "by_pillar": pillar_perf,
        "top_themes": top_themes[:15],
        "best_combos": best_combos[:10],
        "total_tagged": len(all_tags),
    })


@router.get("/api/tags")
async def list_tags():
    tags = query("SELECT * FROM content_tags ORDER BY tagged_at DESC LIMIT 100")
    return JSONResponse({"tags": tags})
