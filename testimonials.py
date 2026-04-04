# Testimonial Collector - Auto-detect and format testimonials into social proof assets
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
CREATE TABLE IF NOT EXISTS testimonials (
    id BIGSERIAL PRIMARY KEY,
    source TEXT DEFAULT 'comment',
    username TEXT DEFAULT '',
    original_text TEXT NOT NULL,
    formatted_quote TEXT DEFAULT '',
    category TEXT DEFAULT 'general',
    ig_comment_id BIGINT,
    is_approved BOOLEAN DEFAULT FALSE,
    is_used BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
"""

try:
    if DATABASE_URL:
        execute(SCHEMA_SQL)
except Exception as e:
    print(f"[testimonials] Schema setup: {e}")


@router.get("/api/testimonials")
async def list_testimonials(req: Request):
    approved_only = req.query_params.get("approved", "").lower() == "true"
    where = "WHERE is_approved = TRUE" if approved_only else ""
    rows = query(f"SELECT * FROM testimonials {where} ORDER BY created_at DESC LIMIT 100")
    return JSONResponse({"testimonials": rows})


@router.post("/api/testimonials/collect")
async def collect_testimonials():
    """Pull testimonial-category comments and add them to the testimonials table."""
    existing = query("SELECT ig_comment_id FROM testimonials WHERE ig_comment_id IS NOT NULL")
    existing_ids = set(r["ig_comment_id"] for r in existing)

    comments = query(
        "SELECT id, username, comment_text, lead_score FROM ig_comments "
        "WHERE category = 'testimonial' AND LOWER(username) != 'angelaschellenberg' "
        "ORDER BY timestamp DESC LIMIT 100"
    )

    added = 0
    for c in comments:
        if c["id"] in existing_ids:
            continue
        insert_returning(
            "INSERT INTO testimonials (source, username, original_text, ig_comment_id) VALUES (%s, %s, %s, %s) RETURNING id",
            ("comment", c["username"], c["comment_text"], c["id"])
        )
        added += 1

    return JSONResponse({"collected": added, "total_testimonial_comments": len(comments)})


@router.post("/api/testimonials/format")
async def format_testimonials(req: Request):
    """Use Claude to format raw testimonials into polished social proof quotes."""
    unformatted = query(
        "SELECT * FROM testimonials WHERE formatted_quote = '' OR formatted_quote IS NULL ORDER BY created_at DESC LIMIT 20"
    )
    if not unformatted:
        return JSONResponse({"formatted": 0, "message": "All testimonials already formatted"})

    lines = "\n".join([
        f"[ID:{t['id']}] @{t['username']}: {t['original_text'][:300]}"
        for t in unformatted
    ])

    prompt = f"""Format these Instagram comment testimonials into polished social proof quotes.

TESTIMONIALS:
{lines}

For each, return a JSON array:
[{{"id": <db_id>, "formatted": "The polished quote", "category": "transformation|validation|recommendation|emotional"}}]

Rules:
- Keep the person's voice and emotion. Don't sanitize the rawness.
- Light editing only: fix typos, trim to the most powerful 1-3 sentences
- If the original is already perfect, keep it as-is
- Category: transformation (they changed), validation (they felt seen), recommendation (they'd tell others), emotional (pure feeling)
- Return ONLY valid JSON, no backticks"""

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
            results = json.loads(text)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    formatted_count = 0
    for item in results:
        tid = item.get("id")
        quote = item.get("formatted", "")
        cat = item.get("category", "general")
        if tid and quote:
            execute("UPDATE testimonials SET formatted_quote = %s, category = %s WHERE id = %s", (quote, cat, tid))
            formatted_count += 1

    return JSONResponse({"formatted": formatted_count})


@router.patch("/api/testimonials/{tid}/approve")
async def toggle_approve(tid: int):
    row = insert_returning("UPDATE testimonials SET is_approved = NOT is_approved WHERE id = %s RETURNING *", (tid,))
    return JSONResponse(row or {"error": "Not found"})


@router.patch("/api/testimonials/{tid}/used")
async def mark_used(tid: int):
    row = insert_returning("UPDATE testimonials SET is_used = TRUE WHERE id = %s RETURNING *", (tid,))
    return JSONResponse(row or {"error": "Not found"})


@router.delete("/api/testimonials/{tid}")
async def delete_testimonial(tid: int):
    execute("DELETE FROM testimonials WHERE id = %s", (tid,))
    return JSONResponse({"ok": True})


@router.post("/api/testimonials/carousel")
async def generate_testimonial_carousel(req: Request):
    """Generate a social proof carousel from approved testimonials."""
    data = await req.json()
    count = min(data.get("count", 5), 10)

    approved = query(
        "SELECT * FROM testimonials WHERE is_approved = TRUE AND formatted_quote != '' ORDER BY created_at DESC LIMIT %s",
        (count,)
    )
    if not approved:
        return JSONResponse({"error": "No approved testimonials available"}, status_code=400)

    quotes = "\n".join([f"- @{t['username']}: \"{t['formatted_quote']}\"" for t in approved])
    prompt = f"""Create a social proof Instagram carousel from these testimonials.

TESTIMONIALS:
{quotes}

Return ONLY valid JSON, no backticks:
{{"slides": [
  {{"type": "hook", "upper": "WHAT THEY'RE SAYING", "italic": "real words from real women"}},
  ...one slide per testimonial with type "body" and "html" field containing the quote with @username attribution...
  {{"type": "close", "text": "your words. your healing. your proof."}}
], "caption": "A caption celebrating these women and their healing journeys. Angela's voice. End with 5 hashtags."}}"""

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post("https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": "claude-sonnet-4-20250514", "max_tokens": 2000,
                    "system": "You are Angela Schellenberg, licensed trauma therapist. Create social proof content. Return only valid JSON.",
                    "messages": [{"role": "user", "content": prompt}]})
            resp.raise_for_status()
            text = "".join(b["text"] for b in resp.json().get("content", []) if b.get("type") == "text").strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            result = json.loads(text)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    # Mark testimonials as used
    for t in approved:
        execute("UPDATE testimonials SET is_used = TRUE WHERE id = %s", (t["id"],))

    return JSONResponse(result)
