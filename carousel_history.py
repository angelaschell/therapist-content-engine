# Carousel History Backend - Server-side storage for generated carousels
import os
import json
import psycopg2
import psycopg2.extras
from datetime import datetime
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()
DATABASE_URL = os.environ.get("DATABASE_URL", "")


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

def insert_returning(sql, params=None):
    conn = get_conn()
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(sql, params or ())
    row = clean(cur.fetchone())
    cur.close()
    conn.close()
    return row


# ── Auto-setup table ──────────────────────────────────────────
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS saved_carousels (
    id BIGSERIAL PRIMARY KEY,
    topic TEXT NOT NULL DEFAULT 'Untitled',
    slides JSONB NOT NULL DEFAULT '[]',
    caption TEXT DEFAULT '',
    trigger_keyword TEXT DEFAULT '',
    template TEXT DEFAULT 'naming',
    research TEXT DEFAULT '',
    is_favorite BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_saved_carousels_created ON saved_carousels(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_saved_carousels_favorite ON saved_carousels(is_favorite) WHERE is_favorite = TRUE;
"""

try:
    if DATABASE_URL:
        execute(SCHEMA_SQL)
except Exception as e:
    print(f"[carousel_history] Schema setup: {e}")


# ── Endpoints ─────────────────────────────────────────────────

@router.post("/api/carousels/save")
async def save_carousel(req: Request):
    data = await req.json()
    row = insert_returning("""
        INSERT INTO saved_carousels (topic, slides, caption, trigger_keyword, template, research)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING *
    """, (
        data.get("topic", "Untitled"),
        json.dumps(data.get("slides", [])),
        data.get("caption", ""),
        data.get("trigger", ""),
        data.get("template", "naming"),
        data.get("research", ""),
    ))
    return JSONResponse(row)


@router.get("/api/carousels")
async def list_carousels(req: Request):
    search = req.query_params.get("search", "").strip()
    favorites_only = req.query_params.get("favorites", "").lower() == "true"
    limit = min(int(req.query_params.get("limit", "50")), 200)
    offset = int(req.query_params.get("offset", "0"))

    conditions = []
    params = []

    if search:
        conditions.append("(topic ILIKE %s OR caption ILIKE %s)")
        params.extend([f"%{search}%", f"%{search}%"])
    if favorites_only:
        conditions.append("is_favorite = TRUE")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    rows = query(f"""
        SELECT * FROM saved_carousels {where}
        ORDER BY created_at DESC LIMIT %s OFFSET %s
    """, (*params, limit, offset))

    count_rows = query(f"SELECT COUNT(*) as total FROM saved_carousels {where}", tuple(params))
    total = count_rows[0]["total"] if count_rows else 0

    return JSONResponse({"carousels": rows, "total": total})


@router.get("/api/carousels/{carousel_id}")
async def get_carousel(carousel_id: int):
    rows = query("SELECT * FROM saved_carousels WHERE id = %s", (carousel_id,))
    if not rows:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return JSONResponse(rows[0])


@router.patch("/api/carousels/{carousel_id}/favorite")
async def toggle_favorite(carousel_id: int):
    row = insert_returning("""
        UPDATE saved_carousels SET is_favorite = NOT is_favorite
        WHERE id = %s RETURNING *
    """, (carousel_id,))
    if not row:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return JSONResponse(row)


@router.delete("/api/carousels/{carousel_id}")
async def delete_carousel(carousel_id: int):
    execute("DELETE FROM saved_carousels WHERE id = %s", (carousel_id,))
    return JSONResponse({"ok": True})


@router.delete("/api/carousels")
async def clear_all_carousels():
    execute("DELETE FROM saved_carousels")
    return JSONResponse({"ok": True})
