from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
import anthropic
import os

app = FastAPI(title="Therapist Content Engine API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
claude_client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

ANGELA_SYSTEM = """You are Angela Schellenberg's AI content assistant. Write exactly as Angela would.

VOICE: Direct. Clinical-but-accessible. No hedging. No fluff. Short punchy lines. Rhythm over grammar.

NEVER USE: Em dashes, "healing era", "holding space", "trauma dump", "do the work", "you are not broken", generic AI language.

PILLARS: Grief education, Mother Hunger (credit Kelly McDaniel), EMDR, Equine therapy at Shakti Ranch Malibu, Somatic healing.

AUDIENCE: High-functioning women navigating grief and attachment wounds.

FORMAT: Hook in line 1. Short punchy line breaks. No bullet points in captions. Trust the reader."""


class ContentRequest(BaseModel):
    content_type: str
    pillar: str
    topic: str
    tone: str = "clinical-but-warm"
    cta_trigger: Optional[str] = None


class DMRequest(BaseModel):
    message: str
    lead_temperature: str


class FlowRequest(BaseModel):
    keyword: str
    offer: str


@app.get("/")
async def root():
    return {"status": "Therapist Content Engine running"}


@app.post("/generate/content")
async def generate_content(req: ContentRequest):
    cta_str = ""
    if req.cta_trigger:
        cta_str = f'\n\nEnd with: "Comment {req.cta_trigger} and I\'ll send it to you."'

    response = claude_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        system=ANGELA_SYSTEM,
        messages=[{
            "role": "user",
            "content": f"Generate a {req.content_type}.\nPillar: {req.pillar}\nTone: {req.tone}\nTopic: {req.topic}{cta_str}\nNo em dashes. Short punchy lines."
        }]
    )
    return {"content": response.content[0].text}


@app.post("/generate/dm-response")
async def generate_dm_response(req: DMRequest):
    response = claude_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=600,
        system=ANGELA_SYSTEM,
        messages=[{
            "role": "user",
            "content": f'DM received: "{req.message}"\nLead temp: {req.lead_temperature}\n\nWrite Angela\'s response. Human, direct, not salesy.\n\n---\nSUGGESTED TRIGGER: [keyword or None]\nREASONING: [one sentence]\nNEXT STEP: [24hr follow-up]'
        }]
    )
    return {"response": response.content[0].text}


@app.post("/generate/manychat-flow")
async def generate_manychat_flow(req: FlowRequest):
    response = claude_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=800,
        system=ANGELA_SYSTEM,
        messages=[{
            "role": "user",
            "content": f"Write a 3-message ManyChat flow.\nTrigger: {req.keyword}\nOffer: {req.offer}\n\nMESSAGE 1 - Instant reply\nMESSAGE 2 - 2 hour follow-up\nMESSAGE 3 - 24 hour last touch\n\nAngela's voice. No em dashes. Human."
        }]
    )
    return {"flow": response.content[0].text}
