# Content Calendar Backend - Aggregates scheduled + published posts into a calendar view
import os
import json
import psycopg2
import psycopg2.extras
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()
DATABASE_URL = os.environ.get("DATABASE_URL", "")
SCHEDULE_FILE = "/tmp/ig_scheduled_posts.json"


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


# ── Schema ─────────────────────────────────────────────────────
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS calendar_events (
    id BIGSERIAL PRIMARY KEY,
    title TEXT NOT NULL DEFAULT 'Untitled',
    event_type TEXT NOT NULL DEFAULT 'post',
    event_date DATE NOT NULL,
    event_time TIME,
    post_type TEXT DEFAULT 'carousel',
    caption_preview TEXT DEFAULT '',
    template TEXT DEFAULT '',
    trigger_keyword TEXT DEFAULT '',
    status TEXT DEFAULT 'draft',
    carousel_id BIGINT,
    schedule_id TEXT,
    ig_media_id TEXT,
    notes TEXT DEFAULT '',
    color TEXT DEFAULT '#90A9EC',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_calendar_date ON calendar_events(event_date);
"""

try:
    conn = get_conn()
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(SCHEMA_SQL)
    cur.close()
    conn.close()
except Exception:
    pass


def load_schedule():
    try:
        if os.path.exists(SCHEDULE_FILE):
            with open(SCHEDULE_FILE, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return []


# ── Endpoints ─────────────────────────────────────────────────

@router.get("/api/calendar")
async def get_calendar(req: Request):
    """Get all calendar events for a date range."""
    start = req.query_params.get("start", "")
    end = req.query_params.get("end", "")

    if not start or not end:
        # Default to current month
        now = datetime.now(timezone.utc)
        start = now.replace(day=1).strftime("%Y-%m-%d")
        next_month = (now.replace(day=28) + timedelta(days=4)).replace(day=1)
        end = (next_month - timedelta(days=1)).strftime("%Y-%m-%d")

    # Get calendar events from DB
    events = query(
        "SELECT * FROM calendar_events WHERE event_date >= %s AND event_date <= %s ORDER BY event_date, event_time",
        (start, end)
    )

    # Merge in scheduled posts from the scheduler file
    scheduled = load_schedule()
    for post in scheduled:
        if post.get("status") != "scheduled":
            continue
        pub_at = post.get("publish_at", "")
        if not pub_at:
            continue
        try:
            pub_date = datetime.fromisoformat(pub_at.replace("Z", "+00:00"))
            date_str = pub_date.strftime("%Y-%m-%d")
            if start <= date_str <= end:
                # Check if already in calendar
                already = any(e.get("schedule_id") == post.get("id") for e in events)
                if not already:
                    events.append({
                        "id": None,
                        "title": (post.get("caption", "")[:50] + "...") if len(post.get("caption", "")) > 50 else post.get("caption", "Scheduled post"),
                        "event_type": "scheduled",
                        "event_date": date_str,
                        "event_time": pub_date.strftime("%H:%M"),
                        "post_type": post.get("post_type", "photo"),
                        "caption_preview": post.get("caption", "")[:200],
                        "status": "scheduled",
                        "schedule_id": post.get("id"),
                        "color": "#FFC696",
                    })
        except Exception:
            pass

    return JSONResponse({"events": events})


@router.post("/api/calendar")
async def create_event(req: Request):
    """Create a new calendar event (draft/idea/reminder)."""
    data = await req.json()
    conn = get_conn()
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        INSERT INTO calendar_events (title, event_type, event_date, event_time, post_type,
            caption_preview, template, trigger_keyword, status, notes, color)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *
    """, (
        data.get("title", "Untitled"),
        data.get("event_type", "post"),
        data.get("event_date"),
        data.get("event_time") or None,
        data.get("post_type", "carousel"),
        data.get("caption_preview", ""),
        data.get("template", ""),
        data.get("trigger_keyword", ""),
        data.get("status", "draft"),
        data.get("notes", ""),
        data.get("color", "#90A9EC"),
    ))
    row = clean(cur.fetchone())
    cur.close()
    conn.close()
    return JSONResponse(row)


@router.patch("/api/calendar/{event_id}")
async def update_event(event_id: int, req: Request):
    """Update a calendar event."""
    data = await req.json()
    sets = []
    params = []
    for field in ["title", "event_type", "event_date", "event_time", "post_type",
                   "caption_preview", "template", "trigger_keyword", "status", "notes", "color"]:
        if field in data:
            sets.append(f"{field} = %s")
            params.append(data[field] or None)
    if not sets:
        return JSONResponse({"error": "No fields to update"}, status_code=400)
    params.append(event_id)
    conn = get_conn()
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(f"UPDATE calendar_events SET {', '.join(sets)} WHERE id = %s RETURNING *", params)
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return JSONResponse(clean(row))


@router.delete("/api/calendar/{event_id}")
async def delete_event(event_id: int):
    conn = get_conn()
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("DELETE FROM calendar_events WHERE id = %s", (event_id,))
    cur.close()
    conn.close()
    return JSONResponse({"ok": True})
