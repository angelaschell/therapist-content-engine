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

    where = " AND ".join(conditions)
    subs = query(
        f"SELECT id, mc_id, full_name, email, ig_username, heat_score, funnel_stage, flodesk_synced "
        f"FROM manychat_subscribers WHERE {where} ORDER BY heat_score DESC LIMIT 200",
        tuple(params)
    )

    return JSONResponse({"subscribers": subs, "count": len(subs)})


@router.post("/api/flodesk/sync")
async def sync_to_flodesk(req: Request):
    """Sync subscribers to Flodesk. Adds them as subscribers and optionally to a segment."""
    data = await req.json()
    segment_id = data.get("segment_id")
    mc_ids = data.get("mc_ids", [])
    min_heat = data.get("min_heat", 30)
    funnel_stages = data.get("funnel_stages", ["engaged", "conversation", "booked"])
    only_unsynced = data.get("only_unsynced", True)

    if not FLODESK_API_KEY:
        return JSONResponse({"error": "FLODESK_API_KEY not configured"}, status_code=400)

    # Build query for subscribers to sync
    if mc_ids:
        placeholders = ",".join(["%s"] * len(mc_ids))
        subs = query(
            f"SELECT * FROM manychat_subscribers WHERE mc_id IN ({placeholders}) "
            f"AND email != '' AND email IS NOT NULL AND do_not_contact = false",
            tuple(mc_ids)
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
            f"SELECT * FROM manychat_subscribers WHERE {where} ORDER BY heat_score DESC LIMIT 200",
            tuple(params)
        )

    if not subs:
        return JSONResponse({"synced": 0, "message": "No subscribers matched the criteria"})

    synced = 0
    errors = []

    for sub in subs:
        email = sub.get("email", "").strip()
        if not email:
            continue
        try:
            # Create or update subscriber in Flodesk
            first = sub.get("first_name", "") or ""
            last = sub.get("last_name", "") or ""
            payload = {
                "email": email,
                "first_name": first,
                "last_name": last,
            }
            if segment_id:
                payload["segment_ids"] = [segment_id]

            await flodesk_request("POST", "/subscribers", payload)

            # Mark as synced in our DB
            execute(
                "UPDATE manychat_subscribers SET flodesk_synced = true, updated_at = now() WHERE mc_id = %s",
                (sub["mc_id"],)
            )
            synced += 1
        except Exception as e:
            errors.append({"email": email, "error": str(e)})

    return JSONResponse({
        "synced": synced,
        "total_attempted": len(subs),
        "errors": errors[:10],
    })


@router.post("/api/flodesk/sync-one/{mc_id}")
async def sync_one_subscriber(mc_id: str, req: Request):
    """Sync a single subscriber to Flodesk."""
    data = await req.json() if req.headers.get("content-type", "").startswith("application/json") else {}
    segment_id = data.get("segment_id")

    if not FLODESK_API_KEY:
        return JSONResponse({"error": "FLODESK_API_KEY not configured"}, status_code=400)

    sub = query_one(
        "SELECT * FROM manychat_subscribers WHERE mc_id = %s AND email != '' AND email IS NOT NULL",
        (mc_id,)
    )
    if not sub:
        return JSONResponse({"error": "Subscriber not found or has no email"}, status_code=404)

    try:
        payload = {
            "email": sub["email"],
            "first_name": sub.get("first_name", "") or "",
            "last_name": sub.get("last_name", "") or "",
        }
        if segment_id:
            payload["segment_ids"] = [segment_id]

        await flodesk_request("POST", "/subscribers", payload)
        execute(
            "UPDATE manychat_subscribers SET flodesk_synced = true, updated_at = now() WHERE mc_id = %s",
            (mc_id,)
        )
        return JSONResponse({"success": True, "email": sub["email"]})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
