from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import anthropic
import os
import uuid
from supabase import create_client

app = FastAPI(title="Therapist Content Engine API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

claude_client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

def get_supabase():
    return create_client(SUPABASE_URL, SUPABASE_KEY)

ANGELA_SYSTEM = """You are Angela Schellenberg's AI content assistant. Write exactly as Angela would.

VOICE: Direct. Clinical-but-accessible. No hedging. No fluff. Short punchy lines. Rhythm over grammar.

NEVER USE: Em dashes, "healing era", "holding space", "trauma dump", "do the work", "you are not broken", generic AI language.

PILLARS: Grief education (neuroscience-backed), Mother Hunger (credit Kelly McDaniel), EMDR and bilateral stimulation, Equine therapy at Shakti Ranch Malibu, Somatic healing and attachment repair.

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


class AnalyzeStyleRequest(BaseModel):
    instagram_urls: Optional[str] = None
    notes: Optional[str] = None


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
            "content": f'DM received: "{req.message}"\nLead temp: {req.lead_temperature}\n\nWrite Angela\'s response. Human, direct, not salesy.\n\n---\nSUGGESTED TRIGGER: [keyword]\nREASONING: [one sentence]\nNEXT STEP: [24hr follow-up]'
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


@app.post("/brand/upload")
async def upload_brand_asset(file: UploadFile = File(...), asset_type: str = Form("carousel")):
    try:
        db = get_supabase()
        file_content = await file.read()
        file_ext = file.filename.split(".")[-1] if "." in file.filename else "png"
        file_name = f"{asset_type}/{uuid.uuid4()}.{file_ext}"

        db.storage.from_("brand-assets").upload(
            file_name,
            file_content,
            {"content-type": file.content_type or "image/png"}
        )

        public_url = f"{SUPABASE_URL}/storage/v1/object/public/brand-assets/{file_name}"
        return {"url": public_url, "filename": file.filename, "type": asset_type}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/brand/assets")
async def get_brand_assets():
    try:
        db = get_supabase()
        files = db.storage.from_("brand-assets").list()
        assets = []
        for f in files:
            if f.get("name"):
                url = f"{SUPABASE_URL}/storage/v1/object/public/brand-assets/{f['name']}"
                assets.append({"name": f["name"], "url": url})
        return {"assets": assets}
    except Exception as e:
        return {"assets": []}


@app.post("/brand/analyze-style")
async def analyze_style(req: AnalyzeStyleRequest):
    prompt = "Analyze the visual and content style of Angela Schellenberg's carousel examples."
    if req.instagram_urls:
        prompt += f"\n\nInstagram accounts/posts to reference: {req.instagram_urls}"
    if req.notes:
        prompt += f"\n\nAdditional style notes: {req.notes}"

    prompt += """

Based on these references, write a detailed style guide covering:
1. Visual style (colors, layout, typography feel)
2. Content structure (how slides are organized)
3. Hook patterns (what makes slide 1 stop the scroll)
4. Text density (how much copy per slide)
5. Tone and voice on screen
6. What makes these carousels perform well

Write this as a practical guide Rosa can follow when designing."""

    response = claude_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        system=ANGELA_SYSTEM,
        messages=[{"role": "user", "content": prompt}]
    )
    return {"style_guide": response.content[0].text}
