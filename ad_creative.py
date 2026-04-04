# Ad Creative Studio — generate ad copy for therapist practices
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import anthropic
import os
import json

router = APIRouter(prefix="/api/ads", tags=["ad_creative"])

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None
MODEL = "claude-sonnet-4-20250514"

AD_FORMATS = {
    "feed_ad": {
        "label": "Instagram Feed Ad",
        "description": "Single image ad for the Instagram feed. Headline + body + CTA.",
        "specs": "Primary text: 125 chars visible (up to 2200). Headline: 27 chars visible (up to 40). CTA button text chosen from Meta options."
    },
    "carousel_ad": {
        "label": "Carousel Ad (3-5 cards)",
        "description": "Multi-card swipeable ad. Each card has a headline + description.",
        "specs": "3-5 cards. Each card: headline (32 chars), description (18 chars), image. Plus overall primary text (125 chars visible)."
    },
    "story_ad": {
        "label": "Instagram Story Ad",
        "description": "Full-screen vertical story ad. Punchy, visual, swipe-up CTA.",
        "specs": "Text overlay: 2-3 short lines max. Swipe up CTA. 9:16 vertical. Keep text in safe zone (middle 80%)."
    },
    "reel_ad": {
        "label": "Reels Ad",
        "description": "Short-form video script for Reels placement. Hook in first 3 seconds.",
        "specs": "15-30 seconds. Hook (0-3s), problem (3-10s), solution (10-20s), CTA (20-30s). Vertical 9:16."
    },
    "lead_gen_ad": {
        "label": "Lead Generation Ad",
        "description": "Ad designed to capture emails/bookings via Meta lead form.",
        "specs": "Primary text (persuasive, addresses pain point). Headline (clear offer). Description (what they get). Form fields suggestion."
    },
    "retargeting_ad": {
        "label": "Retargeting Ad",
        "description": "Ad for warm audiences who already follow you or visited your site.",
        "specs": "Shorter, more direct. References familiarity. Urgency or social proof. Specific offer."
    }
}

AD_GOALS = [
    "Book a therapy session",
    "Join a course or program",
    "Sign up for a retreat",
    "Download a free resource (lead magnet)",
    "Join a community or group",
    "Watch a free training / webinar",
    "Follow on Instagram",
    "Book a free consultation call"
]

AUDIENCE_TYPES = [
    "Cold — never heard of you",
    "Warm — follows you or engaged with content",
    "Hot — visited website or opted in before",
    "Lookalike — similar to existing clients"
]


class AdRequest(BaseModel):
    ad_format: str  # key from AD_FORMATS
    goal: str
    audience_type: str
    offer_name: str  # e.g. "Mother Hunger Course", "1:1 Therapy"
    offer_description: Optional[str] = ""
    target_pain_point: Optional[str] = ""
    price_point: Optional[str] = ""
    variation_count: int = 3
    therapist_name: Optional[str] = "Angela Schellenberg"
    therapist_niche: Optional[str] = "grief, trauma, and attachment therapy"


class AudienceRequest(BaseModel):
    offer_name: str
    therapist_niche: Optional[str] = "grief, trauma, and attachment therapy"


@router.get("/formats")
async def get_formats():
    return {
        "formats": {k: {"label": v["label"], "description": v["description"]} for k, v in AD_FORMATS.items()},
        "goals": AD_GOALS,
        "audience_types": AUDIENCE_TYPES
    }


@router.post("/generate")
async def generate_ad_creative(req: AdRequest):
    fmt = AD_FORMATS.get(req.ad_format)
    if not fmt:
        raise HTTPException(status_code=400, detail=f"Unknown format: {req.ad_format}")

    system = f"""You are an expert Meta Ads copywriter who specializes in therapist and mental health practices.
You write ads that feel human, warm, and non-salesy — because therapists' audiences are sensitive and skeptical of ads.

RULES:
- Never sound like a marketer. Sound like a trusted friend who happens to be a therapist.
- Lead with the pain point or desire, not the offer.
- Use "you" language. Make the reader feel seen.
- No clinical jargon in the ad copy (save that for the landing page).
- No manipulative urgency ("spots filling fast!" "don't miss out!") — therapist audiences see through this.
- Short sentences. Line breaks for rhythm.
- The hook must stop the scroll in under 2 seconds of reading.
- Each variation should use a DIFFERENT emotional angle or hook strategy.

THERAPIST: {req.therapist_name}
NICHE: {req.therapist_niche}

AD FORMAT: {fmt['label']}
FORMAT SPECS: {fmt['specs']}"""

    prompt = f"""Generate {req.variation_count} ad creative variations.

GOAL: {req.goal}
AUDIENCE: {req.audience_type}
OFFER: {req.offer_name}
{f'OFFER DETAILS: {req.offer_description}' if req.offer_description else ''}
{f'PAIN POINT TO TARGET: {req.target_pain_point}' if req.target_pain_point else ''}
{f'PRICE: {req.price_point}' if req.price_point else ''}

Return a JSON object with this structure:
{{
  "variations": [
    {{
      "variation_name": "Descriptive name of the angle (e.g. 'The Permission Hook', 'The Mirror Moment')",
      "hook_strategy": "Brief explanation of why this hook works",
      "primary_text": "The main ad body text",
      "headline": "The headline",
      "description": "Short description/subheadline if applicable",
      "cta_button": "Suggested CTA button text (e.g. 'Learn More', 'Book Now', 'Sign Up')",
      "suggested_image_concept": "Brief description of what the ad image should look like"
      {', "cards": [{"headline": "...", "description": "...", "image_concept": "..."}]' if req.ad_format == 'carousel_ad' else ''}
      {', "script_beats": [{"timestamp": "0-3s", "action": "...", "text_overlay": "..."}]' if req.ad_format == 'reel_ad' else ''}
    }}
  ],
  "audience_targeting_suggestions": {{
    "interests": ["list of Meta interest targets"],
    "behaviors": ["relevant behaviors"],
    "demographics": "age range and other demo notes",
    "lookalike_source": "suggestion for lookalike audience seed"
  }},
  "budget_recommendation": "Suggested daily budget range and campaign structure"
}}

Return ONLY the JSON object, no other text."""

    if not claude:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")
    try:
        resp = claude.messages.create(
            model=MODEL,
            max_tokens=4000,
            system=system,
            messages=[{"role": "user", "content": prompt}]
        )
        text = resp.content[0].text.strip()
        # Extract JSON
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()
        data = json.loads(text)
        return {"success": True, **data}
    except json.JSONDecodeError:
        return {"success": True, "raw": text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/audience-suggestions")
async def suggest_audiences(req: AudienceRequest):
    if not claude:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")
    try:
        resp = claude.messages.create(
            model=MODEL,
            max_tokens=2000,
            messages=[{"role": "user", "content": f"""You are a Meta Ads targeting expert for therapists.

For a therapist in the {req.therapist_niche} niche promoting "{req.offer_name}", suggest detailed Meta Ads audience targeting.

Return JSON:
{{
  "cold_audiences": [
    {{
      "name": "Audience name",
      "interests": ["Meta interest targets"],
      "age_range": "25-55",
      "notes": "Why this works"
    }}
  ],
  "warm_audiences": [
    {{
      "name": "Audience name",
      "source": "Where this audience comes from",
      "strategy": "How to use this audience"
    }}
  ],
  "lookalike_suggestions": [
    {{
      "seed": "What to base the lookalike on",
      "percentage": "1-3%",
      "notes": "Why this seed works"
    }}
  ],
  "exclusions": ["Who to exclude and why"],
  "budget_split": "How to split budget across cold/warm/lookalike"
}}

Return ONLY the JSON."""}]
        )
        text = resp.content[0].text.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()
        data = json.loads(text)
        return {"success": True, **data}
    except json.JSONDecodeError:
        return {"success": True, "raw": text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/hooks")
async def generate_hooks(req: AdRequest):
    """Generate 10 scroll-stopping hooks for a specific offer and audience."""
    if not claude:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")
    try:
        resp = claude.messages.create(
            model=MODEL,
            max_tokens=2000,
            system=f"You write Meta ad hooks for therapists. Niche: {req.therapist_niche}. Name: {req.therapist_name}.",
            messages=[{"role": "user", "content": f"""Generate 10 scroll-stopping ad hooks for:

OFFER: {req.offer_name}
GOAL: {req.goal}
AUDIENCE: {req.audience_type}
{f'PAIN POINT: {req.target_pain_point}' if req.target_pain_point else ''}

Each hook should be a different style:
1. Question hook ("What if the reason you can't stop crying isn't weakness?")
2. Bold claim ("Your nervous system remembers what your mind forgot.")
3. Story opener ("She sat in my office and said 'I thought I was over it.'")
4. Stat/fact hook ("73% of motherless daughters report...")
5. Permission hook ("You're allowed to grieve someone who is still alive.")
6. Mirror moment ("You know that feeling when...")
7. Myth-buster ("Therapy isn't about fixing what's broken.")
8. Time-based ("It's been 3 years and you still...")
9. Identity hook ("For the daughter who became the mother too soon.")
10. Contrast hook ("Everyone says 'stay strong.' No one says 'it's ok to fall apart.'")

Return JSON: {{"hooks": [{{"style": "...", "hook": "...", "why_it_works": "..."}}]}}
Return ONLY the JSON."""}]
        )
        text = resp.content[0].text.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()
        data = json.loads(text)
        return {"success": True, **data}
    except json.JSONDecodeError:
        return {"success": True, "raw": text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
