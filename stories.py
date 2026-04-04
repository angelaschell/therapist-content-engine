# Instagram Stories Generator - Quick story content with polls, quizzes, and prompts
import os
import json
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

STORY_TYPES = {
    "poll": {
        "name": "Poll Story",
        "instruction": "Create a 2-option Instagram poll story. Return: question (bold, short), option_a, option_b. The poll should spark engagement by making people think about their own experience.",
    },
    "this_or_that": {
        "name": "This or That",
        "instruction": "Create a 'This or That' story with 4-5 pairs. Each pair should be relatable therapy/healing choices. Format: pairs array with [option_a, option_b].",
    },
    "quiz": {
        "name": "Quiz Story",
        "instruction": "Create a 4-option quiz story. Return: question, options (array of 4), correct_index (0-3), explanation (1 sentence). Should teach something about grief/trauma/attachment.",
    },
    "prompt": {
        "name": "Journal Prompt",
        "instruction": "Create an Instagram story with a journal/reflection prompt. Return: prompt_text (the question), context (1 sentence of why this matters). The prompt should feel safe but go deep.",
    },
    "behind_scenes": {
        "name": "Behind the Scenes",
        "instruction": "Create a behind-the-scenes story caption for a therapist. Return: caption (casual, warm, 2-3 sentences), suggested_visual (what to photograph/video). Should humanize Angela.",
    },
    "countdown": {
        "name": "Countdown/Hype",
        "instruction": "Create an urgency/countdown story for an upcoming offer or event. Return: headline (bold, short), subtext (1-2 sentences creating FOMO without being pushy), cta (what to comment/DM).",
    },
    "affirmation": {
        "name": "Daily Affirmation",
        "instruction": "Create a powerful affirmation story. Return: affirmation (the statement, 1-2 sentences), note (a tiny context line from Angela). Should feel like a nervous system exhale, not toxic positivity.",
    },
}


@router.post("/api/stories/generate")
async def generate_story(req: Request):
    data = await req.json()
    story_type = data.get("type", "poll")
    topic = data.get("topic", "healing and grief")
    trigger_keyword = data.get("trigger_keyword", "")

    if story_type not in STORY_TYPES:
        return JSONResponse({"error": f"Unknown type. Options: {', '.join(STORY_TYPES.keys())}"}, status_code=400)

    if not ANTHROPIC_KEY:
        return JSONResponse({"error": "ANTHROPIC_API_KEY not configured"}, status_code=500)
    st = STORY_TYPES[story_type]
    cta_note = f"\nIf relevant, include CTA: 'Comment {trigger_keyword}'" if trigger_keyword else ""

    prompt = f"""{st['instruction']}

Topic: {topic}
{cta_note}

Return ONLY valid JSON. No backticks. The JSON should contain all fields mentioned above plus a "type" field with value "{story_type}"."""

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post("https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 800,
                    "system": "You are Angela Schellenberg, licensed grief/trauma therapist. Create Instagram story content. Warm, direct, never preachy. No em dashes. Return only valid JSON.",
                    "messages": [{"role": "user", "content": prompt}]
                })
            resp.raise_for_status()
            text = "".join(b["text"] for b in resp.json().get("content", []) if b.get("type") == "text").strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            result = json.loads(text)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    result["story_type"] = story_type
    result["type_name"] = st["name"]
    return JSONResponse(result)


@router.post("/api/stories/batch")
async def generate_story_batch(req: Request):
    """Generate a week's worth of stories (one of each type)."""
    data = await req.json()
    topic = data.get("topic", "healing and grief")
    types = data.get("types", ["poll", "prompt", "affirmation", "quiz", "behind_scenes"])

    results = []
    for st in types:
        if st not in STORY_TYPES:
            continue
        prompt = f"""{STORY_TYPES[st]['instruction']}
Topic: {topic}
Return ONLY valid JSON with a "type" field set to "{st}"."""

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post("https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                    json={
                        "model": "claude-sonnet-4-20250514",
                        "max_tokens": 600,
                        "system": "You are Angela Schellenberg, licensed grief/trauma therapist. Create story content. Return only valid JSON.",
                        "messages": [{"role": "user", "content": prompt}]
                    })
                resp.raise_for_status()
                text = "".join(b["text"] for b in resp.json().get("content", []) if b.get("type") == "text").strip()
                if text.startswith("```"):
                    text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                result = json.loads(text)
                result["story_type"] = st
                result["type_name"] = STORY_TYPES[st]["name"]
                results.append(result)
        except Exception as e:
            print(f"[stories] Batch generation error for {st}: {e}")

    return JSONResponse({"stories": results})


@router.get("/api/stories/types")
async def list_story_types():
    return JSONResponse({"types": {k: v["name"] for k, v in STORY_TYPES.items()}})
