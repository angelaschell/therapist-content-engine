"""
Therapist Content Engine - Backend API
Built for Angela Schellenberg | Sellable to any therapist
"""

from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import anthropic
import os
from datetime import datetime
from database import (
    get_therapist_by_api_key,
    save_generated_content,
    get_viral_content,
    get_therapist_profile,
    create_therapist,
    update_therapist_profile,
)
from scraper import run_scraper

app = FastAPI(title="Therapist Content Engine API")

# Allow frontend to call this backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Lock this down to your domain in production
    allow_methods=["*"],
    allow_headers=["*"],
)

claude_client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))


# ─── Auth Helper ─────────────────────────────────────────────────────────────

async def get_current_therapist(x_api_key: str = Header(...)):
    therapist = await get_therapist_by_api_key(x_api_key)
    if not therapist:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return therapist


# ─── Request / Response Models ────────────────────────────────────────────────

class ContentRequest(BaseModel):
    content_type: str          # "Instagram Reel Script", "Carousel", etc.
    pillar: str                # "Grief Education", "EMDR", etc.
    topic: str                 # The hook or angle
    tone: str = "clinical-but-warm"
    cta_trigger: Optional[str] = None  # ManyChat keyword

class DMRequest(BaseModel):
    message: str               # The incoming DM
    lead_temperature: str      # "Cold", "Warm", "Hot"

class FlowRequest(BaseModel):
    keyword: str               # ManyChat trigger keyword
    offer: str                 # What this keyword delivers

class OnboardRequest(BaseModel):
    name: str
    practice_name: str
    email: str
    specialties: List[str]
    target_audience: str
    voice_description: str
    never_use_words: List[str]
   offers: List[Dict[str, Any]] = []
instagram_handle: str = ""
brand_colors: Dict[str, Any] = {}

# ─── Build Brand-Aware System Prompt ─────────────────────────────────────────

def build_system_prompt(therapist: dict) -> str:
    profile = therapist["profile"]
    never_use = ", ".join(profile.get("never_use_words", []))
    offers = "\n".join([f"- {o['keyword']}: {o['offer']}" for o in profile.get("offers", [])])
    specialties = ", ".join(profile.get("specialties", []))

    return f"""You are the AI content assistant for {profile['name']}, a {specialties} therapist.

PRACTICE: {profile['practice_name']}
INSTAGRAM: @{profile['instagram_handle']}
TARGET AUDIENCE: {profile['target_audience']}

VOICE: {profile['voice_description']}

NEVER USE: {never_use}

OFFERS AND MANYCHAT TRIGGERS:
{offers}

CONTENT RULES:
- Write in first person as {profile['name']}
- Hook in line 1 — scroll-stopping and specific
- Short punchy lines. Rhythm over grammar.
- No bullet points in captions
- No generic AI-sounding language
- Trust the reader. Do not over-explain.
- ManyChat CTA format: "Comment [KEYWORD] and I'll send it to you."
"""


# ─── Routes ──────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {"status": "Therapist Content Engine running"}


@app.post("/onboard")
async def onboard_therapist(data: OnboardRequest):
    """Create a new therapist account. Called during signup."""
    therapist = await create_therapist(data.dict())
    return {
        "api_key": therapist["api_key"],
        "message": f"Welcome {data.name}. Your Content Engine is ready.",
        "therapist_id": therapist["id"]
    }


@app.get("/profile")
async def get_profile(therapist=Depends(get_current_therapist)):
    return therapist["profile"]


@app.put("/profile")
async def update_profile(data: dict, therapist=Depends(get_current_therapist)):
    updated = await update_therapist_profile(therapist["id"], data)
    return updated


@app.post("/generate/content")
async def generate_content(req: ContentRequest, therapist=Depends(get_current_therapist)):
    """Generate brand-aligned content in the therapist's voice."""
    system_prompt = build_system_prompt(therapist)

    cta_str = ""
    if req.cta_trigger:
        cta_str = f'\n\nEnd with a ManyChat CTA: "Comment {req.cta_trigger} and I\'ll send it to you." Make it feel natural.'

    user_message = f"""Generate a {req.content_type}.

Pillar: {req.pillar}
Tone: {req.tone}
Topic / angle: {req.topic}{cta_str}

Make it punchy and specific. Trust the reader."""

    response = claude_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}]
    )

    result = response.content[0].text

    # Save to history
    await save_generated_content(therapist["id"], {
        "type": req.content_type,
        "pillar": req.pillar,
        "topic": req.topic,
        "output": result,
        "created_at": datetime.utcnow().isoformat()
    })

    return {"content": result}


@app.post("/generate/dm-response")
async def generate_dm_response(req: DMRequest, therapist=Depends(get_current_therapist)):
    """Generate a human-sounding DM response in the therapist's voice."""
    system_prompt = build_system_prompt(therapist)
    profile = therapist["profile"]

    user_message = f"""Someone just sent {profile['name']} this Instagram DM:

"{req.message}"

Lead temperature: {req.lead_temperature}

Write a response AS {profile['name']}. Human, warm, direct. Not salesy. Not robotic.

After the response, add:

---
SUGGESTED TRIGGER: [keyword or "None — respond manually"]
REASONING: [one sentence]
NEXT STEP: [what to do if they don't respond in 24 hours]"""

    response = claude_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=600,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}]
    )

    return {"response": response.content[0].text}


@app.post("/generate/manychat-flow")
async def generate_manychat_flow(req: FlowRequest, therapist=Depends(get_current_therapist)):
    """Generate a complete 3-message ManyChat automation flow."""
    system_prompt = build_system_prompt(therapist)
    profile = therapist["profile"]

    user_message = f"""Write a complete 3-message ManyChat automation flow for trigger: {req.keyword}
Offer: {req.offer}

MESSAGE 1 — Instant reply (sent immediately)
[warm, personal, delivers what was promised, short, includes link or next step]

MESSAGE 2 — Follow-up (sent 2 hours later if no conversion)
[check in, one soft question, no pressure]

MESSAGE 3 — Last touch (sent 24 hours later)
[gentle close, leave door open, no guilt]

{profile['name']}'s voice throughout. Sound human. Short sentences."""

    response = claude_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=800,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}]
    )

    return {"flow": response.content[0].text}


@app.get("/viral-content")
async def get_viral(
    category: str = "grief",
    limit: int = 10,
    therapist=Depends(get_current_therapist)
):
    """Get today's top viral content in the therapist's niche."""
    content = await get_viral_content(category, limit)
    return {"viral_content": content}


@app.post("/viral-content/{content_id}/rewrite")
async def rewrite_viral(
    content_id: str,
    therapist=Depends(get_current_therapist)
):
    """Rewrite a viral piece of content in the therapist's voice."""
    system_prompt = build_system_prompt(therapist)
    profile = therapist["profile"]

    # Get the viral content from DB
    viral_items = await get_viral_content(limit=100)
    item = next((v for v in viral_items if v["id"] == content_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Content not found")

    user_message = f"""This content is going viral in the grief/therapy space:

HOOK: {item['hook']}
FORMAT: {item['format']}
PLATFORM: {item['platform']}
ENGAGEMENT: {item['engagement_summary']}

Rewrite this concept as {profile['name']} would write it. Same emotional core, completely different words. Her voice, her pillars, her audience. Do not copy the original phrasing."""

    response = claude_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=800,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}]
    )

    return {
        "original": item,
        "rewritten": response.content[0].text
    }


@app.post("/scraper/run")
async def trigger_scraper(therapist=Depends(get_current_therapist)):
    """Manually trigger a scraper run. Normally runs on schedule."""
    profile = therapist["profile"]
    categories = [s.lower().replace(" ", "_") for s in profile.get("specialties", ["grief"])]
    result = await run_scraper(categories)
    return {"scraped": result["count"], "message": "Scraper complete"}


@app.get("/content-history")
async def get_history(therapist=Depends(get_current_therapist)):
    """Get previously generated content for this therapist."""
    from database import get_content_history
    history = await get_content_history(therapist["id"])
    return {"history": history}
