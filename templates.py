"""
Carousel Template Generator
────────────────────────────
Analyze uploaded carousel screenshots with Claude Vision,
extract style properties, save/load templates from Supabase.
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
  "watermark": true or false (does it have a handle/watermark at the bottom),
  "description": "2-3 sentence description of the overall visual style, vibe, and what makes it distinctive",
  "suggested_name": "A short memorable name for this template style (e.g. 'Warm Cream Minimal', 'Dark Bold Statement', 'Soft Blue Editorial')"
}

Be precise with hex colors. Look at the actual pixels, not what you think it should be. If you see warm cream/beige, give the exact hex. If you see lavender text, give the exact hex."""


REGEN_PROMPT = """You previously analyzed a carousel screenshot and extracted these style properties:

{previous}

The user wants these changes: {feedback}

Return the UPDATED JSON with the same structure, applying the requested changes. Return ONLY valid JSON with no backticks:
{{
  "bg_color": "#hex",
  "text_color": "#hex",
  "hook_bg": "#hex",
  "hook_text": "#hex",
  "close_bg": "#hex",
  "close_text": "#hex",
  "title_style": "...",
  "body_style": "...",
  "text_size": "...",
  "text_align": "...",
  "spacing": "...",
  "watermark": true/false,
  "description": "updated description",
  "suggested_name": "updated name if needed"
}}"""


@router.post("/analyze")
async def analyze_template(req: Request):
    """Analyze an uploaded carousel screenshot and extract style properties."""
    try:
        data = await req.json()
        image_data = data.get("image", "")
        notes = data.get("notes", "")

        if not image_data:
            return JSONResponse({"success": False, "error": "No image provided"}, status_code=400)

        # Strip data URL prefix if present
        if "," in image_data:
            image_data = image_data.split(",", 1)[1]

        # Detect media type
        media_type = "image/png"
        raw = data.get("image", "")
        if raw.startswith("data:image/jpeg"):
            media_type = "image/jpeg"
        elif raw.startswith("data:image/webp"):
            media_type = "image/webp"

        prompt = ANALYZE_PROMPT
        if notes:
            prompt += f"\n\nAdditional context from the user: {notes}"

        response = claude_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_data
                        }
                    },
                    {
                        "type": "text",
                        "text": prompt
                    }
                ]
            }]
        )

        clean = response.content[0].text.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        result = json.loads(clean)
        return JSONResponse({"success": True, "data": result})

    except json.JSONDecodeError:
        return JSONResponse({"success": True, "data": {"error": "Could not parse style. Try a clearer screenshot."}, "raw": response.content[0].text})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.post("/regenerate")
async def regenerate_template(req: Request):
    """Regenerate template with feedback, optionally re-reading the original image."""
    try:
        data = await req.json()
        previous = data.get("previous", {})
        feedback = data.get("feedback", "")
        image_data = data.get("image", "")

        if not feedback:
            return JSONResponse({"success": False, "error": "No feedback provided"}, status_code=400)

        prompt = REGEN_PROMPT.format(
            previous=json.dumps(previous, indent=2),
            feedback=feedback
        )

        messages_content = []

        # If image is provided, include it for re-analysis
        if image_data:
            if "," in image_data:
                image_data = image_data.split(",", 1)[1]
            media_type = "image/png"
            raw = data.get("image", "")
            if raw.startswith("data:image/jpeg"):
                media_type = "image/jpeg"
            elif raw.startswith("data:image/webp"):
                media_type = "image/webp"

            messages_content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": image_data
                }
            })

        messages_content.append({"type": "text", "text": prompt})

        response = claude_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            messages=[{"role": "user", "content": messages_content}]
        )

        clean = response.content[0].text.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        result = json.loads(clean)
        return JSONResponse({"success": True, "data": result})

    except json.JSONDecodeError:
        return JSONResponse({"success": True, "data": {"error": "Could not parse. Try simpler feedback."}, "raw": response.content[0].text})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.post("/save")
async def save_template(req: Request):
    """Save a template to Supabase."""
    try:
        data = await req.json()
        name = data.get("name", "Untitled Template")
        template_data = data.get("template", {})
        original_image = data.get("original_image", "")

        sb = get_supabase()

        # Upload original image to storage if provided
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
                image_url = ""

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
        }

        result = sb.table("carousel_templates").insert(row).execute()

        if result.data and len(result.data) > 0:
            saved = result.data[0]
            return JSONResponse({"success": True, "template": saved})
        else:
            return JSONResponse({"success": False, "error": "Insert returned no data"}, status_code=500)

    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.get("/")
async def list_templates():
    """List all saved templates."""
    try:
        sb = get_supabase()
        result = sb.table("carousel_templates").select("*").order("created_at", desc=True).execute()
        return JSONResponse({"success": True, "templates": result.data or []})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e), "templates": []})


@router.delete("/{template_id}")
async def delete_template(template_id: str):
    """Delete a template."""
    try:
        sb = get_supabase()
        sb.table("carousel_templates").delete().eq("id", template_id).execute()
        return JSONResponse({"success": True})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)
