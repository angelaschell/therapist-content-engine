"""
Carousel Template Generator
────────────────────────────
Analyze uploaded carousel screenshots with Claude Vision,
generate SVG templates that recreate the design,
save/load templates from Supabase.
"""

import os
import json
import base64
import uuid
import anthropic
import httpx
import psycopg2
import psycopg2.extras
from datetime import datetime, date
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/templates", tags=["templates"])


def serialize_row(row):
    """Convert a DB row dict so it's JSON-safe (dates, UUIDs, etc)."""
    out = {}
    for k, v in row.items():
        if isinstance(v, (datetime, date)):
            out[k] = v.isoformat()
        elif isinstance(v, uuid.UUID):
            out[k] = str(v)
        else:
            out[k] = v
    return out

claude_client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")


def get_db():
    """Get a direct Postgres connection (bypasses PostgREST entirely)."""
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def db_query(sql, params=None):
    """Run a SELECT query, return list of dicts."""
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(sql, params or ())
        rows = cur.fetchall()
        return [serialize_row(dict(r)) for r in rows]
    finally:
        conn.close()


def db_execute(sql, params=None):
    """Run an INSERT/UPDATE/DELETE, return affected rows."""
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(sql, params or ())
        conn.commit()
        try:
            rows = cur.fetchall()
            return [serialize_row(dict(r)) for r in rows]
        except Exception:
            return []
    finally:
        conn.close()


def sb_storage_upload(bucket, path, file_bytes, content_type):
    """Upload file to Supabase Storage via HTTP (storage still works fine)."""
    url = f"{SUPABASE_URL}/storage/v1/object/{bucket}/{path}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": content_type,
    }
    with httpx.Client(timeout=60) as client:
        resp = client.post(url, headers=headers, content=file_bytes)
        resp.raise_for_status()
        return f"{SUPABASE_URL}/storage/v1/object/public/{bucket}/{path}"


ANALYZE_PROMPT = """You are a visual design analyst. Analyze this Instagram carousel slide screenshot and extract the exact visual style properties.

Return ONLY valid JSON with no backticks:
{
  "bg_color": "#hex of the background color",
  "text_color": "#hex of the primary text color",
  "hook_bg": "#hex background for a hook/title slide (if different from body, otherwise same as bg_color)",
  "hook_text": "#hex text color for hook slide",
  "close_bg": "#hex background for closing slide (if different, otherwise same as bg_color)",
  "close_text": "#hex text color for closing slide",
  "title_style": "one of: serif-bold, serif-regular, sans-bold, sans-light, italic-serif, italic-sans, uppercase-sans, uppercase-serif",
  "body_style": "one of: serif-regular, serif-italic, sans-regular, sans-light, sans-medium",
  "text_size": "one of: small, medium, large, xlarge",
  "text_align": "one of: center, left, right",
  "spacing": "one of: tight, normal, spacious, massive",
  "watermark": true or false,
  "description": "2-3 sentence description of the overall visual style and what makes it distinctive",
  "suggested_name": "A short name for this style"
}

Be precise with hex colors. Look at actual pixels."""


SVG_GENERATE_PROMPT = """Look at this Instagram carousel slide screenshot carefully. Recreate its VISUAL DESIGN as three SVG slide templates.

Style analysis:
{analysis}

Generate THREE SVGs that MATCH the visual design of this screenshot. Each SVG is a slide background/layout with empty text zones where content will be injected later.

CRITICAL SVG RULES:
1. viewBox='0 0 1080 1350' and xmlns='http://www.w3.org/2000/svg'
2. Use SINGLE QUOTES for all SVG attributes (the JSON wrapper uses double quotes)
3. Start with a full <rect> for the background fill
4. Recreate ALL visual elements: decorative lines, borders, accent shapes, dividers, gradients, rounded rectangles, subtle patterns
5. If the design is minimal (solid color + text only), keep SVGs minimal too. Do not add elements that aren't in the screenshot.
6. DO NOT include the actual text words from the screenshot

TEXT ZONES - Include these in each SVG:
For the main text area:
<foreignObject x='80' y='[y-position]' width='920' height='[height]' class='tz'><div xmlns='http://www.w3.org/1999/xhtml' class='tz-main' style='color:[text_color]; font-family:[font]; font-size:[size]px; text-align:[align]; line-height:1.3; word-wrap:break-word;'></div></foreignObject>

For subtitle (hook slide mainly):
<foreignObject x='80' y='[y-position]' width='920' height='120' class='tz'><div xmlns='http://www.w3.org/1999/xhtml' class='tz-sub' style='color:[text_color]; font-family:[font]; font-size:[size]px; text-align:[align]; line-height:1.4; font-style:italic; opacity:0.7;'></div></foreignObject>

Watermark at bottom (if design has one):
<text x='540' y='1290' text-anchor='middle' font-family='Jost, sans-serif' font-size='22' letter-spacing='8' fill='[color]' opacity='0.35'>@ANGELASCHELLENBERG</text>

FONT SIZING GUIDE based on text_size:
- Hook title: small=52px, medium=64px, large=78px, xlarge=96px
- Body text: small=32px, medium=38px, large=44px, xlarge=52px
- Close text: small=36px, medium=42px, large=50px, xlarge=58px

FONTS AVAILABLE (use these exact names):
- Serif: 'Cormorant Garamond', serif
- Sans: 'Jost', sans-serif
- Display: 'Playfair Display', serif

THREE SLIDES:
1. svg_hook: Uses hook_bg + hook_text. Large prominent title zone centered vertically. Subtitle zone below it.
2. svg_body: Uses bg_color + text_color. Comfortable reading text zone, vertically centered.
3. svg_close: Uses close_bg + close_text. Slightly smaller, reflective feel. Text zone centered.

TEXT ZONE POSITIONING:
- Center the text zones vertically in the slide
- Hook: main text zone around y=350, height=450. Subtitle around y=820, height=120
- Body: main text zone around y=300, height=700
- Close: main text zone around y=380, height=500

Output ONLY valid JSON. No backticks. No explanation:
{{"svg_hook": "<svg viewBox='0 0 1080 1350' xmlns='http://www.w3.org/2000/svg'>...</svg>", "svg_body": "<svg viewBox='0 0 1080 1350' xmlns='http://www.w3.org/2000/svg'>...</svg>", "svg_close": "<svg viewBox='0 0 1080 1350' xmlns='http://www.w3.org/2000/svg'>...</svg>"}}"""


def detect_media_type(raw_data):
    if raw_data.startswith("data:image/jpeg"):
        return "image/jpeg"
    elif raw_data.startswith("data:image/webp"):
        return "image/webp"
    return "image/png"


def strip_base64_prefix(image_data):
    if "," in image_data:
        return image_data.split(",", 1)[1]
    return image_data


@router.post("/analyze")
async def analyze_template(req: Request):
    """Analyze screenshot(s) and extract style properties."""
    try:
        data = await req.json()
        image_data = data.get("image", "")
        images = data.get("images", [])
        notes = data.get("notes", "")
        slide_count = data.get("slide_count", 1)

        # Use images array if provided, otherwise fall back to single image
        if not images and image_data:
            images = [image_data]

        if not images:
            return JSONResponse({"success": False, "error": "No image provided"}, status_code=400)

        prompt = ANALYZE_PROMPT
        if len(images) > 1:
            prompt = f"""You are analyzing a COMPLETE Instagram carousel with {len(images)} slides. Look at ALL slides together to understand the full visual style.

{ANALYZE_PROMPT}

Since you can see multiple slides, pay close attention to:
- How the hook/first slide differs from body slides (different background or text color?)
- How the closing slide differs from body slides
- Consistent elements across all slides (fonts, spacing, watermarks, decorative elements)
- The overall color palette used across the set"""

        if notes:
            prompt += f"\n\nAdditional context: {notes}"

        # Build content array with all images
        messages_content = []
        for i, img in enumerate(images):
            media_type = detect_media_type(img)
            clean_image = strip_base64_prefix(img)
            messages_content.append({"type": "image", "source": {"type": "base64", "media_type": media_type, "data": clean_image}})
        messages_content.append({"type": "text", "text": prompt})

        response = claude_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            messages=[{"role": "user", "content": messages_content}]
        )

        clean = response.content[0].text.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        return JSONResponse({"success": True, "data": json.loads(clean)})

    except json.JSONDecodeError:
        return JSONResponse({"success": True, "data": {"error": "Could not parse. Try a clearer screenshot."}, "raw": response.content[0].text})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.post("/generate-svg")
async def generate_svg_templates(req: Request):
    """Generate three SVG slide templates from screenshot(s) + analysis."""
    try:
        data = await req.json()
        image_data = data.get("image", "")
        images = data.get("images", [])
        analysis = data.get("analysis", {})
        custom_fonts = data.get("custom_fonts", [])

        if not images and image_data:
            images = [image_data]

        if not images:
            return JSONResponse({"success": False, "error": "No image provided"}, status_code=400)

        prompt = SVG_GENERATE_PROMPT.format(analysis=json.dumps(analysis, indent=2))

        if len(images) > 1:
            prompt += f"\n\nIMPORTANT: You are looking at {len(images)} slides from the same carousel. Use slide 1 as the hook reference, the middle slides as body references, and the last slide as the close reference. Match each SVG to the actual slide type from the screenshots."

        if custom_fonts:
            font_list = ", ".join([f"'{f}'" for f in custom_fonts])
            prompt += f"\n\nADDITIONAL CUSTOM FONTS AVAILABLE (loaded via @font-face, use these if they match the screenshot better than the defaults): {font_list}"

        # Build content with all images
        messages_content = []
        for img in images:
            media_type = detect_media_type(img)
            clean_image = strip_base64_prefix(img)
            messages_content.append({"type": "image", "source": {"type": "base64", "media_type": media_type, "data": clean_image}})
        messages_content.append({"type": "text", "text": prompt})

        response = claude_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=8000,
            messages=[{"role": "user", "content": messages_content}]
        )

        clean = response.content[0].text.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        return JSONResponse({"success": True, "data": json.loads(clean)})

    except json.JSONDecodeError:
        raw_text = response.content[0].text if response else "No response"
        return JSONResponse({"success": False, "error": "Could not generate SVG templates. Try again.", "raw": raw_text[:500]})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.post("/regenerate-svg")
async def regenerate_svg_templates(req: Request):
    """Regenerate SVG templates with feedback."""
    try:
        data = await req.json()
        image_data = data.get("image", "")
        analysis = data.get("analysis", {})
        feedback = data.get("feedback", "")
        current_svgs = data.get("current_svgs", {})

        if not feedback:
            return JSONResponse({"success": False, "error": "No feedback provided"}, status_code=400)

        prompt = f"""You previously generated SVG carousel templates. The user wants changes: {feedback}

Style analysis: {json.dumps(analysis, indent=2)}

Current hook SVG (for reference): {current_svgs.get('svg_hook', '')[:600]}...

Regenerate all three SVGs applying the requested changes. Same rules as before:
- viewBox='0 0 1080 1350', xmlns='http://www.w3.org/2000/svg'
- Single quotes for SVG attributes
- foreignObject text zones with class='tz', inner divs class='tz-main' and 'tz-sub'
- Watermark text at bottom
- Empty text zone content

Return ONLY valid JSON:
{{"svg_hook": "<svg ...>...</svg>", "svg_body": "<svg ...>...</svg>", "svg_close": "<svg ...>...</svg>"}}"""

        messages_content = []
        images = data.get("images", [])
        if not images and image_data:
            images = [image_data]
        for img in images:
            media_type = detect_media_type(img)
            clean_image = strip_base64_prefix(img)
            messages_content.append({"type": "image", "source": {"type": "base64", "media_type": media_type, "data": clean_image}})
        messages_content.append({"type": "text", "text": prompt})

        response = claude_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=8000,
            messages=[{"role": "user", "content": messages_content}]
        )

        clean = response.content[0].text.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        return JSONResponse({"success": True, "data": json.loads(clean)})

    except json.JSONDecodeError:
        return JSONResponse({"success": False, "error": "Could not parse. Try different feedback."})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.post("/regenerate")
async def regenerate_template(req: Request):
    """Regenerate style analysis with feedback."""
    try:
        data = await req.json()
        previous = data.get("previous", {})
        feedback = data.get("feedback", "")
        image_data = data.get("image", "")

        if not feedback:
            return JSONResponse({"success": False, "error": "No feedback"}, status_code=400)

        prompt = f"""Previously extracted style: {json.dumps(previous, indent=2)}

Changes requested: {feedback}

Return updated JSON with same structure. ONLY valid JSON, no backticks."""

        messages_content = []
        images = data.get("images", [])
        if not images and image_data:
            images = [image_data]
        for img in images:
            media_type = detect_media_type(img)
            clean_image = strip_base64_prefix(img)
            messages_content.append({"type": "image", "source": {"type": "base64", "media_type": media_type, "data": clean_image}})
        messages_content.append({"type": "text", "text": prompt})

        response = claude_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            messages=[{"role": "user", "content": messages_content}]
        )

        clean = response.content[0].text.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        return JSONResponse({"success": True, "data": json.loads(clean)})

    except json.JSONDecodeError:
        return JSONResponse({"success": True, "data": {"error": "Could not parse."}, "raw": response.content[0].text})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.post("/save")
async def save_template(req: Request):
    """Save a template with SVGs to Supabase."""
    try:
        data = await req.json()
        name = data.get("name", "Untitled Template")
        template_data = data.get("template", {})
        svg_data = data.get("svg_data", {})
        original_image = data.get("original_image", "")

        image_url = ""
        if original_image and SUPABASE_URL:
            try:
                if "," in original_image:
                    img_bytes = base64.b64decode(original_image.split(",", 1)[1])
                else:
                    img_bytes = base64.b64decode(original_image)
                filename = f"template-refs/{uuid.uuid4().hex}.png"
                image_url = sb_storage_upload("brand-assets", filename, img_bytes, "image/png")
            except Exception as e:
                print(f"Image upload error (non-fatal): {e}")

        row = {
            "name": name,
            "bg_color": template_data.get("bg_color", "#F7EBE0"),
            "text_color": template_data.get("text_color", "#D2C7FF"),
            "hook_bg": template_data.get("hook_bg", "#3B2145"),
            "hook_text": template_data.get("hook_text", "#F7EBE0"),
            "close_bg": template_data.get("close_bg", "#90A9EC"),
            "close_text": template_data.get("close_text", "#F7EBE0"),
            "title_style": template_data.get("title_style", "serif-bold"),
            "body_style": template_data.get("body_style", "serif-regular"),
            "text_size": template_data.get("text_size", "large"),
            "text_align": template_data.get("text_align", "center"),
            "spacing": template_data.get("spacing", "spacious"),
            "watermark": template_data.get("watermark", True),
            "description": template_data.get("description", ""),
            "original_image_url": image_url,
            "full_analysis": json.dumps(template_data),
            "svg_template": json.dumps(svg_data) if svg_data else None,
        }

        result = db_execute(
            """INSERT INTO carousel_templates (name, bg_color, text_color, hook_bg, hook_text, close_bg, close_text,
               title_style, body_style, text_size, text_align, spacing, watermark, description, original_image_url,
               full_analysis, svg_template) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
            (row["name"], row["bg_color"], row["text_color"], row["hook_bg"], row["hook_text"],
             row["close_bg"], row["close_text"], row["title_style"], row["body_style"], row["text_size"],
             row["text_align"], row["spacing"], row["watermark"], row["description"], row["original_image_url"],
             row["full_analysis"], row["svg_template"])
        )

        if result and len(result) > 0:
            return JSONResponse({"success": True, "template": result[0]})
        return JSONResponse({"success": False, "error": "Insert returned no data"}, status_code=500)

    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.get("/")
async def list_templates():
    """List all saved templates."""
    try:
        result = db_query("SELECT id,name,bg_color,text_color,hook_bg,hook_text,close_bg,close_text,title_style,body_style,text_size,text_align,spacing,watermark,description,original_image_url,svg_template,created_at FROM carousel_templates ORDER BY created_at DESC")
        templates = []
        for t in (result or []):
            has_svg = bool(t.get("svg_template"))
            t["has_svg"] = has_svg
            if has_svg:
                t["svg_template"] = "yes"
            templates.append(t)
        return JSONResponse({"success": True, "templates": templates})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e), "templates": []})


# ── CUSTOM FONTS (must be before /{template_id} catch-all) ──

@router.post("/fonts/upload")
async def upload_font(req: Request):
    """Upload a custom font file to Supabase storage and register it."""
    try:
        data = await req.json()
        font_name = data.get("name", "")
        font_family = data.get("font_family", "")
        font_data = data.get("file", "")

        if not font_name or not font_data:
            return JSONResponse({"success": False, "error": "Name and file required"}, status_code=400)

        if not font_family:
            font_family = font_name

        if "," in font_data:
            file_bytes = base64.b64decode(font_data.split(",", 1)[1])
        else:
            file_bytes = base64.b64decode(font_data)

        ext = "ttf"
        if data.get("filename", "").endswith(".otf"):
            ext = "otf"
        elif data.get("filename", "").endswith(".woff2"):
            ext = "woff2"
        elif data.get("filename", "").endswith(".woff"):
            ext = "woff"

        filename = f"fonts/{uuid.uuid4().hex}.{ext}"
        content_types = {"ttf": "font/ttf", "otf": "font/otf", "woff": "font/woff", "woff2": "font/woff2"}

        file_url = sb_storage_upload("brand-assets", filename, file_bytes, content_types.get(ext, "font/ttf"))

        row = {"name": font_name, "font_family": font_family, "file_url": file_url}
        result = db_execute(
            "INSERT INTO custom_fonts (name, font_family, file_url) VALUES (%s, %s, %s) RETURNING *",
            (font_name, font_family, file_url)
        )

        if result and len(result) > 0:
            return JSONResponse({"success": True, "font": result[0]})
        return JSONResponse({"success": False, "error": "Insert failed"}, status_code=500)

    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.get("/fonts/list")
async def list_fonts():
    """List all custom fonts."""
    try:
        result = db_query("SELECT * FROM custom_fonts ORDER BY created_at DESC")
        return JSONResponse({"success": True, "fonts": result or []})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e), "fonts": []})


@router.delete("/fonts/{font_id}")
async def delete_font(font_id: str):
    """Delete a custom font."""
    try:
        db_execute("DELETE FROM custom_fonts WHERE id = %s", (font_id,))
        return JSONResponse({"success": True})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# ── TEMPLATE BY ID (catch-all, must be last) ──

@router.get("/{template_id}")
async def get_template(template_id: str):
    """Get a single template with full SVG data."""
    try:
        result = db_query("SELECT * FROM carousel_templates WHERE id = %s", (template_id,))
        if result and len(result) > 0:
            return JSONResponse({"success": True, "template": result[0]})
        return JSONResponse({"success": False, "error": "Not found"}, status_code=404)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.delete("/{template_id}")
async def delete_template(template_id: str):
    """Delete a template."""
    try:
        db_execute("DELETE FROM carousel_templates WHERE id = %s", (template_id,))
        return JSONResponse({"success": True})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)
