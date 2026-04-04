# Podcast Show Notes Generator - Generate notes, timestamps, and blog posts from Vizard transcripts
import os
import json
import psycopg2
import psycopg2.extras
import httpx
from datetime import datetime
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()
DATABASE_URL = os.environ.get("DATABASE_URL", "")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")


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


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS show_notes (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT,
    project_name TEXT DEFAULT '',
    title TEXT DEFAULT '',
    show_notes TEXT DEFAULT '',
    timestamps TEXT DEFAULT '',
    blog_post TEXT DEFAULT '',
    key_quotes JSONB DEFAULT '[]',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
"""

try:
    if DATABASE_URL:
        execute(SCHEMA_SQL)
except Exception as e:
    print(f"[show_notes] Schema setup: {e}")


@router.post("/api/show-notes/generate")
async def generate_show_notes(req: Request):
    """Generate show notes from a Vizard project's clips and transcripts."""
    data = await req.json()
    project_id = data.get("project_id")
    transcript = data.get("transcript", "")
    project_name = data.get("project_name", "")

    # If project_id given, pull clips from vizard_clips
    if project_id and not transcript:
        clips = query(
            "SELECT transcript, viral_score FROM vizard_clips WHERE project_id = %s ORDER BY id",
            (project_id,)
        )
        transcript = "\n\n".join([c.get("transcript", "") for c in clips if c.get("transcript")])
        if not project_name:
            projects = query("SELECT project_name FROM vizard_projects WHERE id = %s", (project_id,))
            if projects:
                project_name = projects[0].get("project_name", "")

    if not transcript:
        return JSONResponse({"error": "No transcript provided or found for this project"}, status_code=400)

    prompt = f"""Generate comprehensive podcast show notes from this transcript.

PODCAST: {project_name or 'Untitled Episode'}
TRANSCRIPT:
{transcript[:8000]}

Return ONLY valid JSON, no backticks:
{{
  "title": "Episode title (compelling, SEO-friendly)",
  "show_notes": "Full show notes in markdown format. Include: episode summary (2-3 sentences), key topics covered (bulleted), guest info if mentioned, resources mentioned.",
  "timestamps": "Timestamps in format:\\n00:00 - Introduction\\n02:15 - Topic name\\n... (estimate based on content flow, 8-15 timestamps)",
  "blog_post": "A 500-800 word blog post based on the episode content. SEO-optimized with H2 subheadings. Angela's voice. Include key takeaways and a CTA at the end.",
  "key_quotes": ["3-5 standout quotes from the episode that would make good social media posts"]
}}"""

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post("https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 4000,
                    "system": "You are Angela Schellenberg's content assistant. Generate podcast show notes. Return only valid JSON.",
                    "messages": [{"role": "user", "content": prompt}]
                })
            resp.raise_for_status()
            text = "".join(b["text"] for b in resp.json().get("content", []) if b.get("type") == "text").strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            result = json.loads(text)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    # Save
    conn = get_conn()
    try:
        conn.autocommit = True
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "INSERT INTO show_notes (project_id, project_name, title, show_notes, timestamps, blog_post, key_quotes) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *",
            (project_id, project_name, result.get("title", ""), result.get("show_notes", ""),
             result.get("timestamps", ""), result.get("blog_post", ""),
             json.dumps(result.get("key_quotes", [])))
        )
        row = clean(cur.fetchone())
        cur.close()
    finally:
        conn.close()

    return JSONResponse({"result": result, "record": row})


@router.get("/api/show-notes")
async def list_show_notes():
    notes = query("SELECT id, project_name, title, created_at FROM show_notes ORDER BY created_at DESC LIMIT 30")
    return JSONResponse({"notes": notes})


@router.get("/api/show-notes/{note_id}")
async def get_show_note(note_id: int):
    rows = query("SELECT * FROM show_notes WHERE id = %s", (note_id,))
    if not rows:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return JSONResponse(rows[0])
