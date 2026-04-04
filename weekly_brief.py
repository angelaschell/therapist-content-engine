# Weekly Strategy Brief - AI content strategist that analyzes data and generates weekly plans
import os
import json
import psycopg2
import psycopg2.extras
import httpx
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()
DATABASE_URL = os.environ.get("DATABASE_URL", "")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
SCHEDULE_FILE = "/tmp/ig_scheduled_posts.json"


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


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS weekly_briefs (
    id BIGSERIAL PRIMARY KEY,
    week_of DATE NOT NULL,
    brief_text TEXT NOT NULL,
    data_snapshot JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
"""

try:
    if DATABASE_URL:
        conn = get_conn()
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(SCHEMA_SQL)
        cur.close()
        conn.close()
except Exception as e:
    print(f"[weekly_brief] Schema setup: {e}")


def gather_data():
    """Collect analytics, trends, calendar, and CRM data for the brief."""
    now = datetime.now(timezone.utc)
    week_ago = (now - timedelta(days=7)).isoformat()

    data = {}

    # Recent carousel performance
    try:
        data["recent_carousels"] = query(
            "SELECT topic, template, trigger_keyword, created_at FROM saved_carousels ORDER BY created_at DESC LIMIT 10"
        )
    except Exception:
        data["recent_carousels"] = []

    # Niche trends
    try:
        data["niche_trends"] = query(
            "SELECT topic, frequency, avg_engagement FROM niche_trends ORDER BY detected_at DESC LIMIT 10"
        )
    except Exception:
        data["niche_trends"] = []

    # Calendar events this week and next
    try:
        start = now.strftime("%Y-%m-%d")
        end = (now + timedelta(days=14)).strftime("%Y-%m-%d")
        data["calendar_events"] = query(
            "SELECT title, event_type, event_date, status FROM calendar_events WHERE event_date >= %s AND event_date <= %s ORDER BY event_date",
            (start, end)
        )
    except Exception:
        data["calendar_events"] = []

    # Hot leads
    try:
        data["hot_leads"] = query(
            "SELECT full_name, ig_username, heat_score, funnel_stage, trigger_count FROM manychat_subscribers WHERE heat_score >= 50 AND do_not_contact = false ORDER BY heat_score DESC LIMIT 10"
        )
    except Exception:
        data["hot_leads"] = []

    # Top comment categories this week
    try:
        data["comment_categories"] = query(
            "SELECT category, COUNT(*) as count FROM ig_comments WHERE timestamp >= %s AND category IS NOT NULL GROUP BY category ORDER BY count DESC",
            (week_ago,)
        )
    except Exception:
        data["comment_categories"] = []

    # A/B test results
    try:
        data["ab_tests"] = query(
            "SELECT t.topic, t.winner_variant, v.variant_label, v.mode FROM ab_tests t LEFT JOIN ab_variants v ON v.test_id = t.id AND v.is_winner = true WHERE t.status = 'completed' ORDER BY t.created_at DESC LIMIT 5"
        )
    except Exception:
        data["ab_tests"] = []

    # Scheduled posts
    try:
        if os.path.exists(SCHEDULE_FILE):
            with open(SCHEDULE_FILE, 'r') as f:
                scheduled = json.load(f)
            data["scheduled_posts"] = [p for p in scheduled if p.get("status") == "scheduled"]
        else:
            data["scheduled_posts"] = []
    except Exception:
        data["scheduled_posts"] = []

    return data


@router.post("/api/brief/generate")
async def generate_brief(req: Request):
    """Generate a weekly strategy brief using all available data."""
    data = gather_data()
    now = datetime.now(timezone.utc)

    prompt = f"""You are Angela Schellenberg's AI content strategist. Today is {now.strftime('%A, %B %d, %Y')}.
Angela is a licensed grief/trauma therapist in LA with 171K IG followers. Her Healing with Horses retreat is April 30 - May 3, 2026.

Here's this week's data:

RECENT CAROUSELS CREATED:
{json.dumps(data.get('recent_carousels', []), default=str, indent=2)}

TRENDING TOPICS IN THE NICHE:
{json.dumps(data.get('niche_trends', []), default=str, indent=2)}

CALENDAR (next 2 weeks):
{json.dumps(data.get('calendar_events', []), default=str, indent=2)}

HOT LEADS (heat score 50+):
{json.dumps(data.get('hot_leads', []), default=str, indent=2)}

COMMENT ACTIVITY THIS WEEK:
{json.dumps(data.get('comment_categories', []), default=str, indent=2)}

A/B TEST WINNERS:
{json.dumps(data.get('ab_tests', []), default=str, indent=2)}

SCHEDULED POSTS:
{json.dumps(data.get('scheduled_posts', [])[:5], default=str, indent=2)}

Generate a WEEKLY CONTENT STRATEGY BRIEF with these sections:

1. **THIS WEEK'S FOCUS** — One sentence theme for the week based on trends and what's working
2. **CAROUSEL IDEAS** (3-4 specific topics with recommended template type and viral mode)
   - Each should include: Topic, Template (naming/redefine/tribal/framework/pullquote/editorial/conversational), Recommended CTA trigger, Why this topic now
3. **REEL IDEAS** (2 quick concepts with hook lines)
4. **OPTIMAL POSTING SCHEDULE** — Best days/times based on what you know, aim for 4-5 posts this week
5. **LEAD NURTURE ACTIONS** — Top 3 people to DM this week and why (based on hot leads data)
6. **TRENDING OPPORTUNITY** — One niche trend Angela should jump on before it peaks
7. **WHAT'S WORKING** — Pattern from A/B tests or recent performance to double down on

Keep it concise and actionable. Angela should be able to scan this in 3 minutes and know exactly what to do. Use her voice — direct, warm, no fluff."""

    if not ANTHROPIC_KEY:
        return JSONResponse({"error": "ANTHROPIC_API_KEY not configured"}, status_code=500)
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post("https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 3000,
                    "messages": [{"role": "user", "content": prompt}]
                })
            resp.raise_for_status()
            text = "".join(b["text"] for b in resp.json().get("content", []) if b.get("type") == "text").strip()
    except Exception as e:
        return JSONResponse({"error": f"Generation failed: {str(e)[:200]}"}, status_code=500)

    # Save brief
    conn = get_conn()
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "INSERT INTO weekly_briefs (week_of, brief_text, data_snapshot) VALUES (%s, %s, %s) RETURNING *",
        (now.strftime("%Y-%m-%d"), text, json.dumps(data, default=str))
    )
    row = clean(cur.fetchone())
    cur.close()
    conn.close()

    return JSONResponse({"brief": text, "record": row})


@router.get("/api/brief/latest")
async def get_latest_brief():
    briefs = query("SELECT * FROM weekly_briefs ORDER BY created_at DESC LIMIT 1")
    if not briefs:
        return JSONResponse({"brief": None})
    return JSONResponse({"brief": briefs[0]})


@router.get("/api/brief/history")
async def brief_history():
    briefs = query("SELECT id, week_of, created_at FROM weekly_briefs ORDER BY created_at DESC LIMIT 20")
    return JSONResponse({"briefs": briefs})
