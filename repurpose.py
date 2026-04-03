# Content Repurposing Pipeline - Convert carousels into newsletters, blogs, and reel scripts
import os
import json
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

FORMATS = {
    "newsletter": {
        "name": "Flodesk Newsletter",
        "instruction": """Convert this Instagram carousel into an email newsletter for Flodesk.

FORMAT:
- Subject line (under 50 characters, curiosity-driven)
- Preview text (under 90 characters)
- Body: 3-5 short paragraphs in Angela's voice. Expand on the carousel's emotional truth with slightly more depth than Instagram allows. Each paragraph is 2-4 sentences.
- End with a soft CTA (reply to this email, book a session, join the community)
- Keep it intimate. This is a letter to someone who already follows you."""
    },
    "blog": {
        "name": "Blog Post",
        "instruction": """Convert this Instagram carousel into a blog post (600-900 words).

FORMAT:
- SEO-friendly title (include a keyword like grief, trauma, motherless, healing)
- Opening hook paragraph that expands on the carousel's emotional truth
- 3-4 sections with subheadings (use ## for H2)
- Weave in clinical knowledge naturally (attachment theory, somatic experiencing, EMDR, nervous system)
- Include specific examples and scenarios
- Closing paragraph with a gentle invitation (not a hard sell)
- Angela's voice throughout: poetic but grounded, clinical but warm"""
    },
    "reel_script": {
        "name": "Reel Script (60s)",
        "instruction": """Convert this Instagram carousel into a 60-second talking-head reel script.

FORMAT:
- HOOK (first 3 seconds): One punchy sentence that stops the scroll. Start with the most provocative or emotionally resonant point from the carousel.
- BODY (45 seconds): 3-4 key points from the carousel, rewritten as spoken language. Short sentences. Pauses marked with [pause]. Direct eye contact energy.
- CLOSE (10 seconds): Reframe or invitation. End with the CTA if provided.
- [B-ROLL NOTES]: Brief suggestions for any visual cutaways

Rules: Write it exactly as Angela would SAY it, not read it. Conversational. Rhythmic. No em dashes. Include [pause] markers for emotional beats."""
    },
    "thread": {
        "name": "Twitter/X Thread",
        "instruction": """Convert this Instagram carousel into a Twitter/X thread (8-12 tweets).

FORMAT:
- Tweet 1: Hook. Bold claim or emotional truth that makes someone stop scrolling. Under 280 characters.
- Tweets 2-10: One point per tweet. Expand on each carousel slide. Can be slightly more casual than Instagram. Use line breaks for rhythm.
- Final tweet: Reframe + soft CTA. "If this resonated, I help women [specific thing]. Link in bio."
- Each tweet must stand alone AND flow as a thread.
- No hashtags in individual tweets. Add 2-3 at the very end only."""
    }
}


async def generate_format(topic, slides_text, caption, trigger_keyword, format_key):
    fmt = FORMATS[format_key]
    prompt = f"""{fmt['instruction']}

ORIGINAL CAROUSEL TOPIC: {topic}

CAROUSEL SLIDES:
{slides_text}

ORIGINAL CAPTION:
{caption[:1500]}

{"CTA TRIGGER: Comment " + trigger_keyword if trigger_keyword else "No CTA trigger."}

Return ONLY the content. No preamble, no "Here's the..." intro."""

    try:
        async with httpx.AsyncClient(timeout=45) as client:
            resp = await client.post("https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 2000,
                    "system": "You are Angela Schellenberg, a licensed grief and trauma therapist with 171K Instagram followers. Write exactly as Angela would. Short punchy lines. Show don't tell. Trust the reader. No em dashes.",
                    "messages": [{"role": "user", "content": prompt}]
                })
            resp.raise_for_status()
            ai_data = resp.json()
            return "".join(b["text"] for b in ai_data.get("content", []) if b.get("type") == "text").strip()
    except Exception as e:
        return f"[Generation failed: {str(e)[:200]}]"


@router.post("/api/repurpose")
async def repurpose_content(req: Request):
    """Convert a carousel into multiple content formats."""
    data = await req.json()
    topic = data.get("topic", "")
    slides = data.get("slides", [])
    caption = data.get("caption", "")
    trigger_keyword = data.get("trigger_keyword", "")
    formats = data.get("formats", list(FORMATS.keys()))

    if not topic and not slides:
        return JSONResponse({"error": "Topic or slides required"}, status_code=400)

    slides_text = "\n".join([
        f"Slide {i+1}: {s.get('html', s.get('text', s.get('upper', '')))}"
        for i, s in enumerate(slides)
    ])

    results = {}
    for fmt_key in formats:
        if fmt_key in FORMATS:
            results[fmt_key] = {
                "name": FORMATS[fmt_key]["name"],
                "content": await generate_format(topic, slides_text, caption, trigger_keyword, fmt_key)
            }

    return JSONResponse({"results": results, "topic": topic})


@router.get("/api/repurpose/formats")
async def list_formats():
    """List available repurposing formats."""
    return JSONResponse({"formats": {k: v["name"] for k, v in FORMATS.items()}})
