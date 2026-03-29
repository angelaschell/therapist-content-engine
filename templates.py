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
import anthropic
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from supabase import create_client

router = APIRouter(prefix="/api/templates", tags=["templates"])

claude_client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")


def get_supabase():
    return create_client(SUPABASE_URL, SUPABASE_KEY)


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
    """Analyze screenshot and extract style properties."""
    try:
        data = await req.json()
        image_data = data.get("image", "")
        notes = data.get("notes", "")

        if not image_data:
            return JSONResponse({"success": False, "error": "No image provided"}, status_code=400)

        media_type = detect_media_type(image_data)
        clean_image = strip_base64_prefix(image_data)

        prompt = ANALYZE_PROMPT
        if notes:
            prompt += f"\n\nAdditional context: {notes}"

        response = claude_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": clean_image}},
                    {"type": "text", "text": prompt}
                ]
            }]
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
    """Generate three SVG slide templates from screenshot + analysis."""
    try:
        data = await req.json()
        image_data = data.get("image", "")
        analysis = data.get("analysis", {})
        custom_fonts = data.get("custom_fonts", [])

        if not image_data:
            return JSONResponse({"success": False, "error": "No image provided"}, status_code=400)

        media_type = detect_media_type(image_data)
        clean_image = strip_base64_prefix(image_data)
        prompt = SVG_GENERATE_PROMPT.format(analysis=json.dumps(analysis, indent=2))

        if custom_fonts:
            font_list = ", ".join([f"'{f}'" for f in custom_fonts])
            prompt += f"\n\nADDITIONAL CUSTOM FONTS AVAILABLE (loaded via @font-face, use these if they match the screenshot better than the defaults): {font_list}"

        response = claude_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=8000,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": clean_image}},
                    {"type": "text", "text": prompt}
                ]
            }]
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
        if image_data:
            media_type = detect_media_type(image_data)
            clean_image = strip_base64_prefix(image_data)
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
        if image_data:
            media_type = detect_media_type(image_data)
            clean_image = strip_base64_prefix(image_data)
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

        sb = get_supabase()

        image_url = ""
        if original_image and SUPABASE_URL:
            try:
                if "," in original_image:
                    img_bytes = base64.b64decode(original_image.split(",", 1)[1])
                else:
                    img_bytes = base64.b64decode(original_image)
                import uuid
                filename = f"template-refs/{uuid.uuid4().hex}.png"
                sb.storage.from_("brand-assets").upload(filename, img_bytes, {"content-type": "image/png"})
                image_url = f"{SUPABASE_URL}/storage/v1/object/public/brand-assets/{filename}"
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

        result = sb.table("carousel_templates").insert(row).execute()

        if result.data and len(result.data) > 0:
            return JSONResponse({"success": True, "template": result.data[0]})
        return JSONResponse({"success": False, "error": "Insert returned no data"}, status_code=500)

    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.get("/")
async def list_templates():
    """List all saved templates."""
    try:
        sb = get_supabase()
        result = sb.table("carousel_templates").select("id,name,bg_color,text_color,hook_bg,hook_text,close_bg,close_text,title_style,body_style,text_size,text_align,spacing,watermark,description,original_image_url,svg_template,created_at").order("created_at", desc=True).execute()
        templates = []
        for t in (result.data or []):
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

        sb = get_supabase()

        if "," in font_data:
            file_bytes = base64.b64decode(font_data.split(",", 1)[1])
        else:
            file_bytes = base64.b64decode(font_data)

        import uuid
        ext = "ttf"
        if data.get("filename", "").endswith(".otf"):
            ext = "otf"
        elif data.get("filename", "").endswith(".woff2"):
            ext = "woff2"
        elif data.get("filename", "").endswith(".woff"):
            ext = "woff"

        filename = f"fonts/{uuid.uuid4().hex}.{ext}"
        content_types = {"ttf": "font/ttf", "otf": "font/otf", "woff": "font/woff", "woff2": "font/woff2"}

        sb.storage.from_("brand-assets").upload(filename, file_bytes, {"content-type": content_types.get(ext, "font/ttf")})
        file_url = f"{SUPABASE_URL}/storage/v1/object/public/brand-assets/{filename}"

        row = {"name": font_name, "font_family": font_family, "file_url": file_url}
        result = sb.table("custom_fonts").insert(row).execute()

        if result.data and len(result.data) > 0:
            return JSONResponse({"success": True, "font": result.data[0]})
        return JSONResponse({"success": False, "error": "Insert failed"}, status_code=500)

    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.get("/fonts/list")
async def list_fonts():
    """List all custom fonts."""
    try:
        sb = get_supabase()
        result = sb.table("custom_fonts").select("*").order("created_at", desc=True).execute()
        return JSONResponse({"success": True, "fonts": result.data or []})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e), "fonts": []})


@router.delete("/fonts/{font_id}")
async def delete_font(font_id: str):
    """Delete a custom font."""
    try:
        sb = get_supabase()
        sb.table("custom_fonts").delete().eq("id", font_id).execute()
        return JSONResponse({"success": True})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# ── TEMPLATE BY ID (catch-all, must be last) ──

@router.get("/{template_id}")
async def get_template(template_id: str):
    """Get a single template with full SVG data."""
    try:
        sb = get_supabase()
        result = sb.table("carousel_templates").select("*").eq("id", template_id).execute()
        if result.data and len(result.data) > 0:
            return JSONResponse({"success": True, "template": result.data[0]})
        return JSONResponse({"success": False, "error": "Not found"}, status_code=404)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.delete("/{template_id}")
async def delete_template(template_id: str):
    """Delete a template."""
    try:
        sb = get_supabase()
        sb.table("carousel_templates").delete().eq("id", template_id).execute()
        return JSONResponse({"success": True})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)
