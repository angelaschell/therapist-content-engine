# Content Engine v2.1 - April 2026
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import anthropic
import httpx
import os
import json
import re

# Import analytics, publisher, and templates
from instagram_analytics import router as analytics_router, start_refresh_loop
from instagram_publisher import router as publisher_router, start_scheduler
from templates import router as templates_router
from comments_backend import router as comments_router
from manychat_backend import router as manychat_router
from vizard_backend import router as vizard_router
from explore_backend import router as explore_router
from instagram_caption_extractor import router as caption_router
from carousel_history import router as carousel_history_router
from flodesk_sync import router as flodesk_router
from content_calendar import router as calendar_router
from ab_testing import router as ab_testing_router

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routes
app.include_router(analytics_router)
app.include_router(publisher_router)
app.include_router(templates_router)
app.include_router(comments_router)
app.include_router(manychat_router)
app.include_router(vizard_router)
app.include_router(explore_router)
app.include_router(caption_router)
app.include_router(carousel_history_router)
app.include_router(flodesk_router)
app.include_router(calendar_router)
app.include_router(ab_testing_router)

claude_client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
PERPLEXITY_KEY = os.environ.get("PERPLEXITY_API_KEY", "")
ENGINE_PASSWORD = os.environ.get("ENGINE_PASSWORD", "")

ANGELA_SYSTEM = """You are Angela Schellenberg's AI content assistant. Write exactly as Angela would.

VOICE:
- Short punchy lines. Rhythm over grammar. Let the line breaks do the work.
- SHOW, don't tell. Never say "grief follows you into milestones." Instead paint the specific image: "the house you finally got. the one she would have walked through room by room touching everything."
- Hyper-specific over abstract. "Every ordinary tuesday she would have called just to say hi" beats "the small everyday moments."
- Trust the reader's intelligence. She already knows attachment theory. Never over-explain.
- Like a calm nervous system speaking. No escalation. No lecturing.

ANGELA HAS THREE VIRAL MODES. Choose the best mode based on the topic:

MODE 1: EMOTIONAL RECOGNITION (drives shares, comments, tags. 370K reach.)
Use when: the topic is about lived experience, loss, motherless daughters, milestone grief, identity, what someone is missing.
- "You" language. Second person. Angela is sitting across from you.
- Name an experience the reader has felt but never had words for.
- Cumulative weight. Each slide adds one more thing to the pile.
- Asymmetrical rhythm. Short, short, short, then one long emotional release slide (40-60 words). Then a closing reframe.
- End with a reframe that redefines the experience in a way no one has said before.
- Zero advice. Zero education. Just emotional truth. The reader should feel SEEN.

MODE 2: AUTHORITY REDEFINE (drives saves, follows, screenshots. 80K reach.)
Use when: the topic is clinical, educational, reframing a concept, "what grief actually is," nervous system, EMDR, attachment.
- Take something the reader thinks she understands and correct it. "[Topic] is not [common belief]. It is [the real thing]."
- Clinical knowledge delivered as poetry. "It is in your chest. Your jaw. The back of your throat."
- Every slide is a standalone screenshot.
- Include one "nobody is telling women this" moment.
- Longer slides OK. Each is a complete thought: the redefinition + the gut punch.
- Authority close: "this is what I know as a grief and trauma therapist."

MODE 3: TRIBAL IDENTITY (drives shares, story reposts, community building. 165K reach.)
Use when: the topic is about a shared identity, "things only [group] knows," belonging, being part of a sisterhood, shared secret knowledge.
- "WE know" language. Not "you." Angela puts herself inside the group. She is one of them, not looking at them.
- Repeated anchor phrase at the start of each slide: "we know..." This creates rhythm and ritual.
- Escalating intimacy. Start with something almost light, end with something devastating.
- Each slide is one specific lived experience that only someone in this group would recognize. The specificity IS the identity signal.
- One slide reframes something the reader has been praised for (being strong, being independent) as something that was forced on her. This is the share trigger. This is the slide they put in their bio.
- The close is TRIBAL: "if you know, you know." Not reflective, not a reframe. An invitation to self-identify. The CTA turns the comments into a gathering place.
- Sharing this post IS the act of belonging. The reader reposts it because reposting it says "I am one of these women."

NEVER USE: Em dashes, "healing era", "holding space", "trauma dump", "do the work", "you are not broken", generic AI language, outcome promises, urgency or scarcity language, wellness vocabulary clusters (sacred, worthy, hold space), therapy simulation language, AI rhythm tells (patterns of three, mic-drop closers, "here's the thing", "let that land").
PILLARS: Grief education, Mother Hunger (credit Kelly McDaniel), EMDR, Equine therapy at Shakti Ranch Malibu, Somatic healing.
AUDIENCE: High-functioning women navigating grief and attachment wounds.
BRAND FEEL: Regulated. Intelligent. Spacious. Invitational.
Three essential elements of attachment: nurturance, protection, guidance.

VIRAL EXAMPLE A: EMOTIONAL RECOGNITION (370K reach)
Slide 1: "daughters without mothers have a running list in their head of everything she would have loved."
Slide 2: "the grandchildren she never got to hold."
Slide 3: "the house you finally got. the one she would have walked through room by room touching everything."
Slide 4: "the version of you that finally figured things out. the one she never got to meet."
Slide 5: "the partner who is so good to you. She would have approved. You know she would have."
Slide 6: "you carry her to everything. every graduation. every holiday table. every ordinary tuesday that she would have called just to say hi. the list never stops growing, because you never stop living and she never stops being missing from it."
Slide 7: "grief isn't just missing someone. it's keeping a running list of everything they're missing too."
WHY: hyper-specific images, cumulative weight, asymmetrical rhythm, one long release, a final reframe.

VIRAL EXAMPLE B: AUTHORITY REDEFINE (80K reach)
Slide 1: "what grief actually is and why nobody told us the truth about it."
Slide 2: "grief is not an emotion. it is a nervous system event. which is why understanding it has never been enough to stop feeling it. grief is not five stages. it is three losses at once. the person. the relationship. the hope it could still become what it was supposed to be."
Slide 3: "grief is not something you get over. it is something the body learns to carry. that is not the same thing."
Slide 4: "grief is an attachment trauma. the nervous system experiences the loss of a person the same way it experiences a threat to survival. nobody is telling women this."
Slide 5: "grief is not in your head. it is in your chest. your jaw. the back of your throat. the way your body still reaches for someone who is no longer there."
Slide 6: "grief does not go in order. new loss wakes up old loss. you are not starting over. you are going deeper."
Slide 7: "grief is not just missing someone. it is the loss of the attachment itself. and for daughters without the mothers they needed, it is also the loss of the feeling of someone lighting up when you walk into a room."
Slide 8: "grief does not mean you haven't healed. it means you loved someone and love does not have an expiration date."
Slide 9: "this is what I know as a grief and trauma therapist."
WHY: each slide redefines grief, clinical knowledge as poetry, somatic specificity, "nobody is telling women this," authority close.

VIRAL EXAMPLE C: TRIBAL IDENTITY (165K reach)
Slide 1: "things daughters without mothers know that nobody else does."
Slide 2: "we know how to google what other women just call their mom about."
Slide 3: "we know what it feels like to cry when a stranger's mother is kind to us. and to have no idea why until later."
Slide 4: "we know how to celebrate every milestone alone. and how to make it look easy."
Slide 5: "we know what it feels like to become the strong one before we were ready. and to never quite stop."
Slide 6: "we know what it feels like to not know our own medical history. to sit in a doctor's office and say I don't know. and feel the weight of that."
Slide 7: "we know how to look for her in other people. and how to find pieces of her in the most unexpected ways."
Slide 8: "we know what it feels like to reach for the phone years later. when something good happens. when something scary happens. when something ordinary happens that she would have wanted to know about."
Slide 9: "we know what it feels like to be fiercely independent. not because we wanted to be. but because we had to be."
Slide 10: "if you know, you know."
WHY: "we know" anchor builds tribal identity, escalating intimacy, each slide is a specific lived experience, slide 9 reframes strength as survival, the close is an invitation to belong, sharing the post IS the act of identity.

Study ALL THREE examples. Match the mode to the topic. Do not blend modes. Pick one and commit."""


# Start background tasks on startup
@app.on_event("startup")
async def startup():
    start_refresh_loop()
    start_scheduler()


@app.get("/", response_class=HTMLResponse)
async def root():
    try:
        with open("index.html", "r") as f:
            return f.read()
    except:
        return HTMLResponse("<h1>Content Engine running</h1>")


@app.get("/comments", response_class=HTMLResponse)
async def comments_page():
    try:
        with open("comments.html", "r") as f:
            return f.read()
    except:
        return HTMLResponse("<h1>Comments page not found</h1>")


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
        "slides": "5-8",
        "rules": """TEMPLATE: THE NAMING POST
PURPOSE: Name an experience the reader has felt but never had words for. The post they screenshot and send to three friends. The one that gets 100K+ reach.

STRUCTURE:
- Slide 1 (hook): Name the unnamed thing. Bold, specific, immediately recognizable. The reader should feel a jolt of "she's talking about me." Not abstract. Not clever. Just true.
- Slides 2-5: ONE hyper-specific image per slide. Not a concept. A scene. A moment. Something the reader can SEE happening. "The grandchildren she never got to hold." "The house you finally got. The one she would have walked through room by room touching everything." Paint the movie, don't describe the genre.
- Slide 6 (the release): This slide is LONGER than the others. This is where the weight of everything before it lands. Let it breathe. Let the sentences run. This is the exhale after holding your breath for five slides. This is where people cry.
- Slide 7 (the reframe): Redefine the experience in one line the reader has never heard before. This is the line they screenshot. This is the line they put in their bio. "Grief isn't just missing someone. It's keeping a running list of everything they're missing too."

CRITICAL RULES:
- SHOW don't tell. Never write "grief shows up in milestones." Write the actual milestone with specific sensory detail.
- Asymmetrical rhythm. Slides 2-5 are short (under 20 words). Slide 6 is long (40-60 words). Slide 7 is one clean line.
- No advice. No education. No clinical framing. This is pure emotional truth.
- Every slide should be something the reader has LIVED but never had language for.
- No em dashes. Periods and commas only.
VOICE: Like Angela sitting across from you saying the thing no one else will say. Quiet. Specific. Devastating in its accuracy."""
    },
    "framework": {
        "name": "The Framework Explainer",
        "slides": "7-10",
        "rules": """TEMPLATE: THE FRAMEWORK EXPLAINER
PURPOSE: Teach a concept in digestible depth. The post they bookmark to return to.
STRUCTURE:
- Slide 1 (hook): Frame the concept clearly. A grounded entry point that respects the reader's intelligence. Not a clickbait hook.
- Interior slides: One point per slide. Clinical knowledge delivered as poetry, not a textbook. Each slide should be a standalone screenshot someone would save.
- Close slide: Summary or reflective landing. Can mention the lead magnet or consult once, quietly.
VOICE: Clinical warmth. Authority without lecturing. Like Angela explaining something in session that makes the client go quiet because it finally makes sense."""
    },
    "redefine": {
        "name": "The Authority Redefine",
        "slides": "7-9",
        "rules": """TEMPLATE: THE AUTHORITY REDEFINE (use Mode 2 from the system prompt)
PURPOSE: Take something the reader thinks she understands and show her the real version. 80K+ reach. The post that gets saved and screenshotted and makes people follow you because they trust your brain.

STRUCTURE:
- Slide 1 (hook): "what [topic] actually is and why nobody told us the truth about it." Direct. Promises the real version.
- Slides 2-7: Each slide REDEFINES one aspect. Pattern: "[topic] is not [common belief]. it is [the real thing]." Then the gut punch that makes it land in the body.
  Example: "grief is not an emotion. it is a nervous system event. which is why understanding it has never been enough to stop feeling it."
  Example: "grief is not in your head. it is in your chest. your jaw. the back of your throat. the way your body still reaches for someone who is no longer there."
- ONE slide must include "nobody is telling women this."
- Slides are LONGER here. Each is a complete thought: the redefinition + the somatic or emotional proof. 2-3 sentences per slide.
- Slide 8 (tender close): The most gentle redefinition. The one that gives permission. "grief does not mean you haven't healed. it means you loved someone and love does not have an expiration date."
- Slide 9 (authority stamp): "this is what I know as a grief and trauma therapist."

CRITICAL: Clinical knowledge as POETRY. Somatic specificity. Every slide is a standalone screenshot.
VOICE: Angela the therapist who has sat with a thousand women and is now telling you what she has learned."""
    },
    "tribal": {
        "name": "The Tribal Identity",
        "slides": "8-10",
        "rules": """TEMPLATE: THE TRIBAL IDENTITY (use Mode 3 from the system prompt)
PURPOSE: Create a gathering place. The reader shares the post because sharing it says "I am one of these women." 165K reach. Drives story reposts, comments, and community.

STRUCTURE:
- Slide 1 (hook): "things [group] know that nobody else does." or "what only [group] understand." Name the tribe.
- Slides 2-9: Each slide starts with the SAME anchor phrase: "we know..." (not "you know"). Angela puts herself INSIDE the group.
  - Each slide is one specific lived experience only someone in this group would recognize.
  - ESCALATING INTIMACY: Start with something almost light and relatable. End with something devastating.
  - Example progression:
    Start light: "we know how to google what other women just call their mom about."
    Mid: "we know how to celebrate every milestone alone. and how to make it look easy."
    Deep: "we know what it feels like to reach for the phone years later. when something good happens. when something scary happens. when something ordinary happens that she would have wanted to know about."
  - ONE slide must reframe something the reader has been PRAISED for (being strong, being independent, having it together) as something that was forced on her. This is the share trigger. "we know what it feels like to be fiercely independent. not because we wanted to be. but because we had to be."
- Slide 10 (tribal close): "if you know, you know." Short. Not reflective. An invitation to self-identify. The CTA turns the comments into a room.

CRITICAL RULES:
- "We" language ONLY. Angela is one of them, not looking at them.
- The anchor phrase ("we know") must start every interior slide. Rhythm and repetition create ritual.
- Specificity IS the identity signal. "sit in a doctor's office and say I don't know" not "navigate medical appointments alone."
- Sharing this post is the ACT of belonging.
VOICE: Angela the motherless daughter. Not the therapist. The sister. The one who knows because she has lived it."""
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
- Slide 1: A framing statement that makes the reader stop scrolling. "Things that make sense if you were strong too soon." "What nobody warns you about becoming the family's strong one." Not clickbait, a genuine entry point.
- Slides 2-6: One item per slide. Plain type. Each item should be a specific, lived experience, not a vague concept. "Flinching when someone is too nice to you because you learned love always costs something." Not "difficulty receiving love." The specificity IS the virality.
- Slide 7 (optional long slide): Break the pattern. Let one item breathe longer. The one that makes the reader stop and reread.
- Close slide: Brief. Warm. The last item should land like a quiet truth, not a conclusion.
VOICE: Like Angela texting a close friend who gets it. Unpolished. Real. Direct. Each slide should feel like something someone would DM to a friend with "this is literally me."
CRITICAL: Must NOT sound AI-generated. No parallel structure across all slides. Vary sentence length. Some slides are fragments. Some are full thoughts. Imperfection is the point."""
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
    trigger_keyword = data.get("trigger_keyword", "")
    trigger_label = data.get("trigger_label", "")
    trigger_description = data.get("trigger_description", "")
    all_triggers = data.get("all_triggers", [])

    template = TEMPLATE_RULES.get(template_type, TEMPLATE_RULES["naming"])
    template_rules = template["rules"]

    context_block = ""
    if viral_context:
        context_block += f"\n\nVIRAL POSTS:\n{viral_context}"
    if analysis_context:
        context_block += f"\n\nANALYSIS:\n{analysis_context}"
    if research_context:
        context_block += f"\n\nCLINICAL RESEARCH (weave 1-2 naturally into slides, cite the researcher):\n{research_context}"

    # Build CTA instruction based on selected trigger
    if trigger_keyword and trigger_keyword.strip():
        cta_instruction = f"""- End the caption with EXACTLY ONE CTA line starting with "Comment {trigger_keyword}".
- The product/service for this trigger is: {trigger_label} ({trigger_description}).
- Write the CTA so it directly connects this product to the carousel topic. Do NOT use generic "I'll send you a free resource." Instead, tie what they will receive to what they just read. Example: if the carousel is about nervous system dysregulation and the trigger is EMDR, write "Comment EMDR and I'll walk you through how bilateral stimulation helps your nervous system process what your body has been holding."
- Only ONE CTA line. No other Comment triggers."""
        trigger_json = trigger_keyword
    else:
        cta_instruction = "- Do NOT include any Comment CTA line. No ManyChat triggers."
        trigger_json = ""

    # Build JSON example (clean, no inline conditionals)
    json_example = f'{{"slides": [{{"type":"hook","upper":"TEXT","italic":"subtitle or empty"}},{{"type":"body","html":"One truth."}},{{"type":"close","text":"Reflective invitation."}}], "caption": "Full Instagram caption text here", "trigger": "{trigger_json}", "template": "{template_type}"}}'

    response = claude_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4000,
        system=ANGELA_SYSTEM,
        messages=[{"role": "user", "content": f"""Generate a {slide_count}-slide Instagram carousel using this template:

{template_rules}

TOPIC: {topic}
PILLAR: {pillar}
TONE: {tone}
{context_block}

FORMATTING RULES:
- Do NOT use <br> tags. Each slide is a single flowing sentence or phrase.
- Slide 1: type "hook" with "upper" (UPPERCASE, short, MAX 5 WORDS / 40 CHARACTERS) and "italic" (subtitle, MAX 8 WORDS / 60 CHARACTERS, can be empty string).
- Body slides: type "body" with "html" field. Use <em> for emphasis on emotional words ONLY. No <br> tags.
- Last slide: type "close" with "text" field. A reframe or quiet truth, NOT a hard CTA. MAX 15 WORDS.

*** WORD LIMITS (NON-NEGOTIABLE, WILL BREAK THE DESIGN IF EXCEEDED) ***
These slides render at large font on a 1080x1350 Instagram image. If you exceed these limits, the text overflows and gets cut off. THIS IS THE MOST IMPORTANT RULE:
  * BODY SLIDES: MAX 25 WORDS EACH. Most should be 8-15 words. Count the words. If over 25, split into two slides.
  * Hook upper: MAX 5 WORDS.
  * Hook subtitle: MAX 8 WORDS.
  * Close slide: MAX 15 WORDS.
  * ONE emotional release slide can go up to 30 words. Only ONE. The rest must be under 25.
  * If a thought needs more than 25 words, SPLIT it across two slides. Add a slide. Never cram.
*** END WORD LIMITS ***

- ASYMMETRICAL RHYTHM IS KEY: Most slides should be short (8-15 words). ONE slide (usually second-to-last) can be longer (20-25 words max) as the emotional release. Do NOT make every slide the same length.
- SHOW don't tell. Paint specific images the reader can see, not abstract concepts.
- No em dashes. No "you're not broken" structures. No outcome promises. No urgency.
- Would a shame-sensitive woman feel steadied, not activated?

Return ONLY valid JSON, no backticks:
{json_example}

CAPTION RULES:
- Write the caption in Angela's voice. Short punchy lines. Line breaks between thoughts.
- If clinical research was provided above, cite 1-2 findings naturally in the caption (e.g. "Research from Dr. Mary Frances O'Connor shows grief literally reshapes the brain."). This is important. The research must appear in the caption.
- Include exactly 5 highly relevant hashtags at the end. Quality over quantity.
{cta_instruction}
- No em dashes in the caption either."""}]
    )

    def extract_json(text):
        """Try multiple strategies to extract valid JSON from Claude's response."""
        clean = text.strip()
        # Strategy 1: Remove ```json or ``` wrappers
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        try:
            return json.loads(clean)
        except Exception:
            pass
        # Strategy 2: Find JSON object with regex (handles text before/after)
        match = re.search(r'\{[\s\S]*"slides"\s*:\s*\[[\s\S]*\]\s*[\s\S]*\}', clean)
        if match:
            try:
                return json.loads(match.group())
            except Exception:
                # Strategy 3: Fix trailing commas
                fixed = re.sub(r',\s*([}\]])', r'\1', match.group())
                try:
                    return json.loads(fixed)
                except Exception:
                    pass
        # Strategy 4: Find anything between first { and last }
        first_brace = clean.find('{')
        last_brace = clean.rfind('}')
        if first_brace != -1 and last_brace > first_brace:
            substr = clean[first_brace:last_brace+1]
            try:
                return json.loads(substr)
            except Exception:
                fixed = re.sub(r',\s*([}\]])', r'\1', substr)
                try:
                    return json.loads(fixed)
                except Exception:
                    pass
        return None

    raw_text = response.content[0].text
    result = extract_json(raw_text)

    # If parsing failed, ask Claude to fix the JSON (one retry)
    if result is None:
        try:
            fix_resp = claude_client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=4000,
                messages=[{"role": "user", "content": f"""The following text was supposed to be valid JSON but it has errors. Fix it and return ONLY valid JSON with no backticks, no commentary, no text before or after. Just the raw JSON object:

{raw_text[:6000]}"""}]
            )
            result = extract_json(fix_resp.content[0].text)
        except Exception:
            pass

    if result and isinstance(result, dict) and "slides" in result and isinstance(result["slides"], list) and len(result["slides"]) > 0:
        # Enforce WORD limits per slide (words are the real constraint for large fonts)
        MAX_BODY_WORDS = 30
        MAX_HOOK_UPPER_WORDS = 6
        MAX_HOOK_ITALIC_WORDS = 10
        MAX_CLOSE_WORDS = 18
        enforced_slides = []
        for s in result["slides"]:
            stype = s.get("type", "body")
            if stype == "hook":
                upper_words = s.get("upper", "").split()
                if len(upper_words) > MAX_HOOK_UPPER_WORDS:
                    s["upper"] = " ".join(upper_words[:MAX_HOOK_UPPER_WORDS])
                italic_words = s.get("italic", "").split()
                if len(italic_words) > MAX_HOOK_ITALIC_WORDS:
                    s["italic"] = " ".join(italic_words[:MAX_HOOK_ITALIC_WORDS])
                enforced_slides.append(s)
            elif stype == "close":
                close_words = s.get("text", "").split()
                if len(close_words) > MAX_CLOSE_WORDS:
                    s["text"] = " ".join(close_words[:MAX_CLOSE_WORDS]) + "."
                enforced_slides.append(s)
            elif stype == "cta":
                enforced_slides.append(s)
            else:
                text = s.get("html", s.get("text", ""))
                plain = re.sub(r'<[^>]+>', '', text)
                words = plain.split()
                if len(words) > MAX_BODY_WORDS:
                    # Split into two slides at the nearest sentence boundary
                    mid_word = len(words) // 2
                    rejoined = " ".join(words)
                    # Try to find a sentence break near the middle
                    mid_char = len(" ".join(words[:mid_word]))
                    split_at = rejoined.rfind(". ", 0, mid_char + 40)
                    if split_at == -1:
                        split_at = rejoined.rfind(", ", 0, mid_char + 40)
                    if split_at == -1:
                        split_at = rejoined.rfind(" ", 0, mid_char + 20)
                    if split_at == -1:
                        split_at = mid_char
                    part1 = rejoined[:split_at + 1].strip()
                    part2 = rejoined[split_at + 1:].strip()
                    if part1:
                        enforced_slides.append({"type": "body", "html": part1, "text": part1})
                    if part2:
                        enforced_slides.append({"type": "body", "html": part2, "text": part2})
                else:
                    enforced_slides.append(s)
        result["slides"] = enforced_slides

        # Trigger ranking as a separate fast call
        if all_triggers and len(all_triggers) > 0:
            try:
                trigger_list = ", ".join([f"{t['keyword']} ({t['label']})" for t in all_triggers])
                slide_summary = ""
                for s in result.get("slides", []):
                    if s.get("type") == "hook":
                        slide_summary += s.get("upper", "") + " "
                    elif s.get("type") == "close":
                        slide_summary += s.get("text", "") + " "
                    else:
                        slide_summary += s.get("html", s.get("text", "")) + " "

                rank_resp = claude_client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=500,
                    messages=[{"role": "user", "content": f"""Given this carousel about: {topic}
Slide content: {slide_summary[:500]}

Available ManyChat triggers: {trigger_list}

Pick the top 3 triggers that best fit this specific post. Return ONLY valid JSON, no backticks:
[{{"keyword":"KEYWORD","label":"Label","reason":"One sentence why this fits"}}]"""}]
                )
                rank_clean = rank_resp.content[0].text.strip()
                if rank_clean.startswith("```"):
                    rank_clean = rank_clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                result["trigger_ranking"] = json.loads(rank_clean)
            except Exception:
                result["trigger_ranking"] = []

        return JSONResponse({"success": True, "data": result})
    else:
        return JSONResponse({"success": True, "data": raw_text, "raw": True})


@app.post("/api/login")
async def login(req: Request):
    data = await req.json()
    password = data.get("password", "")
    if ENGINE_PASSWORD and password == ENGINE_PASSWORD:
        return JSONResponse({"success": True, "token": ENGINE_PASSWORD})
    return JSONResponse({"success": False, "error": "Wrong password"}, status_code=401)


@app.get("/health")
async def health():
    return {"status": "ok", "version": "v10-publisher"}
