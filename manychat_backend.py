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
            d = await mc_get("/fb/page/getFlows"); flows = d.get("data", []); synced += len(flows)
        except: pass
        try:
            d = await mc_get("/fb/page/getTags"); tags = d.get("data", []); synced += len(tags)
        except: pass
        try:
            d = await mc_get("/fb/page/getCustomFields"); fields = d.get("data", [])
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
        return JSONResponse(content={"flows": d.get("data", [])})
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
        recent = query("SELECT * FROM subscriber_triggers WHERE fired_at > now() - interval '7 days' ORDER BY fired_at DESC LIMIT 50")
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
            summaries.append({
                "name":s.get("full_name","Unknown"), "mc_id":s.get("mc_id"), "ig":s.get("ig_username",""),
                "interest":s.get("interest_level"), "heat":s.get("heat_score"), "funnel":s.get("funnel_stage"),
                "triggers_fired":s.get("trigger_count",0), "conversations":s.get("conversation_count",0),
                "subscribed":str(s.get("subscribed_at","")), "last_interaction":str(s.get("last_interaction","")),
                "tags":[t.get("name",str(t)) if isinstance(t,dict) else str(t) for t in tags][:10],
                "email":bool(s.get("email")), "flodesk_synced":s.get("flodesk_synced",False)
            })

        activity = [{"mc_id":t["mc_id"],"keyword":t["keyword"],"when":str(t["fired_at"]),"source":t.get("source","")} for t in recent]

        now = datetime.now(timezone.utc)
        prompt = f"""You are Angela Schellenberg's lead intelligence analyst. Today is {now.strftime('%B %d, %Y')}.
Angela is a licensed trauma therapist in LA with 171K IG followers. Healing with Horses retreat April 30-May 3, 2026, few spots left.

Trigger keywords: {json.dumps({k:v.get('label','')+' - '+v.get('description','') for k,v in tmap.items()}, indent=2)}

Top subscribers: {json.dumps(summaries, indent=2, default=str)}

Recent triggers (7 days): {json.dumps(activity, indent=2, default=str)}

Already pending (skip these): {json.dumps(list(existing))}

Generate 5-8 actionable recommendations. Return ONLY a JSON array:
[{{"mc_id":"...","subscriber_name":"...","priority":1,"category":"follow_up","title":"...","description":"...","suggested_action":"...","suggested_flow":"KEYWORD or empty"}}]

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
            execute("INSERT INTO lead_recommendations (mc_id,subscriber_name,priority,category,title,description,suggested_action,suggested_flow,expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (rec.get("mc_id"), rec.get("subscriber_name",""), rec.get("priority",5), rec.get("category","follow_up"),
                 rec.get("title",""), rec.get("description",""), rec.get("suggested_action",""), rec.get("suggested_flow",""),
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
