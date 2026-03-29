from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import anthropic
import httpx
import os
import json

# Import analytics
from instagram_analytics import router as analytics_router, start_refresh_loop

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register analytics routes
app.include_router(analytics_router)

claude_client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
PERPLEXITY_KEY = os.environ.get("PERPLEXITY_API_KEY", "")
ENGINE_PASSWORD = os.environ.get("ENGINE_PASSWORD", "")

ANGELA_SYSTEM = """You are Angela Schellenberg's AI content assistant. Write exactly as Angela would.
VOICE: Direct. Clinical-but-accessible. No hedging. Short punchy lines. Rhythm over grammar.
NEVER USE: Em dashes, "healing era", "holding space", "trauma dump", "do the work", "you are not broken", generic AI language, outcome promises, urgency or scarcity language, wellness vocabulary clusters (sacred, worthy, hold space), therapy simulation language, AI rhythm tells (threes, mic-drop closers).
PILLARS: Grief education, Mother Hunger (credit Kelly McDaniel), EMDR, Equine therapy at Shakti Ranch Malibu, Somatic healing.
AUDIENCE: High-functioning women navigating grief and attachment wounds. She already knows attachment theory. Never over-explain. Write as if the reader is the smartest person in the room.
FORMAT: Hook in line 1. Short punchy line breaks. No bullet points. Trust the reader.
BRAND FEEL: Regulated (calm nervous system speaking). Intelligent. Spacious. Invitational.
Three essential elements of attachment: nurturance, protection, guidance."""


# Start token auto-refresh on startup
@app.on_event("startup")
async def startup():
    start_refresh_loop()


@app.get("/", response_class=HTMLResponse)
async def root():
    try:
        with open("index.html", "r") as f:
            return f.read()
    except:
        return HTMLResponse("<h1>Content Engine running</h1>")


@app.post("/generate/content")
async def generate_content(req: Request):
    data = await req.json()
    topic = data.get("topic", "")
    content_type = data.get("content_type", "Instagram Caption")
    pillar = data.get("pillar", "Grief Education")
    tone = data.get("tone", "clinical-but-warm")
    cta = data.get("cta_trigger", "")
    research = data.get("research_context", "")
    cta_str = f'\n\nEnd with: "Comment {cta} and I\'ll send it to you."' if cta else ""
    research_str = f'\n\nCLINICAL RESEARCH TO REFERENCE (cite naturally, e.g. "Research from [name] shows..."):\n{research}' if research else ""
    response = claude_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        system=ANGELA_SYSTEM,
        messages=[{"role": "user", "content": f"Generate a {content_type}.\nPillar: {pillar}\nTone: {tone}\nTopic: {topic}{cta_str}{research_str}\nNo em dashes. Short punchy lines."}]
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


DEMO_POSTS = [
    {"src": "reddit", "sub": "r/GriefSupport", "title": "Does anyone else feel guilty for laughing after losing a parent?", "stats": "2,847 upvotes . 412 comments", "excerpt": "My mom died 6 months ago and I caught myself genuinely laughing yesterday and immediately felt like the worst person alive.", "tag": "naming unnamed grief"},
    {"src": "reddit", "sub": "r/MotherlessDaughters", "title": "My wedding is in 3 months and I can't stop crying about my mom not being there", "stats": "1,923 upvotes . 287 comments", "excerpt": "Everyone keeps saying she'll be there in spirit and I want to scream.", "tag": "milestone grief"},
    {"src": "reddit", "sub": "r/CPTSD", "title": "Does anyone else grieve a mother who is technically still alive?", "stats": "3,102 upvotes . 518 comments", "excerpt": "She's alive but she was never really there.", "tag": "living loss"},
]


@app.post("/api/recommend-hashtags")
async def recommend_hashtags_endpoint(req: Request):
    try:
        data = await req.json()
        topic = data.get("topic", "grief")
    except:
        topic = "grief"
    try:
        from scraper import recommend_hashtags
        hashtags = recommend_hashtags(topic)
        return JSONResponse({"success": True, "hashtags": hashtags, "topic": topic})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e), "hashtags": ["grief", "trauma", "healingjourney", "therapistsofinstagram"]})


@app.get("/api/viral")
async def get_viral():
    try:
        from scraper import load_cache
        cache = load_cache()
        if cache and cache.get("posts"):
            return JSONResponse({"success": True, "posts": cache["posts"], "hooks": cache.get("hooks", []), "scraped_at": cache.get("scraped_at", ""), "topic": cache.get("topic", ""), "source": "live"})
    except Exception as e:
        print(f"Cache error: {e}")
    return JSONResponse({"success": True, "posts": DEMO_POSTS, "hooks": [], "scraped_at": "demo", "topic": "", "source": "demo"})


@app.post("/api/scrape")
async def trigger_scrape(req: Request):
    try:
        data = await req.json()
        topic = data.get("topic", "grief mother loss")
        hashtags = data.get("hashtags", None)
    except:
        topic = "grief mother loss"
        hashtags = None
    try:
        from scraper import run_scraper
        result = run_scraper(topic, hashtags)
        return JSONResponse({"success": True, "total": result.get("total_found", 0), "saved": len(result.get("posts", [])), "posts": result.get("posts", []), "hooks": result.get("hooks", []), "scraped_at": result.get("scraped_at", ""), "topic": topic})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/analyze")
async def analyze_viral(req: Request):
    data = await req.json()
    posts = data.get("posts", [])
    posts_text = "\n\n".join([f"Source: {p.get('sub', '')}\nTitle: {p.get('title', '')}\nEngagement: {p.get('stats', '')}\nExcerpt: {p.get('excerpt', '')}\nPattern: {p.get('tag', '')}" for p in posts])
    response = claude_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1500,
        system=ANGELA_SYSTEM,
        messages=[{"role": "user", "content": f"""Analyze these viral posts. Advise Angela on what carousel to create.

{posts_text}

Return ONLY valid JSON, no backticks:
{{"patterns": ["pattern 1", "pattern 2"], "hooks": ["UPPERCASE HOOK / italic subtitle rewritten for Angela", "ANOTHER HOOK / subtitle"], "angle": "Angela's unique angle", "suggested_topic": "Best carousel topic", "suggested_trigger": "WORTHY"}}

IMPORTANT: The "hooks" should be ready-to-use slide 1 text. Format each as: UPPERCASE TITLE / italic subtitle. These will be shown as options Angela can click to use as her hook slide."""}]
    )
    try:
        clean = response.content[0].text.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return JSONResponse({"success": True, "data": json.loads(clean)})
    except:
        return JSONResponse({"success": True, "data": response.content[0].text})


@app.post("/api/research")
async def research_topic(req: Request):
    data = await req.json()
    topic = data.get("topic", "grief")
    if not PERPLEXITY_KEY:
        return JSONResponse({"success": False, "error": "No PERPLEXITY_API_KEY set"})
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post("https://api.perplexity.ai/chat/completions",
                headers={"Authorization": f"Bearer {PERPLEXITY_KEY}", "Content-Type": "application/json"},
                json={"model": "sonar", "messages": [
                    {"role": "system", "content": "Return 3-5 clinical research findings with researcher/study name and year. Focus on grief, trauma, attachment, EMDR, somatic therapy. Keep each finding to 1-2 sentences. Include the source name."},
                    {"role": "user", "content": f"Find clinical research about: {topic}\n\nReturn ONLY valid JSON, no backticks:\n{{\"findings\": [\"Finding with researcher name and year\", \"Finding 2\"]}}"}
                ]}, timeout=30)
        if resp.status_code == 200:
            content = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
            try:
                clean = content.strip()
                if clean.startswith("```"):
                    clean = clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                return JSONResponse({"success": True, "data": json.loads(clean)})
            except:
                return JSONResponse({"success": True, "data": {"findings": [content]}})
        return JSONResponse({"success": False, "error": f"Perplexity returned {resp.status_code}"})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


# --- CAROUSEL TEMPLATE SYSTEM ---

TEMPLATE_RULES = {
    "naming": {
        "name": "The Naming Post",
        "slides": "5-7",
        "rules": """TEMPLATE: THE NAMING POST
PURPOSE: Give language to unnamed experiences. The post they screenshot and send to three people.
STRUCTURE:
- Slide 1 (hook): Bold statement in display type. Minimal or no supporting text. This names something they have felt but never had words for.
- Interior slides: ONE named experience per slide. 10 words max. Name it and let it sit. No explanations. The reader's recognition IS the content.
- Close slide: One reflective question or quiet invitation. NEVER a hard CTA. No urgency.
VOICE: Like a calm nervous system speaking. No escalation. No over-explaining."""
    },
    "framework": {
        "name": "The Framework Explainer",
        "slides": "7-10",
        "rules": """TEMPLATE: THE FRAMEWORK EXPLAINER
PURPOSE: Teach a concept in digestible depth. The post they bookmark to return to.
STRUCTURE:
- Slide 1 (hook): Frame the concept clearly. A grounded entry point that respects the reader's intelligence. Not a clickbait hook.
- Interior slides: One point per slide. Short heading, 1-2 sentences of context. Name and connect, don't educate from scratch.
- Close slide: Summary or reflective landing. Can mention the lead magnet or consult once, quietly.
VOICE: Clinical warmth. Authority without lecturing."""
    },
    "pullquote": {
        "name": "The Pull Quote",
        "slides": "3-5",
        "rules": """TEMPLATE: THE PULL QUOTE
PURPOSE: One truth, maximum visual weight. Fast to produce, high reach potential.
STRUCTURE:
- Slide 1: The statement takes up most of the slide. Let the words be the design. No decoration.
- Slide 2-3 (optional): One or two sentences of context. A deepening, not an explanation.
- Close slide: A single reflective question, or nothing at all.
VOICE: Deliberately minimal. Maximum 3-5 slides total."""
    },
    "editorial": {
        "name": "The Layered Editorial",
        "slides": "6-9",
        "rules": """TEMPLATE: THE LAYERED EDITORIAL
PURPOSE: Mixed layout, more visual depth. Brand recognition and profile visits.
STRUCTURE:
- Slide 1 (cover): Strong visual cover. Bold statement or single powerful word.
- Interior slides: Mix of full text slides and minimal single-word or single-line slides. Let negative space work. One or two slides can be a single word or short phrase for visual punch.
- Close: Reflective. No hard CTA.
VOICE: Elevated. Short. Precise. Spacious."""
    },
    "conversational": {
        "name": "The Conversational List",
        "slides": "5-8",
        "rules": """TEMPLATE: THE CONVERSATIONAL LIST
PURPOSE: Raw, voice-led, "notes app" feel. Less produced, more Angela. Drives comments and shares.
STRUCTURE:
- Slide 1: A framing statement. "Things that make sense if you were strong too soon." "Signs grief is living in your body." Not clickbait, a genuine entry point.
- Interior slides: One item per slide. Plain type. Should feel like a thoughtful text message, not a branded graphic. The rawness IS the format.
- Close slide: Brief, warm, no pressure. Let the last item land.
VOICE: Like Angela texting a close friend who gets it. Unpolished. Real. Direct. Must NOT sound AI-generated."""
    },
    "covercontext": {
        "name": "The Cover + Context",
        "slides": "6-8",
        "rules": """TEMPLATE: THE COVER + CONTEXT
PURPOSE: Hook to depth to consent-based close. Funnel-aware. Drives saves, follows, DMs.
STRUCTURE:
- Slide 1 (cover): Strong enough to earn the swipe. Grounded enough to match Angela's brand. Not clickbait.
- Slide 2: Context. What are we unpacking, and why does it matter for this specific person.
- Slides 3-6: The depth. One concept per slide, building on the previous. Angela's clinical knowledge earns trust here.
- Close slide: A quiet invitation. One mention of a resource, no urgency, no pressure. Optional to include any offer mention at all.
VOICE: Strategic but genuine. The funnel awareness should be invisible to the reader. She should feel seen, not sold to."""
    },
}


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
    template_type = data.get("template_type", "naming")

    template = TEMPLATE_RULES.get(template_type, TEMPLATE_RULES["naming"])
    template_rules = template["rules"]

    context_block = ""
    if viral_context:
        context_block += f"\n\nVIRAL POSTS:\n{viral_context}"
    if analysis_context:
        context_block += f"\n\nANALYSIS:\n{analysis_context}"
    if research_context:
        context_block += f"\n\nCLINICAL RESEARCH (weave 1-2 naturally into slides, cite the researcher):\n{research_context}"

    response = claude_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2500,
        system=ANGELA_SYSTEM,
        messages=[{"role": "user", "content": f"""Generate a {slide_count}-slide Instagram carousel using this template:

{template_rules}

TOPIC: {topic}
PILLAR: {pillar}
TONE: {tone}
{context_block}

FORMATTING RULES:
- Do NOT use <br> tags. Each slide is a single flowing sentence or phrase.
- Slide 1: type "hook" with "upper" (UPPERCASE, short) and "italic" (subtitle, can be empty string).
- Body slides: type "body" with "html" field. Use <em> for emphasis on emotional words ONLY. No <br> tags.
- Last slide: type "close" with "text" field. Reflective invitation, NOT a hard CTA.
- MAX 15 words per slide for Naming Post and Pull Quote. MAX 25 for others.
- No em dashes. No "you're not broken" structures. No outcome promises. No urgency.
- Would a shame-sensitive woman feel steadied, not activated?

Return ONLY valid JSON, no backticks:
{{"slides": [{{"type":"hook","upper":"TEXT","italic":"subtitle or empty"}},{{"type":"body","html":"One truth."}},{{"type":"close","text":"Reflective invitation."}}], "caption": "Full Instagram caption text here", "trigger": "WORTHY", "template": "{template_type}"}}

CAPTION RULES:
- Write the caption in Angela's voice. Short punchy lines. Line breaks between thoughts.
- If clinical research was provided above, cite 1-2 findings naturally in the caption (e.g. "Research from Dr. Mary Frances O'Connor shows grief literally reshapes the brain."). This is important. The research must appear in the caption.
- Include 15-20 relevant hashtags at the end.
- End with: Comment WORTHY and I'll send you my free Emotional Starter Kit.
- No em dashes in the caption either."""}]
    )
    try:
        clean = response.content[0].text.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return JSONResponse({"success": True, "data": json.loads(clean)})
    except:
        return JSONResponse({"success": True, "data": response.content[0].text, "raw": True})


@app.post("/api/login")
async def login(req: Request):
    data = await req.json()
    password = data.get("password", "")
    if ENGINE_PASSWORD and password == ENGINE_PASSWORD:
        return JSONResponse({"success": True, "token": ENGINE_PASSWORD})
    return JSONResponse({"success": False, "error": "Wrong password"}, status_code=401)


@app.get("/health")
async def health():
    return {"status": "ok", "version": "v9-analytics"}
