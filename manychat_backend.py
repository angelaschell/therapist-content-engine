"""
Lead CRM Backend - uses DATABASE_URL (psycopg2) directly.
No Supabase REST client needed.

Add to main.py:
  from manychat_backend import router as manychat_router
  app.include_router(manychat_router)
"""

"""
Lead CRM Backend - uses DATABASE_URL (psycopg2) directly.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, HTMLResponse
import httpx
import os
import json
import re
import psycopg2
import psycopg2.extras
from datetime import datetime, timedelta, timezone

router = APIRouter()

DATABASE_URL = os.environ.get("DATABASE_URL", "")
MC_API = "https://api.manychat.com"
MC_KEY = os.environ.get("MANYCHAT_API_KEY", "")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# ── DB Helper ─────────────────────────────────────────────────
def get_conn():
    if not DATABASE_URL:
        raise Exception("DATABASE_URL not configured")
    return psycopg2.connect(DATABASE_URL)

def clean(row):
    if not row:
        return None
    d = dict(row)
    for k, v in d.items():
        if isinstance(v, (datetime,)):
            d[k] = v.isoformat()
        elif hasattr(v, 'isoformat'):
            d[k] = v.isoformat()
    return d

def query(sql, params=None, fetch=True):
    conn = get_conn()
    try:
        conn.autocommit = True
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params or ())
        result = [clean(r) for r in cur.fetchall()] if fetch else []
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

def query_one(sql, params=None):
    rows = query(sql, params)
    return rows[0] if rows else None

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


# ── FIXED SCHEMA ───────────────────────────────────────────────
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS manychat_leads_clean (
  id BIGSERIAL PRIMARY KEY,
  contact_id TEXT NOT NULL UNIQUE,
  keyword TEXT DEFAULT '',
  first_name TEXT DEFAULT '',
  last_name TEXT DEFAULT '',
  ig_username TEXT DEFAULT '',
  email TEXT DEFAULT '',
  grief_type TEXT DEFAULT '',
  user_location_state TEXT DEFAULT '',
  audience_segment TEXT DEFAULT '',
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- ✅ CRITICAL FIXES
ALTER TABLE manychat_leads_clean ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT now();
ALTER TABLE manychat_leads_clean ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();

ALTER TABLE manychat_leads_clean ADD COLUMN IF NOT EXISTS grief_type TEXT DEFAULT '';
ALTER TABLE manychat_leads_clean ADD COLUMN IF NOT EXISTS user_location_state TEXT DEFAULT '';
ALTER TABLE manychat_leads_clean ADD COLUMN IF NOT EXISTS audience_segment TEXT DEFAULT '';
ALTER TABLE manychat_leads_clean ADD COLUMN IF NOT EXISTS keyword TEXT DEFAULT '';
ALTER TABLE manychat_leads_clean ADD COLUMN IF NOT EXISTS ig_username TEXT DEFAULT '';
"""

try:
    if DATABASE_URL:
        execute(SCHEMA_SQL)
except Exception as e:
    print(f"[manychat] Schema setup warning: {e}")


# ── Helpers ────────────────────────────────────────────────────
def clean_template_vars(text):
    if not text:
        return ""
    return re.sub(r'\{\{[^}]+\}\}', '', text).strip()

def normalize_keyword(keyword):
    if not keyword:
        return ""
    return re.sub(r'\s+', '', keyword.upper())


# ================================================================
# WEBHOOK (FIXED)
# ================================================================
@router.post("/api/manychat/webhook")
async def manychat_webhook(request: Request):
    try:
        body = await request.json()

        contact_id = str(
            body.get("contact_id")
            or body.get("id")
            or body.get("subscriber_id")
            or ""
        ).strip()

        if not contact_id:
            return JSONResponse(
                content={"error": "No subscriber ID provided"},
                status_code=400
            )

        keyword = normalize_keyword(
            body.get("keyword")
            or body.get("trigger")
            or ""
        )

        first_name = clean_template_vars(body.get("first_name", ""))
        last_name = clean_template_vars(body.get("last_name", ""))
        email = body.get("email", "")
        ig_username = body.get("ig_username", "")

        grief_type = body.get("grief_type", "")
        user_location_state = body.get("user_location_state", "")
        audience_segment = body.get("audience_segment", "")

        # ✅ SAFE INSERT
        execute("""
            INSERT INTO manychat_leads_clean
            (contact_id, keyword, first_name, last_name, ig_username, email,
             grief_type, user_location_state, audience_segment, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
            ON CONFLICT (contact_id) DO UPDATE SET
                keyword = COALESCE(NULLIF(EXCLUDED.keyword,''), manychat_leads_clean.keyword),
                first_name = COALESCE(NULLIF(EXCLUDED.first_name,''), manychat_leads_clean.first_name),
                last_name = COALESCE(NULLIF(EXCLUDED.last_name,''), manychat_leads_clean.last_name),
                ig_username = COALESCE(NULLIF(EXCLUDED.ig_username,''), manychat_leads_clean.ig_username),
                email = COALESCE(NULLIF(EXCLUDED.email,''), manychat_leads_clean.email),
                grief_type = COALESCE(NULLIF(EXCLUDED.grief_type,''), manychat_leads_clean.grief_type),
                user_location_state = COALESCE(NULLIF(EXCLUDED.user_location_state,''), manychat_leads_clean.user_location_state),
                audience_segment = COALESCE(NULLIF(EXCLUDED.audience_segment,''), manychat_leads_clean.audience_segment),
                updated_at = now()
        """, (
            contact_id, keyword, first_name, last_name,
            ig_username, email, grief_type,
            user_location_state, audience_segment
        ))

        return JSONResponse(content={
            "success": True,
            "contact_id": contact_id,
            "keyword": keyword
        })

    except Exception as e:
        print("[manychat webhook error]", e)
        return JSONResponse(content={"error": str(e)}, status_code=500)


# ================================================================
# SYNC (FIXED log_id bug)
# ================================================================
@router.post("/api/manychat/sync")
async def sync_data():
    log_id = None
    try:
        log = insert_returning(
            "INSERT INTO sync_log (sync_type,status) VALUES ('subscribers','running') RETURNING *"
        )
        log_id = log["id"] if log else None

        synced = 0

        async with httpx.AsyncClient() as c:
            r = await c.get(f"{MC_API}/fb/page/getFlows", headers={"Authorization": f"Bearer {MC_KEY}"})
            flows = r.json().get("data", {}).get("flows", [])
            synced += len(flows)

        if log_id:
            execute(
                "UPDATE sync_log SET records_synced=%s, status='success', completed_at=now() WHERE id=%s",
                (synced, log_id)
            )

        return {"success": True, "synced": synced}

    except Exception as e:
        if log_id:
            execute(
                "UPDATE sync_log SET status='error', error_message=%s WHERE id=%s",
                (str(e), log_id)
            )
        return JSONResponse(content={"error": str(e)}, status_code=500)


# ================================================================
# HEALTH CHECK
# ================================================================
@router.get("/api/manychat/webhook")
async def webhook_status():
    return {"status": "ok"}
