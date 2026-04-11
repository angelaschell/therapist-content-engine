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
MC_WEBHOOK_SECRET = os.environ.get("MANYCHAT_WEBHOOK_SECRET", "")

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


ALTER TABLE manychat_leads_clean ADD COLUMN IF NOT EXISTS heat_score INT DEFAULT 0;
ALTER TABLE manychat_leads_clean ADD COLUMN IF NOT EXISTS interest_level TEXT DEFAULT 'new';
ALTER TABLE manychat_leads_clean ADD COLUMN IF NOT EXISTS funnel_stage TEXT DEFAULT '';
ALTER TABLE manychat_leads_clean ADD COLUMN IF NOT EXISTS do_not_contact BOOLEAN DEFAULT false;
ALTER TABLE manychat_leads_clean ADD COLUMN IF NOT EXISTS trigger_count INT DEFAULT 0;
ALTER TABLE manychat_leads_clean ADD COLUMN IF NOT EXISTS conversation_count INT DEFAULT 0;
ALTER TABLE manychat_leads_clean ADD COLUMN IF NOT EXISTS phone TEXT DEFAULT '';
ALTER TABLE manychat_leads_clean ADD COLUMN IF NOT EXISTS last_interaction TIMESTAMPTZ;
ALTER TABLE manychat_leads_clean ADD COLUMN IF NOT EXISTS last_seen TIMESTAMPTZ;
ALTER TABLE manychat_leads_clean ADD COLUMN IF NOT EXISTS tags JSONB DEFAULT '[]';
ALTER TABLE manychat_leads_clean ADD COLUMN IF NOT EXISTS flodesk_synced BOOLEAN DEFAULT false;
ALTER TABLE manychat_leads_clean ADD COLUMN IF NOT EXISTS analysis JSONB DEFAULT '{}';

CREATE TABLE IF NOT EXISTS subscriber_notes (
  id BIGSERIAL PRIMARY KEY,
  contact_id TEXT NOT NULL,
  note TEXT DEFAULT '',
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS subscriber_recommendations (
  id BIGSERIAL PRIMARY KEY,
  contact_id TEXT NOT NULL,
  subscriber_name TEXT DEFAULT '',
  title TEXT DEFAULT '',
  priority INT DEFAULT 3,
  category TEXT DEFAULT '',
  description TEXT DEFAULT '',
  suggested_action TEXT DEFAULT '',
  suggested_flow TEXT DEFAULT '',
  data_points JSONB DEFAULT '{}',
  status TEXT DEFAULT 'pending',
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS action_log (
  id BIGSERIAL PRIMARY KEY,
  contact_id TEXT DEFAULT '',
  subscriber_name TEXT DEFAULT '',
  action_type TEXT DEFAULT '',
  action_detail TEXT DEFAULT '',
  created_at TIMESTAMPTZ DEFAULT now()
);


CREATE TABLE IF NOT EXISTS manychat_triggers (
  id BIGSERIAL PRIMARY KEY,
  keyword TEXT NOT NULL UNIQUE,
  label TEXT NOT NULL DEFAULT '',
  description TEXT DEFAULT '',
  is_active BOOLEAN DEFAULT true,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS manychat_flows (
  id BIGSERIAL PRIMARY KEY,
  namespace TEXT NOT NULL UNIQUE,
  name TEXT DEFAULT '',
  folder TEXT DEFAULT '',
  last_seen TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS manychat_tags (
  id BIGSERIAL PRIMARY KEY,
  tag_id TEXT NOT NULL UNIQUE,
  name TEXT DEFAULT '',
  last_seen TIMESTAMPTZ DEFAULT now()
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
def verify_manychat_secret(request: Request) -> bool:
    """Reject unauthenticated webhook traffic when MANYCHAT_WEBHOOK_SECRET is set.

    ManyChat's External Request node supports custom headers — configure it to send
    X-ManyChat-Secret: <value> matching the MANYCHAT_WEBHOOK_SECRET env var.
    If the env var is empty, verification is skipped (keeps local dev easy).
    """
    if not MC_WEBHOOK_SECRET:
        return True
    provided = (
        request.headers.get("x-manychat-secret")
        or request.headers.get("X-ManyChat-Secret")
        or ""
    )
    return provided == MC_WEBHOOK_SECRET


@router.post("/api/manychat/webhook")
async def manychat_webhook(request: Request):
    try:
        if not verify_manychat_secret(request):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

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
            # Pull flows (store namespace + name so Claude can reference real flows)
            try:
                r = await c.get(f"{MC_API}/fb/page/getFlows", headers=headers)
                if r.status_code == 200:
                    flows = r.json().get("data", {}).get("flows", []) or []
                    flow_count = len(flows)
                    for f in flows:
                        namespace = (f.get("ns") or f.get("namespace") or "").strip()
                        if not namespace:
                            continue
                        execute(
                            """INSERT INTO manychat_flows (namespace, name, folder, last_seen)
                               VALUES (%s, %s, %s, now())
                               ON CONFLICT (namespace) DO UPDATE SET
                                 name = EXCLUDED.name,
                                 folder = EXCLUDED.folder,
                                 last_seen = now()""",
                            (namespace, (f.get("name") or "").strip(), (f.get("folder") or "").strip()),
                        )
            except Exception as e:
                print(f"[SYNC] Flows error: {e}")

            # Pull tags (store name + id so we can tag leads by name, not count)
            try:
                r = await c.get(f"{MC_API}/fb/page/getTags", headers=headers)
                if r.status_code == 200:
                    tags = r.json().get("data", []) or []
                    tag_count = len(tags)
                    for t in tags:
                        tag_id = str(t.get("id") or "").strip()
                        if not tag_id:
                            continue
                        execute(
                            """INSERT INTO manychat_tags (tag_id, name, last_seen)
                               VALUES (%s, %s, now())
                               ON CONFLICT (tag_id) DO UPDATE SET
                                 name = EXCLUDED.name,
                                 last_seen = now()""",
                            (tag_id, (t.get("name") or "").strip()),
                        )
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


# ───────────────── SUBSCRIBERS ─────────────────
def build_subscriber(row):
    """Format a subscriber row for the frontend."""
    if not row:
        return None
    d = dict(row)
    d["mc_id"] = d.get("contact_id", "")
    fn = d.get("first_name", "") or ""
    ln = d.get("last_name", "") or ""
    d["full_name"] = f"{fn} {ln}".strip() or d.get("ig_username", "") or "Unknown"
    d["subscribed_at"] = d.get("created_at", "")
    return d


@router.get("/api/manychat/subscribers")
async def list_subscribers(request: Request):
    try:
        page = int(request.query_params.get("page", "1"))
        limit = min(int(request.query_params.get("limit", "30")), 100)
        sort = request.query_params.get("sort", "recent")
        search = request.query_params.get("search", "").strip()
        interest = request.query_params.get("interest", "").strip()
        offset = (page - 1) * limit

        where = "WHERE 1=1"
        params = []

        if search:
            where += " AND (first_name ILIKE %s OR last_name ILIKE %s OR ig_username ILIKE %s OR email ILIKE %s OR contact_id ILIKE %s)"
            sq = f"%{search}%"
            params.extend([sq, sq, sq, sq, sq])

        if interest and interest != "all":
            where += " AND interest_level = %s"
            params.append(interest)

        order = "updated_at DESC"
        if sort == "heat":
            order = "heat_score DESC"
        elif sort == "name":
            order = "first_name ASC, last_name ASC"
        elif sort == "recent":
            order = "COALESCE(last_interaction, updated_at) DESC"

        count_rows = query(f"SELECT COUNT(*) AS cnt FROM manychat_leads_clean {where}", tuple(params))
        total = count_rows[0]["cnt"] if count_rows else 0

        params.extend([limit, offset])
        rows = query(
            f"SELECT * FROM manychat_leads_clean {where} ORDER BY {order} LIMIT %s OFFSET %s",
            tuple(params)
        )

        subs = [build_subscriber(r) for r in rows]
        return {"subscribers": subs, "total": total, "pages": max(1, -(-total // limit))}
    except Exception as e:
        print(f"[SUBSCRIBERS] Error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/manychat/subscribers/stats")
async def subscriber_stats():
    try:
        total_rows = query("SELECT COUNT(*) AS cnt FROM manychat_leads_clean")
        total = total_rows[0]["cnt"] if total_rows else 0

        level_rows = query(
            "SELECT interest_level, COUNT(*) AS cnt FROM manychat_leads_clean GROUP BY interest_level"
        )
        by_interest = {"vip": 0, "hot": 0, "warm": 0, "cold": 0, "new": 0}
        for r in level_rows:
            lvl = (r.get("interest_level") or "new").lower()
            if lvl in by_interest:
                by_interest[lvl] = r["cnt"]
            else:
                by_interest["new"] += r["cnt"]

        return {"total": total, "by_interest": by_interest}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/manychat/subscribers/{mc_id}")
async def get_subscriber(mc_id: str):
    try:
        rows = query("SELECT * FROM manychat_leads_clean WHERE contact_id = %s", (mc_id,))
        if not rows:
            return JSONResponse({"error": "Subscriber not found"}, status_code=404)

        sub = build_subscriber(rows[0])

        # Get notes
        notes = query(
            "SELECT * FROM subscriber_notes WHERE contact_id = %s ORDER BY created_at DESC",
            (mc_id,)
        )

        # Get actions
        actions = query(
            "SELECT * FROM action_log WHERE contact_id = %s ORDER BY created_at DESC LIMIT 20",
            (mc_id,)
        )

        return {
            "subscriber": sub,
            "notes": notes or [],
            "triggers": [],
            "conversations": [],
            "actions": actions or [],
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/manychat/subscribers/{mc_id}/notes")
async def add_note(mc_id: str, request: Request):
    try:
        body = await request.json()
        note_text = (body.get("note") or "").strip()
        if not note_text:
            return JSONResponse({"error": "Note text required"}, status_code=400)
        execute(
            "INSERT INTO subscriber_notes (contact_id, note) VALUES (%s, %s)",
            (mc_id, note_text)
        )
        # Log action
        rows = query("SELECT first_name, last_name FROM manychat_leads_clean WHERE contact_id = %s", (mc_id,))
        name = f"{(rows[0].get('first_name','') if rows else '')} {(rows[0].get('last_name','') if rows else '')}".strip() or mc_id
        execute(
            "INSERT INTO action_log (contact_id, subscriber_name, action_type, action_detail) VALUES (%s, %s, 'added_note', %s)",
            (mc_id, name, f"Note: {note_text[:100]}")
        )
        return {"success": True}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.delete("/api/manychat/notes/{note_id}")
async def delete_note(note_id: int):
    try:
        execute("DELETE FROM subscriber_notes WHERE id = %s", (note_id,))
        return {"success": True}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.patch("/api/manychat/subscribers/{mc_id}/dnc")
async def toggle_dnc(mc_id: str):
    try:
        execute(
            "UPDATE manychat_leads_clean SET do_not_contact = NOT do_not_contact WHERE contact_id = %s",
            (mc_id,)
        )
        rows = query("SELECT do_not_contact FROM manychat_leads_clean WHERE contact_id = %s", (mc_id,))
        dnc = rows[0]["do_not_contact"] if rows else False
        return {"do_not_contact": dnc}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/manychat/lookup")
async def lookup_subscriber(request: Request):
    try:
        body = await request.json()
        mc_id = (body.get("mc_id") or "").strip()
        email = (body.get("email") or "").strip()
        phone = (body.get("phone") or "").strip()

        row = None
        if mc_id:
            rows = query("SELECT * FROM manychat_leads_clean WHERE contact_id = %s", (mc_id,))
            if rows:
                row = rows[0]
        if not row and email:
            rows = query("SELECT * FROM manychat_leads_clean WHERE email ILIKE %s", (email,))
            if rows:
                row = rows[0]
        if not row and phone:
            rows = query("SELECT * FROM manychat_leads_clean WHERE phone = %s", (phone,))
            if rows:
                row = rows[0]

        if row:
            return {"subscriber": build_subscriber(row)}
        return JSONResponse({"error": "No subscriber found"}, status_code=404)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ───────────────── RECOMMENDATIONS ─────────────────
@router.get("/api/manychat/recommendations")
async def get_recommendations(request: Request):
    try:
        status_filter = request.query_params.get("status", "pending")
        rows = query(
            "SELECT * FROM subscriber_recommendations WHERE status = %s ORDER BY priority ASC, created_at DESC LIMIT 50",
            (status_filter,)
        )
        return {"recommendations": rows or []}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.patch("/api/manychat/recommendations/{rec_id}/complete")
async def complete_recommendation(rec_id: int):
    try:
        execute("UPDATE subscriber_recommendations SET status = 'completed' WHERE id = %s", (rec_id,))
        return {"success": True}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.patch("/api/manychat/recommendations/{rec_id}/dismiss")
async def dismiss_recommendation(rec_id: int):
    try:
        execute("UPDATE subscriber_recommendations SET status = 'dismissed' WHERE id = %s", (rec_id,))
        # Log action
        rows = query("SELECT subscriber_name, title FROM subscriber_recommendations WHERE id = %s", (rec_id,))
        name = rows[0]["subscriber_name"] if rows else ""
        execute(
            "INSERT INTO action_log (subscriber_name, action_type, action_detail) VALUES (%s, 'dismissed_rec', %s)",
            (name, f"Dismissed: {rows[0]['title']}" if rows else "Dismissed recommendation")
        )
        return {"success": True}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ───────────────── AI ANALYSIS ─────────────────
@router.post("/api/manychat/subscribers/{mc_id}/analyze")
async def analyze_subscriber(mc_id: str):
    try:
        if not ANTHROPIC_KEY:
            return JSONResponse({"error": "ANTHROPIC_API_KEY not configured"}, status_code=500)
        import anthropic
        rows = query("SELECT * FROM manychat_leads_clean WHERE contact_id = %s", (mc_id,))
        if not rows:
            return JSONResponse({"error": "Subscriber not found"}, status_code=404)
        sub = rows[0]
        notes = query("SELECT note FROM subscriber_notes WHERE contact_id = %s ORDER BY created_at DESC LIMIT 10", (mc_id,))

        # Get available triggers
        triggers = query("SELECT keyword, label, description FROM manychat_triggers WHERE is_active = true")
        trigger_list = "\n".join([f"- {t['keyword']}: {t['label']} — {t.get('description','')}" for t in (triggers or [])])

        fn = sub.get("first_name","") or ""
        ln = sub.get("last_name","") or ""
        name = f"{fn} {ln}".strip() or sub.get("ig_username","") or "Unknown"

        prompt = f"""Analyze this therapy practice subscriber and recommend next steps.

Subscriber: {name}
Instagram: @{sub.get('ig_username','')}
Email: {sub.get('email','') or 'none'}
Keyword they triggered: {sub.get('keyword','')}
Grief type: {sub.get('grief_type','')}
Location: {sub.get('user_location_state','')}
Segment: {sub.get('audience_segment','')}
Heat score: {sub.get('heat_score',0)}/100
Interest level: {sub.get('interest_level','new')}
Notes: {', '.join([n['note'] for n in notes]) if notes else 'none'}

Available ManyChat triggers:
{trigger_list}

Return a JSON object (no backticks) with:
- "overview": 2-3 sentence summary of this subscriber and where they are in their journey
- "recommended_triggers": array of 2-3 triggers to send them, each with "keyword" and "reason" """

        client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        resp = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}]
        )
        text = resp.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        analysis = json.loads(text)

        # Save analysis
        execute(
            "UPDATE manychat_leads_clean SET analysis = %s WHERE contact_id = %s",
            (json.dumps(analysis), mc_id)
        )

        return {"success": True, "analysis": analysis}
    except json.JSONDecodeError:
        return {"success": True, "analysis": {"overview": text, "recommended_triggers": []}}
    except Exception as e:
        print(f"[ANALYZE] Error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/manychat/analyze")
async def analyze_all(request: Request):
    """Run Claude analysis on subscribers who need attention."""
    try:
        if not ANTHROPIC_KEY:
            return JSONResponse({"error": "ANTHROPIC_API_KEY not configured"}, status_code=500)
        import anthropic

        # Get subscribers with recent activity but no recent recommendations
        subs = query("""
            SELECT * FROM manychat_leads_clean
            WHERE keyword != '' AND keyword IS NOT NULL
            ORDER BY updated_at DESC LIMIT 20
        """)

        if not subs:
            return {"success": True, "recommendations": 0}

        triggers = query("SELECT keyword, label, description FROM manychat_triggers WHERE is_active = true")
        trigger_list = "\n".join([f"- {t['keyword']}: {t['label']} — {t.get('description','')}" for t in (triggers or [])])

        sub_summaries = []
        for s in subs:
            fn = s.get("first_name","") or ""
            ln = s.get("last_name","") or ""
            name = f"{fn} {ln}".strip() or s.get("ig_username","") or "Unknown"
            sub_summaries.append(f"- {name} (ID: {s['contact_id']}): keyword={s.get('keyword','')}, heat={s.get('heat_score',0)}, level={s.get('interest_level','new')}")

        prompt = f"""You are an AI assistant for a grief therapist's CRM. Analyze these subscribers and generate actionable recommendations.

Subscribers:
{chr(10).join(sub_summaries)}

Available ManyChat triggers:
{trigger_list}

For each subscriber who needs attention, generate a recommendation. Return a JSON array (no backticks) of objects, each with:
- "contact_id": the subscriber's ID
- "subscriber_name": their name
- "title": short action title
- "priority": 1 (urgent) to 4 (low)
- "category": one of "follow_up", "re_engage", "high_intent", "new_lead"
- "description": why this person needs attention
- "suggested_action": what Angela should do
- "suggested_flow": trigger keyword to send (or empty string)

Return max 10 recommendations. Focus on high-value opportunities."""

        client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        resp = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]
        )
        text = resp.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        recs = json.loads(text)

        count = 0
        for rec in recs:
            try:
                execute(
                    """INSERT INTO subscriber_recommendations
                    (contact_id, subscriber_name, title, priority, category, description, suggested_action, suggested_flow, data_points)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (rec.get("contact_id",""), rec.get("subscriber_name",""), rec.get("title",""),
                     rec.get("priority",3), rec.get("category","follow_up"), rec.get("description",""),
                     rec.get("suggested_action",""), rec.get("suggested_flow",""),
                     json.dumps(rec.get("data_points",{})))
                )
                count += 1
            except Exception as e:
                print(f"[ANALYZE] Rec insert error: {e}")

        return {"success": True, "recommendations": count}
    except Exception as e:
        print(f"[ANALYZE ALL] Error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


# ───────────────── ACTIONS ─────────────────
@router.get("/api/manychat/actions")
async def get_actions():
    try:
        rows = query("SELECT * FROM action_log ORDER BY created_at DESC LIMIT 50")
        return {"actions": rows or []}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ───────────────── SMART SEND / DM ─────────────────
@router.post("/api/manychat/smart-send")
async def smart_send(request: Request):
    """Send a ManyChat flow to a subscriber."""
    try:
        body = await request.json()
        mc_id = body.get("mc_id", "")
        flow_ns = body.get("flow_ns", "")
        if not mc_id or not flow_ns:
            return JSONResponse({"error": "mc_id and flow_ns required"}, status_code=400)
        if not MC_KEY:
            return JSONResponse({"error": "MANYCHAT_API_KEY not configured"}, status_code=500)

        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"{MC_API}/fb/sending/sendFlow",
                headers={"Authorization": f"Bearer {MC_KEY}", "Content-Type": "application/json"},
                json={"subscriber_id": mc_id, "flow_ns": flow_ns}
            )
            result = r.json()

        # Log action
        rows = query("SELECT first_name, last_name FROM manychat_leads_clean WHERE contact_id = %s", (mc_id,))
        name = f"{(rows[0].get('first_name','') if rows else '')} {(rows[0].get('last_name','') if rows else '')}".strip() or mc_id
        execute(
            "INSERT INTO action_log (contact_id, subscriber_name, action_type, action_detail) VALUES (%s, %s, 'sent_flow', %s)",
            (mc_id, name, f"Sent flow: {flow_ns}")
        )

        if result.get("status") == "success":
            return {"success": True}
        return JSONResponse({"error": result.get("message", "Failed to send flow")}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/manychat/send-dm")
async def send_dm(request: Request):
    """Send a direct message via ManyChat."""
    try:
        body = await request.json()
        mc_id = body.get("mc_id", "")
        message = body.get("message", "").strip()
        if not mc_id or not message:
            return JSONResponse({"error": "mc_id and message required"}, status_code=400)
        if not MC_KEY:
            return JSONResponse({"error": "MANYCHAT_API_KEY not configured"}, status_code=500)

        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"{MC_API}/fb/sending/sendContent",
                headers={"Authorization": f"Bearer {MC_KEY}", "Content-Type": "application/json"},
                json={"subscriber_id": mc_id, "data": {"version": "v2", "content": {"messages": [{"type": "text", "text": message}]}}}
            )
            result = r.json()

        # Log action
        rows = query("SELECT first_name, last_name FROM manychat_leads_clean WHERE contact_id = %s", (mc_id,))
        name = f"{(rows[0].get('first_name','') if rows else '')} {(rows[0].get('last_name','') if rows else '')}".strip() or mc_id
        execute(
            "INSERT INTO action_log (contact_id, subscriber_name, action_type, action_detail) VALUES (%s, %s, 'manual_message', %s)",
            (mc_id, name, f"DM: {message[:100]}")
        )

        if result.get("status") == "success":
            return {"success": True}
        return JSONResponse({"error": result.get("message", "Failed to send message")}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ───────────────── HEALTH ─────────────────
@router.get("/api/manychat/webhook")
async def webhook_status():
    return {"status": "ok", "message": "Webhook working"}
