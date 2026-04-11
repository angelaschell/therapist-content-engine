# Flodesk Email Sync - Push warm leads from ManyChat CRM into Flodesk segments
import os
import json
import psycopg2
import psycopg2.extras
import httpx
import base64
from datetime import datetime
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()
DATABASE_URL = os.environ.get("DATABASE_URL", "")
FLODESK_API_KEY = os.environ.get("FLODESK_API_KEY", "")
FLODESK_API = "https://api.flodesk.com/v1"


# ── DB Helpers ─────────────────────────────────────────────────
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
    try:
        conn.autocommit = True
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params or ())
        result = [clean(r) for r in cur.fetchall()]
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


# ── Flodesk API helpers ────────────────────────────────────────
def flodesk_headers():
    encoded = base64.b64encode(f"{FLODESK_API_KEY}:".encode()).decode()
    return {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/json",
    }


async def flodesk_request(method, path, data=None):
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.request(
            method, f"{FLODESK_API}{path}",
            headers=flodesk_headers(),
            json=data,
        )
        resp.raise_for_status()
        return resp.json() if resp.content else {}


# ── Endpoints ─────────────────────────────────────────────────

@router.get("/api/flodesk/status")
async def flodesk_status():
    """Check if Flodesk API key is configured and valid."""
    if not FLODESK_API_KEY:
        return JSONResponse({"configured": False, "message": "FLODESK_API_KEY not set"})
    try:
        segments = await flodesk_request("GET", "/segments")
        return JSONResponse({
            "configured": True,
            "segments": [{"id": s["id"], "name": s["name"]} for s in segments]
        })
    except Exception as e:
        return JSONResponse({"configured": True, "error": str(e)}, status_code=500)


@router.get("/api/flodesk/segments")
async def list_segments():
    """List all Flodesk segments."""
    try:
        segments = await flodesk_request("GET", "/segments")
        return JSONResponse({"segments": [{"id": s["id"], "name": s["name"]} for s in segments]})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/flodesk/sync-preview")
async def sync_preview(req: Request):
    """Preview which subscribers would be synced based on filters."""
    min_heat = int(req.query_params.get("min_heat", "30"))
    funnel_stages = req.query_params.get("funnel_stages", "engaged,conversation,booked")
    only_unsynced = req.query_params.get("only_unsynced", "true").lower() == "true"
    claude_category = req.query_params.get("claude_category", "").strip()
    claude_product_fit = req.query_params.get("claude_product_fit", "").strip()

    stages = [s.strip() for s in funnel_stages.split(",") if s.strip()]

    conditions = ["do_not_contact = false", "email != ''", "email IS NOT NULL"]
    params = []

    if min_heat > 0:
        conditions.append("heat_score >= %s")
        params.append(min_heat)
    if stages:
        placeholders = ",".join(["%s"] * len(stages))
        conditions.append(f"funnel_stage IN ({placeholders})")
        params.extend(stages)
    if only_unsynced:
        conditions.append("flodesk_synced = false")
    if claude_category:
        conditions.append("analysis->>'category' = %s")
        params.append(claude_category)
    if claude_product_fit:
        conditions.append("analysis->>'product_fit' = %s")
        params.append(claude_product_fit)

    where = " AND ".join(conditions)
    subs = query(
        f"SELECT id, contact_id, first_name, last_name, email, ig_username, "
        f"heat_score, funnel_stage, flodesk_synced, analysis "
        f"FROM manychat_leads_clean WHERE {where} ORDER BY heat_score DESC LIMIT 200",
        tuple(params)
    )

    return JSONResponse({"subscribers": subs, "count": len(subs)})


@router.post("/api/flodesk/sync")
async def sync_to_flodesk(req: Request):
    """Sync subscribers to Flodesk. Adds them as subscribers and optionally to a segment.

    If no explicit segment_id is given, the lead's claude_category + claude_product_fit
    are looked up in flodesk_segment_map to route automatically.
    """
    data = await req.json()
    segment_id = data.get("segment_id")
    contact_ids = data.get("contact_ids") or data.get("mc_ids") or []
    min_heat = data.get("min_heat", 30)
    funnel_stages = data.get("funnel_stages", ["engaged", "conversation", "booked"])
    only_unsynced = data.get("only_unsynced", True)

    if not FLODESK_API_KEY:
        return JSONResponse({"error": "FLODESK_API_KEY not configured"}, status_code=400)

    # Build query for subscribers to sync
    if contact_ids:
        placeholders = ",".join(["%s"] * len(contact_ids))
        subs = query(
            f"SELECT * FROM manychat_leads_clean WHERE contact_id IN ({placeholders}) "
            f"AND email != '' AND email IS NOT NULL AND do_not_contact = false",
            tuple(contact_ids)
        )
    else:
        conditions = ["do_not_contact = false", "email != ''", "email IS NOT NULL"]
        params = []
        if min_heat > 0:
            conditions.append("heat_score >= %s")
            params.append(min_heat)
        if funnel_stages:
            ph = ",".join(["%s"] * len(funnel_stages))
            conditions.append(f"funnel_stage IN ({ph})")
            params.extend(funnel_stages)
        if only_unsynced:
            conditions.append("flodesk_synced = false")
        where = " AND ".join(conditions)
        subs = query(
            f"SELECT * FROM manychat_leads_clean WHERE {where} ORDER BY heat_score DESC LIMIT 200",
            tuple(params)
        )

    if not subs:
        return JSONResponse({"synced": 0, "message": "No subscribers matched the criteria"})

    synced = 0
    errors = []

    for sub in subs:
        email = (sub.get("email") or "").strip()
        if not email:
            continue
        try:
            first = sub.get("first_name", "") or ""
            last = sub.get("last_name", "") or ""
            payload = {
                "email": email,
                "first_name": first,
                "last_name": last,
            }

            # Resolve segment: explicit override → claude_category/product_fit map → none
            target_segment = segment_id
            if not target_segment:
                analysis = sub.get("analysis") or {}
                if isinstance(analysis, str):
                    try:
                        analysis = json.loads(analysis)
                    except Exception:
                        analysis = {}
                target_segment = resolve_segment_for_lead(
                    analysis.get("category", ""),
                    analysis.get("product_fit", ""),
                )
            if target_segment:
                payload["segment_ids"] = [target_segment]

            await flodesk_request("POST", "/subscribers", payload)

            # Mark as synced in our DB
            execute(
                "UPDATE manychat_leads_clean SET flodesk_synced = true, updated_at = now() WHERE contact_id = %s",
                (sub["contact_id"],)
            )
            synced += 1
        except Exception as e:
            errors.append({"email": email, "error": str(e)})

    return JSONResponse({
        "synced": synced,
        "total_attempted": len(subs),
        "errors": errors[:10],
    })


@router.post("/api/flodesk/sync-one/{contact_id}")
async def sync_one_subscriber(contact_id: str, req: Request):
    """Sync a single subscriber to Flodesk."""
    data = await req.json() if req.headers.get("content-type", "").startswith("application/json") else {}
    segment_id = data.get("segment_id")

    if not FLODESK_API_KEY:
        return JSONResponse({"error": "FLODESK_API_KEY not configured"}, status_code=400)

    sub = query_one(
        "SELECT * FROM manychat_leads_clean WHERE contact_id = %s AND email != '' AND email IS NOT NULL",
        (contact_id,)
    )
    if not sub:
        return JSONResponse({"error": "Subscriber not found or has no email"}, status_code=404)

    try:
        payload = {
            "email": sub["email"],
            "first_name": sub.get("first_name", "") or "",
            "last_name": sub.get("last_name", "") or "",
        }

        target_segment = segment_id
        if not target_segment:
            analysis = sub.get("analysis") or {}
            if isinstance(analysis, str):
                try:
                    analysis = json.loads(analysis)
                except Exception:
                    analysis = {}
            target_segment = resolve_segment_for_lead(
                analysis.get("category", ""),
                analysis.get("product_fit", ""),
            )
        if target_segment:
            payload["segment_ids"] = [target_segment]

        await flodesk_request("POST", "/subscribers", payload)
        execute(
            "UPDATE manychat_leads_clean SET flodesk_synced = true, updated_at = now() WHERE contact_id = %s",
            (contact_id,)
        )
        return JSONResponse({"success": True, "email": sub["email"]})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Flodesk segment mapping (claude_category + claude_product_fit → segment_id) ──
# Managed via the flodesk_segment_map table, falls through to FLODESK_DEFAULT_SEGMENT.

SEGMENT_MAP_SCHEMA = """
CREATE TABLE IF NOT EXISTS flodesk_segment_map (
  id BIGSERIAL PRIMARY KEY,
  claude_category TEXT DEFAULT '',
  claude_product_fit TEXT DEFAULT '',
  segment_id TEXT NOT NULL,
  label TEXT DEFAULT '',
  priority INT DEFAULT 100,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS flodesk_segment_map_lookup
  ON flodesk_segment_map (claude_category, claude_product_fit, priority);
"""

try:
    if DATABASE_URL:
        execute(SEGMENT_MAP_SCHEMA)
except Exception as e:
    print("[FLODESK SEGMENT MAP SCHEMA ERROR]", e)


def resolve_segment_for_lead(category: str, product_fit: str) -> str:
    """Look up the right Flodesk segment for a lead.

    Tries most-specific match first (category + product_fit), then category alone,
    then product_fit alone, then the FLODESK_DEFAULT_SEGMENT env var.
    """
    category = (category or "").strip().lower()
    product_fit = (product_fit or "").strip().lower()

    try:
        # Most specific: both match
        if category and product_fit:
            rows = query(
                "SELECT segment_id FROM flodesk_segment_map "
                "WHERE claude_category = %s AND claude_product_fit = %s "
                "ORDER BY priority ASC LIMIT 1",
                (category, product_fit),
            )
            if rows:
                return rows[0]["segment_id"]

        # Category only
        if category:
            rows = query(
                "SELECT segment_id FROM flodesk_segment_map "
                "WHERE claude_category = %s AND claude_product_fit = '' "
                "ORDER BY priority ASC LIMIT 1",
                (category,),
            )
            if rows:
                return rows[0]["segment_id"]

        # Product fit only
        if product_fit:
            rows = query(
                "SELECT segment_id FROM flodesk_segment_map "
                "WHERE claude_product_fit = %s AND claude_category = '' "
                "ORDER BY priority ASC LIMIT 1",
                (product_fit,),
            )
            if rows:
                return rows[0]["segment_id"]
    except Exception as e:
        print(f"[FLODESK SEGMENT RESOLVE] {e}")

    return os.environ.get("FLODESK_DEFAULT_SEGMENT", "")


@router.get("/api/flodesk/segment-map")
async def list_segment_map():
    try:
        rows = query("SELECT * FROM flodesk_segment_map ORDER BY priority ASC, id ASC")
        return {"mappings": rows}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/flodesk/segment-map")
async def create_segment_map(req: Request):
    try:
        body = await req.json()
        segment_id = (body.get("segment_id") or "").strip()
        if not segment_id:
            return JSONResponse({"error": "segment_id is required"}, status_code=400)
        category = (body.get("claude_category") or "").strip().lower()
        product_fit = (body.get("claude_product_fit") or "").strip().lower()
        label = (body.get("label") or "").strip()
        priority = int(body.get("priority") or 100)
        execute(
            "INSERT INTO flodesk_segment_map (claude_category, claude_product_fit, segment_id, label, priority) "
            "VALUES (%s, %s, %s, %s, %s)",
            (category, product_fit, segment_id, label, priority),
        )
        return {"success": True}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.delete("/api/flodesk/segment-map/{map_id}")
async def delete_segment_map(map_id: int):
    try:
        execute("DELETE FROM flodesk_segment_map WHERE id = %s", (map_id,))
        return {"success": True}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
