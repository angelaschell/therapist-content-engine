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
import psycopg2.errors
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



CREATE TABLE IF NOT EXISTS sync_log (
  id BIGSERIAL PRIMARY KEY,
  sync_type TEXT DEFAULT '',
  status TEXT DEFAULT 'pending',
  records_synced INT DEFAULT 0,
  error_message TEXT DEFAULT '',
  started_at TIMESTAMPTZ DEFAULT now(),
  completed_at TIMESTAMPTZ
);


CREATE TABLE IF NOT EXISTS manychat_triggers (
  id BIGSERIAL PRIMARY KEY,
  keyword TEXT NOT NULL UNIQUE,
  label TEXT NOT NULL DEFAULT '',
  description TEXT DEFAULT '',
  is_active BOOLEAN DEFAULT true,
  created_at TIMESTAMPTZ DEFAULT now()
);

"""

SEED_TRIGGERS = [
    ("WORTHY", "Emotional Starter Kit", "my free Emotional Starter Kit"),
    ("HEAL", "1:1 Therapy Session", "the link to book a 1:1 therapy session"),
    ("MALIBURETREAT", "Healing with Horses Retreat", "details about the Healing with Horses Somatic Grief Retreat in Malibu"),
    ("MALIBU RETREAT", "Healing with Horses Retreat", "details about the Healing with Horses Somatic Grief Retreat in Malibu"),
    ("UNLEARN", "Mother Hunger Course", "info about the Mother Hunger Course"),
    ("GRIEFRELIEF", "Grief Relief Video Series", "the Grief Relief Video Series"),
    ("TOOLS", "101 Tools Resource", "my 101 Tools resource"),
    ("EQUINE", "Equine Therapy Guide", "my Equine Therapy digital guide"),
    ("MOM", "Grief, Trauma and Your Mama", "the link to join the Grief, Trauma and Your Mama community"),
    ("EMDR", "EMDR Therapy Sessions", "info about EMDR therapy sessions"),
    ("UNTANGLE", "1:1 Session", "the link to book a 1:1 session"),
    ("STEADY", "1:1 Session", "the link to book a 1:1 session"),
    ("COMMUNITYCALL", "Motherless Daughters Thursday Group", "the link to the Motherless Daughters Thursday group"),
    ("TAPPERS", "Dharma Dr. Resource", "info about the Dharma Dr. resource"),
    ("HORSEHEALING", "Equine Therapy Guide", "my Equine Therapy digital guide"),
    ("GRIEFTOOLS", "Grief Relief Video Series", "the Grief Relief Video Series"),
]

try:
    if DATABASE_URL:
        execute(SCHEMA_SQL)
        # Seed default triggers if table is empty
        rows = query("SELECT COUNT(*) AS cnt FROM manychat_triggers")
        if rows and rows[0]["cnt"] == 0:
            for kw, lbl, desc in SEED_TRIGGERS:
                execute(
                    "INSERT INTO manychat_triggers (keyword, label, description) VALUES (%s, %s, %s) ON CONFLICT (keyword) DO NOTHING",
                    (kw, lbl, desc)
                )
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


# ───────────────── TRIGGERS CRUD ─────────────────
@router.get("/api/manychat/triggers")
async def list_triggers():
    try:
        rows = query("SELECT * FROM manychat_triggers ORDER BY keyword")
        return {"triggers": rows}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/manychat/triggers")
async def create_trigger(request: Request):
    try:
        body = await request.json()
        keyword = (body.get("keyword") or "").strip().upper()
        label = (body.get("label") or "").strip()
        description = (body.get("description") or "").strip()
        if not keyword or not label:
            return JSONResponse({"error": "Keyword and label are required"}, status_code=400)
        row = insert_returning(
            "INSERT INTO manychat_triggers (keyword, label, description) VALUES (%s, %s, %s) RETURNING *",
            (keyword, label, description)
        )
        return {"trigger": row}
    except psycopg2.errors.UniqueViolation:
        return JSONResponse({"error": "A trigger with that keyword already exists"}, status_code=409)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.put("/api/manychat/triggers/{trigger_id}")
async def update_trigger(trigger_id: int, request: Request):
    try:
        body = await request.json()
        label = (body.get("label") or "").strip()
        description = (body.get("description") or "").strip()
        if not label:
            return JSONResponse({"error": "Label is required"}, status_code=400)
        execute(
            "UPDATE manychat_triggers SET label=%s, description=%s WHERE id=%s",
            (label, description, trigger_id)
        )
        return {"success": True}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.patch("/api/manychat/triggers/{trigger_id}/toggle")
async def toggle_trigger(trigger_id: int):
    try:
        execute(
            "UPDATE manychat_triggers SET is_active = NOT is_active WHERE id=%s",
            (trigger_id,)
        )
        return {"success": True}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.delete("/api/manychat/triggers/{trigger_id}")
async def delete_trigger(trigger_id: int):
    try:
        execute("DELETE FROM manychat_triggers WHERE id=%s", (trigger_id,))
        return {"success": True}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


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


# ───────────────── SYNC ─────────────────
@router.post("/api/manychat/sync")
async def sync_data():
    if not MC_KEY:
        return JSONResponse({"error": "MANYCHAT_API_KEY not configured"}, status_code=500)

    log_id = None
    try:
        log = insert_returning(
            "INSERT INTO sync_log (sync_type,status) VALUES ('subscribers','running') RETURNING *"
        )
        log_id = log["id"] if log else None

        headers = {"Authorization": f"Bearer {MC_KEY}", "Accept": "application/json"}
        flow_count = 0
        tag_count = 0
        sub_count = 0

        async with httpx.AsyncClient(timeout=30) as c:
            # Pull flows
            try:
                r = await c.get(f"{MC_API}/fb/page/getFlows", headers=headers)
                if r.status_code == 200:
                    flow_count = len(r.json().get("data", {}).get("flows", []))
            except Exception as e:
                print(f"[SYNC] Flows error: {e}")

            # Pull tags
            try:
                r = await c.get(f"{MC_API}/fb/page/getTags", headers=headers)
                if r.status_code == 200:
                    tag_count = len(r.json().get("data", []))
            except Exception as e:
                print(f"[SYNC] Tags error: {e}")

            # Pull subscribers (paginated)
            try:
                page = 1
                while page <= 20:  # Safety limit
                    r = await c.get(
                        f"{MC_API}/fb/subscriber/getSubscribers",
                        headers=headers,
                        params={"page": page, "limit": 100}
                    )
                    if r.status_code != 200:
                        break
                    data = r.json().get("data", {})
                    subs = data.get("data", [])
                    if not subs:
                        break

                    for sub in subs:
                        contact_id = str(sub.get("id", "")).strip()
                        if not contact_id:
                            continue
                        first_name = (sub.get("first_name") or "").strip()
                        last_name = (sub.get("last_name") or "").strip()
                        ig_username = (sub.get("ig_username") or "").strip()
                        email = (sub.get("email") or "").strip()

                        try:
                            execute("""
                                INSERT INTO manychat_leads_clean
                                (contact_id, first_name, last_name, ig_username, email, updated_at)
                                VALUES (%s,%s,%s,%s,%s,now())
                                ON CONFLICT (contact_id) DO UPDATE SET
                                    first_name = COALESCE(NULLIF(EXCLUDED.first_name,''), manychat_leads_clean.first_name),
                                    last_name = COALESCE(NULLIF(EXCLUDED.last_name,''), manychat_leads_clean.last_name),
                                    ig_username = COALESCE(NULLIF(EXCLUDED.ig_username,''), manychat_leads_clean.ig_username),
                                    email = COALESCE(NULLIF(EXCLUDED.email,''), manychat_leads_clean.email),
                                    updated_at = now()
                            """, (contact_id, first_name, last_name, ig_username, email))
                            sub_count += 1
                        except Exception as e:
                            print(f"[SYNC] Sub insert error: {e}")

                    # Check for next page
                    if not data.get("next_page_url") and page >= data.get("last_page", page):
                        break
                    page += 1
            except Exception as e:
                print(f"[SYNC] Subscribers error: {e}")

        total = flow_count + tag_count + sub_count
        if log_id:
            execute(
                "UPDATE sync_log SET records_synced=%s, status='success', completed_at=now(), error_message=%s WHERE id=%s",
                (total, json.dumps({"flows": flow_count, "tags": tag_count, "subscribers": sub_count}), log_id)
            )

        return {"success": True, "synced": total, "flows": flow_count, "tags": tag_count, "subscribers": sub_count}

    except Exception as e:
        if log_id:
            execute(
                "UPDATE sync_log SET status='error', error_message=%s WHERE id=%s",
                (str(e), log_id)
            )
        return JSONResponse({"error": str(e)}, status_code=500)


# ───────────────── SYNC STATUS ─────────────────
@router.get("/api/manychat/sync/status")
async def sync_status():
    try:
        rows = query("SELECT * FROM sync_log WHERE sync_type='subscribers' ORDER BY id DESC LIMIT 1")
        if rows:
            return {"last_sync": rows[0]}
        return {"last_sync": None}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ───────────────── HEALTH ─────────────────
@router.get("/api/manychat/webhook")
async def webhook_status():
    return {"status": "ok", "message": "Webhook working"}
