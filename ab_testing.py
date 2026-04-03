# A/B Caption Testing - Generate multiple caption variants and track performance
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
CREATE TABLE IF NOT EXISTS ab_tests (
    id BIGSERIAL PRIMARY KEY,
    topic TEXT NOT NULL,
    slide_count INT DEFAULT 10,
    template TEXT DEFAULT 'naming',
    trigger_keyword TEXT DEFAULT '',
    status TEXT DEFAULT 'active',
    winner_variant TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS ab_variants (
    id BIGSERIAL PRIMARY KEY,
    test_id BIGINT NOT NULL REFERENCES ab_tests(id) ON DELETE CASCADE,
    variant_label TEXT NOT NULL,
    mode TEXT NOT NULL,
    caption TEXT NOT NULL,
    ig_media_id TEXT,
    likes INT DEFAULT 0,
    comments INT DEFAULT 0,
    shares INT DEFAULT 0,
    saves INT DEFAULT 0,
    reach INT DEFAULT 0,
    is_winner BOOLEAN DEFAULT FALSE,
    published_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ab_variants_test ON ab_variants(test_id);
"""

try:
    execute(SCHEMA_SQL)
except Exception:
    pass


MODES = {
    "emotional_recognition": {
        "name": "Emotional Recognition",
        "instruction": "Write in MODE 1: EMOTIONAL RECOGNITION. 'You' language. Name an experience the reader felt but never had words for. Cumulative weight. End with a reframe. Zero advice. The reader should feel SEEN.",
    },
    "authority_redefine": {
        "name": "Authority Redefine",
        "instruction": "Write in MODE 2: AUTHORITY REDEFINE. Take something the reader thinks she understands and correct it. Clinical knowledge delivered as poetry. Every line a standalone screenshot. Authority close.",
    },
    "tribal_identity": {
        "name": "Tribal Identity",
        "instruction": "Write in MODE 3: TRIBAL IDENTITY. 'We know' language. Repeated anchor phrase. Escalating intimacy. Specific lived experiences. One slide reframes something praised as something forced.",
    },
}


@router.post("/api/ab/generate")
async def generate_ab_variants(req: Request):
    """Generate 3 caption variants (one per viral mode) for A/B testing."""
    data = await req.json()
    topic = data.get("topic", "")
    slides_text = data.get("slides_text", "")
    trigger_keyword = data.get("trigger_keyword", "")
    template = data.get("template", "naming")

    if not topic:
        return JSONResponse({"error": "Topic required"}, status_code=400)

    # Create the test
    test = insert_returning(
        "INSERT INTO ab_tests (topic, template, trigger_keyword) VALUES (%s, %s, %s) RETURNING *",
        (topic, template, trigger_keyword)
    )

    cta_line = ""
    if trigger_keyword:
        cta_line = f"\n- End the caption with: Comment {trigger_keyword} and I'll send you the link."

    variants = []
    for mode_key, mode in MODES.items():
        prompt = f"""Write an Instagram caption for this carousel topic: {topic}

{mode['instruction']}

Carousel slides summary: {slides_text[:1000]}

CAPTION RULES:
- Write in Angela Schellenberg's voice. Short punchy lines. Line breaks between thoughts.
- No em dashes. No "you're not broken." No outcome promises.
- Include exactly 5 relevant hashtags at the end.{cta_line}
- Return ONLY the caption text, nothing else."""

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post("https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                    json={
                        "model": "claude-sonnet-4-20250514",
                        "max_tokens": 1500,
                        "system": "You are Angela Schellenberg, a licensed grief and trauma therapist with 171K Instagram followers. Write exactly as Angela would. Short punchy lines. Show don't tell. Trust the reader.",
                        "messages": [{"role": "user", "content": prompt}]
                    })
                resp.raise_for_status()
                ai_data = resp.json()
                caption = "".join(b["text"] for b in ai_data.get("content", []) if b.get("type") == "text").strip()
        except Exception as e:
            caption = f"[Generation failed: {str(e)[:100]}]"

        variant = insert_returning(
            "INSERT INTO ab_variants (test_id, variant_label, mode, caption) VALUES (%s, %s, %s, %s) RETURNING *",
            (test["id"], mode["name"], mode_key, caption)
        )
        variants.append(variant)

    return JSONResponse({"test": test, "variants": variants})


@router.get("/api/ab/tests")
async def list_tests(req: Request):
    """List all A/B tests with their variants."""
    tests = query("SELECT * FROM ab_tests ORDER BY created_at DESC LIMIT 50")
    for t in tests:
        t["variants"] = query(
            "SELECT * FROM ab_variants WHERE test_id = %s ORDER BY variant_label",
            (t["id"],)
        )
    return JSONResponse({"tests": tests})


@router.get("/api/ab/tests/{test_id}")
async def get_test(test_id: int):
    tests = query("SELECT * FROM ab_tests WHERE id = %s", (test_id,))
    if not tests:
        return JSONResponse({"error": "Not found"}, status_code=404)
    test = tests[0]
    test["variants"] = query("SELECT * FROM ab_variants WHERE test_id = %s ORDER BY variant_label", (test_id,))
    return JSONResponse(test)


@router.patch("/api/ab/variants/{variant_id}/metrics")
async def update_variant_metrics(variant_id: int, req: Request):
    """Update engagement metrics for a variant after publishing."""
    data = await req.json()
    sets = []
    params = []
    for field in ["likes", "comments", "shares", "saves", "reach", "ig_media_id"]:
        if field in data:
            sets.append(f"{field} = %s")
            params.append(data[field])
    if "published" in data and data["published"]:
        sets.append("published_at = NOW()")
    if not sets:
        return JSONResponse({"error": "No fields to update"}, status_code=400)
    params.append(variant_id)
    row = insert_returning(f"UPDATE ab_variants SET {', '.join(sets)} WHERE id = %s RETURNING *", params)
    return JSONResponse(row or {"error": "Not found"})


@router.post("/api/ab/tests/{test_id}/pick-winner")
async def pick_winner(test_id: int, req: Request):
    """Mark a variant as the winner."""
    data = await req.json()
    variant_id = data.get("variant_id")
    if not variant_id:
        return JSONResponse({"error": "variant_id required"}, status_code=400)

    # Reset all variants
    execute("UPDATE ab_variants SET is_winner = FALSE WHERE test_id = %s", (test_id,))
    # Set winner
    row = insert_returning(
        "UPDATE ab_variants SET is_winner = TRUE WHERE id = %s AND test_id = %s RETURNING *",
        (variant_id, test_id)
    )
    if row:
        execute("UPDATE ab_tests SET winner_variant = %s, status = 'completed' WHERE id = %s",
                (row["variant_label"], test_id))
    return JSONResponse({"success": True, "winner": row})
