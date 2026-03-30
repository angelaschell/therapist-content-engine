"""
Lead CRM Backend
ManyChat sync, subscriber management, Claude lead intelligence, action logging.

Add to main.py:
  from manychat_backend import router as manychat_router
  app.include_router(manychat_router)
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, HTMLResponse
import httpx
import os
import json
from datetime import datetime, timedelta, timezone

router = APIRouter()

# ── Config ────────────────────────────────────────────────────
from supabase import create_client
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

MC_API = "https://api.manychat.com"
MC_KEY = os.environ.get("MANYCHAT_API_KEY", "")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")


# ================================================================
#  AUTO-SETUP: Create tables via DATABASE_URL
# ================================================================

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS manychat_triggers (
  id            BIGSERIAL PRIMARY KEY,
  keyword       TEXT NOT NULL UNIQUE,
  label         TEXT NOT NULL,
  description   TEXT DEFAULT '',
  product_url   TEXT DEFAULT '',
  is_active     BOOLEAN DEFAULT true,
  sort_order    INT DEFAULT 0,
  created_at    TIMESTAMPTZ DEFAULT now(),
  updated_at    TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS manychat_subscribers (
  id              BIGSERIAL PRIMARY KEY,
  mc_id           TEXT NOT NULL UNIQUE,
  first_name      TEXT DEFAULT '',
  last_name       TEXT DEFAULT '',
  full_name       TEXT DEFAULT '',
  email           TEXT DEFAULT '',
  phone           TEXT DEFAULT '',
  ig_username     TEXT DEFAULT '',
  profile_pic     TEXT DEFAULT '',
  gender          TEXT DEFAULT '',
  locale          TEXT DEFAULT '',
  subscribed_at   TIMESTAMPTZ,
  last_interaction TIMESTAMPTZ,
  last_seen       TIMESTAMPTZ,
  ig_last_interaction TIMESTAMPTZ,
  opted_in_ig     BOOLEAN DEFAULT false,
  opted_in_email  BOOLEAN DEFAULT false,
  tags            JSONB DEFAULT '[]'::jsonb,
  custom_fields   JSONB DEFAULT '{}'::jsonb,
  trigger_count   INT DEFAULT 0,
  conversation_count INT DEFAULT 0,
  interest_level  TEXT DEFAULT 'new',
  heat_score      INT DEFAULT 0,
  funnel_stage    TEXT DEFAULT 'subscriber',
  flodesk_synced  BOOLEAN DEFAULT false,
  do_not_contact  BOOLEAN DEFAULT false,
  synced_at       TIMESTAMPTZ DEFAULT now(),
  created_at      TIMESTAMPTZ DEFAULT now(),
  updated_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS subscriber_triggers (
  id              BIGSERIAL PRIMARY KEY,
  mc_id           TEXT NOT NULL,
  keyword         TEXT NOT NULL,
  source          TEXT DEFAULT 'instagram',
  fired_at        TIMESTAMPTZ DEFAULT now(),
  post_id         TEXT DEFAULT '',
  created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS subscriber_conversations (
  id              BIGSERIAL PRIMARY KEY,
  mc_id           TEXT NOT NULL,
  direction       TEXT NOT NULL,
  message_preview TEXT DEFAULT '',
  flow_name       TEXT DEFAULT '',
  channel         TEXT DEFAULT 'instagram',
  sent_at         TIMESTAMPTZ DEFAULT now(),
  created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS lead_recommendations (
  id              BIGSERIAL PRIMARY KEY,
  mc_id           TEXT,
  subscriber_name TEXT DEFAULT '',
  priority        INT DEFAULT 5,
  category        TEXT DEFAULT 'follow_up',
  title           TEXT NOT NULL,
  description     TEXT NOT NULL,
  suggested_action TEXT DEFAULT '',
  suggested_flow  TEXT DEFAULT '',
  data_points     JSONB DEFAULT '{}'::jsonb,
  status          TEXT DEFAULT 'pending',
  completed_at    TIMESTAMPTZ,
  completed_note  TEXT DEFAULT '',
  created_at      TIMESTAMPTZ DEFAULT now(),
  expires_at      TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS completed_actions (
  id              BIGSERIAL PRIMARY KEY,
  mc_id           TEXT NOT NULL,
  subscriber_name TEXT DEFAULT '',
  action_type     TEXT NOT NULL,
  action_detail   TEXT DEFAULT '',
  flow_id         TEXT DEFAULT '',
  recommendation_id BIGINT,
  created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS subscriber_notes (
  id              BIGSERIAL PRIMARY KEY,
  mc_id           TEXT NOT NULL,
  note            TEXT NOT NULL,
  created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sync_log (
  id              BIGSERIAL PRIMARY KEY,
  sync_type       TEXT NOT NULL,
  records_synced  INT DEFAULT 0,
  status          TEXT DEFAULT 'success',
  error_message   TEXT DEFAULT '',
  started_at      TIMESTAMPTZ DEFAULT now(),
  completed_at    TIMESTAMPTZ
);

-- Seed triggers if empty
INSERT INTO manychat_triggers (keyword, label, description, sort_order)
SELECT * FROM (VALUES
  ('HEAL',            '1:1 Session',                  'Book a 1:1 therapy session with Angela.',                                          1),
  ('UNTANGLE',        '1:1 Session',                  'Book a 1:1 therapy session. Alternate trigger for HEAL.',                           2),
  ('STEADY',          '1:1 Session',                  'Book a 1:1 therapy session. Alternate trigger for HEAL.',                           3),
  ('MALIBURETREAT',   'Healing with Horses Retreat',  'Healing with Horses Somatic Grief Retreat in Malibu. April 30 to May 3, 2026.',    4),
  ('MALIBU RETREAT',  'Healing with Horses Retreat',  'Alternate trigger for the Malibu retreat.',                                         5),
  ('UNLEARN',         'Mother Hunger Course',          'Eight-week live Mother Hunger course by Kelly McDaniel.',                           6),
  ('WORTHY',          'Emotional Starter Kit',        'Free Emotional Starter Kit. Entry-level lead magnet.',                              7),
  ('GRIEFRELIEF',     'Grief Relief Video Series',    'Grief Relief Video Series. Paid digital product.',                                  8),
  ('GRIEFTOOLS',      'Grief Relief Video Series',    'Alternate trigger for Grief Relief.',                                               9),
  ('TOOLS',           '101 Tools',                    '101 Tools digital product.',                                                        10),
  ('EQUINE',          'Equine Digital Product',       'Equine-assisted learning digital product.',                                         11),
  ('HORSEHEALING',    'Equine Digital Product',       'Alternate trigger for Equine product.',                                             12),
  ('MOM',             'Community Circle',             'Free Grief, Trauma & Your Mama community on Circle.',                               13),
  ('COMMUNITYCALL',   'Motherless Daughters Group',   'Hope Edelman Motherless Daughters Thursday group call.',                            14),
  ('EMDR',            'EMDR Therapy',                 'EMDR therapy information and booking.',                                             15),
  ('TAPPERS',         'Dharma Dr.',                   'Dharma Dr. bilateral stimulation tappers.',                                         16)
) AS v(keyword, label, description, sort_order)
WHERE NOT EXISTS (SELECT 1 FROM manychat_triggers LIMIT 1)
ON CONFLICT (keyword) DO NOTHING;
"""

def run_setup():
    """Create all tables using direct PostgreSQL connection."""
    if not DATABASE_URL:
        return False, "DATABASE_URL not set"
    try:
        import psycopg2
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(SCHEMA_SQL)
        cur.close()
        conn.close()
        return True, "All tables created successfully"
    except Exception as e:
        return False, str(e)

@router.get("/api/manychat/setup")
async def setup_database():
    """Create all Lead CRM tables. Hit this once."""
    success, msg = run_setup()
    if success:
        return JSONResponse(content={"success": True, "message": msg})
    return JSONResponse(content={"success": False, "error": msg}, status_code=500)

# Try auto-setup on import
try:
    run_setup()
except:
    pass

def mc_headers():
    return {
        "Authorization": f"Bearer {MC_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json"
    }


# ================================================================
#  SERVE PAGES
# ================================================================

@router.get("/manychat", response_class=HTMLResponse)
async def manychat_page():
    try:
        with open("manychat.html", "r") as f:
            return HTMLResponse(content=f.read())
    except:
        return HTMLResponse(content="<h1>manychat.html not found</h1>", status_code=404)


# ================================================================
#  MANYCHAT API PROXY LAYER
# ================================================================

async def mc_get(path: str):
    """GET request to ManyChat API."""
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(f"{MC_API}{path}", headers=mc_headers())
        r.raise_for_status()
        return r.json()

async def mc_post(path: str, payload: dict):
    """POST request to ManyChat API."""
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(f"{MC_API}{path}", headers=mc_headers(), json=payload)
        r.raise_for_status()
        return r.json()


# ================================================================
#  MANYCHAT TRIGGER CRUD (unchanged from before)
# ================================================================

@router.get("/api/manychat/triggers")
async def list_triggers():
    try:
        res = sb.table("manychat_triggers").select("*").order("sort_order", desc=False).execute()
        return JSONResponse(content={"triggers": res.data})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@router.get("/api/manychat/triggers/{trigger_id}")
async def get_trigger(trigger_id: int):
    try:
        res = sb.table("manychat_triggers").select("*").eq("id", trigger_id).single().execute()
        return JSONResponse(content={"trigger": res.data})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@router.post("/api/manychat/triggers")
async def create_trigger(request: Request):
    try:
        body = await request.json()
        keyword = body.get("keyword", "").strip().upper()
        label = body.get("label", "").strip()
        description = body.get("description", "").strip()
        if not keyword or not label:
            return JSONResponse(content={"error": "Keyword and label are required."}, status_code=400)
        max_res = sb.table("manychat_triggers").select("sort_order").order("sort_order", desc=True).limit(1).execute()
        next_order = (max_res.data[0]["sort_order"] + 1) if max_res.data else 1
        res = sb.table("manychat_triggers").insert({
            "keyword": keyword, "label": label, "description": description, "sort_order": next_order
        }).execute()
        return JSONResponse(content={"trigger": res.data[0] if res.data else None})
    except Exception as e:
        if "duplicate" in str(e).lower() or "unique" in str(e).lower():
            return JSONResponse(content={"error": f"Trigger '{keyword}' already exists."}, status_code=409)
        return JSONResponse(content={"error": str(e)}, status_code=500)

@router.put("/api/manychat/triggers/{trigger_id}")
async def update_trigger(trigger_id: int, request: Request):
    try:
        body = await request.json()
        updates = {}
        for k in ["keyword", "label", "description", "product_url", "is_active", "sort_order"]:
            if k in body:
                updates[k] = body[k].strip().upper() if k == "keyword" else (body[k].strip() if isinstance(body[k], str) else body[k])
        if not updates:
            return JSONResponse(content={"error": "No fields to update."}, status_code=400)
        res = sb.table("manychat_triggers").update(updates).eq("id", trigger_id).execute()
        return JSONResponse(content={"trigger": res.data[0] if res.data else None})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@router.delete("/api/manychat/triggers/{trigger_id}")
async def delete_trigger(trigger_id: int):
    try:
        sb.table("manychat_triggers").delete().eq("id", trigger_id).execute()
        return JSONResponse(content={"deleted": True})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@router.patch("/api/manychat/triggers/{trigger_id}/toggle")
async def toggle_trigger(trigger_id: int):
    try:
        current = sb.table("manychat_triggers").select("is_active").eq("id", trigger_id).single().execute()
        new_status = not current.data["is_active"]
        res = sb.table("manychat_triggers").update({"is_active": new_status}).eq("id", trigger_id).execute()
        return JSONResponse(content={"trigger": res.data[0] if res.data else None})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


# ================================================================
#  MANYCHAT SYNC ENGINE
# ================================================================

def calc_interest_level(trigger_count: int, conversation_count: int, tags: list, triggers_fired: list):
    """Calculate interest level based on behavior."""
    tag_names = [t.get("name", "").lower() if isinstance(t, dict) else str(t).lower() for t in tags]
    trigger_keywords = [t.get("keyword", "").upper() if isinstance(t, dict) else str(t).upper() for t in triggers_fired]

    # High-ticket signals
    high_ticket = any(k in trigger_keywords for k in ["MALIBURETREAT", "MALIBU RETREAT", "HEAL", "UNTANGLE", "STEADY", "EMDR"])
    multi_category = len(set(trigger_keywords)) >= 3

    if high_ticket and (conversation_count >= 3 or multi_category):
        return "vip", 90
    if high_ticket or (trigger_count >= 3 and conversation_count >= 2):
        return "hot", 70
    if trigger_count >= 2 or conversation_count >= 2:
        return "warm", 50
    if trigger_count >= 1:
        return "cold", 25
    return "new", 5

def calc_funnel_stage(trigger_count: int, conversation_count: int, tags: list):
    """Determine funnel stage."""
    tag_names = [t.get("name", "").lower() if isinstance(t, dict) else str(t).lower() for t in tags]
    if any("booked" in t or "purchased" in t or "client" in t for t in tag_names):
        return "booked"
    if conversation_count >= 3:
        return "conversation"
    if trigger_count >= 3:
        return "multi_trigger"
    if trigger_count >= 1 or conversation_count >= 1:
        return "engaged"
    return "subscriber"


@router.post("/api/manychat/sync")
async def sync_data():
    """Pull flows, tags, and custom fields from ManyChat. Test API connection."""
    log_id = None
    try:
        # Log sync start
        log = sb.table("sync_log").insert({
            "sync_type": "subscribers",
            "status": "running"
        }).execute()
        log_id = log.data[0]["id"] if log.data else None

        synced = 0

        # 1. Pull flows
        try:
            flows_data = await mc_get("/fb/page/getFlows")
            flows = flows_data.get("data", [])
            synced += len(flows)
        except Exception as e:
            flows = []

        # 2. Pull tags
        try:
            tags_data = await mc_get("/fb/page/getTags")
            tags = tags_data.get("data", [])
            synced += len(tags)
        except:
            tags = []

        # 3. Pull custom fields
        try:
            fields_data = await mc_get("/fb/page/getCustomFields")
            fields = fields_data.get("data", [])
        except:
            fields = []

        # 4. Pull bot fields
        try:
            bot_data = await mc_get("/fb/page/getBotFields")
            bot_fields = bot_data.get("data", [])
        except:
            bot_fields = []

        # Update sync log
        if log_id:
            sb.table("sync_log").update({
                "records_synced": synced,
                "status": "success",
                "error_message": json.dumps({
                    "flows": len(flows),
                    "tags": len(tags),
                    "custom_fields": len(fields),
                    "bot_fields": len(bot_fields)
                }),
                "completed_at": datetime.now(timezone.utc).isoformat()
            }).eq("id", log_id).execute()

        return JSONResponse(content={
            "success": True,
            "synced": synced,
            "flows": len(flows),
            "tags": len(tags),
            "custom_fields": len(fields),
            "bot_fields": len(bot_fields),
            "flow_list": [{"name": f.get("name", ""), "ns": f.get("ns", ""), "id": f.get("id", "")} for f in flows[:50]],
            "tag_list": [{"name": t.get("name", ""), "id": t.get("id", "")} for t in tags[:50]]
        })

    except Exception as e:
        if log_id:
            sb.table("sync_log").update({
                "status": "error",
                "error_message": str(e),
                "completed_at": datetime.now(timezone.utc).isoformat()
            }).eq("id", log_id).execute()
        return JSONResponse(content={"error": str(e)}, status_code=500)


@router.post("/api/manychat/lookup")
async def lookup_subscriber(request: Request):
    """Look up a single subscriber by ID, email, or phone and cache in Supabase."""
    try:
        body = await request.json()
        mc_id = body.get("mc_id", "")
        email = body.get("email", "")
        phone = body.get("phone", "")

        sub_data = None

        if mc_id:
            data = await mc_get(f"/fb/subscriber/getInfo?subscriber_id={mc_id}")
            sub_data = data.get("data", {})
        elif email:
            data = await mc_get(f"/fb/subscriber/findBySystemField?email={email}")
            sub_data = data.get("data", {})
        elif phone:
            data = await mc_get(f"/fb/subscriber/findBySystemField?phone={phone}")
            sub_data = data.get("data", {})
        else:
            return JSONResponse(content={"error": "Provide mc_id, email, or phone."}, status_code=400)

        if not sub_data or not sub_data.get("id"):
            return JSONResponse(content={"error": "Subscriber not found."}, status_code=404)

        # Cache in Supabase
        mc_sub_id = str(sub_data.get("id", ""))
        tags = sub_data.get("tags", [])
        custom_fields = sub_data.get("custom_fields", {})

        # Get existing trigger/conversation history
        trig_res = sb.table("subscriber_triggers").select("keyword").eq("mc_id", mc_sub_id).execute()
        triggers_fired = trig_res.data if trig_res.data else []
        trigger_count = len(triggers_fired)
        conv_res = sb.table("subscriber_conversations").select("id").eq("mc_id", mc_sub_id).execute()
        conversation_count = len(conv_res.data) if conv_res.data else 0

        interest_level, heat_score = calc_interest_level(trigger_count, conversation_count, tags, triggers_fired)
        funnel_stage = calc_funnel_stage(trigger_count, conversation_count, tags)

        record = {
            "mc_id": mc_sub_id,
            "first_name": sub_data.get("first_name", ""),
            "last_name": sub_data.get("last_name", ""),
            "full_name": f"{sub_data.get('first_name', '')} {sub_data.get('last_name', '')}".strip(),
            "email": sub_data.get("email", "") or "",
            "phone": sub_data.get("phone", "") or "",
            "ig_username": sub_data.get("ig_username", "") or sub_data.get("instagram_username", "") or "",
            "profile_pic": sub_data.get("profile_pic", "") or "",
            "gender": sub_data.get("gender", "") or "",
            "locale": sub_data.get("locale", "") or "",
            "subscribed_at": sub_data.get("subscribed", sub_data.get("created_at")),
            "last_interaction": sub_data.get("last_interaction"),
            "last_seen": sub_data.get("last_seen"),
            "ig_last_interaction": sub_data.get("last_interaction_in_instagram"),
            "opted_in_ig": sub_data.get("opted_in_for_instagram", False),
            "opted_in_email": bool(sub_data.get("email")),
            "tags": json.dumps(tags) if isinstance(tags, list) else "[]",
            "custom_fields": json.dumps(custom_fields) if isinstance(custom_fields, (dict, list)) else "{}",
            "trigger_count": trigger_count,
            "conversation_count": conversation_count,
            "interest_level": interest_level,
            "heat_score": heat_score,
            "funnel_stage": funnel_stage,
            "synced_at": datetime.now(timezone.utc).isoformat()
        }

        sb.table("manychat_subscribers").upsert(record, on_conflict="mc_id").execute()

        return JSONResponse(content={"success": True, "subscriber": record})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@router.get("/api/manychat/sync/status")
async def sync_status():
    """Get last sync info."""
    try:
        res = sb.table("sync_log").select("*").order("started_at", desc=True).limit(1).execute()
        return JSONResponse(content={"last_sync": res.data[0] if res.data else None})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@router.get("/api/manychat/flows")
async def get_flows():
    """Get all flows from ManyChat."""
    try:
        data = await mc_get("/fb/page/getFlows")
        flows = data.get("data", [])
        return JSONResponse(content={"flows": flows})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@router.get("/api/manychat/tags")
async def get_tags():
    """Get all tags from ManyChat."""
    try:
        data = await mc_get("/fb/page/getTags")
        tags = data.get("data", [])
        return JSONResponse(content={"tags": tags})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


# ================================================================
#  SUBSCRIBER ENDPOINTS
# ================================================================

@router.get("/api/manychat/subscribers")
async def list_subscribers(
    page: int = 1,
    limit: int = 50,
    interest: str = "",
    funnel: str = "",
    search: str = "",
    sort: str = "heat_score"
):
    """List cached subscribers with filtering."""
    try:
        q = sb.table("manychat_subscribers").select("*")

        if interest:
            q = q.eq("interest_level", interest)
        if funnel:
            q = q.eq("funnel_stage", funnel)
        if search:
            q = q.or_(f"full_name.ilike.%{search}%,ig_username.ilike.%{search}%,email.ilike.%{search}%")

        # Sort
        if sort == "heat_score":
            q = q.order("heat_score", desc=True)
        elif sort == "recent":
            q = q.order("last_interaction", desc=True)
        elif sort == "subscribed":
            q = q.order("subscribed_at", desc=True)
        elif sort == "triggers":
            q = q.order("trigger_count", desc=True)
        else:
            q = q.order("heat_score", desc=True)

        # Pagination
        offset = (page - 1) * limit
        q = q.range(offset, offset + limit - 1)

        res = q.execute()

        # Get total count
        count_q = sb.table("manychat_subscribers").select("id", count="exact")
        if interest:
            count_q = count_q.eq("interest_level", interest)
        if funnel:
            count_q = count_q.eq("funnel_stage", funnel)
        count_res = count_q.execute()
        total = count_res.count if hasattr(count_res, 'count') and count_res.count else len(res.data)

        return JSONResponse(content={
            "subscribers": res.data,
            "total": total,
            "page": page,
            "pages": (total + limit - 1) // limit
        })
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@router.get("/api/manychat/subscribers/{mc_id}")
async def get_subscriber_detail(mc_id: str):
    """Get full subscriber detail with timeline."""
    try:
        # Subscriber data
        sub = sb.table("manychat_subscribers").select("*").eq("mc_id", mc_id).single().execute()

        # Trigger history
        triggers = sb.table("subscriber_triggers").select("*").eq("mc_id", mc_id).order("fired_at", desc=True).execute()

        # Conversations
        convos = sb.table("subscriber_conversations").select("*").eq("mc_id", mc_id).order("sent_at", desc=True).limit(50).execute()

        # Notes
        notes = sb.table("subscriber_notes").select("*").eq("mc_id", mc_id).order("created_at", desc=True).execute()

        # Actions taken
        actions = sb.table("completed_actions").select("*").eq("mc_id", mc_id).order("created_at", desc=True).limit(20).execute()

        return JSONResponse(content={
            "subscriber": sub.data,
            "triggers": triggers.data,
            "conversations": convos.data,
            "notes": notes.data,
            "actions": actions.data
        })
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@router.get("/api/manychat/subscribers/stats")
async def subscriber_stats():
    """Dashboard stats."""
    try:
        all_subs = sb.table("manychat_subscribers").select("interest_level,funnel_stage,heat_score,do_not_contact").execute()
        data = all_subs.data or []
        total = len(data)
        active = [s for s in data if not s.get("do_not_contact")]

        by_interest = {}
        by_funnel = {}
        for s in active:
            il = s.get("interest_level", "new")
            fs = s.get("funnel_stage", "subscriber")
            by_interest[il] = by_interest.get(il, 0) + 1
            by_funnel[fs] = by_funnel.get(fs, 0) + 1

        return JSONResponse(content={
            "total": total,
            "active": len(active),
            "by_interest": by_interest,
            "by_funnel": by_funnel,
            "avg_heat": round(sum(s.get("heat_score", 0) for s in active) / max(len(active), 1), 1)
        })
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


# ================================================================
#  SUBSCRIBER NOTES
# ================================================================

@router.post("/api/manychat/subscribers/{mc_id}/notes")
async def add_note(mc_id: str, request: Request):
    try:
        body = await request.json()
        note = body.get("note", "").strip()
        if not note:
            return JSONResponse(content={"error": "Note is required."}, status_code=400)
        res = sb.table("subscriber_notes").insert({"mc_id": mc_id, "note": note}).execute()
        return JSONResponse(content={"note": res.data[0] if res.data else None})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@router.delete("/api/manychat/notes/{note_id}")
async def delete_note(note_id: int):
    try:
        sb.table("subscriber_notes").delete().eq("id", note_id).execute()
        return JSONResponse(content={"deleted": True})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


# ================================================================
#  DO NOT CONTACT
# ================================================================

@router.patch("/api/manychat/subscribers/{mc_id}/dnc")
async def toggle_dnc(mc_id: str):
    try:
        current = sb.table("manychat_subscribers").select("do_not_contact").eq("mc_id", mc_id).single().execute()
        new_val = not current.data["do_not_contact"]
        sb.table("manychat_subscribers").update({"do_not_contact": new_val}).eq("mc_id", mc_id).execute()
        return JSONResponse(content={"do_not_contact": new_val})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


# ================================================================
#  ACTIONS: SEND FLOW, ADD TAG
# ================================================================

@router.post("/api/manychat/send-flow")
async def send_flow(request: Request):
    """Send a ManyChat flow to a subscriber."""
    try:
        body = await request.json()
        mc_id = body.get("mc_id", "")
        flow_ns = body.get("flow_ns", "")
        if not mc_id or not flow_ns:
            return JSONResponse(content={"error": "mc_id and flow_ns required."}, status_code=400)

        result = await mc_post("/fb/sending/sendFlow", {
            "subscriber_id": int(mc_id),
            "flow_ns": flow_ns
        })

        # Log the action
        sub = sb.table("manychat_subscribers").select("full_name").eq("mc_id", mc_id).execute()
        name = sub.data[0]["full_name"] if sub.data else "Unknown"

        sb.table("completed_actions").insert({
            "mc_id": mc_id,
            "subscriber_name": name,
            "action_type": "sent_flow",
            "action_detail": f"Sent flow {flow_ns}",
            "flow_id": flow_ns,
            "recommendation_id": body.get("recommendation_id")
        }).execute()

        return JSONResponse(content={"success": True, "result": result})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@router.post("/api/manychat/add-tag")
async def add_tag(request: Request):
    """Add a tag to a subscriber in ManyChat."""
    try:
        body = await request.json()
        mc_id = body.get("mc_id", "")
        tag_id = body.get("tag_id", "")
        if not mc_id or not tag_id:
            return JSONResponse(content={"error": "mc_id and tag_id required."}, status_code=400)

        result = await mc_post("/fb/subscriber/addTag", {
            "subscriber_id": int(mc_id),
            "tag_id": int(tag_id)
        })

        sub = sb.table("manychat_subscribers").select("full_name").eq("mc_id", mc_id).execute()
        name = sub.data[0]["full_name"] if sub.data else "Unknown"

        sb.table("completed_actions").insert({
            "mc_id": mc_id,
            "subscriber_name": name,
            "action_type": "added_tag",
            "action_detail": f"Added tag {tag_id}"
        }).execute()

        return JSONResponse(content={"success": True, "result": result})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


# ================================================================
#  COMPLETED ACTIONS
# ================================================================

@router.get("/api/manychat/actions")
async def list_actions(page: int = 1, limit: int = 30):
    try:
        offset = (page - 1) * limit
        res = sb.table("completed_actions").select("*").order("created_at", desc=True).range(offset, offset + limit - 1).execute()
        return JSONResponse(content={"actions": res.data})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@router.post("/api/manychat/actions")
async def log_action(request: Request):
    """Manually log an action."""
    try:
        body = await request.json()
        res = sb.table("completed_actions").insert({
            "mc_id": body.get("mc_id", ""),
            "subscriber_name": body.get("subscriber_name", ""),
            "action_type": body.get("action_type", "manual"),
            "action_detail": body.get("action_detail", ""),
            "recommendation_id": body.get("recommendation_id")
        }).execute()
        return JSONResponse(content={"action": res.data[0] if res.data else None})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


# ================================================================
#  RECOMMENDATIONS: Complete / Dismiss
# ================================================================

@router.get("/api/manychat/recommendations")
async def list_recommendations(status: str = "pending"):
    try:
        res = sb.table("lead_recommendations") \
            .select("*") \
            .eq("status", status) \
            .order("priority", desc=False) \
            .order("created_at", desc=True) \
            .limit(30) \
            .execute()
        return JSONResponse(content={"recommendations": res.data})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@router.patch("/api/manychat/recommendations/{rec_id}/complete")
async def complete_recommendation(rec_id: int, request: Request):
    try:
        body = await request.json()
        sb.table("lead_recommendations").update({
            "status": "completed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "completed_note": body.get("note", "")
        }).eq("id", rec_id).execute()
        return JSONResponse(content={"success": True})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@router.patch("/api/manychat/recommendations/{rec_id}/dismiss")
async def dismiss_recommendation(rec_id: int):
    try:
        sb.table("lead_recommendations").update({
            "status": "dismissed",
            "completed_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", rec_id).execute()
        return JSONResponse(content={"success": True})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


# ================================================================
#  CLAUDE LEAD INTELLIGENCE
# ================================================================

@router.post("/api/manychat/analyze")
async def analyze_leads():
    """Have Claude analyze subscriber data and generate recommendations."""
    try:
        # Get all active subscribers sorted by heat score
        subs = sb.table("manychat_subscribers") \
            .select("*") \
            .eq("do_not_contact", False) \
            .order("heat_score", desc=True) \
            .limit(200) \
            .execute()

        # Get recent trigger activity
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        recent_triggers = sb.table("subscriber_triggers") \
            .select("*") \
            .gte("fired_at", week_ago) \
            .order("fired_at", desc=True) \
            .execute()

        # Get existing pending recs to avoid duplicates
        pending = sb.table("lead_recommendations") \
            .select("mc_id,category") \
            .eq("status", "pending") \
            .execute()
        existing_recs = set()
        for r in (pending.data or []):
            existing_recs.add(f"{r.get('mc_id')}_{r.get('category')}")

        # Get trigger definitions
        triggers = sb.table("manychat_triggers").select("keyword,label,description").execute()
        trigger_map = {t["keyword"]: t for t in (triggers.data or [])}

        # Build context for Claude
        subscriber_summaries = []
        for s in (subs.data or [])[:100]:  # top 100 by heat score
            tags = s.get("tags", "[]")
            if isinstance(tags, str):
                try:
                    tags = json.loads(tags)
                except:
                    tags = []

            summary = {
                "name": s.get("full_name", "Unknown"),
                "mc_id": s.get("mc_id"),
                "ig": s.get("ig_username", ""),
                "interest": s.get("interest_level"),
                "heat": s.get("heat_score"),
                "funnel": s.get("funnel_stage"),
                "triggers_fired": s.get("trigger_count", 0),
                "conversations": s.get("conversation_count", 0),
                "subscribed": s.get("subscribed_at", ""),
                "last_interaction": s.get("last_interaction", ""),
                "last_seen": s.get("last_seen", ""),
                "tags": [t.get("name", str(t)) if isinstance(t, dict) else str(t) for t in tags][:10],
                "email": bool(s.get("email")),
                "flodesk_synced": s.get("flodesk_synced", False)
            }
            subscriber_summaries.append(summary)

        recent_activity = []
        for t in (recent_triggers.data or [])[:50]:
            recent_activity.append({
                "mc_id": t.get("mc_id"),
                "keyword": t.get("keyword"),
                "when": t.get("fired_at"),
                "source": t.get("source")
            })

        now = datetime.now(timezone.utc)
        prompt = f"""You are Angela Schellenberg's lead intelligence analyst. Today is {now.strftime('%B %d, %Y')}.

Angela is a licensed trauma and grief therapist based in LA with 171K Instagram followers. She has a Healing with Horses Somatic Grief Retreat coming up April 30 to May 3, 2026 in Malibu with a few spots remaining.

Her ManyChat trigger keywords map to these products:
{json.dumps({k: v.get('label','') + ' - ' + v.get('description','') for k,v in trigger_map.items()}, indent=2)}

Here are her top subscribers (by engagement score):
{json.dumps(subscriber_summaries[:50], indent=2, default=str)}

Recent trigger activity (last 7 days):
{json.dumps(recent_activity, indent=2, default=str)}

Already pending recommendations (DO NOT duplicate these):
{json.dumps(list(existing_recs))}

Generate 5-8 specific, actionable lead recommendations. For each one, provide:
- mc_id (the subscriber's ManyChat ID, or null for general recommendations)
- subscriber_name
- priority (1 = most urgent, 5 = least)
- category (one of: follow_up, re_engage, retreat, stalled, high_intent, new_lead, flodesk_sync)
- title (short, punchy, like a notification)
- description (2-3 sentences explaining WHY this matters and WHAT the data shows)
- suggested_action (specific thing Angela should do)
- suggested_flow (a trigger keyword to send, if applicable, or empty string)

Prioritize:
1. Retreat leads (time-sensitive, limited spots)
2. High-intent subscribers showing buying signals across multiple triggers
3. Stalled conversations (started talking then went quiet)
4. New subscribers who triggered high-value keywords
5. People with email who haven't been added to Flodesk
6. Re-engagement for subscribers who haven't interacted in 30+ days

Do NOT recommend contacting anyone marked do_not_contact.
Do NOT duplicate categories for the same mc_id if they're already in pending recommendations.

Respond with ONLY a JSON array. No markdown, no backticks, no explanation. Just the array."""

        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json"
                },
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 4000,
                    "messages": [{"role": "user", "content": prompt}]
                }
            )
            r.raise_for_status()
            response = r.json()

        # Parse Claude's response
        text = response.get("content", [{}])[0].get("text", "[]")
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        recs = json.loads(text)

        # Insert recommendations
        inserted = 0
        for rec in recs:
            key = f"{rec.get('mc_id')}_{rec.get('category')}"
            if key in existing_recs:
                continue

            sb.table("lead_recommendations").insert({
                "mc_id": rec.get("mc_id"),
                "subscriber_name": rec.get("subscriber_name", ""),
                "priority": rec.get("priority", 5),
                "category": rec.get("category", "follow_up"),
                "title": rec.get("title", ""),
                "description": rec.get("description", ""),
                "suggested_action": rec.get("suggested_action", ""),
                "suggested_flow": rec.get("suggested_flow", ""),
                "expires_at": (now + timedelta(days=7)).isoformat()
            }).execute()
            inserted += 1

        return JSONResponse(content={"success": True, "recommendations": inserted, "total_analyzed": len(subscriber_summaries)})

    except json.JSONDecodeError as e:
        return JSONResponse(content={"error": f"Could not parse Claude response: {str(e)}"}, status_code=500)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


# ================================================================
#  FUNNEL STATS
# ================================================================

@router.get("/api/manychat/funnel")
async def funnel_stats():
    """Get funnel stage counts for visualization."""
    try:
        stages = ["subscriber", "engaged", "multi_trigger", "conversation", "booked"]
        result = {}
        for stage in stages:
            res = sb.table("manychat_subscribers") \
                .select("id,full_name,ig_username,heat_score,interest_level", count="exact") \
                .eq("funnel_stage", stage) \
                .eq("do_not_contact", False) \
                .order("heat_score", desc=True) \
                .limit(10) \
                .execute()
            result[stage] = {
                "count": res.count if hasattr(res, 'count') and res.count else len(res.data),
                "top": res.data
            }
        return JSONResponse(content={"funnel": result})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@router.get("/api/manychat/retreat-pipeline")
async def retreat_pipeline():
    """Get all subscribers who triggered retreat keywords."""
    try:
        # Find subscribers who triggered retreat keywords
        retreat_triggers = sb.table("subscriber_triggers") \
            .select("mc_id,keyword,fired_at") \
            .in_("keyword", ["MALIBURETREAT", "MALIBU RETREAT"]) \
            .order("fired_at", desc=True) \
            .execute()

        mc_ids = list(set(t["mc_id"] for t in (retreat_triggers.data or [])))
        if not mc_ids:
            return JSONResponse(content={"pipeline": [], "count": 0})

        subs = sb.table("manychat_subscribers") \
            .select("*") \
            .in_("mc_id", mc_ids) \
            .eq("do_not_contact", False) \
            .order("heat_score", desc=True) \
            .execute()

        # Get actions taken for these people
        actions = sb.table("completed_actions") \
            .select("mc_id,action_type,action_detail,created_at") \
            .in_("mc_id", mc_ids) \
            .order("created_at", desc=True) \
            .execute()

        action_map = {}
        for a in (actions.data or []):
            mid = a["mc_id"]
            if mid not in action_map:
                action_map[mid] = []
            action_map[mid].append(a)

        pipeline = []
        for s in (subs.data or []):
            s["retreat_actions"] = action_map.get(s["mc_id"], [])
            s["retreat_triggers"] = [t for t in retreat_triggers.data if t["mc_id"] == s["mc_id"]]
            pipeline.append(s)

        return JSONResponse(content={"pipeline": pipeline, "count": len(pipeline)})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)
