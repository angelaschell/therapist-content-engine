from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import anthropic
import os
import json

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

claude_client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

ANGELA_SYSTEM = """You are Angela Schellenberg's AI content assistant. Write exactly as Angela would.
VOICE: Direct. Clinical-but-accessible. No hedging. Short punchy lines. Rhythm over grammar.
NEVER USE: Em dashes, "healing era", "holding space", "trauma dump", "do the work", "you are not broken", generic AI language.
PILLARS: Grief education, Mother Hunger© (credit Kelly McDaniel), EMDR, Equine therapy at Shakti Ranch Malibu, Somatic healing.
AUDIENCE: High-functioning women navigating grief and attachment wounds.
FORMAT: Hook in line 1. Short punchy line breaks. No bullet points. Trust the reader.
Three essential elements of attachment: nurturance, protection, guidance."""


@app.get("/", response_class=HTMLResponse)
async def root():
    try:
        with open("index.html", "r") as f:
            return f.read()
    except:
        return HTMLResponse("<h1>Content Engine running</h1>")


# ─── EXISTING ENDPOINTS (unchanged) ───

@app.post("/generate/content")
async def generate_content(req: Request):
    data = await req.json()
    topic = data.get("topic", "")
    content_type = data.get("content_type", "Instagram Caption")
    pillar = data.get("pillar", "Grief Education")
    tone = data.get("tone", "clinical-but-warm")
    cta = data.get("cta_trigger", "")
    cta_str = f'\n\nEnd with: "Comment {cta} and I\'ll send it to you."' if cta else ""
    response = claude_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        system=ANGELA_SYSTEM,
        messages=[{"role": "user", "content": f"Generate a {content_type}.\nPillar: {pillar}\nTone: {tone}\nTopic: {topic}{cta_str}\nNo em dashes. Short punchy lines."}]
    )
    return {"content": response.content[0].text}


@app.post("/generate/dm-response")
async def generate_dm_response(req: Request):
    data = await req.json()
    message = data.get("message", "")
    lead_temp = data.get("lead_temperature", "")
    response = claude_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=600,
        system=ANGELA_SYSTEM,
        messages=[{"role": "user", "content": f'DM: "{message}"\nLead temp: {lead_temp}\nWrite Angela\'s response. Human, direct.\n\n---\nSUGGESTED TRIGGER: [keyword]\nREASONING: [one sentence]\nNEXT STEP: [24hr follow-up]'}]
    )
    return {"response": response.content[0].text}


@app.post("/generate/manychat-flow")
async def generate_manychat_flow(req: Request):
    data = await req.json()
    keyword = data.get("keyword", "")
    offer = data.get("offer", "")
    response = claude_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=800,
        system=ANGELA_SYSTEM,
        messages=[{"role": "user", "content": f"Write a 3-message ManyChat flow.\nTrigger: {keyword}\nOffer: {offer}\n\nMESSAGE 1 - Instant reply\nMESSAGE 2 - 2 hour follow-up\nMESSAGE 3 - 24 hour last touch\n\nAngela's voice. No em dashes. Human."}]
    )
    return {"flow": response.content[0].text}


# ─── SCRAPER ENDPOINTS ───

DEMO_POSTS = [
    {"src":"reddit","sub":"r/GriefSupport","title":"Does anyone else feel guilty for laughing after losing a parent?","stats":"2,847 upvotes · 412 comments","excerpt":"My mom died 6 months ago and I caught myself genuinely laughing yesterday and immediately felt like the worst person alive.","tag":"naming unnamed grief"},
    {"src":"reddit","sub":"r/MotherlessDaughters","title":"My wedding is in 3 months and I can't stop crying about my mom not being there","stats":"1,923 upvotes · 287 comments","excerpt":"Everyone keeps saying she'll be there in spirit and I want to scream. I don't want her in spirit.","tag":"milestone grief"},
    {"src":"reddit","sub":"r/CPTSD","title":"Does anyone else grieve a mother who is technically still alive?","stats":"3,102 upvotes · 518 comments","excerpt":"She's alive but she was never really there. I mourn the mother I deserved but never had.","tag":"living loss"},
    {"src":"reddit","sub":"r/GriefSupport","title":"I hate when people say 'stay strong' at funerals","stats":"4,211 upvotes · 673 comments","excerpt":"Why is falling apart not an option? Why do I have to perform composure for YOUR comfort?","tag":"challenging platitudes"},
    {"src":"reddit","sub":"r/raisedbynarcissists","title":"I just realized I've been parenting my parent since I was 8","stats":"2,456 upvotes · 389 comments","excerpt":"I was the one checking if she was okay. I was the one managing her emotions. I was 8.","tag":"parentification"},
    {"src":"reddit","sub":"r/MotherlessDaughters","title":"Things nobody tells you about losing your mom young","stats":"1,678 upvotes · 234 comments","excerpt":"You become everyone else's therapist. You learn to swallow your feelings. Mother's Day becomes a minefield.","tag":"community identification"},
]


@app.get("/api/viral")
async def get_viral():
    """Return cached viral posts, or demo data if no cache."""
    try:
        from scraper import load_cache
        cache = load_cache()
        if cache and cache.get("posts"):
            return JSONResponse({
                "success": True,
                "posts": cache["posts"],
                "scraped_at": cache.get("scraped_at", "unknown"),
                "topic": cache.get("topic", ""),
                "source": "live"
            })
    except Exception as e:
        print(f"Cache error: {e}")

    return JSONResponse({
        "success": True,
        "posts": DEMO_POSTS,
        "scraped_at": "demo data",
        "topic": "",
        "source": "demo"
    })


@app.post("/api/scrape")
async def trigger_scrape(req: Request):
    """Scrape Reddit + Instagram based on a topic."""
    try:
        data = await req.json()
        topic = data.get("topic", "grief mother loss")
    except:
        topic = "grief mother loss"

    try:
        from scraper import run_scraper
        result = run_scraper(topic)
        return JSONResponse({
            "success": True,
            "total": result.get("total_found", 0),
            "saved": len(result.get("posts", [])),
            "posts": result.get("posts", []),
            "scraped_at": result.get("scraped_at", ""),
            "topic": topic
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/analyze")
async def analyze_viral(req: Request):
    data = await req.json()
    posts = data.get("posts", [])
    posts_text = "\n\n".join([f"Source: {p.get('sub','')}\nTitle: {p.get('title','')}\nEngagement: {p.get('stats','')}\nExcerpt: {p.get('excerpt','')}\nPattern: {p.get('tag','')}" for p in posts])
    response = claude_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1500,
        system=ANGELA_SYSTEM,
        messages=[{"role": "user", "content": f"""Analyze these viral posts from grief/trauma communities. Advise Angela on what carousel to create next.

{posts_text}

Return ONLY valid JSON, no markdown backticks:
{{"patterns": ["pattern 1 with detail", "pattern 2"], "hooks": ["hook rewritten for Angela 1", "hook 2"], "angle": "Angela's unique angle connecting to her framework", "suggested_topic": "Best carousel topic", "suggested_trigger": "WORTHY"}}"""}]
    )
    try:
        clean = response.content[0].text.strip()
        if clean.startswith("```"): clean = clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return JSONResponse({"success": True, "data": json.loads(clean)})
    except:
        return JSONResponse({"success": True, "data": response.content[0].text})


@app.post("/api/carousel")
async def generate_carousel(req: Request):
    data = await req.json()
    topic = data.get("topic", "grief and attachment trauma")
    viral_context = data.get("viral_context", "")
    analysis_context = data.get("analysis_context", "")
    pillar = data.get("pillar", "Grief Education")
    tone = data.get("tone", "clinical-but-warm")

    context_block = ""
    if viral_context:
        context_block += f"\n\nVIRAL POSTS THAT ARE PERFORMING RIGHT NOW (base your carousel on these):\n{viral_context}"
    if analysis_context:
        context_block += f"\n\nANALYSIS OF WHAT'S WORKING:\n{analysis_context}"

    response = claude_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        system=ANGELA_SYSTEM,
        messages=[{"role": "user", "content": f"""Generate a 10-slide Instagram carousel.

TOPIC: {topic}
PILLAR: {pillar}
TONE: {tone}
{context_block}

YOUR JOB: Write a carousel that captures the exact emotional nerve these viral posts are hitting, but through Angela's clinical lens and voice. Use the language and framing people are already responding to. Name what they're feeling. Don't just write about the topic generically. Write about the SPECIFIC experience these viral posts describe.

Return ONLY valid JSON, no markdown backticks:

{{"slides": [{{"type":"hook","upper":"BOLD UPPERCASE HOOK","italic":"italic subtitle."}},{{"type":"body","html":"Sentence case with <em>italic emphasis</em> on emotional words."}},{{"type":"body","html":"Next point."}},{{"type":"body","html":"Continue."}},{{"type":"body","html":"Deepen."}},{{"type":"body","html":"More."}},{{"type":"body","html":"Almost."}},{{"type":"body","html":"Validate."}},{{"type":"body","html":"Bridge."}},{{"type":"cta","top":"This is what I work with.","bottom":"Comment <strong>WORTHY</strong> for my free resource."}}],"caption":"Full caption with hashtags","trigger":"WORTHY"}}

SLIDE RULES:
- Slide 1: type "hook" with uppercase title and italic subtitle. Echo the language from viral posts.
- Slides 2-9: type "body" with sentence case. Use <em> on emotional gut-punch words ONLY. Name SPECIFIC experiences from the viral posts.
- Slide 10: type "cta"
- MAX 25 words per slide.
- Angela's voice. No em dashes. No fluff."""}]
    )
    try:
        clean = response.content[0].text.strip()
        if clean.startswith("```"): clean = clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return JSONResponse({"success": True, "data": json.loads(clean)})
    except:
        return JSONResponse({"success": True, "data": response.content[0].text, "raw": True})


@app.get("/health")
async def health():
    return {"status": "ok", "version": "v4-topic-scraper"}
