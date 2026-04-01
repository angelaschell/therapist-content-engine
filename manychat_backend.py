"""
Lead CRM Backend - uses DATABASE_URL (psycopg2) directly.
No Supabase REST client needed.

Add to main.py:
  from manychat_backend import router as manychat_router
  app.include_router(manychat_router)
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
    return psycopg2.connect(DATABASE_URL)

def clean(row):
    """Convert a RealDictRow to a JSON-safe dict."""
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
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(sql, params or ())
    result = [clean(r) for r in cur.fetchall()] if fetch else []
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

def query_one(sql, params=None):
    rows = query(sql, params)
    return rows[0] if rows else None

def insert_returning(sql, params=None):
    conn = get_conn()
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(sql, params or ())
    row = clean(cur.fetchone())
    cur.close()
    conn.close()
    return row


# ── Auto-setup tables ─────────────────────────────────────────
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS manychat_triggers (
  id BIGSERIAL PRIMARY KEY, keyword TEXT NOT NULL UNIQUE, label TEXT NOT NULL,
  description TEXT DEFAULT '', product_url TEXT DEFAULT '', is_active BOOLEAN DEFAULT true,
  sort_order INT DEFAULT 0, created_at TIMESTAMPTZ DEFAULT now(), updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS manychat_subscribers (
  id BIGSERIAL PRIMARY KEY, mc_id TEXT NOT NULL UNIQUE, first_name TEXT DEFAULT '',
  last_name TEXT DEFAULT '', full_name TEXT DEFAULT '', email TEXT DEFAULT '', phone TEXT DEFAULT '',
  ig_username TEXT DEFAULT '', profile_pic TEXT DEFAULT '', gender TEXT DEFAULT '', locale TEXT DEFAULT '',
  subscribed_at TIMESTAMPTZ, last_interaction TIMESTAMPTZ, last_seen TIMESTAMPTZ,
  ig_last_interaction TIMESTAMPTZ, opted_in_ig BOOLEAN DEFAULT false, opted_in_email BOOLEAN DEFAULT false,
  tags JSONB DEFAULT '[]'::jsonb, custom_fields JSONB DEFAULT '{}'::jsonb,
  trigger_count INT DEFAULT 0, conversation_count INT DEFAULT 0,
  interest_level TEXT DEFAULT 'new', heat_score INT DEFAULT 0, funnel_stage TEXT DEFAULT 'subscriber',
  flodesk_synced BOOLEAN DEFAULT false, do_not_contact BOOLEAN DEFAULT false,
  synced_at TIMESTAMPTZ DEFAULT now(), created_at TIMESTAMPTZ DEFAULT now(), updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS subscriber_triggers (
  id BIGSERIAL PRIMARY KEY, mc_id TEXT NOT NULL, keyword TEXT NOT NULL,
  source TEXT DEFAULT 'instagram', fired_at TIMESTAMPTZ DEFAULT now(),
  post_id TEXT DEFAULT '', created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS subscriber_conversations (
  id BIGSERIAL PRIMARY KEY, mc_id TEXT NOT NULL, direction TEXT NOT NULL,
  message_preview TEXT DEFAULT '', flow_name TEXT DEFAULT '', channel TEXT DEFAULT 'instagram',
  sent_at TIMESTAMPTZ DEFAULT now(), created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS lead_recommendations (
  id BIGSERIAL PRIMARY KEY, mc_id TEXT, subscriber_name TEXT DEFAULT '', priority INT DEFAULT 5,
  category TEXT DEFAULT 'follow_up', title TEXT NOT NULL, description TEXT NOT NULL,
  suggested_action TEXT DEFAULT '', suggested_flow TEXT DEFAULT '',
  data_points JSONB DEFAULT '{}'::jsonb, status TEXT DEFAULT 'pending',
  completed_at TIMESTAMPTZ, completed_note TEXT DEFAULT '',
  created_at TIMESTAMPTZ DEFAULT now(), expires_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS completed_actions (
  id BIGSERIAL PRIMARY KEY, mc_id TEXT NOT NULL, subscriber_name TEXT DEFAULT '',
  action_type TEXT NOT NULL, action_detail TEXT DEFAULT '', flow_id TEXT DEFAULT '',
  recommendation_id BIGINT, created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS subscriber_notes (
  id BIGSERIAL PRIMARY KEY, mc_id TEXT NOT NULL, note TEXT NOT NULL, created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS sync_log (
  id BIGSERIAL PRIMARY KEY, sync_type TEXT NOT NULL, records_synced INT DEFAULT 0,
  status TEXT DEFAULT 'success', error_message TEXT DEFAULT '',
  started_at TIMESTAMPTZ DEFAULT now(), completed_at TIMESTAMPTZ
);
INSERT INTO manychat_triggers (keyword, label, description, sort_order)
SELECT * FROM (VALUES
  ('HEAL','1:1 Session','Book a 1:1 therapy session with Angela.',1),
  ('UNTANGLE','1:1 Session','Alternate trigger for HEAL.',2),
  ('STEADY','1:1 Session','Alternate trigger for HEAL.',3),
  ('MALIBURETREAT','Healing with Horses Retreat','Malibu retreat. April 30 to May 3, 2026.',4),
  ('MALIBU RETREAT','Healing with Horses Retreat','Alternate trigger for Malibu retreat.',5),
  ('UNLEARN','Mother Hunger Course','Eight-week live Mother Hunger course.',6),
  ('WORTHY','Emotional Starter Kit','Free Emotional Starter Kit.',7),
  ('GRIEFRELIEF','Grief Relief Video Series','Grief Relief Video Series.',8),
  ('GRIEFTOOLS','Grief Relief Video Series','Alternate trigger for Grief Relief.',9),
  ('TOOLS','101 Tools','101 Tools digital product.',10),
  ('EQUINE','Equine Digital Product','Equine-assisted learning.',11),
  ('HORSEHEALING','Equine Digital Product','Alternate trigger for Equine.',12),
  ('MOM','Community Circle','Free community on Circle.',13),
  ('COMMUNITYCALL','Motherless Daughters Group','Hope Edelman Thursday group.',14),
  ('EMDR','EMDR Therapy','EMDR therapy information.',15),
  ('TAPPERS','Dharma Dr.','Dharma Dr. tappers.',16)
) AS v(keyword, label, description, sort_order)
WHERE NOT EXISTS (SELECT 1 FROM manychat_triggers LIMIT 1)
ON CONFLICT (keyword) DO NOTHING;
UPDATE manychat_subscribers SET
  first_name = regexp_replace(first_name, '\\{{[^}]+\\}}', '', 'g'),
  last_name = regexp_replace(last_name, '\\{{[^}]+\\}}', '', 'g'),
  full_name = regexp_replace(full_name, '\\{{[^}]+\\}}', '', 'g')
WHERE full_name LIKE '%{{%' OR first_name LIKE '%{{%' OR last_name LIKE '%{{%';
"""

try:
    execute(SCHEMA_SQL)
except Exception as e:
    print(f"Schema setup error: {e}")


# ── ManyChat API ──────────────────────────────────────────────
def mc_headers():
    return {"Authorization": f"Bearer {MC_KEY}", "Accept": "application/json", "Content-Type": "application/json"}

async def mc_get(path):
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{MC_API}{path}", headers=mc_headers())
        r.raise_for_status()
        return r.json()

async def mc_post(path, payload):
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{MC_API}{path}", headers=mc_headers(), json=payload)
        r.raise_for_status()
        return r.json()


# ================================================================
#  SERVE PAGE
# ================================================================
@router.get("/manychat", response_class=HTMLResponse)
async def manychat_page():
    try:
        with open("manychat.html", "r") as f:
            return HTMLResponse(content=f.read())
    except:
        return HTMLResponse(content="<h1>manychat.html not found</h1>", status_code=404)


# ================================================================
#  TRIGGER CRUD
# ================================================================
@router.get("/api/manychat/triggers")
async def list_triggers():
    try:
        rows = query("SELECT * FROM manychat_triggers ORDER BY sort_order ASC")
        return JSONResponse(content={"triggers": [dict(r) for r in rows]}, media_type="application/json")
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@router.post("/api/manychat/triggers")
async def create_trigger(request: Request):
    try:
        body = await request.json()
        kw = body.get("keyword", "").strip().upper()
        label = body.get("label", "").strip()
        desc = body.get("description", "").strip()
        if not kw or not label:
            return JSONResponse(content={"error": "Keyword and label required."}, status_code=400)
        mx = query_one("SELECT COALESCE(MAX(sort_order),0)+1 as n FROM manychat_triggers")
        row = insert_returning(
            "INSERT INTO manychat_triggers (keyword,label,description,sort_order) VALUES (%s,%s,%s,%s) RETURNING *",
            (kw, label, desc, mx["n"] if mx else 1)
        )
        return JSONResponse(content={"trigger": dict(row) if row else None})
    except Exception as e:
        if "unique" in str(e).lower() or "duplicate" in str(e).lower():
            return JSONResponse(content={"error": f"'{kw}' already exists."}, status_code=409)
        return JSONResponse(content={"error": str(e)}, status_code=500)

@router.put("/api/manychat/triggers/{tid}")
async def update_trigger(tid: int, request: Request):
    try:
        body = await request.json()
        sets, vals = [], []
        for k in ["keyword", "label", "description", "product_url"]:
            if k in body:
                sets.append(f"{k}=%s")
                vals.append(body[k].strip().upper() if k == "keyword" else body[k].strip())
        if "is_active" in body:
            sets.append("is_active=%s"); vals.append(body["is_active"])
        if not sets:
            return JSONResponse(content={"error": "Nothing to update."}, status_code=400)
        sets.append("updated_at=now()")
        vals.append(tid)
        row = insert_returning(f"UPDATE manychat_triggers SET {','.join(sets)} WHERE id=%s RETURNING *", vals)
        return JSONResponse(content={"trigger": dict(row) if row else None})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@router.delete("/api/manychat/triggers/{tid}")
async def delete_trigger(tid: int):
    try:
        execute("DELETE FROM manychat_triggers WHERE id=%s", (tid,))
        return JSONResponse(content={"deleted": True})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@router.patch("/api/manychat/triggers/{tid}/toggle")
async def toggle_trigger(tid: int):
    try:
        row = insert_returning("UPDATE manychat_triggers SET is_active=NOT is_active, updated_at=now() WHERE id=%s RETURNING *", (tid,))
        return JSONResponse(content={"trigger": dict(row) if row else None})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


# ================================================================
#  MANYCHAT SYNC
# ================================================================
def clean_template_vars(text):
    """Strip ManyChat {{template_variables}} from names and other fields."""
    if not text:
        return ""
    cleaned = re.sub(r'\{\{[^}]+\}\}', '', text).strip()
    # If nothing left after stripping, return empty
    return cleaned if cleaned else ""


def calc_interest(trigger_count, conv_count, tags, triggers_fired):
    kws = [t.upper() if isinstance(t, str) else t.get("keyword","").upper() for t in triggers_fired]
    high = any(k in kws for k in ["MALIBURETREAT","MALIBU RETREAT","HEAL","UNTANGLE","STEADY","EMDR"])
    multi = len(set(kws)) >= 3
    if high and (conv_count >= 3 or multi): return "vip", 90
    if high or (trigger_count >= 3 and conv_count >= 2): return "hot", 70
    if trigger_count >= 2 or conv_count >= 2: return "warm", 50
    if trigger_count >= 1: return "cold", 25
    return "new", 5

def calc_funnel(trigger_count, conv_count, tags):
    tnames = [t.get("name","").lower() if isinstance(t,dict) else str(t).lower() for t in tags]
    if any("booked" in t or "purchased" in t or "client" in t for t in tnames): return "booked"
    if conv_count >= 3: return "conversation"
    if trigger_count >= 3: return "multi_trigger"
    if trigger_count >= 1 or conv_count >= 1: return "engaged"
    return "subscriber"

@router.post("/api/manychat/sync")
async def sync_data():
    try:
        log = insert_returning("INSERT INTO sync_log (sync_type,status) VALUES ('subscribers','running') RETURNING *")
        log_id = log["id"] if log else None
        synced = 0
        flows, tags = [], []
        try:
            d = await mc_get("/fb/page/getFlows")
            raw = d.get("data", {})
            flows = raw.get("flows", []) if isinstance(raw, dict) else raw if isinstance(raw, list) else []
            synced += len(flows)
        except: pass
        try:
            d = await mc_get("/fb/page/getTags")
            raw = d.get("data", [])
            tags = raw if isinstance(raw, list) else []
            synced += len(tags)
        except: pass
        try:
            d = await mc_get("/fb/page/getCustomFields")
            raw = d.get("data", [])
            fields = raw if isinstance(raw, list) else []
        except: fields = []
        info = json.dumps({"flows": len(flows), "tags": len(tags), "fields": len(fields)})
        if log_id:
            execute("UPDATE sync_log SET records_synced=%s, status='success', error_message=%s, completed_at=now() WHERE id=%s",
                    (synced, info, log_id))
        return JSONResponse(content={
            "success": True, "synced": synced, "flows": len(flows), "tags": len(tags),
            "flow_list": [{"name":f.get("name",""),"ns":f.get("ns",""),"id":f.get("id","")} for f in flows[:50]],
            "tag_list": [{"name":t.get("name",""),"id":t.get("id","")} for t in tags[:50]]
        })
    except Exception as e:
        if log_id:
            execute("UPDATE sync_log SET status='error', error_message=%s, completed_at=now() WHERE id=%s", (str(e), log_id))
        return JSONResponse(content={"error": str(e)}, status_code=500)

@router.get("/api/manychat/sync/status")
async def sync_status():
    try:
        row = query_one("SELECT * FROM sync_log ORDER BY started_at DESC LIMIT 1")
        return JSONResponse(content={"last_sync": dict(row) if row else None})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@router.get("/api/manychat/flows")
async def get_flows():
    try:
        d = await mc_get("/fb/page/getFlows")
        raw = d.get("data", {})
        flows = raw.get("flows", []) if isinstance(raw, dict) else raw if isinstance(raw, list) else []
        return JSONResponse(content={"flows": flows})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@router.get("/api/manychat/tags")
async def get_tags():
    try:
        d = await mc_get("/fb/page/getTags")
        return JSONResponse(content={"tags": d.get("data", [])})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@router.get("/api/manychat/setup")
async def setup_db():
    try:
        execute(SCHEMA_SQL)
        return JSONResponse(content={"success": True, "message": "Tables created."})
    except Exception as e:
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)


# ================================================================
#  WEBHOOK: ManyChat pushes subscriber data here automatically
# ================================================================
@router.post("/api/manychat/webhook")
async def manychat_webhook(request: Request):
    """
    ManyChat sends subscriber data here via External Request.
    Auto-enriches with full profile from ManyChat API.
    """
    try:
        body = await request.json()

        mc_id = str(body.get("id", "") or body.get("subscriber_id", "") or body.get("mc_id", "") or body.get("user_id", ""))
        first_name = body.get("first_name", "") or ""
        last_name = body.get("last_name", "") or ""
        full_name = body.get("full_name", "") or body.get("name", "") or f"{first_name} {last_name}".strip()
        email = body.get("email", "") or ""
        phone = body.get("phone", "") or ""
        ig_username = body.get("ig_username", "") or body.get("instagram_username", "") or ""
        profile_pic = body.get("profile_pic", "") or body.get("profile_picture", "") or ""
        gender = body.get("gender", "") or ""
        keyword = (body.get("keyword", "") or body.get("trigger", "") or body.get("trigger_keyword", "") or "").strip().upper()
        source = body.get("source", "instagram") or "instagram"
        tags = body.get("tags", [])
        custom_fields = body.get("custom_fields", {})

        if not mc_id:
            return JSONResponse(content={"error": "No subscriber ID provided."}, status_code=400)

        # ── ENRICHMENT: Pull full profile from ManyChat API ──
        try:
            full_profile = await mc_get(f"/fb/subscriber/getInfo?subscriber_id={mc_id}")
            sub_data = full_profile.get("data", {})
            if sub_data:
                # Fill in any missing fields from the API
                if not email and sub_data.get("email"):
                    email = sub_data["email"]
                if not phone and sub_data.get("phone"):
                    phone = sub_data["phone"]
                if not ig_username:
                    ig_username = sub_data.get("ig_username", "") or sub_data.get("instagram_username", "") or ""
                if not first_name and sub_data.get("first_name"):
                    first_name = sub_data["first_name"]
                if not last_name and sub_data.get("last_name"):
                    last_name = sub_data["last_name"]
                if not profile_pic and sub_data.get("profile_pic"):
                    profile_pic = sub_data["profile_pic"]
                if not gender and sub_data.get("gender"):
                    gender = sub_data["gender"]
                # Always grab tags and custom fields from API (more complete)
                api_tags = sub_data.get("tags", [])
                if api_tags:
                    tags = api_tags
                api_cf = sub_data.get("custom_fields", {})
                if api_cf:
                    custom_fields = api_cf
                # Update full_name if we got better data
                full_name = f"{first_name} {last_name}".strip() or full_name
        except:
            pass  # API enrichment is best-effort, webhook data is fallback

        # Clean any {{template_variables}} from names
        first_name = clean_template_vars(first_name)
        last_name = clean_template_vars(last_name)
        full_name = clean_template_vars(full_name)
        # If name is still empty after cleaning, try ig_username
        if not full_name and ig_username:
            full_name = ig_username

        # Get existing counts
        trig_res = query("SELECT keyword FROM subscriber_triggers WHERE mc_id=%s", (mc_id,))
        trigger_count = len(trig_res) + (1 if keyword else 0)
        conv_res = query("SELECT id FROM subscriber_conversations WHERE mc_id=%s", (mc_id,))
        conversation_count = len(conv_res)

        all_triggers = trig_res + ([{"keyword": keyword}] if keyword else [])
        il, hs = calc_interest(trigger_count, conversation_count, tags, all_triggers)
        fs = calc_funnel(trigger_count, conversation_count, tags)

        # Upsert subscriber
        execute("""INSERT INTO manychat_subscribers
            (mc_id, first_name, last_name, full_name, email, phone, ig_username, profile_pic, gender,
             tags, custom_fields, trigger_count, conversation_count,
             interest_level, heat_score, funnel_stage, opted_in_email, synced_at, last_interaction)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now(),now())
            ON CONFLICT (mc_id) DO UPDATE SET
             first_name=COALESCE(NULLIF(EXCLUDED.first_name,''), manychat_subscribers.first_name),
             last_name=COALESCE(NULLIF(EXCLUDED.last_name,''), manychat_subscribers.last_name),
             full_name=COALESCE(NULLIF(EXCLUDED.full_name,''), manychat_subscribers.full_name),
             email=COALESCE(NULLIF(EXCLUDED.email,''), manychat_subscribers.email),
             phone=COALESCE(NULLIF(EXCLUDED.phone,''), manychat_subscribers.phone),
             ig_username=COALESCE(NULLIF(EXCLUDED.ig_username,''), manychat_subscribers.ig_username),
             profile_pic=COALESCE(NULLIF(EXCLUDED.profile_pic,''), manychat_subscribers.profile_pic),
             tags=EXCLUDED.tags,
             custom_fields=EXCLUDED.custom_fields,
             trigger_count=EXCLUDED.trigger_count,
             conversation_count=EXCLUDED.conversation_count,
             interest_level=EXCLUDED.interest_level,
             heat_score=EXCLUDED.heat_score,
             funnel_stage=EXCLUDED.funnel_stage,
             opted_in_email=EXCLUDED.opted_in_email,
             last_interaction=now(),
             synced_at=now(),
             updated_at=now()""",
            (mc_id, first_name, last_name, full_name, email, phone, ig_username, profile_pic, gender,
             json.dumps(tags) if isinstance(tags, list) else "[]",
             json.dumps(custom_fields) if isinstance(custom_fields, (dict, list)) else "{}",
             trigger_count, conversation_count, il, hs, fs, bool(email)))

        # Log the trigger
        if keyword:
            execute("INSERT INTO subscriber_triggers (mc_id, keyword, source, fired_at) VALUES (%s,%s,%s,now())",
                    (mc_id, keyword, source))

        return JSONResponse(content={
            "success": True,
            "subscriber": mc_id,
            "name": full_name,
            "keyword": keyword,
            "interest_level": il,
            "heat_score": hs,
            "email": email or None,
            "ig_username": ig_username or None,
            "enriched": True
        })

    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@router.get("/api/manychat/webhook")
async def webhook_status():
    """Quick check that the webhook endpoint is alive."""
    return JSONResponse(content={"status": "ok", "message": "ManyChat webhook is active. Send POST requests here."})


# ================================================================
#  SUBSCRIBER LOOKUP
# ================================================================
@router.post("/api/manychat/lookup")
async def lookup_subscriber(request: Request):
    try:
        body = await request.json()
        mc_id = body.get("mc_id",""); email = body.get("email",""); phone = body.get("phone","")
        sub = None
        if mc_id:
            d = await mc_get(f"/fb/subscriber/getInfo?subscriber_id={mc_id}")
            sub = d.get("data",{})
        elif email:
            d = await mc_get(f"/fb/subscriber/findBySystemField?email={email}")
            sub = d.get("data",{})
        elif phone:
            d = await mc_get(f"/fb/subscriber/findBySystemField?phone={phone}")
            sub = d.get("data",{})
        else:
            return JSONResponse(content={"error":"Provide mc_id, email, or phone."}, status_code=400)
        if not sub or not sub.get("id"):
            return JSONResponse(content={"error":"Not found."}, status_code=404)

        sid = str(sub["id"])
        stags = sub.get("tags",[]); scf = sub.get("custom_fields",{})
        trigs = query("SELECT keyword FROM subscriber_triggers WHERE mc_id=%s", (sid,))
        tc = len(trigs)
        convs = query("SELECT id FROM subscriber_conversations WHERE mc_id=%s", (sid,))
        cc = len(convs)
        il, hs = calc_interest(tc, cc, stags, trigs)
        fs = calc_funnel(tc, cc, stags)
        fn = f"{sub.get('first_name','')} {sub.get('last_name','')}".strip()

        execute("""INSERT INTO manychat_subscribers
            (mc_id,first_name,last_name,full_name,email,phone,ig_username,profile_pic,gender,locale,
             subscribed_at,last_interaction,last_seen,tags,custom_fields,trigger_count,conversation_count,
             interest_level,heat_score,funnel_stage,synced_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
            ON CONFLICT (mc_id) DO UPDATE SET
             first_name=EXCLUDED.first_name, last_name=EXCLUDED.last_name, full_name=EXCLUDED.full_name,
             email=EXCLUDED.email, phone=EXCLUDED.phone, ig_username=EXCLUDED.ig_username,
             tags=EXCLUDED.tags, custom_fields=EXCLUDED.custom_fields,
             trigger_count=EXCLUDED.trigger_count, conversation_count=EXCLUDED.conversation_count,
             interest_level=EXCLUDED.interest_level, heat_score=EXCLUDED.heat_score,
             funnel_stage=EXCLUDED.funnel_stage, synced_at=now(), updated_at=now()""",
            (sid, sub.get("first_name",""), sub.get("last_name",""), fn,
             sub.get("email","") or "", sub.get("phone","") or "",
             sub.get("ig_username","") or sub.get("instagram_username","") or "",
             sub.get("profile_pic","") or "", sub.get("gender","") or "", sub.get("locale","") or "",
             sub.get("subscribed",sub.get("created_at")), sub.get("last_interaction"),
             sub.get("last_seen"), json.dumps(stags), json.dumps(scf) if isinstance(scf,(dict,list)) else "{}",
             tc, cc, il, hs, fs))

        return JSONResponse(content={"success":True,"subscriber":{"mc_id":sid,"full_name":fn,
            "ig_username":sub.get("ig_username",""),"email":sub.get("email",""),
            "interest_level":il,"heat_score":hs,"trigger_count":tc,"conversation_count":cc}})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


# ================================================================
#  SUBSCRIBERS LIST
# ================================================================
@router.get("/api/manychat/subscribers")
async def list_subscribers(page:int=1, limit:int=50, interest:str="", funnel:str="", search:str="", sort:str="heat_score"):
    try:
        where, params = ["do_not_contact=false"], []
        if interest: where.append("interest_level=%s"); params.append(interest)
        if funnel: where.append("funnel_stage=%s"); params.append(funnel)
        if search: where.append("(full_name ILIKE %s OR ig_username ILIKE %s OR email ILIKE %s)"); s=f"%{search}%"; params+=[s,s,s]
        w = " AND ".join(where)
        order = {"heat_score":"heat_score DESC","recent":"last_interaction DESC NULLS LAST","triggers":"trigger_count DESC","subscribed":"subscribed_at DESC NULLS LAST"}.get(sort,"heat_score DESC")
        offset = (page-1)*limit
        params2 = params + [limit, offset]
        rows = query(f"SELECT * FROM manychat_subscribers WHERE {w} ORDER BY {order} LIMIT %s OFFSET %s", params2)
        cnt = query_one(f"SELECT COUNT(*) as total FROM manychat_subscribers WHERE {w}", params)
        total = cnt["total"] if cnt else 0
        return JSONResponse(content={"subscribers":[dict(r) for r in rows],"total":total,"page":page,"pages":(total+limit-1)//limit})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@router.get("/api/manychat/subscribers/stats")
async def sub_stats():
    try:
        rows = query("SELECT interest_level, COUNT(*) as cnt FROM manychat_subscribers WHERE do_not_contact=false GROUP BY interest_level")
        bi = {r["interest_level"]:r["cnt"] for r in rows}
        total = sum(bi.values())
        return JSONResponse(content={"total":total,"by_interest":bi})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@router.get("/api/manychat/subscribers/{mc_id}")
async def get_subscriber_detail(mc_id: str):
    """Full subscriber detail with timeline."""
    try:
        sub = query_one("SELECT * FROM manychat_subscribers WHERE mc_id=%s", (mc_id,))
        if not sub:
            return JSONResponse(content={"error": "Subscriber not found."}, status_code=404)
        triggers = query("SELECT * FROM subscriber_triggers WHERE mc_id=%s ORDER BY fired_at DESC", (mc_id,))
        convos = query("SELECT * FROM subscriber_conversations WHERE mc_id=%s ORDER BY sent_at DESC LIMIT 50", (mc_id,))
        notes = query("SELECT * FROM subscriber_notes WHERE mc_id=%s ORDER BY created_at DESC", (mc_id,))
        actions = query("SELECT * FROM completed_actions WHERE mc_id=%s ORDER BY created_at DESC LIMIT 20", (mc_id,))
        return JSONResponse(content={
            "subscriber": sub,
            "triggers": triggers,
            "conversations": convos,
            "notes": notes,
            "actions": actions
        })
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@router.post("/api/manychat/subscribers/{mc_id}/analyze")
async def analyze_subscriber(mc_id: str):
    """Claude analyzes a single subscriber and recommends which triggers to send."""
    try:
        sub = query_one("SELECT * FROM manychat_subscribers WHERE mc_id=%s", (mc_id,))
        if not sub:
            return JSONResponse(content={"error": "Subscriber not found."}, status_code=404)

        triggers_fired = query("SELECT keyword, fired_at, source FROM subscriber_triggers WHERE mc_id=%s ORDER BY fired_at DESC", (mc_id,))
        notes = query("SELECT note, created_at FROM subscriber_notes WHERE mc_id=%s ORDER BY created_at DESC", (mc_id,))
        actions = query("SELECT action_type, action_detail, created_at FROM completed_actions WHERE mc_id=%s ORDER BY created_at DESC LIMIT 10", (mc_id,))
        available_triggers = query("SELECT keyword, label, description FROM manychat_triggers WHERE is_active=true ORDER BY sort_order")

        tags = sub.get("tags", "[]")
        if isinstance(tags, str):
            try: tags = json.loads(tags)
            except: tags = []

        now = datetime.now(timezone.utc)
        prompt = f"""You are Angela Schellenberg's lead intelligence analyst. Today is {now.strftime('%B %d, %Y')}.

Angela is a licensed trauma and grief therapist in LA with 171K IG followers. Her Healing with Horses Somatic Grief Retreat runs April 30 to May 3, 2026, with a few spots remaining.

Here is one subscriber's full profile:

Name: {sub.get('full_name','Unknown')}
Instagram: @{sub.get('ig_username','')}
Interest Level: {sub.get('interest_level','new')} (Heat Score: {sub.get('heat_score',0)})
Funnel Stage: {sub.get('funnel_stage','subscriber')}
Subscribed: {sub.get('subscribed_at','')}
Last Interaction: {sub.get('last_interaction','')}
Email: {'Yes' if sub.get('email') else 'No'}
Tags: {json.dumps([t.get('name',str(t)) if isinstance(t,dict) else str(t) for t in tags])}

Triggers they've fired:
{json.dumps([{{'keyword':t['keyword'],'when':str(t['fired_at']),'source':t.get('source','')}} for t in triggers_fired], indent=2)}

Actions taken on this person:
{json.dumps([{{'type':a['action_type'],'detail':a['action_detail'],'when':str(a['created_at'])}} for a in actions], indent=2)}

Notes about this person:
{json.dumps([{{'note':n['note'],'when':str(n['created_at'])}} for n in notes], indent=2)}

Available triggers Angela can send (these are her actual products):
{json.dumps([{{'keyword':t['keyword'],'label':t['label'],'description':t['description']}} for t in available_triggers], indent=2)}

Analyze this subscriber and respond with ONLY a JSON object (no markdown, no backticks):
{{
  "overview": "2-3 sentence analysis of where this person is in their journey and what they need next. Be specific to their behavior, not generic.",
  "recommended_triggers": [
    {{
      "keyword": "THE_KEYWORD",
      "reason": "One sentence explaining why this specific trigger fits this person right now."
    }}
  ]
}}

Rules:
- Only recommend triggers from the available list above.
- Rank by relevance. Most relevant first.
- Recommend 2-4 triggers max.
- Do NOT recommend triggers they've already been sent via actions.
- Do NOT recommend triggers they've already fired themselves (they already have that product).
- If the retreat is relevant, prioritize it (time-sensitive).
- Be specific. Reference their actual behavior, not generic advice."""

        async with httpx.AsyncClient(timeout=45) as c:
            r = await c.post("https://api.anthropic.com/v1/messages", headers={
                "x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"
            }, json={"model": "claude-sonnet-4-20250514", "max_tokens": 1500, "messages": [{"role": "user", "content": prompt}]})
            r.raise_for_status()
            resp = r.json()

        text = resp.get("content", [{}])[0].get("text", "{}").strip()
        if text.startswith("```"): text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        analysis = json.loads(text)

        return JSONResponse(content={"success": True, "analysis": analysis})
    except json.JSONDecodeError:
        return JSONResponse(content={"error": "Could not parse analysis."}, status_code=500)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


# ================================================================
#  NOTES
# ================================================================
@router.post("/api/manychat/subscribers/{mc_id}/notes")
async def add_note(mc_id:str, request:Request):
    try:
        body = await request.json(); note = body.get("note","").strip()
        if not note: return JSONResponse(content={"error":"Note required."}, status_code=400)
        row = insert_returning("INSERT INTO subscriber_notes (mc_id,note) VALUES (%s,%s) RETURNING *", (mc_id, note))
        return JSONResponse(content={"note":dict(row) if row else None})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@router.delete("/api/manychat/notes/{nid}")
async def del_note(nid:int):
    try:
        execute("DELETE FROM subscriber_notes WHERE id=%s",(nid,))
        return JSONResponse(content={"deleted":True})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


# ================================================================
#  DO NOT CONTACT
# ================================================================
@router.patch("/api/manychat/subscribers/{mc_id}/dnc")
async def toggle_dnc(mc_id:str):
    try:
        row = insert_returning("UPDATE manychat_subscribers SET do_not_contact=NOT do_not_contact, updated_at=now() WHERE mc_id=%s RETURNING do_not_contact", (mc_id,))
        return JSONResponse(content={"do_not_contact": row["do_not_contact"] if row else None})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


# ================================================================
#  ACTIONS
# ================================================================
@router.post("/api/manychat/send-flow")
async def send_flow(request:Request):
    try:
        body = await request.json()
        mc_id = body.get("mc_id",""); flow_ns = body.get("flow_ns","")
        if not mc_id or not flow_ns: return JSONResponse(content={"error":"mc_id and flow_ns required."}, status_code=400)
        result = await mc_post("/fb/sending/sendFlow", {"subscriber_id":int(mc_id),"flow_ns":flow_ns})
        sub = query_one("SELECT full_name FROM manychat_subscribers WHERE mc_id=%s",(mc_id,))
        name = sub["full_name"] if sub else "Unknown"
        execute("INSERT INTO completed_actions (mc_id,subscriber_name,action_type,action_detail,flow_id,recommendation_id) VALUES (%s,%s,%s,%s,%s,%s)",
                (mc_id, name, "sent_flow", f"Sent flow {flow_ns}", flow_ns, body.get("recommendation_id")))
        return JSONResponse(content={"success":True,"result":result})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@router.post("/api/manychat/smart-send")
async def smart_send(request:Request):
    """Send a flow to a subscriber by keyword. Automatically finds the right flow."""
    try:
        body = await request.json()
        mc_id = body.get("mc_id","")
        keyword = body.get("keyword","").strip().upper()
        rec_id = body.get("recommendation_id")

        if not mc_id:
            return JSONResponse(content={"error":"No subscriber to send to (mc_id missing)."}, status_code=400)
        if not keyword:
            return JSONResponse(content={"error":"No keyword specified."}, status_code=400)

        # Fetch all flows from ManyChat
        d = await mc_get("/fb/page/getFlows")
        raw = d.get("data", {})
        flows = raw.get("flows", []) if isinstance(raw, dict) else raw if isinstance(raw, list) else []

        # Find matching flow by keyword in the name
        matched_flow = None
        keyword_lower = keyword.lower()
        for f in flows:
            name = (f.get("name","") or "").lower()
            # Match patterns like "| WORTHY", "| HEAL", "MALIBURETREAT" in flow name
            if keyword_lower in name or f"| {keyword_lower}" in name:
                matched_flow = f
                break

        if not matched_flow:
            return JSONResponse(content={"error": f"Could not find a ManyChat flow matching '{keyword}'. Check your flow names."}, status_code=404)

        flow_ns = matched_flow.get("ns","")
        flow_name = matched_flow.get("name","")

        if not flow_ns:
            return JSONResponse(content={"error": f"Flow '{flow_name}' has no namespace ID."}, status_code=400)

        # Send it
        result = await mc_post("/fb/sending/sendFlow", {"subscriber_id":int(mc_id),"flow_ns":flow_ns})

        # Log the action
        sub = query_one("SELECT full_name FROM manychat_subscribers WHERE mc_id=%s",(mc_id,))
        name = sub["full_name"] if sub else "Unknown"
        execute("INSERT INTO completed_actions (mc_id,subscriber_name,action_type,action_detail,flow_id,recommendation_id) VALUES (%s,%s,%s,%s,%s,%s)",
                (mc_id, name, "sent_flow", f"Sent {keyword} flow ({flow_name}) to {name}", flow_ns, rec_id))

        # Mark recommendation as completed if provided
        if rec_id:
            execute("UPDATE lead_recommendations SET status='completed', completed_at=now(), completed_note=%s WHERE id=%s",
                    (f"Sent {keyword} flow to {name}", rec_id))

        return JSONResponse(content={
            "success": True,
            "sent_to": name,
            "mc_id": mc_id,
            "flow": flow_name,
            "keyword": keyword
        })
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@router.post("/api/manychat/send-dm")
async def send_personal_dm(request: Request):
    """Send a custom personal DM to a subscriber via ManyChat sendContent API."""
    try:
        body = await request.json()
        mc_id = body.get("mc_id", "")
        message = body.get("message", "").strip()

        if not mc_id:
            return JSONResponse(content={"error": "No subscriber specified."}, status_code=400)
        if not message:
            return JSONResponse(content={"error": "Message cannot be empty."}, status_code=400)
        if not MC_KEY:
            return JSONResponse(content={"error": "MANYCHAT_API_KEY not configured."}, status_code=500)

        # No message_tag — Instagram DMs don't use Facebook Messenger tags
        payload = {
            "subscriber_id": int(mc_id),
            "data": {
                "version": "v2",
                "content": {
                    "messages": [
                        {
                            "type": "text",
                            "text": message
                        }
                    ]
                }
            }
        }

        # Use raw httpx so we can read the error response
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"{MC_API}/fb/sending/sendContent",
                headers=mc_headers(),
                json=payload
            )
            result = r.json()

            if r.status_code != 200 or result.get("status") == "error":
                err = result.get("message", "") or result.get("error", "") or f"ManyChat returned {r.status_code}"
                return JSONResponse(content={"error": f"ManyChat: {err}"}, status_code=400)

        sub = query_one("SELECT full_name FROM manychat_subscribers WHERE mc_id=%s", (mc_id,))
        name = clean_template_vars(sub["full_name"]) if sub else "Unknown"

        execute(
            "INSERT INTO subscriber_conversations (mc_id, direction, message_preview, channel, sent_at) VALUES (%s, 'outbound', %s, 'instagram', now())",
            (mc_id, message[:200])
        )

        execute(
            "UPDATE manychat_subscribers SET conversation_count = conversation_count + 1, last_interaction = now(), updated_at = now() WHERE mc_id = %s",
            (mc_id,)
        )

        execute(
            "INSERT INTO completed_actions (mc_id, subscriber_name, action_type, action_detail) VALUES (%s, %s, 'manual_message', %s)",
            (mc_id, name, "Sent DM: " + message[:100])
        )

        return JSONResponse(content={
            "success": True,
            "sent_to": name,
            "mc_id": mc_id,
            "message_preview": message[:100]
        })

    except Exception as e:
        error_msg = str(e)
        if hasattr(e, 'response'):
            try:
                error_msg = e.response.json().get("message", error_msg)
            except:
                pass
        return JSONResponse(content={"error": error_msg}, status_code=500)

@router.post("/api/manychat/add-tag")
async def add_tag(request:Request):
    try:
        body = await request.json()
        mc_id = body.get("mc_id",""); tag_id = body.get("tag_id","")
        if not mc_id or not tag_id: return JSONResponse(content={"error":"mc_id and tag_id required."}, status_code=400)
        result = await mc_post("/fb/subscriber/addTag", {"subscriber_id":int(mc_id),"tag_id":int(tag_id)})
        sub = query_one("SELECT full_name FROM manychat_subscribers WHERE mc_id=%s",(mc_id,))
        name = sub["full_name"] if sub else "Unknown"
        execute("INSERT INTO completed_actions (mc_id,subscriber_name,action_type,action_detail) VALUES (%s,%s,%s,%s)",
                (mc_id, name, "added_tag", f"Added tag {tag_id}"))
        return JSONResponse(content={"success":True,"result":result})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@router.get("/api/manychat/actions")
async def list_actions(page:int=1, limit:int=30):
    try:
        offset = (page-1)*limit
        rows = query("SELECT * FROM completed_actions ORDER BY created_at DESC LIMIT %s OFFSET %s",(limit,offset))
        return JSONResponse(content={"actions":[dict(r) for r in rows]})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@router.post("/api/manychat/actions")
async def log_action(request:Request):
    try:
        body = await request.json()
        row = insert_returning("INSERT INTO completed_actions (mc_id,subscriber_name,action_type,action_detail,recommendation_id) VALUES (%s,%s,%s,%s,%s) RETURNING *",
            (body.get("mc_id",""), body.get("subscriber_name",""), body.get("action_type","manual"), body.get("action_detail",""), body.get("recommendation_id")))
        return JSONResponse(content={"action":dict(row) if row else None})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


# ================================================================
#  RECOMMENDATIONS
# ================================================================
@router.get("/api/manychat/recommendations")
async def list_recs(status:str="pending"):
    try:
        rows = query("SELECT * FROM lead_recommendations WHERE status=%s ORDER BY priority ASC, created_at DESC LIMIT 30",(status,))
        return JSONResponse(content={"recommendations":[dict(r) for r in rows]})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@router.patch("/api/manychat/recommendations/{rid}/complete")
async def complete_rec(rid:int, request:Request):
    try:
        body = await request.json()
        execute("UPDATE lead_recommendations SET status='completed', completed_at=now(), completed_note=%s WHERE id=%s",(body.get("note",""), rid))
        return JSONResponse(content={"success":True})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@router.patch("/api/manychat/recommendations/{rid}/dismiss")
async def dismiss_rec(rid:int):
    try:
        execute("UPDATE lead_recommendations SET status='dismissed', completed_at=now() WHERE id=%s",(rid,))
        return JSONResponse(content={"success":True})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


# ================================================================
#  CLAUDE LEAD INTELLIGENCE
# ================================================================
@router.post("/api/manychat/analyze")
async def analyze_leads():
    try:
        subs = query("SELECT * FROM manychat_subscribers WHERE do_not_contact=false ORDER BY heat_score DESC LIMIT 100")
        pending = query("SELECT mc_id, category FROM lead_recommendations WHERE status='pending'")
        existing = set(f"{r['mc_id']}_{r['category']}" for r in pending)
        trigs = query("SELECT keyword, label, description FROM manychat_triggers")
        tmap = {t["keyword"]: t for t in trigs}

        summaries = []
        for s in subs[:50]:
            tags = s.get("tags","[]")
            if isinstance(tags, str):
                try: tags = json.loads(tags)
                except: tags = []

            # Get ACTUAL trigger keywords this person has fired (not just count)
            sub_triggers = query("SELECT keyword, fired_at FROM subscriber_triggers WHERE mc_id=%s ORDER BY fired_at DESC", (s["mc_id"],))
            trigger_keywords = list(set(t["keyword"] for t in sub_triggers))
            latest_trigger = sub_triggers[0] if sub_triggers else None

            summaries.append({
                "name": s.get("full_name","Unknown"),
                "mc_id": s.get("mc_id"),
                "ig": s.get("ig_username",""),
                "interest": s.get("interest_level"),
                "heat": s.get("heat_score"),
                "funnel": s.get("funnel_stage"),
                "triggers_they_have": trigger_keywords,
                "latest_trigger": {"keyword": latest_trigger["keyword"], "when": str(latest_trigger["fired_at"])} if latest_trigger else None,
                "conversations": s.get("conversation_count",0),
                "subscribed": str(s.get("subscribed_at","")),
                "last_interaction": str(s.get("last_interaction","")),
                "tags": [t.get("name",str(t)) if isinstance(t,dict) else str(t) for t in tags][:10],
                "email": bool(s.get("email")),
                "flodesk_synced": s.get("flodesk_synced",False)
            })

        now = datetime.now(timezone.utc)
        prompt = f"""You are Angela Schellenberg's lead intelligence analyst. Today is {now.strftime('%B %d, %Y')}.
Angela is a licensed trauma therapist in LA with 171K IG followers. Healing with Horses retreat April 30-May 3, 2026, few spots left.

AVAILABLE TRIGGERS (Angela's products):
{json.dumps({k:v.get('label','')+' - '+v.get('description','') for k,v in tmap.items()}, indent=2)}

SUBSCRIBER DATA:
{json.dumps(summaries, indent=2, default=str)}

Already pending (skip these mc_id + category combos): {json.dumps(list(existing))}

STRICT RULES:
1. ONLY reference data I provided above. NEVER invent details about a subscriber. If you don't have info about someone, say "based on their [actual data point]." NEVER make up job titles, locations, or personal details.
2. NEVER recommend sending a flow/trigger that the subscriber has ALREADY fired. Check their "triggers_they_have" array. If they triggered MALIBURETREAT, do NOT suggest sending MALIBU flow. They already have that info. Instead, suggest a PERSONAL follow-up DM.
3. For warm/hot leads who already triggered a product keyword, the best action is "personal_dm" with a draft message Angela can send. The DM should feel human, not automated.
4. Only suggest "send_flow" for triggers the person has NOT already fired.
5. Every recommendation MUST include the person's ig username so Angela knows who it is.

ACTION TYPES:
- "personal_dm": Angela sends a custom personal message. Include a "dm_draft" field with a ready-to-send DM in Angela's voice (warm, direct, no em dashes, 2-4 sentences).
- "send_flow": Send an automated ManyChat flow. Only for triggers they DON'T already have.

Generate 5-8 actionable recommendations. Return ONLY a JSON array, no backticks:
[{{
  "mc_id": "their mc_id",
  "subscriber_name": "Their Name",
  "ig_username": "their_ig_handle",
  "priority": 1,
  "category": "follow_up",
  "title": "Clear title mentioning their name",
  "description": "What you know about this person based ONLY on the data above. What they need.",
  "triggers_they_have": ["KEYWORDS", "THEY", "ALREADY", "FIRED"],
  "action_type": "personal_dm or send_flow",
  "suggested_action": "What Angela should do and why",
  "suggested_flow": "KEYWORD (only if action_type is send_flow, otherwise empty string)",
  "dm_draft": "Ready-to-send personal DM in Angela's voice (only if action_type is personal_dm, otherwise empty string)"
}}]

Categories: follow_up, re_engage, retreat, stalled, high_intent, new_lead, flodesk_sync.
Priority 1=urgent, 5=low."""

        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post("https://api.anthropic.com/v1/messages", headers={
                "x-api-key":ANTHROPIC_KEY,"anthropic-version":"2023-06-01","content-type":"application/json"
            }, json={"model":"claude-sonnet-4-20250514","max_tokens":4000,"messages":[{"role":"user","content":prompt}]})
            r.raise_for_status()
            resp = r.json()

        text = resp.get("content",[{}])[0].get("text","[]").strip()
        if text.startswith("```"): text = text.split("\n",1)[1].rsplit("```",1)[0].strip()
        recs = json.loads(text)

        inserted = 0
        for rec in recs:
            key = f"{rec.get('mc_id')}_{rec.get('category')}"
            if key in existing: continue

            # Store extra fields in data_points JSONB
            data_points = {
                "ig_username": rec.get("ig_username", ""),
                "action_type": rec.get("action_type", "send_flow"),
                "triggers_they_have": rec.get("triggers_they_have", []),
                "dm_draft": rec.get("dm_draft", ""),
            }

            execute("INSERT INTO lead_recommendations (mc_id,subscriber_name,priority,category,title,description,suggested_action,suggested_flow,data_points,expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (rec.get("mc_id"), rec.get("subscriber_name",""), rec.get("priority",5), rec.get("category","follow_up"),
                 rec.get("title",""), rec.get("description",""), rec.get("suggested_action",""),
                 rec.get("suggested_flow","") if rec.get("action_type") == "send_flow" else "",
                 json.dumps(data_points),
                 (now+timedelta(days=7)).isoformat()))
            inserted += 1

        return JSONResponse(content={"success":True,"recommendations":inserted,"total_analyzed":len(summaries)})
    except json.JSONDecodeError as e:
        return JSONResponse(content={"error":f"Parse error: {e}"}, status_code=500)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


# ================================================================
#  FUNNEL + RETREAT PIPELINE
# ================================================================
@router.get("/api/manychat/funnel")
async def funnel_stats():
    try:
        stages = ["subscriber","engaged","multi_trigger","conversation","booked"]
        result = {}
        for s in stages:
            rows = query("SELECT id,full_name,ig_username,heat_score,interest_level FROM manychat_subscribers WHERE funnel_stage=%s AND do_not_contact=false ORDER BY heat_score DESC LIMIT 10",(s,))
            cnt = query_one("SELECT COUNT(*) as c FROM manychat_subscribers WHERE funnel_stage=%s AND do_not_contact=false",(s,))
            result[s] = {"count":cnt["c"] if cnt else 0, "top":[dict(r) for r in rows]}
        return JSONResponse(content={"funnel":result})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@router.get("/api/manychat/retreat-pipeline")
async def retreat_pipeline():
    try:
        trigs = query("SELECT mc_id,keyword,fired_at FROM subscriber_triggers WHERE keyword IN ('MALIBURETREAT','MALIBU RETREAT') ORDER BY fired_at DESC")
        mc_ids = list(set(t["mc_id"] for t in trigs))
        if not mc_ids:
            return JSONResponse(content={"pipeline":[],"count":0})
        ph = ",".join(["%s"]*len(mc_ids))
        subs = query(f"SELECT * FROM manychat_subscribers WHERE mc_id IN ({ph}) AND do_not_contact=false ORDER BY heat_score DESC", mc_ids)
        return JSONResponse(content={"pipeline":[dict(s) for s in subs],"count":len(subs)})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)
