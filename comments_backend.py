"""
COMMENT COMMAND CENTER - FastAPI Backend
=========================================
Add these routes to your existing main.py on Render.
Requires: supabase-py, httpx, anthropic
"""

import os
import json
import httpx
from datetime import datetime, timedelta
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel
from supabase import create_client

# ── Config ──────────────────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
PERPLEXITY_KEY = os.getenv("PERPLEXITY_API_KEY", "")
GRAPH_API_BASE = "https://graph.facebook.com/v21.0"

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


async def get_page_token_and_ig_id():
    """Discover page token and IG ID dynamically (same as publisher)."""
    from instagram_analytics import token_mgr
    token = token_mgr.user_token
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(
            f"{GRAPH_API_BASE}/me/accounts",
            params={"access_token": token, "fields": "id,name,access_token,instagram_business_account{id,username}"}
        )
        if r.status_code != 200:
            raise HTTPException(status_code=r.status_code, detail="Could not fetch page token")
        for page in r.json().get("data", []):
            ig = page.get("instagram_business_account")
            if ig:
                return page["access_token"], ig["id"], ig.get("username", "")
        raise HTTPException(status_code=404, detail="No IG business account found")

router = APIRouter(prefix="/api/comments", tags=["Comment Command Center"])


# ── Pydantic Models ─────────────────────────────────────
class CommentUpdateRequest(BaseModel):
    status: Optional[str] = None
    assigned_to: Optional[str] = None
    reply_draft: Optional[str] = None


class ReplyRequest(BaseModel):
    comment_ig_id: str
    message: str


class BulkActionRequest(BaseModel):
    comment_ids: List[int]
    action: str
    assigned_to: Optional[str] = None


# ── FETCH COMMENTS FROM INSTAGRAM ───────────────────────
@router.post("/fetch")
async def fetch_comments(limit: int = Query(default=25, le=50)):
    try:
        page_token, ig_id, username = await get_page_token_and_ig_id()

        media_url = f"{GRAPH_API_BASE}/{ig_id}/media"
        media_params = {
            "fields": "id,caption,permalink,thumbnail_url,media_url,timestamp,media_type",
            "limit": limit,
            "access_token": page_token
        }

        async with httpx.AsyncClient(timeout=30) as client:
            media_resp = await client.get(media_url, params=media_params)
            media_resp.raise_for_status()
            media_data = media_resp.json()

        posts = media_data.get("data", [])
        total_new = 0
        total_fetched = 0
        posts_scanned = 0

        for post in posts:
            media_id = post["id"]
            posts_scanned += 1

            comments_url = f"{GRAPH_API_BASE}/{media_id}/comments"
            comments_params = {
                "fields": "id,text,timestamp,username,like_count,replies{id,text,timestamp,username,like_count}",
                "limit": 100,
                "access_token": page_token
            }

            async with httpx.AsyncClient(timeout=30) as client:
                comments_resp = await client.get(comments_url, params=comments_params)
                if comments_resp.status_code != 200:
                    continue
                comments_data = comments_resp.json()

            comments = comments_data.get("data", [])
            total_fetched += len(comments)

            for comment in comments:
                existing = supabase.table("ig_comments").select("id").eq(
                    "ig_comment_id", comment["id"]
                ).execute()

                if not existing.data:
                    row = {
                        "ig_comment_id": comment["id"],
                        "ig_media_id": media_id,
                        "media_permalink": post.get("permalink"),
                        "media_caption": (post.get("caption") or "")[:500],
                        "media_thumbnail_url": post.get("thumbnail_url") or post.get("media_url"),
                        "username": comment["username"],
                        "comment_text": comment["text"],
                        "like_count": comment.get("like_count", 0),
                        "is_reply": False,
                        "timestamp": comment["timestamp"],
                        "status": "unread",
                        "category": "uncategorized"
                    }
                    supabase.table("ig_comments").insert(row).execute()
                    total_new += 1

                replies = comment.get("replies", {}).get("data", [])
                for reply in replies:
                    existing_reply = supabase.table("ig_comments").select("id").eq(
                        "ig_comment_id", reply["id"]
                    ).execute()
                    if not existing_reply.data:
                        reply_row = {
                            "ig_comment_id": reply["id"],
                            "ig_media_id": media_id,
                            "media_permalink": post.get("permalink"),
                            "media_caption": (post.get("caption") or "")[:500],
                            "username": reply["username"],
                            "comment_text": reply["text"],
                            "like_count": reply.get("like_count", 0),
                            "is_reply": True,
                            "parent_comment_id": comment["id"],
                            "timestamp": reply["timestamp"],
                            "status": "unread",
                            "category": "uncategorized"
                        }
                        supabase.table("ig_comments").insert(reply_row).execute()
                        total_new += 1

            supabase.table("comment_fetch_log").insert({
                "ig_media_id": media_id,
                "comments_fetched": len(comments),
                "new_comments": total_new
            }).execute()

        return {
            "success": True,
            "posts_scanned": posts_scanned,
            "total_comments_fetched": total_fetched,
            "new_comments_stored": total_new
        }
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Meta API error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── AI CATEGORIZATION ───────────────────────────────────
@router.post("/categorize")
async def categorize_comments(batch_size: int = Query(default=20, le=50)):
    result = supabase.table("ig_comments").select("*").eq(
        "category", "uncategorized"
    ).order("timestamp", desc=True).limit(batch_size).execute()

    comments = result.data
    if not comments:
        return {"success": True, "categorized": 0, "message": "No uncategorized comments"}

    comments_for_ai = [{"db_id": c["id"], "username": c["username"], "text": c["comment_text"],
        "post_caption": (c.get("media_caption") or "")[:200], "like_count": c.get("like_count", 0),
        "is_reply": c.get("is_reply", False)} for c in comments]

    system_prompt = """You analyze Instagram comments for Angela Schellenberg, a licensed trauma/grief therapist (171K followers, @angelaschellenberg). Brand: "Grief, Trauma & Your Mama." Specializes in motherless daughters, Mother Hunger (Kelly McDaniel), EMDR, frozen grief, somatic/equine therapy.

Products: 1:1 sessions (HEAL/UNTANGLE/STEADY), Mother Hunger course (UNLEARN), Healing with Horses Retreat Malibu (MALIBURETREAT), Grief Relief Videos (GRIEFRELIEF), Starter Kit (WORTHY), Community (MOM), Equine Digital (EQUINE).

For each comment return: db_id, category (warm_lead|testimonial|engagement_opportunity|question_needs_reply|support_request|spam_noise), category_confidence (0-1), category_reasoning, sentiment (positive|negative|neutral|vulnerable), sentiment_score (-1 to 1), lead_score (0-100), lead_signals (array), reply_draft (warm, no em dashes, 1-3 sentences).

Return ONLY valid JSON array."""

    user_prompt = f"Categorize these {len(comments_for_ai)} comments:\n{json.dumps(comments_for_ai, indent=2)}"

    async with httpx.AsyncClient(timeout=60) as client:
        ai_resp = await client.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-sonnet-4-20250514", "max_tokens": 4096, "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}]})
        ai_resp.raise_for_status()
        ai_data = ai_resp.json()

    ai_text = "".join(b["text"] for b in ai_data.get("content", []) if b.get("type") == "text")
    ai_text = ai_text.strip().strip("`").strip()
    if ai_text.startswith("json"): ai_text = ai_text[4:].strip()

    try:
        categorized = json.loads(ai_text)
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="AI returned invalid JSON")

    updated = 0
    for item in categorized:
        db_id = item.get("db_id")
        if not db_id: continue
        supabase.table("ig_comments").update({
            "category": item.get("category", "uncategorized"),
            "category_confidence": item.get("category_confidence"),
            "category_reasoning": item.get("category_reasoning"),
            "sentiment": item.get("sentiment"),
            "sentiment_score": item.get("sentiment_score"),
            "lead_score": item.get("lead_score", 0),
            "lead_signals": item.get("lead_signals", []),
            "reply_draft": item.get("reply_draft", ""),
            "categorized_at": datetime.utcnow().isoformat()
        }).eq("id", db_id).execute()
        updated += 1
        username = next((c["username"] for c in comments if c["id"] == db_id), None)
        if username: await _update_commenter_profile(username)

    return {"success": True, "categorized": updated, "categories_breakdown": _count_categories(categorized)}


async def _update_commenter_profile(username: str):
    result = supabase.table("ig_comments").select("*").eq("username", username).execute()
    uc = result.data
    if not uc: return
    cats = {}
    scores = []
    has_warm = False
    for c in uc:
        cat = c.get("category", "uncategorized")
        cats[cat] = cats.get(cat, 0) + 1
        if c.get("sentiment_score") is not None: scores.append(c["sentiment_score"])
        if cat == "warm_lead": has_warm = True
    ts = [c["timestamp"] for c in uc if c.get("timestamp")]
    profile = {"username": username, "total_comments": len(uc),
        "first_seen": min(ts) if ts else None, "last_seen": max(ts) if ts else None,
        "avg_sentiment_score": round(sum(scores)/len(scores), 2) if scores else 0,
        "categories_breakdown": cats, "is_repeat_commenter": len(uc) >= 2,
        "is_superfan": len(uc) >= 5,
        "is_potential_client": has_warm or any(c.get("lead_score", 0) >= 60 for c in uc)}
    existing = supabase.table("ig_commenters").select("id").eq("username", username).execute()
    if existing.data:
        supabase.table("ig_commenters").update(profile).eq("username", username).execute()
    else:
        supabase.table("ig_commenters").insert(profile).execute()


def _count_categories(items):
    counts = {}
    for i in items: counts[i.get("category", "?")] = counts.get(i.get("category", "?"), 0) + 1
    return counts


# ══════════════════════════════════════════════════════════
# DEEP ANALYSIS + AUTO DRAFT REPLY (Perplexity + Claude)
# ══════════════════════════════════════════════════════════

@router.post("/deep-analysis/{comment_id}")
async def deep_analysis(comment_id: int):
    """
    Full AI intelligence brief on a comment:
    1. Perplexity researches sales psychology for the commenter's language
    2. Claude synthesizes into: decoded message, emotional state, best product fit,
       sales approach, and an optimized auto-draft reply
    """
    result = supabase.table("ig_comments").select("*").eq("id", comment_id).single().execute()
    comment = result.data
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    # Get commenter history
    user_history = supabase.table("ig_comments").select(
        "comment_text,category,sentiment,lead_score,timestamp,media_caption"
    ).eq("username", comment["username"]).order("timestamp", desc=True).limit(20).execute()

    history_texts = [f"- \"{h['comment_text']}\" (post: {(h.get('media_caption') or '')[:80]})"
                     for h in (user_history.data or [])]

    commenter = supabase.table("ig_commenters").select("*").eq("username", comment["username"]).execute()
    cd = commenter.data[0] if commenter.data else {}

    # ── Step 1: Perplexity Sales Research ────────────
    perplexity_insights = ""
    if PERPLEXITY_KEY:
        try:
            pplx_prompt = f"""Analyze this Instagram comment on a grief therapist's post. Give actionable sales psychology insights.

Comment: "{comment['comment_text']}"
Post context: "{(comment.get('media_caption') or '')[:200]}"

Research:
1. What psychological state is this person likely in? (attachment theory lens)
2. What sales/persuasion approach works best for someone in this state? (cite Cialdini, motivational interviewing, etc.)
3. What therapeutic offer would feel most relevant?
4. What language patterns signal readiness to invest in help?

Return ONLY valid JSON, no backticks:
{{"psychological_state": "...", "sales_approach": "...", "best_offer_type": "...", "readiness_signals": ["..."], "do_not_say": ["..."], "key_insight": "..."}}"""

            async with httpx.AsyncClient(timeout=30) as client:
                pplx_resp = await client.post("https://api.perplexity.ai/chat/completions",
                    headers={"Authorization": f"Bearer {PERPLEXITY_KEY}", "Content-Type": "application/json"},
                    json={"model": "sonar", "messages": [
                        {"role": "system", "content": "You are a sales psychology and therapeutic marketing expert. Attachment theory, grief psychology, ethical persuasion for mental health. Be specific."},
                        {"role": "user", "content": pplx_prompt}
                    ]})
                if pplx_resp.status_code == 200:
                    perplexity_insights = pplx_resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
        except Exception as e:
            perplexity_insights = f"Perplexity unavailable: {str(e)}"

    # ── Step 2: Claude Deep Analysis ─────────────────
    system_prompt = """You are Angela Schellenberg's AI sales intelligence assistant. She is a licensed trauma/grief therapist, 171K followers, brand "Grief, Trauma & Your Mama."

Product suite (by price):
1. FREE: Emotional Starter Kit (trigger: WORTHY)
2. FREE: GT&YM Community Circle (trigger: MOM)
3. $: Grief Relief Video Series (trigger: GRIEFRELIEF)
4. $: 101 Tools (trigger: TOOLS)
5. $: Equine Digital Product (trigger: EQUINE)
6. $$: Mother Hunger© course, Kelly McDaniel (trigger: UNLEARN)
7. $$$: 1:1 Therapy Sessions (triggers: HEAL, UNTANGLE, STEADY)
8. $$$$: Healing with Horses Retreat, Malibu April 29 - May 3, 2026 (trigger: MALIBURETREAT)

REPLY RULES:
- Write as Angela. Warm, direct, real. Not clinical or corporate.
- NEVER use em dashes
- NEVER use "It might not be X, it might be Y" framing
- 1-3 sentences. Brief for Instagram.
- For warm leads: naturally guide to DM a trigger word. Invitational, not pushy.
- For people in pain: validate FIRST, offer second.
- For testimonials: genuine gratitude.
- Match their energy.

Return ONLY valid JSON, no markdown or backticks."""

    user_prompt = f"""COMMENT:
@{comment['username']}: "{comment['comment_text']}"
Post: "{(comment.get('media_caption') or '')[:300]}"
Likes: {comment.get('like_count', 0)} | Category: {comment.get('category', '?')} | Lead score: {comment.get('lead_score', 0)}

HISTORY ({cd.get('total_comments', 1)} total comments):
{chr(10).join(history_texts) if history_texts else "First-time commenter."}
Superfan={cd.get('is_superfan', False)}, Repeat={cd.get('is_repeat_commenter', False)}

PERPLEXITY RESEARCH:
{perplexity_insights or "Not available"}

Return JSON:
{{
  "decoded_message": "What they're REALLY saying, 2-3 sentences, attachment lens",
  "emotional_state": "Plain language emotional state",
  "attachment_signals": ["detected attachment indicators"],
  "readiness_level": "cold|warming|warm|hot|ready_to_buy",
  "readiness_explanation": "1 sentence why",
  "best_product_fit": "Best product/service for them right now",
  "product_fit_reason": "1 sentence why",
  "secondary_product": "Backup if primary is too big a leap",
  "sales_approach": "validation-first|education-bridge|social-proof|scarcity|community-pull|direct-invitation",
  "approach_detail": "Exactly how to execute, 2-3 sentences",
  "do_not_say": ["things to avoid"],
  "trigger_word": "ManyChat trigger to use, or empty",
  "reply_draft": "The actual reply, ready to send on Instagram",
  "reply_strategy_note": "Why the reply is crafted this way (Angela's eyes only)"
}}"""

    async with httpx.AsyncClient(timeout=60) as client:
        ai_resp = await client.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-sonnet-4-20250514", "max_tokens": 2000, "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}]})
        ai_resp.raise_for_status()
        ai_data = ai_resp.json()

    ai_text = "".join(b["text"] for b in ai_data.get("content", []) if b.get("type") == "text")
    ai_text = ai_text.strip().strip("`").strip()
    if ai_text.startswith("json"): ai_text = ai_text[4:].strip()

    try:
        analysis = json.loads(ai_text)
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="AI returned invalid JSON")

    # Save draft + update lead score
    update_fields = {"reply_draft": analysis.get("reply_draft", "")}
    readiness_map = {"cold": 15, "warming": 35, "warm": 55, "hot": 75, "ready_to_buy": 95}
    new_score = readiness_map.get(analysis.get("readiness_level", ""))
    if new_score: update_fields["lead_score"] = new_score
    supabase.table("ig_comments").update(update_fields).eq("id", comment_id).execute()

    # Parse perplexity
    pplx_parsed = {}
    if perplexity_insights:
        try:
            clean = perplexity_insights.strip()
            if clean.startswith("```"): clean = clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            pplx_parsed = json.loads(clean)
        except:
            pplx_parsed = {"key_insight": perplexity_insights[:500]}

    return {
        "success": True,
        "analysis": analysis,
        "perplexity": pplx_parsed,
        "commenter": {
            "username": comment["username"],
            "total_comments": cd.get("total_comments", 1),
            "is_superfan": cd.get("is_superfan", False),
            "is_repeat": cd.get("is_repeat_commenter", False),
            "is_potential_client": cd.get("is_potential_client", False)
        }
    }


# ── QUICK AUTO-DRAFT (no Perplexity, faster) ────────────
@router.post("/auto-draft/{comment_id}")
async def auto_draft_reply(comment_id: int):
    result = supabase.table("ig_comments").select("*").eq("id", comment_id).single().execute()
    comment = result.data
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    system_prompt = """You are Angela Schellenberg, licensed trauma/grief therapist. Write a warm Instagram reply.
Rules: Never use em dashes. Be direct, compassionate, not clinical. 1-3 sentences.
If potential client, guide to DM: HEAL/UNTANGLE/STEADY (1:1), UNLEARN (course), MALIBURETREAT (retreat), WORTHY (free kit), MOM (community).
Sound human. Respond with ONLY the reply text."""

    prompt = f"Post: {(comment.get('media_caption') or '')[:200]}\n@{comment['username']}: {comment['comment_text']}\nCategory: {comment.get('category', '?')} | Lead: {comment.get('lead_score', 0)}"

    async with httpx.AsyncClient(timeout=30) as client:
        ai_resp = await client.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-sonnet-4-20250514", "max_tokens": 300, "system": system_prompt,
                "messages": [{"role": "user", "content": prompt}]})
        ai_resp.raise_for_status()
        ai_data = ai_resp.json()

    reply = "".join(b["text"] for b in ai_data.get("content", []) if b.get("type") == "text").strip()
    supabase.table("ig_comments").update({"reply_draft": reply}).eq("id", comment_id).execute()
    return {"success": True, "reply_draft": reply}


# ── LIST / FILTER ───────────────────────────────────────
@router.get("/list")
async def list_comments(
    category: Optional[str] = None, status: Optional[str] = None,
    assigned_to: Optional[str] = None, min_lead_score: Optional[int] = None,
    username: Optional[str] = None, search: Optional[str] = None,
    sort_by: str = Query(default="timestamp", regex="^(timestamp|lead_score|like_count)$"),
    sort_dir: str = Query(default="desc", regex="^(asc|desc)$"),
    page: int = Query(default=1, ge=1), per_page: int = Query(default=25, le=100)
):
    query = supabase.table("ig_comments").select("*", count="exact")
    if category: query = query.eq("category", category)
    if status: query = query.eq("status", status)
    if assigned_to: query = query.eq("assigned_to", assigned_to)
    if min_lead_score: query = query.gte("lead_score", min_lead_score)
    if username: query = query.ilike("username", f"%{username}%")
    if search: query = query.ilike("comment_text", f"%{search}%")
    query = query.order(sort_by, desc=(sort_dir == "desc"))
    offset = (page - 1) * per_page
    query = query.range(offset, offset + per_page - 1)
    result = query.execute()
    return {"data": result.data, "total": result.count, "page": page, "per_page": per_page,
        "total_pages": (result.count + per_page - 1) // per_page if result.count else 0}


# ── STATS ───────────────────────────────────────────────
@router.get("/stats")
async def get_stats(days: int = Query(default=7, le=90)):
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    total = supabase.table("ig_comments").select("id", count="exact").gte("timestamp", since).execute()
    all_recent = supabase.table("ig_comments").select("category,status,lead_score,sentiment").gte("timestamp", since).execute()
    cats, sts, sents = {}, {}, {}
    for c in (all_recent.data or []):
        cats[c.get("category", "?")] = cats.get(c.get("category", "?"), 0) + 1
        sts[c.get("status", "?")] = sts.get(c.get("status", "?"), 0) + 1
        sents[c.get("sentiment", "?")] = sents.get(c.get("sentiment", "?"), 0) + 1
    sf = supabase.table("ig_commenters").select("*").eq("is_superfan", True).order("total_comments", desc=True).limit(10).execute()
    pc = supabase.table("ig_commenters").select("*").eq("is_potential_client", True).order("last_seen", desc=True).limit(10).execute()
    return {"period_days": days, "total_comments": total.count, "categories": cats, "statuses": sts,
        "sentiments": sents, "warm_leads_count": cats.get("warm_lead", 0),
        "unread_count": sts.get("unread", 0), "needs_reply_count": cats.get("question_needs_reply", 0),
        "superfans": sf.data, "potential_clients": pc.data}


@router.patch("/{comment_id}")
async def update_comment(comment_id: int, update: CommentUpdateRequest):
    update_data = {k: v for k, v in update.dict().items() if v is not None}
    if not update_data: raise HTTPException(status_code=400, detail="No update data")
    return {"success": True, "updated": supabase.table("ig_comments").update(update_data).eq("id", comment_id).execute().data}


@router.post("/reply")
async def reply_to_comment(req: ReplyRequest):
    page_token, ig_id, username = await get_page_token_and_ig_id()
    url = f"{GRAPH_API_BASE}/{req.comment_ig_id}/replies"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, params={"message": req.message, "access_token": page_token})
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"IG error: {resp.json().get('error', {}).get('message', '?')}")
        reply_data = resp.json()
    supabase.table("ig_comments").update({
        "status": "replied", "replied_at": datetime.utcnow().isoformat(), "reply_ig_id": reply_data.get("id")
    }).eq("ig_comment_id", req.comment_ig_id).execute()
    return {"success": True, "reply_id": reply_data.get("id")}


@router.post("/bulk")
async def bulk_action(req: BulkActionRequest):
    action_map = {"archive": {"status": "archived"}, "flag": {"status": "flagged"}, "mark_read": {"status": "read"}}
    if req.action == "assign" and req.assigned_to:
        ud = {"assigned_to": req.assigned_to}
    elif req.action in action_map:
        ud = action_map[req.action]
    else:
        raise HTTPException(status_code=400, detail=f"Invalid action: {req.action}")
    for cid in req.comment_ids:
        supabase.table("ig_comments").update(ud).eq("id", cid).execute()
    return {"success": True, "updated": len(req.comment_ids), "action": req.action}


@router.get("/commenters")
async def list_commenters(
    filter_type: Optional[str] = Query(default=None, regex="^(superfan|potential_client|repeat)$"),
    page: int = Query(default=1, ge=1), per_page: int = Query(default=20, le=50)
):
    query = supabase.table("ig_commenters").select("*", count="exact")
    if filter_type == "superfan": query = query.eq("is_superfan", True)
    elif filter_type == "potential_client": query = query.eq("is_potential_client", True)
    elif filter_type == "repeat": query = query.eq("is_repeat_commenter", True)
    query = query.order("total_comments", desc=True)
    offset = (page - 1) * per_page
    query = query.range(offset, offset + per_page - 1)
    result = query.execute()
    return {"data": result.data, "total": result.count, "page": page}
