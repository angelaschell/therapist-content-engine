"""
Vizard Reels Studio Backend
────────────────────────────
Integrates Vizard AI API for podcast/video clipping.
Submits videos, polls for clips, stores results, generates captions.

Add to main.py:
  from vizard_backend import router as vizard_router
  app.include_router(vizard_router)
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
import httpx
import os
import json
import anthropic
import psycopg2
import psycopg2.extras
from datetime import datetime

router = APIRouter()

DATABASE_URL = os.environ.get("DATABASE_URL", "")
VIZARD_API_KEY = os.environ.get("VIZARD_API_KEY", "")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
VIZARD_BASE = "https://elb-api.vizard.ai/hvizard-server-front/open-api/v1"

try:
    claude_client = anthropic.Anthropic(api_key=ANTHROPIC_KEY) if ANTHROPIC_KEY else None
except Exception:
    claude_client = None

ANGELA_REEL_SYSTEM = """You are Angela Schellenberg, a licensed trauma and grief therapist (LMHC, LPC, LPCC, EMDR Certified). You write Instagram Reel captions.

VOICE:
- Second person. "You" language. Direct address.
- Short punchy lines. Line breaks between thoughts.
- Hyper-specific over abstract. Paint the image, don't describe the concept.
- Never use em dashes.
- Never use "you're not broken" or soft-landing phrases.
- Never sound clinical or educational in a reel caption. Sound like someone saying the quiet part out loud.
- End with exactly ONE ManyChat CTA that ties the product to the clip content.
- Include exactly 5 highly relevant hashtags.
- Keep it under 150 words."""


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


# ── Auto-setup tables ─────────────────────────────────────────
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS vizard_projects (
    id BIGSERIAL PRIMARY KEY,
    vizard_project_id BIGINT,
    project_name TEXT DEFAULT '',
    video_source TEXT DEFAULT '',
    video_type TEXT DEFAULT 'upload',
    status TEXT DEFAULT 'processing',
    clip_count INT DEFAULT 0,
    settings JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT now(),
    updated_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS vizard_clips (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT REFERENCES vizard_projects(id) ON DELETE CASCADE,
    vizard_video_id BIGINT,
    video_url TEXT DEFAULT '',
    editor_url TEXT DEFAULT '',
    title TEXT DEFAULT '',
    transcript TEXT DEFAULT '',
    duration_ms BIGINT DEFAULT 0,
    viral_score INT DEFAULT 0,
    viral_reason TEXT DEFAULT '',
    caption TEXT DEFAULT '',
    trigger_keyword TEXT DEFAULT '',
    starred BOOLEAN DEFAULT false,
    hidden BOOLEAN DEFAULT false,
    published_instagram BOOLEAN DEFAULT false,
    published_tiktok BOOLEAN DEFAULT false,
    published_youtube BOOLEAN DEFAULT false,
    created_at TIMESTAMP DEFAULT now()
);
"""

try:
    if DATABASE_URL:
        execute(SCHEMA_SQL)
except Exception as e:
    print(f"Vizard schema setup: {e}")


# ── Vizard API helpers ────────────────────────────────────────
def vizard_headers():
    return {
        "Content-Type": "application/json",
        "VIZARDAI_API_KEY": VIZARD_API_KEY
    }


# ── Submit video for clipping ─────────────────────────────────
@router.post("/api/vizard/submit")
async def submit_video(req: Request):
    """Submit a video URL to Vizard for AI clipping."""
    try:
        data = await req.json()
        video_url = data.get("video_url", "")
        project_name = data.get("project_name", "")
        video_type = data.get("video_type", 2)  # 1=upload, 2=youtube, 3=gdrive, 4=vimeo

        if not video_url:
            return JSONResponse({"success": False, "error": "No video URL provided"}, status_code=400)

        if not VIZARD_API_KEY:
            return JSONResponse({"success": False, "error": "VIZARD_API_KEY not configured"}, status_code=500)

        # Build Vizard payload
        payload = {
            "lang": data.get("lang", "en"),
            "videoUrl": video_url,
            "videoType": video_type,
            "preferLength": data.get("prefer_length", [0]),
            "ratioOfClip": data.get("ratio", 1),
            "maxClipNumber": data.get("max_clips", 15),
            "removeSilenceSwitch": 1 if data.get("remove_silence") else 0,
            "subtitleSwitch": 1 if data.get("subtitles", True) else 0,
            "headlineSwitch": 1 if data.get("headline", True) else 0,
            "autoBrollSwitch": 1 if data.get("auto_broll") else 0,
            "emojiSwitch": 1 if data.get("emoji") else 0,
            "highlightSwitch": 1 if data.get("highlight_keywords") else 0,
        }

        if data.get("template_id"):
            payload["templateId"] = int(data["template_id"])

        if data.get("keywords"):
            payload["keywords"] = data["keywords"]

        if project_name:
            payload["projectName"] = project_name

        # Submit to Vizard
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{VIZARD_BASE}/project/create",
                headers=vizard_headers(),
                json=payload
            )

        result = resp.json()

        if result.get("code") != 2000:
            return JSONResponse({
                "success": False,
                "error": result.get("errMsg", f"Vizard error code: {result.get('code')}"),
                "vizard_response": result
            })

        vizard_project_id = result.get("projectId")

        # Save to our DB
        settings = {
            "ratio": data.get("ratio", 1),
            "max_clips": data.get("max_clips", 15),
            "prefer_length": data.get("prefer_length", [0]),
            "remove_silence": data.get("remove_silence", False),
            "subtitles": data.get("subtitles", True),
            "headline": data.get("headline", True),
            "keywords": data.get("keywords", ""),
        }

        row = insert_returning(
            """INSERT INTO vizard_projects (vizard_project_id, project_name, video_source, video_type, status, settings)
               VALUES (%s, %s, %s, %s, 'processing', %s) RETURNING *""",
            (vizard_project_id, project_name or "Untitled", video_url, str(video_type), json.dumps(settings))
        )

        return JSONResponse({
            "success": True,
            "project": row,
            "vizard_project_id": vizard_project_id
        })

    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# ── Poll project status / retrieve clips ──────────────────────
@router.get("/api/vizard/status/{project_id}")
async def check_status(project_id: int):
    """Poll Vizard for project status and retrieve clips when ready."""
    try:
        project = query_one("SELECT * FROM vizard_projects WHERE id = %s", (project_id,))
        if not project:
            return JSONResponse({"success": False, "error": "Project not found"}, status_code=404)

        vizard_pid = project["vizard_project_id"]

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{VIZARD_BASE}/project/query/{vizard_pid}",
                headers=vizard_headers()
            )

        result = resp.json()
        code = result.get("code")

        # Still processing
        if code == 1000:
            return JSONResponse({
                "success": True,
                "status": "processing",
                "project": project
            })

        # Failed
        if code not in (2000,):
            execute(
                "UPDATE vizard_projects SET status='failed', updated_at=now() WHERE id=%s",
                (project_id,)
            )
            return JSONResponse({
                "success": False,
                "status": "failed",
                "error": result.get("errMsg", f"Error code: {code}")
            })

        # Success - save clips
        videos = result.get("videos", [])
        if videos:
            # Clear old clips for this project (in case of re-poll)
            execute("DELETE FROM vizard_clips WHERE project_id = %s", (project_id,))

            for v in videos:
                insert_returning(
                    """INSERT INTO vizard_clips 
                       (project_id, vizard_video_id, video_url, editor_url, title, transcript, 
                        duration_ms, viral_score, viral_reason)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (project_id, v.get("videoId"), v.get("videoUrl", ""),
                     v.get("clipEditorUrl", ""), v.get("title", ""),
                     v.get("transcript", ""), v.get("videoMsDuration", 0),
                     int(v.get("viralScore", 0)), v.get("viralReason", ""))
                )

            execute(
                "UPDATE vizard_projects SET status='complete', clip_count=%s, updated_at=now() WHERE id=%s",
                (len(videos), project_id)
            )

        return JSONResponse({
            "success": True,
            "status": "complete",
            "clip_count": len(videos),
            "share_link": result.get("shareLink", "")
        })

    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# ── Get clips for a project ───────────────────────────────────
@router.get("/api/vizard/clips/{project_id}")
async def get_clips(project_id: int):
    """Get all clips for a project from our DB."""
    try:
        clips = query(
            """SELECT * FROM vizard_clips WHERE project_id = %s AND hidden = false 
               ORDER BY viral_score DESC, created_at ASC""",
            (project_id,)
        )
        project = query_one("SELECT * FROM vizard_projects WHERE id = %s", (project_id,))
        return JSONResponse({"success": True, "clips": clips, "project": project})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e), "clips": []})


# ── Toggle star/hide on a clip ────────────────────────────────
@router.patch("/api/vizard/clips/{clip_id}/star")
async def toggle_star(clip_id: int):
    try:
        row = insert_returning(
            "UPDATE vizard_clips SET starred = NOT starred WHERE id = %s RETURNING *",
            (clip_id,)
        )
        return JSONResponse({"success": True, "clip": row})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@router.patch("/api/vizard/clips/{clip_id}/hide")
async def toggle_hide(clip_id: int):
    try:
        row = insert_returning(
            "UPDATE vizard_clips SET hidden = NOT hidden WHERE id = %s RETURNING *",
            (clip_id,)
        )
        return JSONResponse({"success": True, "clip": row})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


# ── Generate Angela-voice caption for a clip ──────────────────
@router.post("/api/vizard/caption")
async def generate_caption(req: Request):
    """Generate an Angela-voice caption for a specific clip."""
    try:
        data = await req.json()
        clip_id = data.get("clip_id")
        trigger = data.get("trigger", "")
        trigger_label = data.get("trigger_label", "")
        trigger_description = data.get("trigger_description", "")

        clip = query_one("SELECT * FROM vizard_clips WHERE id = %s", (clip_id,))
        if not clip:
            return JSONResponse({"success": False, "error": "Clip not found"})

        transcript = clip.get("transcript", "")
        title = clip.get("title", "")

        cta_instruction = ""
        if trigger:
            cta_instruction = f"""
End with EXACTLY ONE CTA line starting with "Comment {trigger}".
The product/service is: {trigger_label} ({trigger_description}).
Write the CTA so it directly connects this product to what was discussed in the clip.
Only ONE CTA line."""

        prompt = f"""Write a Reel caption for this clip.

CLIP TITLE: {title}
TRANSCRIPT: {transcript[:1500]}

{cta_instruction}

Include exactly 5 highly relevant hashtags at the end.
No em dashes. No soft-landing phrases. Angela's voice: direct, clinical but warm."""

        if not claude_client:
            return JSONResponse({"error": "ANTHROPIC_API_KEY not configured"}, status_code=400)
        response = claude_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=800,
            system=ANGELA_REEL_SYSTEM,
            messages=[{"role": "user", "content": prompt}]
        )

        caption = response.content[0].text.strip()

        # Save caption to clip
        execute(
            "UPDATE vizard_clips SET caption = %s, trigger_keyword = %s WHERE id = %s",
            (caption, trigger, clip_id)
        )

        return JSONResponse({"success": True, "caption": caption})

    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


# ── Generate caption via Vizard's AI ──────────────────────────
@router.post("/api/vizard/ai-social")
async def vizard_ai_social(req: Request):
    """Generate a social caption using Vizard's built-in AI."""
    try:
        data = await req.json()
        video_id = data.get("video_id")
        platform = data.get("platform", 3)  # 3=Instagram
        tone = data.get("tone", 2)  # 2=Catchy
        voice = data.get("voice", 0)  # 0=First person

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{VIZARD_BASE}/project/ai-social",
                headers=vizard_headers(),
                json={
                    "finalVideoId": video_id,
                    "aiSocialPlatform": platform,
                    "tone": tone,
                    "voice": voice
                }
            )

        result = resp.json()
        if result.get("code") == 2000:
            return JSONResponse({
                "success": True,
                "caption": result.get("aiSocialContent", ""),
                "title": result.get("aiSocialTitle", "")
            })
        return JSONResponse({"success": False, "error": result.get("errMsg", "Unknown error")})

    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


# ── List all projects ─────────────────────────────────────────
@router.get("/api/vizard/projects")
async def list_projects():
    try:
        projects = query(
            "SELECT * FROM vizard_projects ORDER BY created_at DESC LIMIT 50"
        )
        return JSONResponse({"success": True, "projects": projects})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e), "projects": []})


# ── Delete a project ──────────────────────────────────────────
@router.delete("/api/vizard/projects/{project_id}")
async def delete_project(project_id: int):
    try:
        execute("DELETE FROM vizard_clips WHERE project_id = %s", (project_id,))
        execute("DELETE FROM vizard_projects WHERE id = %s", (project_id,))
        return JSONResponse({"success": True})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


# ── Batch generate captions ───────────────────────────────────
@router.post("/api/vizard/batch-caption")
async def batch_caption(req: Request):
    """Generate captions for multiple clips at once."""
    try:
        data = await req.json()
        clip_ids = data.get("clip_ids", [])
        trigger = data.get("trigger", "")
        trigger_label = data.get("trigger_label", "")
        trigger_description = data.get("trigger_description", "")

        results = []
        for cid in clip_ids:
            clip = query_one("SELECT * FROM vizard_clips WHERE id = %s", (cid,))
            if not clip:
                continue

            cta_instruction = ""
            if trigger:
                cta_instruction = f'End with: Comment {trigger} and tie it to the clip content. Product: {trigger_label} ({trigger_description}).'

            prompt = f"""Write a Reel caption for this clip.
TITLE: {clip.get('title','')}
TRANSCRIPT: {clip.get('transcript','')[:800]}
{cta_instruction}
5 hashtags. No em dashes. Angela's voice."""

            try:
                if not claude_client:
                    results.append({"clip_id": cid, "error": "ANTHROPIC_API_KEY not configured"})
                    continue
                response = claude_client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=500,
                    system=ANGELA_REEL_SYSTEM,
                    messages=[{"role": "user", "content": prompt}]
                )
                caption = response.content[0].text.strip()
                execute(
                    "UPDATE vizard_clips SET caption=%s, trigger_keyword=%s WHERE id=%s",
                    (caption, trigger, cid)
                )
                results.append({"clip_id": cid, "caption": caption, "success": True})
            except Exception as e:
                results.append({"clip_id": cid, "error": str(e), "success": False})

        return JSONResponse({"success": True, "results": results})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


# ── Refresh expired clip URLs ─────────────────────────────────
@router.post("/api/vizard/refresh/{project_id}")
async def refresh_urls(project_id: int):
    """Re-poll Vizard to get fresh download URLs (they expire after 7 days)."""
    try:
        project = query_one("SELECT * FROM vizard_projects WHERE id = %s", (project_id,))
        if not project:
            return JSONResponse({"success": False, "error": "Not found"})

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{VIZARD_BASE}/project/query/{project['vizard_project_id']}",
                headers=vizard_headers()
            )

        result = resp.json()
        if result.get("code") == 2000:
            for v in result.get("videos", []):
                execute(
                    "UPDATE vizard_clips SET video_url=%s WHERE vizard_video_id=%s AND project_id=%s",
                    (v.get("videoUrl", ""), v.get("videoId"), project_id)
                )
            return JSONResponse({"success": True, "refreshed": len(result.get("videos", []))})

        return JSONResponse({"success": False, "error": "Could not refresh"})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})
