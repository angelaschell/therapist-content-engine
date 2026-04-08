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

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")


def get_db():
    """Get a direct Postgres connection (bypasses PostgREST entirely)."""
    if not DATABASE_URL:
        raise Exception("DATABASE_URL not configured")
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

IMPORTANT: Look at EVERYTHING in the screenshot — not just text and background color. Describe ALL decorative elements (shapes, lines, patterns, borders, textures, gradients, illustrations, ornaments, frames) in the description field. This description is used to generate the SVG template, so be extremely specific about what you see.

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
  "decorative_elements": "Detailed description of ALL visual elements beyond solid color: e.g. 'flowing pastel curves in blue #A8C4E8 and yellow #E8C97A across top-left and bottom-right corners, organic ribbon shapes with 20-30px stroke width at 30-40% opacity'. If the design is just solid color + text, say 'none'. Be specific about colors, positions, sizes, opacity, and shapes.",
  "description": "2-3 sentence description of the overall visual style and what makes it distinctive. Include details about decorative elements, not just colors and fonts.",
  "suggested_name": "A short name for this style"
}

Be precise with hex colors. Look at actual pixels. Pay special attention to decorative/ornamental elements — they are what make each template unique."""


SVG_GENERATE_PROMPT = """You are an expert SVG designer. Your job is to look at the Instagram carousel slide screenshot(s) and produce SVG code that VISUALLY MATCHES the screenshot as closely as possible.

This is the MOST IMPORTANT RULE: The SVG output must look like the screenshot when rendered. If someone looked at the screenshot and then at your SVG, they should think it is the same design. Do not simplify. Do not skip elements. RECREATE EVERYTHING YOU SEE.

Style analysis for reference:
{analysis}

WHAT TO RECREATE — examine the screenshot pixel by pixel:
- Background color or gradient (if gradient, use <linearGradient> or <radialGradient>)
- ALL decorative elements: shapes, lines, curves, borders, frames, circles, blobs, squiggles, chains, ribbons, patterns, ornaments, dividers, accent marks, corner decorations
- Background textures or patterns (use SVG <pattern> elements or repeated shapes)
- Any borders, frames, or outlines around the slide or text area
- Drop shadows, glows, or overlay effects (use <filter> elements)
- Color relationships: if some decorative elements are lighter/semi-transparent, match that opacity
- If the design has organic flowing shapes, reproduce them with <path> curves using bezier control points

DO NOT just make a solid-color rectangle with text on it. That is WRONG unless the screenshot is literally just a solid color with text.

SVG RULES:
1. viewBox='0 0 1080 1350' and xmlns='http://www.w3.org/2000/svg'
2. Use SINGLE QUOTES for SVG attributes (JSON wrapper uses double quotes)
3. Start with the full background (rect, gradient, or pattern)
4. Then add ALL decorative/visual elements you see in the screenshot
5. Then add the text zones (foreignObject) on top
6. DO NOT include the actual text words from the screenshot — leave text zones empty

TEXT ZONES — Include these in each SVG:
Main text:
<foreignObject x='80' y='[y]' width='920' height='[h]' class='tz'><div xmlns='http://www.w3.org/1999/xhtml' class='tz-main' style='color:[color]; font-family:[font]; font-size:[size]px; text-align:[align]; line-height:1.3; word-wrap:break-word;'></div></foreignObject>

Subtitle (hook slide):
<foreignObject x='80' y='[y]' width='920' height='120' class='tz'><div xmlns='http://www.w3.org/1999/xhtml' class='tz-sub' style='color:[color]; font-family:[font]; font-size:[size]px; text-align:[align]; line-height:1.4; font-style:italic; opacity:0.7;'></div></foreignObject>

Watermark (if visible in design):
<text x='540' y='1290' text-anchor='middle' font-family='Jost, sans-serif' font-size='22' letter-spacing='8' fill='[color]' opacity='0.35'>@ANGELASCHELLENBERG</text>

FONT SIZING: Hook title: small=52, medium=64, large=78, xlarge=96. Body: small=32, medium=38, large=44, xlarge=52. Close: small=36, medium=42, large=50, xlarge=58.

FONTS: 'Cormorant Garamond', serif | 'Jost', sans-serif | 'Playfair Display', serif

THREE SLIDES:
1. svg_hook — Hook/title slide. Match the screenshot's hook design: background + decorations + large title zone + subtitle zone.
2. svg_body — Body content slide. Same visual style and decorations as the screenshot, with a reading text zone.
3. svg_close — Closing slide. Same visual style. Text zone centered, reflective feel.

If the screenshot shows the same background/decorations across all slides, use the same decorative elements in all three SVGs.

Output ONLY valid JSON. No backticks. No explanation:
{{"svg_hook": "<svg viewBox='0 0 1080 1350' xmlns='http://www.w3.org/2000/svg'>...</svg>", "svg_body": "<svg viewBox='0 0 1080 1350' xmlns='http://www.w3.org/2000/svg'>...</svg>", "svg_close": "<svg viewBox='0 0 1080 1350' xmlns='http://www.w3.org/2000/svg'>...</svg>"}}""""""


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

        if not claude_client:
            return JSONResponse({"success": False, "error": "ANTHROPIC_API_KEY not configured"}, status_code=500)
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
    """Generate three SVG slide templates using the uploaded image as the background.
    The image is stored in Supabase and embedded as an <image> element in the SVG.
    No AI generation of decorative elements — pixel-perfect backgrounds every time."""
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

        # Upload images to Supabase storage for permanent hosting
        image_urls = []
        for i, img in enumerate(images):
            uploaded = False
            if SUPABASE_URL and SUPABASE_KEY:
                try:
                    clean_img = strip_base64_prefix(img)
                    img_bytes = base64.b64decode(clean_img)
                    ext = "png"
                    if img.startswith("data:image/jpeg"):
                        ext = "jpg"
                    elif img.startswith("data:image/webp"):
                        ext = "webp"
                    filename = f"template-bgs/{uuid.uuid4().hex}.{ext}"
                    url = sb_storage_upload("brand-assets", filename, img_bytes, f"image/{ext}")
                    if url and url.startswith("http"):
                        image_urls.append(url)
                        uploaded = True
                        print(f"[TEMPLATE] Image {i} uploaded to: {url}")
                    else:
                        print(f"[TEMPLATE] Image upload returned invalid URL: {url}")
                except Exception as e:
                    print(f"[TEMPLATE] Image upload error: {e}")
            if not uploaded:
                # Fallback: embed as base64 data URI directly in SVG
                media_type = detect_media_type(img)
                clean_img = strip_base64_prefix(img)
                data_uri = f"data:{media_type};base64,{clean_img}"
                image_urls.append(data_uri)
                print(f"[TEMPLATE] Image {i} using base64 fallback ({len(clean_img)} chars)")

        # Use the uploaded images as backgrounds:
        # - 1 image: same background for all three slide types
        # - 2 images: first for hook, second for body and close
        # - 3+ images: first for hook, middle for body, last for close
        hook_bg_url = image_urls[0]
        body_bg_url = image_urls[1] if len(image_urls) > 1 else image_urls[0]
        close_bg_url = image_urls[-1] if len(image_urls) > 2 else body_bg_url

        # Extract style info from analysis
        hook_text = analysis.get("hook_text", "#F7EBE0")
        text_color = analysis.get("text_color", "#D2C7FF")
        close_text = analysis.get("close_text", "#F7EBE0")
        text_size = analysis.get("text_size", "large")
        text_align = analysis.get("text_align", "center")
        title_style = analysis.get("title_style", "sans-bold")
        body_style = analysis.get("body_style", "serif-regular")
        watermark = analysis.get("watermark", True)

        # Font mapping
        font_map = {
            "serif-bold": ("'Cormorant Garamond',serif", "700", "normal"),
            "serif-regular": ("'Cormorant Garamond',serif", "400", "normal"),
            "serif-italic": ("'Cormorant Garamond',serif", "400", "italic"),
            "sans-bold": ("'Jost',sans-serif", "700", "normal"),
            "sans-light": ("'Jost',sans-serif", "300", "normal"),
            "sans-regular": ("'Jost',sans-serif", "400", "normal"),
            "sans-medium": ("'Jost',sans-serif", "500", "normal"),
            "italic-serif": ("'Cormorant Garamond',serif", "400", "italic"),
            "italic-sans": ("'Jost',sans-serif", "400", "italic"),
            "uppercase-sans": ("'Jost',sans-serif", "700", "normal"),
            "uppercase-serif": ("'Cormorant Garamond',serif", "700", "normal"),
        }

        # Use custom font if available
        title_font_family, title_font_weight, title_font_style = font_map.get(title_style, font_map["sans-bold"])
        body_font_family, body_font_weight, body_font_style = font_map.get(body_style, font_map["serif-regular"])

        if custom_fonts:
            title_font_family = f"'{custom_fonts[0]}',{title_font_family}"
            body_font_family = f"'{custom_fonts[0]}',{body_font_family}"

        title_transform = "text-transform:uppercase;" if "uppercase" in title_style else ""

        # Size mapping
        size_map = {"small": (52, 32, 36), "medium": (64, 38, 42), "large": (78, 44, 50), "xlarge": (96, 52, 58)}
        hook_size, body_size, close_size = size_map.get(text_size, size_map["large"])

        # Text zone positioning (from drag editor, percentages → 1080x1350 pixels)
        text_zone = analysis.get("text_zone", {})
        tz_x = int(text_zone.get("x", 7) * 1080 / 100)
        tz_y = int(text_zone.get("y", 25) * 1350 / 100)
        tz_w = int(text_zone.get("w", 85) * 1080 / 100)
        tz_h = int(text_zone.get("h", 50) * 1350 / 100)
        # Hook subtitle position: below the main text zone
        sub_y = tz_y + tz_h + 20

        # Watermark SVG
        wm = ""
        if watermark:
            wm = f"<text x='540' y='1280' text-anchor='middle' font-family='Jost,sans-serif' font-size='22' letter-spacing='4' fill='{text_color}' opacity='0.35'>@ANGELASCHELLENBERG</text>"

        # Build deterministic SVGs with image backgrounds
        svg_hook = f"""<svg viewBox='0 0 1080 1350' xmlns='http://www.w3.org/2000/svg'>
<image href='{hook_bg_url}' x='0' y='0' width='1080' height='1350' preserveAspectRatio='xMidYMid slice'/>
<foreignObject x='{tz_x}' y='{tz_y}' width='{tz_w}' height='{tz_h}' class='tz'>
  <div xmlns='http://www.w3.org/1999/xhtml' class='tz-main' style='color:{hook_text}; font-family:{title_font_family}; font-size:{hook_size}px; font-weight:{title_font_weight}; font-style:{title_font_style}; {title_transform} text-align:{text_align}; line-height:1.2; word-wrap:break-word;'></div>
</foreignObject>
<foreignObject x='{tz_x}' y='{sub_y}' width='{tz_w}' height='100' class='tz'>
  <div xmlns='http://www.w3.org/1999/xhtml' class='tz-sub' style='color:{hook_text}; font-family:{body_font_family}; font-size:24px; font-weight:400; text-align:{text_align}; line-height:1.4; letter-spacing:3px; text-transform:uppercase; opacity:0.7;'></div>
</foreignObject>
{wm}
</svg>"""

        svg_body = f"""<svg viewBox='0 0 1080 1350' xmlns='http://www.w3.org/2000/svg'>
<image href='{body_bg_url}' x='0' y='0' width='1080' height='1350' preserveAspectRatio='xMidYMid slice'/>
<foreignObject x='{tz_x}' y='{tz_y}' width='{tz_w}' height='{tz_h}' class='tz'>
  <div xmlns='http://www.w3.org/1999/xhtml' class='tz-main' style='color:{text_color}; font-family:{body_font_family}; font-size:{body_size}px; font-weight:{body_font_weight}; font-style:{body_font_style}; text-align:{text_align}; line-height:1.5; word-wrap:break-word;'></div>
</foreignObject>
</svg>"""

        svg_close = f"""<svg viewBox='0 0 1080 1350' xmlns='http://www.w3.org/2000/svg'>
<image href='{close_bg_url}' x='0' y='0' width='1080' height='1350' preserveAspectRatio='xMidYMid slice'/>
<foreignObject x='{tz_x}' y='{tz_y}' width='{tz_w}' height='{tz_h}' class='tz'>
  <div xmlns='http://www.w3.org/1999/xhtml' class='tz-main' style='color:{close_text}; font-family:{body_font_family}; font-size:{close_size}px; font-weight:{body_font_weight}; font-style:italic; text-align:{text_align}; line-height:1.45; word-wrap:break-word;'></div>
</foreignObject>
{wm}
</svg>"""

        return JSONResponse({"success": True, "data": {
            "svg_hook": svg_hook,
            "svg_body": svg_body,
            "svg_close": svg_close,
            "bg_urls": {"hook": hook_bg_url, "body": body_bg_url, "close": close_bg_url}
        }})

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

        if not claude_client:
            return JSONResponse({"success": False, "error": "ANTHROPIC_API_KEY not configured"}, status_code=500)
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

        if not claude_client:
            return JSONResponse({"success": False, "error": "ANTHROPIC_API_KEY not configured"}, status_code=500)
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
        logo_image = data.get("logo_image", "")

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

        logo_url = template_data.get("logo_url", "")
        if logo_image and SUPABASE_URL:
            try:
                if "," in logo_image:
                    logo_bytes = base64.b64decode(logo_image.split(",", 1)[1])
                else:
                    logo_bytes = base64.b64decode(logo_image)
                logo_filename = f"template-logos/{uuid.uuid4().hex}.png"
                logo_url = sb_storage_upload("brand-assets", logo_filename, logo_bytes, "image/png")
            except Exception as e:
                print(f"Logo upload error (non-fatal): {e}")

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
            "watermark": bool(template_data.get("watermark", True)) if not isinstance(template_data.get("watermark"), bool) else template_data.get("watermark", True),
            "description": template_data.get("description", ""),
            "original_image_url": image_url,
            "full_analysis": json.dumps({**template_data, "logo_url": logo_url}),
            "svg_template": json.dumps(svg_data) if svg_data else None,
        }

        # Add logo_url column if it doesn't exist
        try:
            db_execute("ALTER TABLE carousel_templates ADD COLUMN IF NOT EXISTS logo_url TEXT DEFAULT ''")
        except Exception:
            pass

        result = db_execute(
            """INSERT INTO carousel_templates (name, bg_color, text_color, hook_bg, hook_text, close_bg, close_text,
               title_style, body_style, text_size, text_align, spacing, watermark, description, original_image_url,
               full_analysis, svg_template, logo_url) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
            (row["name"], row["bg_color"], row["text_color"], row["hook_bg"], row["hook_text"],
             row["close_bg"], row["close_text"], row["title_style"], row["body_style"], row["text_size"],
             row["text_align"], row["spacing"], row["watermark"], row["description"], row["original_image_url"],
             row["full_analysis"], row["svg_template"], logo_url)
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
        result = db_query("SELECT id,name,bg_color,text_color,hook_bg,hook_text,close_bg,close_text,title_style,body_style,text_size,text_align,spacing,watermark,description,original_image_url,svg_template,logo_url,created_at FROM carousel_templates ORDER BY created_at DESC")
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


try:
    if DATABASE_URL:
        db_execute("ALTER TABLE carousel_templates ADD COLUMN IF NOT EXISTS logo_url TEXT DEFAULT ''")
        # Remove old auto-seeded GTYM template only (has specific description from seed code)
        db_execute("DELETE FROM carousel_templates WHERE name = 'Grief Trauma & Your Mama' AND description = 'Soft cream background with flowing pastel chain decorations in blue, yellow, and pink. Playful bold sans-serif hook with serif body text in muted blue. Designed for the Grief Trauma & Your Mama brand.'")
except Exception:
    pass
