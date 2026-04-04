# Webhook Health Dashboard - Monitor ManyChat webhook events, success/failure rates
import os
import json
import psycopg2
import psycopg2.extras
from datetime import datetime, timedelta
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()
DATABASE_URL = os.environ.get("DATABASE_URL", "")


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
CREATE TABLE IF NOT EXISTS webhook_events (
    id BIGSERIAL PRIMARY KEY,
    event_type TEXT NOT NULL DEFAULT 'trigger',
    source TEXT DEFAULT 'manychat',
    mc_id TEXT DEFAULT '',
    keyword TEXT DEFAULT '',
    subscriber_name TEXT DEFAULT '',
    status TEXT DEFAULT 'success',
    error_message TEXT DEFAULT '',
    payload_preview TEXT DEFAULT '',
    processing_ms INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_webhook_events_created ON webhook_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_webhook_events_status ON webhook_events(status);
"""

try:
    if DATABASE_URL:
        execute(SCHEMA_SQL)
except Exception as e:
    print(f"[webhook_dashboard] Schema setup: {e}")


def log_webhook(event_type, source, mc_id="", keyword="", subscriber_name="",
                status="success", error_message="", payload_preview="", processing_ms=0):
    """Call this from other modules to log webhook events."""
    try:
        execute("""
            INSERT INTO webhook_events (event_type, source, mc_id, keyword, subscriber_name, status, error_message, payload_preview, processing_ms)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (event_type, source, mc_id, keyword, subscriber_name, status, error_message, payload_preview[:500], processing_ms))
    except Exception:
        pass


@router.get("/api/webhooks/dashboard")
async def webhook_dashboard():
    """Get webhook health overview."""
    now_str = datetime.utcnow().isoformat()

    # Last 24 hours stats
    stats_24h = query("""
        SELECT status, COUNT(*) as count
        FROM webhook_events
        WHERE created_at >= NOW() - INTERVAL '24 hours'
        GROUP BY status
    """)
    total_24h = sum(s["count"] for s in stats_24h)
    success_24h = next((s["count"] for s in stats_24h if s["status"] == "success"), 0)
    error_24h = next((s["count"] for s in stats_24h if s["status"] == "error"), 0)

    # Last 7 days daily breakdown
    daily = query("""
        SELECT DATE(created_at) as day, status, COUNT(*) as count
        FROM webhook_events
        WHERE created_at >= NOW() - INTERVAL '7 days'
        GROUP BY DATE(created_at), status
        ORDER BY day
    """)

    # Top triggers in last 7 days
    top_triggers = query("""
        SELECT keyword, COUNT(*) as count, COUNT(DISTINCT mc_id) as unique_users
        FROM webhook_events
        WHERE keyword != '' AND created_at >= NOW() - INTERVAL '7 days'
        GROUP BY keyword ORDER BY count DESC LIMIT 10
    """)

    # Recent events
    recent = query("SELECT * FROM webhook_events ORDER BY created_at DESC LIMIT 25")

    # Recent errors
    errors = query("SELECT * FROM webhook_events WHERE status = 'error' ORDER BY created_at DESC LIMIT 10")

    # Avg processing time
    avg_ms = query("SELECT AVG(processing_ms) as avg_ms FROM webhook_events WHERE created_at >= NOW() - INTERVAL '24 hours' AND processing_ms > 0")
    avg_processing = round(avg_ms[0]["avg_ms"] or 0, 1) if avg_ms else 0

    # Sync log from ManyChat
    try:
        syncs = query("SELECT * FROM sync_log ORDER BY started_at DESC LIMIT 5")
    except Exception:
        syncs = []

    return JSONResponse({
        "summary": {
            "total_24h": total_24h,
            "success_24h": success_24h,
            "error_24h": error_24h,
            "success_rate": round((success_24h / total_24h * 100), 1) if total_24h > 0 else 100,
            "avg_processing_ms": avg_processing,
        },
        "daily_breakdown": daily,
        "top_triggers": top_triggers,
        "recent_events": recent,
        "recent_errors": errors,
        "recent_syncs": syncs,
    })


@router.get("/api/webhooks/events")
async def list_events(req: Request):
    status = req.query_params.get("status", "")
    limit = min(int(req.query_params.get("limit", "50")), 200)

    where = ""
    params = []
    if status:
        where = "WHERE status = %s"
        params.append(status)
    params.append(limit)

    events = query(f"SELECT * FROM webhook_events {where} ORDER BY created_at DESC LIMIT %s", tuple(params))
    return JSONResponse({"events": events})
