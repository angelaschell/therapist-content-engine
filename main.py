from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import anthropic
import httpx
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

PERPLEXITY_KEY = os.environ.get("PERPLEXITY_API_KEY", "")

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
    {"src":"reddit","sub":"r/MotherlessDaughters","title":"My wedding is in 3 months and I can't stop crying about my mom not being there","stats":"1,923 upvotes · 287 comments","excerpt":"Everyone keeps saying she'll be there in spirit and I want to scream.","tag":"milestone grief"},
    {"src":"reddit","sub":"r/CPTSD","title":"Does anyone else grieve a mother who is technically still alive?","stats":"3,102 upvotes · 518 comments","excerpt":"She's alive but she was never really there. I mourn the mother I deserved but never had.","tag":"living loss"},
]


@app.get("/api/viral")
async def get_viral():
    try:
        from scraper import load_cache
        cache = load_cache()
        if cache and cache.get("posts"):
            return JSONResponse({"success": True, "posts": cache["posts"], "scraped_at": cache.get("scraped_at", ""), "topic": cache.get("topic", ""), "source": "live"})
    except Exception as e:
        print(f"Cache error: {e}")
    return JSONResponse({"success": True, "posts": DEMO_POSTS, "scraped_at": "demo", "topic": "", "source": "demo"})


@app.post("/api/scrape")
async def trigger_scrape(req: Request):
    try:
        data = await req.json()
        topic = data.get("topic", "grief mother loss")
    except:
        topic = "grief mother loss"
    try:
        from scraper import run_scraper
        result = run_scraper(topic)
        return JSONResponse({"success": True, "total": result.get("total_found", 0), "saved": len(result.get("posts", [])), "posts": result.get("posts", []), "scraped_at": result.get("scraped_at", ""), "topic": topic})
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


# ─── PERPLEXITY RESEARCH ENDPOINT ───

@app.post("/api/research")
async def research_topic(req: Request):
    """Call Perplexity to find clinical research and studies on a topic."""
    data = await req.json()
    topic = data.get("topic", "grief")

    if not PERPLEXITY_KEY:
        return JSONResponse({"success": False, "error": "No PERPLEXITY_API_KEY set"})

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.perplexity.ai/chat/completions",
                headers={
                    "Authorization": f"Bearer {PERPLEXITY_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "sonar",
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are a clinical research assistant. Return 3-5 key research findings, studies, or statistics about the topic. Include the researcher/study name and year when available. Be specific and cite real research. Focus on findings that would resonate with women aged 25-55 processing grief, trauma, or attachment wounds. Keep each finding to 1-2 sentences."
                        },
                        {
                            "role": "user",
                            "content": f"Find recent clinical research, studies, and statistics about: {topic}\n\nFocus on: psychology, neuroscience, attachment theory, grief research, trauma studies, EMDR research, somatic therapy research.\n\nReturn ONLY valid JSON, no backticks:\n{{\"findings\": [\"Finding 1 with researcher name and year\", \"Finding 2\", \"Finding 3\"]}}"
                        }
                    ],
                },
                timeout=30,
            )

        if resp.status_code == 200:
            result = resp.json()
            content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            # Try to parse as JSON
            try:
                clean = content.strip()
                if clean.startswith("```"): clean = clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                parsed = json.loads(clean)
                return JSONResponse({"success": True, "data": parsed})
            except:
                return JSONResponse({"success": True, "data": {"findings": [content]}})
        else:
            return JSONResponse({"success": False, "error": f"Perplexity returned {resp.status_code}"})

    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


# ─── CAROUSEL ENDPOINT (with research + slide count) ───

@app.post("/api/carousel")
async def generate_carousel(req: Request):
    data = await req.json()
    topic = data.get("topic", "grief and attachment trauma")
    viral_context = data.get("viral_context", "")
    analysis_context = data.get("analysis_context", "")
    research_context = data.get("research_context", "")
    pillar = data.get("pillar", "Grief Education")
    tone = data.get("tone", "clinical-but-warm")
    slide_count = data.get("slide_count", 10)

    context_block = ""
    if viral_context:
        context_block += f"\n\nVIRAL POSTS (base your carousel on these real conversations):\n{viral_context}"
    if analysis_context:
        context_block += f"\n\nANALYSIS:\n{analysis_context}"
    if research_context:
        context_block += f"\n\nCLINICAL RESEARCH (weave 1-2 of these into your slides for credibility):\n{research_context}"

    # Build slide structure based on count
    if slide_count <= 5:
        structure = 'Slide 1: hook. Slides 2-{}: body. Slide {}: cta.'.format(slide_count - 1, slide_count)
    elif slide_count <= 7:
        structure = 'Slide 1: hook. Slides 2-{}: body (name experiences, deepen, validate). Slide {}: cta.'.format(slide_count - 1, slide_count)
    else:
        structure = 'Slide 1: hook. Slides 2-{}: body (name, contextualize, deepen, validate, bridge). Slide {}: cta.'.format(slide_count - 1, slide_count)

    # Build example slides JSON matching count
    example_slides = [{"type": "hook", "upper": "BOLD HOOK", "italic": "italic subtitle."}]
    for i in range(slide_count - 2):
        example_slides.append({"type": "body", "html": "Sentence with <em>emphasis</em> words."})
    example_slides.append({"type": "cta", "top": "This is what I work with.", "bottom": "Comment <strong>WORTHY</strong> for my free resource."})

    response = claude_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2500,
        system=ANGELA_SYSTEM,
        messages=[{"role": "user", "content": f"""Generate a {slide_count}-slide Instagram carousel.

TOPIC: {topic}
PILLAR: {pillar}
TONE: {tone}
SLIDE COUNT: {slide_count}
STRUCTURE: {structure}
{context_block}

YOUR JOB: Write a carousel that captures the exact emotional nerve from the viral posts, through Angela's clinical lens. If research is provided, weave 1-2 findings naturally into body slides (don't cite like a paper, say it like Angela would: "Research shows..." or "Studies found..."). Name what people are feeling. Be SPECIFIC, not generic.

Return ONLY valid JSON, no backticks:

{{"slides": {json.dumps(example_slides)}, "caption": "Full caption with hashtags", "trigger": "WORTHY"}}

RULES:
- Slide 1: type "hook" uppercase title + italic subtitle. Echo viral language.
- Body slides: sentence case. Use <em> on emotional gut-punch words ONLY.
- Last slide: type "cta"
- MAX 25 words per slide.
- Angela's voice. No em dashes.
- If research provided, work it into 1-2 body slides naturally."""}]
    )
    try:
        clean = response.content[0].text.strip()
        if clean.startswith("```"): clean = clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return JSONResponse({"success": True, "data": json.loads(clean)})
    except:
        return JSONResponse({"success": True, "data": response.content[0].text, "raw": True})


@app.get("/health")
async def health():
    return {"status": "ok", "version": "v5-research"}
