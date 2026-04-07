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

# ───────────────── DB HELPERS ─────────────────
def get_conn():
    if not DATABASE_URL:
        raise Exception("DATABASE_URL not configured")
    return psycopg2.connect(DATABASE_URL)

def clean(row):
    if not row:
        return None
    d = dict(row)
    for k, v in d.items():
        if hasattr(v, "isoformat"):
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


# ───────────────── FIXED SCHEMA ─────────────────
SCHEMA_SQL = """

CREATE TABLE IF NOT EXISTS manychat_leads_clean (
  id BIGSERIAL PRIMARY KEY,
  contact_id TEXT NOT NULL,
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

-- Ensure columns exist
ALTER TABLE manychat_leads_clean ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT now();
ALTER TABLE manychat_leads_clean ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();

-- Remove duplicates BEFORE adding unique constraint
DELETE FROM manychat_leads_clean a
USING manychat_leads_clean b
WHERE a.id < b.id
AND a.contact_id = b.contact_id;

-- Add UNIQUE constraint safely
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'manychat_leads_clean_contact_id_key'
    ) THEN
        ALTER TABLE manychat_leads_clean
        ADD CONSTRAINT manychat_leads_clean_contact_id_key UNIQUE (contact_id);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS manychat_triggers (
  id BIGSERIAL PRIMARY KEY,
  keyword TEXT NOT NULL UNIQUE,
  label TEXT NOT NULL DEFAULT '',
  description TEXT DEFAULT '',
  is_active BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sync_log (
  id BIGSERIAL PRIMARY KEY,
  sync_type TEXT DEFAULT '',
  status TEXT DEFAULT 'pending',
  records_synced INT DEFAULT 0,
  error_message TEXT DEFAULT '',
  started_at TIMESTAMPTZ DEFAULT now(),
  completed_at TIMESTAMPTZ
);

-- Seed default triggers if table is empty
INSERT INTO manychat_triggers (keyword, label, description) VALUES
  ('BOOK', '1:1 Therapy Session', 'the link to book a 1:1 therapy session'),
  ('15MIN', 'Free 15-min Intro Call', 'the link to book a free 15-minute intro call'),
  ('HORSE', 'Equine Therapy', 'info about Equine Therapy in LA'),
  ('EMDR', 'EMDR Therapy Booking', 'the link to book an EMDR therapy session'),
  ('UNLEARN', 'Mother Hunger Course', 'info about the 8-week live Mother Hunger Course'),
  ('WORTHY', 'Free Emotional Starter Kit', 'my free Emotional Starter Kit'),
  ('TOOLS101', '101 Tools Digital Product', 'my 101 Tools digital product'),
  ('CIRCLES', 'Free GT&YM Community', 'the link to join the free Grief, Trauma and Your Mama community'),
  ('COMMUNITYCALL', 'Hope Edelman Thursday Group', 'the link to the Hope Edelman Thursday Group community call'),
  ('MALIBU', 'Healing with Horses Retreat', 'details about the Healing with Horses Retreat in Malibu'),
  ('TAPPERS', 'Dharma Dr. Bilateral Tappers', 'info about the Dharma Dr. Bilateral Tappers'),
  ('HELPTA', 'Trauma Tools Affiliate', 'affiliate links for trauma tools')
ON CONFLICT (keyword) DO UPDATE SET
  label = EXCLUDED.label,
  description = EXCLUDED.description,
  updated_at = now();

-- Remove old triggers that are no longer in ManyChat
DELETE FROM manychat_triggers WHERE keyword IN (
  'HEAL', 'MALIBURETREAT', 'GRIEFRELIEF', 'TOOLS', 'EQUINE',
  'MOM', 'UNTANGLE', 'STEADY', 'HORSEHEALING', 'GRIEFTOOLS'
);

"""

try:
    if DATABASE_URL:
        execute(SCHEMA_SQL)
except Exception as e:
    print("[SCHEMA ERROR]", e)


# ───────────────── HELPERS ─────────────────
def clean_template_vars(text):
    if not text:
        return ""
    return re.sub(r'\{\{[^}]+\}\}', '', text).strip()

def normalize_keyword(keyword):
    if not keyword:
        return ""
    return re.sub(r'\s+', '', keyword.upper())


# ───────────────── WEBHOOK ─────────────────
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
            return JSONResponse({"error": "No subscriber ID"}, status_code=400)

        keyword = normalize_keyword(
            body.get("keyword") or body.get("trigger") or ""
        )

        first_name = clean_template_vars(body.get("first_name", ""))
        last_name = clean_template_vars(body.get("last_name", ""))
        email = body.get("email", "")
        ig_username = body.get("ig_username", "")

        grief_type = body.get("grief_type", "")
        user_location_state = body.get("user_location_state", "")
        audience_segment = body.get("audience_segment", "")

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
            ig_username, email,
            grief_type, user_location_state, audience_segment
        ))

        return {"success": True, "contact_id": contact_id}

    except Exception as e:
        print("[WEBHOOK ERROR]", e)
        return JSONResponse({"error": str(e)}, status_code=500)


# ───────────────── SYNC (FIXED) ─────────────────
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
        return JSONResponse({"error": str(e)}, status_code=500)


# ───────────────── HEALTH ─────────────────
@router.get("/api/manychat/webhook")
async def webhook_status():
    return {"status": "ok", "message": "Webhook working"}


# ───────────────── TRIGGERS CRUD ─────────────────

@router.get("/api/manychat/triggers")
async def list_triggers():
    rows = query("SELECT * FROM manychat_triggers ORDER BY keyword ASC")
    return {"triggers": rows}


@router.post("/api/manychat/triggers")
async def create_trigger(request: Request):
    try:
        body = await request.json()
        keyword = (body.get("keyword") or "").strip().upper()
        label = (body.get("label") or "").strip()
        description = (body.get("description") or "").strip()

        if not keyword:
            return JSONResponse({"error": "Keyword is required"}, status_code=400)

        row = insert_returning(
            """INSERT INTO manychat_triggers (keyword, label, description)
               VALUES (%s, %s, %s)
               ON CONFLICT (keyword) DO UPDATE SET
                 label = EXCLUDED.label,
                 description = EXCLUDED.description,
                 updated_at = now()
               RETURNING *""",
            (keyword, label, description)
        )
        return {"success": True, "trigger": row}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.put("/api/manychat/triggers/{trigger_id}")
async def update_trigger(trigger_id: int, request: Request):
    try:
        body = await request.json()
        label = (body.get("label") or "").strip()
        description = (body.get("description") or "").strip()

        execute(
            """UPDATE manychat_triggers
               SET label = %s, description = %s, updated_at = now()
               WHERE id = %s""",
            (label, description, trigger_id)
        )
        return {"success": True}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.patch("/api/manychat/triggers/{trigger_id}/toggle")
async def toggle_trigger(trigger_id: int):
    try:
        execute(
            "UPDATE manychat_triggers SET is_active = NOT is_active, updated_at = now() WHERE id = %s",
            (trigger_id,)
        )
        return {"success": True}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.delete("/api/manychat/triggers/{trigger_id}")
async def delete_trigger(trigger_id: int):
    try:
        execute("DELETE FROM manychat_triggers WHERE id = %s", (trigger_id,))
        return {"success": True}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ───────────────── SYNC STATUS ─────────────────

@router.get("/api/manychat/sync/status")
async def sync_status():
    try:
        rows = query(
            "SELECT * FROM sync_log ORDER BY started_at DESC LIMIT 1"
        )
        return {"last_sync": rows[0] if rows else None}
    except Exception:
        return {"last_sync": None}


# ───────────────── SUBSCRIBERS ─────────────────

@router.get("/api/manychat/subscribers")
async def list_subscribers(request: Request):
    try:
        params = request.query_params
        page = int(params.get("page", 1))
        limit = min(int(params.get("limit", 30)), 100)
        search = params.get("search", "")
        interest = params.get("interest", "")
        sort = params.get("sort", "recent")
        offset = (page - 1) * limit

        where_clauses = []
        where_params = []

        if search:
            where_clauses.append(
                "(first_name ILIKE %s OR last_name ILIKE %s OR ig_username ILIKE %s OR email ILIKE %s)"
            )
            s = f"%{search}%"
            where_params.extend([s, s, s, s])

        if interest:
            where_clauses.append("audience_segment = %s")
            where_params.append(interest)

        where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        order_map = {
            "recent": "updated_at DESC",
            "heat_score": "updated_at DESC",
            "triggers": "updated_at DESC",
            "subscribed": "created_at DESC",
        }
        order = order_map.get(sort, "updated_at DESC")

        count_rows = query(f"SELECT COUNT(*) as cnt FROM manychat_leads_clean{where_sql}", where_params)
        total = count_rows[0]["cnt"] if count_rows else 0

        rows = query(
            f"""SELECT contact_id as mc_id, first_name, last_name,
                       CONCAT(first_name, ' ', last_name) as full_name,
                       ig_username, email, grief_type, audience_segment as interest_level,
                       0 as heat_score, 0 as trigger_count, 0 as conversation_count,
                       updated_at as last_interaction, created_at as subscribed_at
                FROM manychat_leads_clean{where_sql}
                ORDER BY {order} LIMIT %s OFFSET %s""",
            where_params + [limit, offset]
        )
        pages = max(1, -(-total // limit))
        return {"subscribers": rows, "total": total, "page": page, "pages": pages}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/manychat/subscribers/stats")
async def subscriber_stats():
    try:
        count_rows = query("SELECT COUNT(*) as total FROM manychat_leads_clean")
        total = count_rows[0]["total"] if count_rows else 0

        segment_rows = query(
            """SELECT COALESCE(audience_segment, 'new') as segment, COUNT(*) as cnt
               FROM manychat_leads_clean GROUP BY audience_segment"""
        )
        by_interest = {}
        for r in segment_rows:
            seg = r["segment"] or "new"
            by_interest[seg] = r["cnt"]

        return {"total": total, "by_interest": by_interest}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/manychat/subscribers/{mc_id}")
async def get_subscriber(mc_id: str):
    try:
        rows = query(
            """SELECT contact_id as mc_id, first_name, last_name,
                      CONCAT(first_name, ' ', last_name) as full_name,
                      ig_username, email, grief_type,
                      audience_segment as interest_level,
                      0 as heat_score, 0 as trigger_count, 0 as conversation_count,
                      updated_at as last_interaction, created_at as subscribed_at,
                      '[]'::text as tags
               FROM manychat_leads_clean WHERE contact_id = %s LIMIT 1""",
            (mc_id,)
        )
        if not rows:
            return JSONResponse({"error": "Not found"}, status_code=404)
        return {"subscriber": rows[0]}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ───────────────── SUBSCRIBER LOOKUP ─────────────────

@router.post("/api/manychat/lookup")
async def lookup_subscriber(request: Request):
    try:
        body = await request.json()
        contact_id = (body.get("contact_id") or "").strip()
        email = (body.get("email") or "").strip()
        phone = (body.get("phone") or "").strip()

        if contact_id:
            rows = query(
                "SELECT * FROM manychat_leads_clean WHERE contact_id = %s LIMIT 1",
                (contact_id,)
            )
        elif email:
            rows = query(
                "SELECT * FROM manychat_leads_clean WHERE email ILIKE %s LIMIT 1",
                (email,)
            )
        else:
            return JSONResponse({"error": "Provide contact_id or email"}, status_code=400)

        if rows:
            return {"success": True, "subscriber": rows[0], "source": "database"}
        return {"success": True, "subscriber": None, "message": "Not found in local database"}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
