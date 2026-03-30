"""
COMMENT COMMAND CENTER - FastAPI Backend v3
Uses psycopg2 + DATABASE_URL (same as templates.py)
"""

import os
import json
import uuid
import httpx
import psycopg2
import psycopg2.extras
from datetime import datetime, date, timedelta
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

DATABASE_URL = os.environ.get("DATABASE_URL", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
PERPLEXITY_KEY = os.environ.get("PERPLEXITY_API_KEY", "")
GRAPH_API_BASE = "https://graph.facebook.com/v21.0"
SELF_USERNAME = "angelaschellenberg"

router = APIRouter(prefix="/api/comments", tags=["Comment Command Center"])


def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)

def serialize_row(row):
    out = {}
    for k, v in row.items():
        if isinstance(v, (datetime, date)):
            out[k] = v.isoformat()
        elif isinstance(v, uuid.UUID):
            out[k] = str(v)
        else:
            out[k] = v
    return out

def db_query(sql, params=None):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(sql, params or ())
        return [serialize_row(dict(r)) for r in cur.fetchall()]
    finally:
        conn.close()

def db_execute(sql, params=None):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(sql, params or ())
        conn.commit()
        try:
            return [serialize_row(dict(r)) for r in cur.fetchall()]
        except:
            return []
    finally:
        conn.close()

def db_count(sql, params=None):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(sql, params or ())
        row = cur.fetchone()
        return row["count"] if row else 0
    finally:
        conn.close()


async def get_page_token_and_ig_id():
    from instagram_analytics import token_mgr
    token = token_mgr.user_token
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(f"{GRAPH_API_BASE}/me/accounts",
            params={"access_token": token, "fields": "id,name,access_token,instagram_business_account{id,username}"})
        if r.status_code != 200:
            raise HTTPException(status_code=r.status_code, detail="Could not fetch page token")
        for page in r.json().get("data", []):
            ig = page.get("instagram_business_account")
            if ig:
                return page["access_token"], ig["id"], ig.get("username", "")
        raise HTTPException(status_code=404, detail="No IG business account found")


class CommentUpdateRequest(BaseModel):
    status: Optional[str] = None
    assigned_to: Optional[str] = None
    reply_draft: Optional[str] = None
    dm_sent: Optional[bool] = None
    dm_note: Optional[str] = None

class ReplyRequest(BaseModel):
    comment_ig_id: str
    message: str

class BulkActionRequest(BaseModel):
    comment_ids: List[int]
    action: str
    assigned_to: Optional[str] = None


# ═══════════════════════════════════════════════════════
# FETCH COMMENTS
# ═══════════════════════════════════════════════════════

@router.post("/fetch")
async def fetch_comments(limit: int = Query(default=25, le=50)):
    try:
        page_token, ig_id, username = await get_page_token_and_ig_id()
        media_url = f"{GRAPH_API_BASE}/{ig_id}/media"
        media_params = {
            "fields": "id,caption,permalink,thumbnail_url,media_url,timestamp,media_type",
            "limit": limit, "access_token": page_token
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
                "limit": 100, "access_token": page_token
            }
            async with httpx.AsyncClient(timeout=30) as client:
                comments_resp = await client.get(comments_url, params=comments_params)
                if comments_resp.status_code != 200:
                    continue
                comments_data = comments_resp.json()

            comments = comments_data.get("data", [])
            total_fetched += len(comments)

            for comment in comments:
                cid = comment.get("id")
                if not cid:
                    continue
                c_username = comment.get("username", "unknown")
                existing = db_query("SELECT id FROM ig_comments WHERE ig_comment_id = %s", (cid,))
                if not existing:
                    db_execute("""
                        INSERT INTO ig_comments (ig_comment_id, ig_media_id, media_permalink, media_caption,
                            media_thumbnail_url, username, comment_text, like_count, is_reply, timestamp, status, category)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'unread','uncategorized')
                    """, (cid, media_id, post.get("permalink"), (post.get("caption") or "")[:500],
                        post.get("thumbnail_url") or post.get("media_url"),
                        c_username, comment.get("text", ""),
                        comment.get("like_count", 0), False,
                        comment.get("timestamp", datetime.utcnow().isoformat())))
                    total_new += 1

                for reply in comment.get("replies", {}).get("data", []):
                    rid = reply.get("id")
                    if not rid:
                        continue
                    existing_r = db_query("SELECT id FROM ig_comments WHERE ig_comment_id = %s", (rid,))
                    if not existing_r:
                        db_execute("""
                            INSERT INTO ig_comments (ig_comment_id, ig_media_id, media_permalink, media_caption,
                                username, comment_text, like_count, is_reply, parent_comment_id, timestamp, status, category)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'unread','uncategorized')
                        """, (rid, media_id, post.get("permalink"), (post.get("caption") or "")[:500],
                            reply.get("username", "unknown"), reply.get("text", ""),
                            reply.get("like_count", 0), True, cid,
                            reply.get("timestamp", datetime.utcnow().isoformat())))
                        total_new += 1

            db_execute("INSERT INTO comment_fetch_log (ig_media_id, comments_fetched, new_comments) VALUES (%s,%s,%s)",
                       (media_id, len(comments), total_new))

        return {"success": True, "posts_scanned": posts_scanned,
                "total_comments_fetched": total_fetched, "new_comments_stored": total_new}
    except HTTPException:
        raise
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Meta API error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════
# THREAD VIEW — get a comment + all its replies
# ═══════════════════════════════════════════════════════

@router.get("/thread/{comment_id}")
async def get_thread(comment_id: int):
    """Get a comment and all replies in thread order, including Angela's replies."""
    rows = db_query("SELECT * FROM ig_comments WHERE id = %s", (comment_id,))
    if not rows:
        raise HTTPException(status_code=404, detail="Not found")
    comment = rows[0]

    # Get all replies to this comment
    replies = []
    if not comment.get("is_reply"):
        replies = db_query("""
            SELECT * FROM ig_comments WHERE parent_comment_id = %s ORDER BY timestamp ASC
        """, (comment["ig_comment_id"],))
    else:
        # This IS a reply — get the parent and all siblings
        parent_rows = db_query("SELECT * FROM ig_comments WHERE ig_comment_id = %s", (comment.get("parent_comment_id"),))
        if parent_rows:
            comment = parent_rows[0]
            replies = db_query("""
                SELECT * FROM ig_comments WHERE parent_comment_id = %s ORDER BY timestamp ASC
            """, (comment["ig_comment_id"],))

    angela_replied = any(r.get("username", "").lower() == SELF_USERNAME for r in replies)

    return {
        "comment": comment,
        "replies": replies,
        "angela_replied": angela_replied,
        "reply_count": len(replies)
    }


# ═══════════════════════════════════════════════════════
# AI CATEGORIZATION
# ═══════════════════════════════════════════════════════

@router.post("/categorize")
async def categorize_comments(batch_size: int = Query(default=20, le=50)):
    # Exclude self comments
    comments = db_query("""
        SELECT * FROM ig_comments WHERE category = 'uncategorized'
        AND LOWER(username) != %s
        ORDER BY timestamp DESC LIMIT %s
    """, (SELF_USERNAME, batch_size))

    if not comments:
        return {"success": True, "categorized": 0, "message": "No uncategorized comments"}

    comments_for_ai = [{"db_id": c["id"], "username": c["username"], "text": c["comment_text"],
        "post_caption": (c.get("media_caption") or "")[:200], "like_count": c.get("like_count", 0),
        "is_reply": c.get("is_reply", False)} for c in comments]

    system_prompt = """You analyze Instagram comments for Angela Schellenberg, a licensed trauma/grief therapist (171K followers). Brand: "Grief, Trauma & Your Mama." Specializes in motherless daughters, Mother Hunger (Kelly McDaniel), EMDR, frozen grief, somatic/equine therapy.

Products: 1:1 sessions (HEAL/UNTANGLE/STEADY), Mother Hunger course (UNLEARN), Healing with Horses Retreat Malibu (MALIBURETREAT), Grief Relief Videos (GRIEFRELIEF), Starter Kit (WORTHY), Community (MOM), Equine Digital (EQUINE).

For each comment return: db_id, category (warm_lead|testimonial|engagement_opportunity|question_needs_reply|support_request|spam_noise), category_confidence (0-1), category_reasoning, sentiment (positive|negative|neutral|vulnerable), sentiment_score (-1 to 1), lead_score (0-100), lead_signals (array), reply_draft (warm, no em dashes, 1-3 sentences).

Return ONLY valid JSON array."""

    async with httpx.AsyncClient(timeout=60) as client:
        ai_resp = await client.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-sonnet-4-20250514", "max_tokens": 4096, "system": system_prompt,
                "messages": [{"role": "user", "content": f"Categorize these {len(comments_for_ai)} comments:\n{json.dumps(comments_for_ai, indent=2)}"}]})
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
        db_execute("""UPDATE ig_comments SET category=%s, category_confidence=%s, category_reasoning=%s,
            sentiment=%s, sentiment_score=%s, lead_score=%s, lead_signals=%s, reply_draft=%s, categorized_at=NOW()
            WHERE id=%s""",
            (item.get("category", "uncategorized"), item.get("category_confidence"),
             item.get("category_reasoning"), item.get("sentiment"), item.get("sentiment_score"),
             item.get("lead_score", 0), json.dumps(item.get("lead_signals", [])),
             item.get("reply_draft", ""), db_id))
        updated += 1
        username = next((c["username"] for c in comments if c["id"] == db_id), None)
        if username: _update_commenter_profile(username)

    cats = {}
    for i in categorized: c = i.get("category", "?"); cats[c] = cats.get(c, 0) + 1
    return {"success": True, "categorized": updated, "categories_breakdown": cats}


def _update_commenter_profile(username):
    uc = db_query("SELECT * FROM ig_comments WHERE username = %s", (username,))
    if not uc: return
    cats, scores, has_warm = {}, [], False
    for c in uc:
        cat = c.get("category", "uncategorized"); cats[cat] = cats.get(cat, 0) + 1
        if c.get("sentiment_score") is not None: scores.append(float(c["sentiment_score"]))
        if cat == "warm_lead": has_warm = True
    ts = [c["timestamp"] for c in uc if c.get("timestamp")]
    avg_s = round(sum(scores)/len(scores), 2) if scores else 0
    total = len(uc); is_rep = total >= 2; is_sf = total >= 5
    is_cl = has_warm or any(int(c.get("lead_score") or 0) >= 60 for c in uc)
    existing = db_query("SELECT id FROM ig_commenters WHERE username = %s", (username,))
    if existing:
        db_execute("""UPDATE ig_commenters SET total_comments=%s, first_seen=%s, last_seen=%s,
            avg_sentiment_score=%s, categories_breakdown=%s, is_repeat_commenter=%s,
            is_superfan=%s, is_potential_client=%s, updated_at=NOW() WHERE username=%s""",
            (total, min(ts) if ts else None, max(ts) if ts else None, avg_s,
             json.dumps(cats), is_rep, is_sf, is_cl, username))
    else:
        db_execute("""INSERT INTO ig_commenters (username, total_comments, first_seen, last_seen,
            avg_sentiment_score, categories_breakdown, is_repeat_commenter, is_superfan, is_potential_client)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (username, total, min(ts) if ts else None, max(ts) if ts else None,
             avg_s, json.dumps(cats), is_rep, is_sf, is_cl))


# ═══════════════════════════════════════════════════════
# DEEP ANALYSIS
# ═══════════════════════════════════════════════════════

@router.post("/deep-analysis/{comment_id}")
async def deep_analysis(comment_id: int):
    rows = db_query("SELECT * FROM ig_comments WHERE id = %s", (comment_id,))
    if not rows: raise HTTPException(status_code=404, detail="Not found")
    comment = rows[0]
    history = db_query("SELECT comment_text, category, sentiment, lead_score, timestamp, media_caption FROM ig_comments WHERE username = %s ORDER BY timestamp DESC LIMIT 20", (comment["username"],))
    history_texts = [f"- \"{h['comment_text']}\" (post: {(h.get('media_caption') or '')[:80]})" for h in history]
    cd_rows = db_query("SELECT * FROM ig_commenters WHERE username = %s", (comment["username"],))
    cd = cd_rows[0] if cd_rows else {}

    perplexity_insights = ""
    if PERPLEXITY_KEY:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                pplx_resp = await client.post("https://api.perplexity.ai/chat/completions",
                    headers={"Authorization": f"Bearer {PERPLEXITY_KEY}", "Content-Type": "application/json"},
                    json={"model": "sonar", "messages": [
                        {"role": "system", "content": "Sales psychology and therapeutic marketing expert."},
                        {"role": "user", "content": f'Analyze this comment on a grief therapist\'s post. Return JSON with psychological_state, sales_approach, best_offer_type, readiness_signals, do_not_say, key_insight.\n\nComment: "{comment["comment_text"]}"\nPost: "{(comment.get("media_caption") or "")[:200]}"'}
                    ]})
                if pplx_resp.status_code == 200:
                    perplexity_insights = pplx_resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
        except: pass

    system_prompt = """You are Angela Schellenberg's AI sales intelligence assistant. Licensed trauma/grief therapist, 171K followers, "Grief, Trauma & Your Mama."
Product suite: FREE Starter Kit (WORTHY), FREE Community (MOM), $ Grief Relief Videos (GRIEFRELIEF), $ 101 Tools (TOOLS), $ Equine Digital (EQUINE), $$ Mother Hunger course Kelly McDaniel (UNLEARN), $$$ 1:1 Therapy (HEAL/UNTANGLE/STEADY), $$$$ Horses Retreat Malibu Apr 29-May 3 2026 (MALIBURETREAT).
REPLY RULES: Write as Angela. Warm, direct, real. NEVER em dashes. NEVER "It might not be X, it might be Y." 1-3 sentences. Validate first, offer second.
Return ONLY valid JSON, no backticks."""

    user_prompt = f"""COMMENT: @{comment['username']}: "{comment['comment_text']}"
Post: "{(comment.get('media_caption') or '')[:300]}"
Likes: {comment.get('like_count', 0)} | Category: {comment.get('category', '?')} | Lead: {comment.get('lead_score', 0)}
HISTORY ({cd.get('total_comments', 1)} comments):
{chr(10).join(history_texts) if history_texts else "First-time commenter."}
Superfan={cd.get('is_superfan', False)}, Repeat={cd.get('is_repeat_commenter', False)}
PERPLEXITY: {perplexity_insights or "N/A"}

Return JSON: {{"decoded_message":"2-3 sentences","emotional_state":"plain language","attachment_signals":[],"readiness_level":"cold|warming|warm|hot|ready_to_buy","readiness_explanation":"1 sentence","best_product_fit":"product","product_fit_reason":"1 sentence","secondary_product":"backup","sales_approach":"validation-first|education-bridge|social-proof|scarcity|community-pull|direct-invitation","approach_detail":"2-3 sentences","do_not_say":[],"trigger_word":"or empty","reply_draft":"ready to send","reply_strategy_note":"why"}}"""

    async with httpx.AsyncClient(timeout=60) as client:
        ai_resp = await client.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-sonnet-4-20250514", "max_tokens": 2000, "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}]})
        ai_resp.raise_for_status()
        ai_data = ai_resp.json()

    ai_text = "".join(b["text"] for b in ai_data.get("content", []) if b.get("type") == "text").strip().strip("`").strip()
    if ai_text.startswith("json"): ai_text = ai_text[4:].strip()
    try: analysis = json.loads(ai_text)
    except: raise HTTPException(status_code=500, detail="AI returned invalid JSON")

    readiness_map = {"cold": 15, "warming": 35, "warm": 55, "hot": 75, "ready_to_buy": 95}
    ns = readiness_map.get(analysis.get("readiness_level", ""))
    if analysis.get("reply_draft"):
        if ns: db_execute("UPDATE ig_comments SET reply_draft=%s, lead_score=%s WHERE id=%s", (analysis["reply_draft"], ns, comment_id))
        else: db_execute("UPDATE ig_comments SET reply_draft=%s WHERE id=%s", (analysis["reply_draft"], comment_id))

    pplx_parsed = {}
    if perplexity_insights:
        try:
            cl = perplexity_insights.strip()
            if cl.startswith("```"): cl = cl.split("\n",1)[1].rsplit("```",1)[0].strip()
            pplx_parsed = json.loads(cl)
        except: pplx_parsed = {"key_insight": perplexity_insights[:500]}

    return {"success": True, "analysis": analysis, "perplexity": pplx_parsed,
        "commenter": {"username": comment["username"], "total_comments": cd.get("total_comments", 1),
            "is_superfan": cd.get("is_superfan", False), "is_repeat": cd.get("is_repeat_commenter", False),
            "is_potential_client": cd.get("is_potential_client", False)}}


@router.post("/auto-draft/{comment_id}")
async def auto_draft_reply(comment_id: int):
    rows = db_query("SELECT * FROM ig_comments WHERE id = %s", (comment_id,))
    if not rows: raise HTTPException(status_code=404)
    c = rows[0]
    async with httpx.AsyncClient(timeout=30) as client:
        ai_resp = await client.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-sonnet-4-20250514", "max_tokens": 300,
                "system": "You are Angela Schellenberg, licensed trauma/grief therapist. Warm Instagram reply. Never em dashes. 1-3 sentences. If potential client, guide to DM: HEAL/UNTANGLE/STEADY, UNLEARN, MALIBURETREAT, WORTHY, MOM. Only the reply text.",
                "messages": [{"role": "user", "content": f"Post: {(c.get('media_caption') or '')[:200]}\n@{c['username']}: {c['comment_text']}\nCategory: {c.get('category','?')} | Lead: {c.get('lead_score',0)}"}]})
        ai_resp.raise_for_status()
        ai_data = ai_resp.json()
    reply = "".join(b["text"] for b in ai_data.get("content", []) if b.get("type") == "text").strip()
    db_execute("UPDATE ig_comments SET reply_draft=%s WHERE id=%s", (reply, comment_id))
    return {"success": True, "reply_draft": reply}


# ═══════════════════════════════════════════════════════
# LIST / STATS (excludes self comments)
# ═══════════════════════════════════════════════════════

@router.get("/list")
async def list_comments(
    category: Optional[str] = None, status: Optional[str] = None,
    assigned_to: Optional[str] = None, min_lead_score: Optional[int] = None,
    username: Optional[str] = None, search: Optional[str] = None,
    sort_by: str = Query(default="timestamp", regex="^(timestamp|lead_score|like_count)$"),
    sort_dir: str = Query(default="desc", regex="^(asc|desc)$"),
    page: int = Query(default=1, ge=1), per_page: int = Query(default=25, le=100)
):
    # Always exclude Angela's own comments and replies from list
    wheres = ["LOWER(username) != %s", "is_reply = FALSE"]
    params = [SELF_USERNAME]
    if category: wheres.append("category = %s"); params.append(category)
    if status: wheres.append("status = %s"); params.append(status)
    if assigned_to: wheres.append("assigned_to = %s"); params.append(assigned_to)
    if min_lead_score: wheres.append("lead_score >= %s"); params.append(min_lead_score)
    if username: wheres.append("username ILIKE %s"); params.append(f"%{username}%")
    if search: wheres.append("comment_text ILIKE %s"); params.append(f"%{search}%")
    where_clause = "WHERE " + " AND ".join(wheres)
    sort_col = {"timestamp":"timestamp","lead_score":"lead_score","like_count":"like_count"}.get(sort_by, "timestamp")
    direction = "DESC" if sort_dir == "desc" else "ASC"
    total = db_count(f"SELECT COUNT(*) as count FROM ig_comments {where_clause}", params)
    offset = (page - 1) * per_page
    data = db_query(f"SELECT * FROM ig_comments {where_clause} ORDER BY {sort_col} {direction} LIMIT %s OFFSET %s", params + [per_page, offset])

    # For each comment, check if Angela replied
    for d in data:
        replies = db_query("SELECT username FROM ig_comments WHERE parent_comment_id = %s", (d["ig_comment_id"],))
        d["angela_replied"] = any(r.get("username", "").lower() == SELF_USERNAME for r in replies)
        d["reply_count"] = len(replies)

    return {"data": data, "total": total, "page": page, "per_page": per_page,
        "total_pages": (total + per_page - 1) // per_page if total else 0}


@router.get("/stats")
async def get_stats(days: int = Query(default=7, le=90)):
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    # Exclude self
    total = db_count("SELECT COUNT(*) as count FROM ig_comments WHERE timestamp >= %s AND LOWER(username) != %s AND is_reply = FALSE", (since, SELF_USERNAME))
    all_recent = db_query("SELECT category, status, lead_score, sentiment FROM ig_comments WHERE timestamp >= %s AND LOWER(username) != %s AND is_reply = FALSE", (since, SELF_USERNAME))
    cats, sts, sents = {}, {}, {}
    for c in all_recent:
        ca = c.get("category","?"); cats[ca] = cats.get(ca,0)+1
        st = c.get("status","?"); sts[st] = sts.get(st,0)+1
        se = c.get("sentiment","?"); sents[se] = sents.get(se,0)+1
    sf = db_query("SELECT * FROM ig_commenters WHERE is_superfan = TRUE ORDER BY total_comments DESC LIMIT 10")
    pc = db_query("SELECT * FROM ig_commenters WHERE is_potential_client = TRUE ORDER BY last_seen DESC LIMIT 10")
    return {"period_days": days, "total_comments": total, "categories": cats, "statuses": sts,
        "sentiments": sents, "warm_leads_count": cats.get("warm_lead", 0),
        "unread_count": sts.get("unread", 0), "needs_reply_count": cats.get("question_needs_reply", 0),
        "superfans": sf, "potential_clients": pc}


# ═══════════════════════════════════════════════════════
# ACTIONS
# ═══════════════════════════════════════════════════════

@router.patch("/{comment_id}")
async def update_comment(comment_id: int, update: CommentUpdateRequest):
    sets, params = [], []
    if update.status is not None: sets.append("status=%s"); params.append(update.status)
    if update.assigned_to is not None: sets.append("assigned_to=%s"); params.append(update.assigned_to)
    if update.reply_draft is not None: sets.append("reply_draft=%s"); params.append(update.reply_draft)
    if update.dm_sent is not None:
        sets.append("dm_sent=%s"); params.append(update.dm_sent)
        if update.dm_sent: sets.append("dm_sent_at=NOW()")
    if update.dm_note is not None: sets.append("dm_note=%s"); params.append(update.dm_note)
    if not sets: raise HTTPException(status_code=400, detail="No data")
    sets.append("updated_at=NOW()")
    params.append(comment_id)
    db_execute(f"UPDATE ig_comments SET {', '.join(sets)} WHERE id=%s", params)
    return {"success": True}

@router.post("/reply")
async def reply_to_comment(req: ReplyRequest):
    page_token, ig_id, username = await get_page_token_and_ig_id()
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(f"{GRAPH_API_BASE}/{req.comment_ig_id}/replies",
            params={"message": req.message, "access_token": page_token})
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"IG error: {resp.json().get('error',{}).get('message','?')}")
        reply_data = resp.json()
    db_execute("UPDATE ig_comments SET status='replied', replied_at=NOW(), reply_ig_id=%s WHERE ig_comment_id=%s",
        (reply_data.get("id"), req.comment_ig_id))
    return {"success": True, "reply_id": reply_data.get("id")}

@router.post("/bulk")
async def bulk_action(req: BulkActionRequest):
    amap = {"archive": "archived", "flag": "flagged", "mark_read": "read"}
    if req.action == "assign" and req.assigned_to:
        for cid in req.comment_ids: db_execute("UPDATE ig_comments SET assigned_to=%s WHERE id=%s", (req.assigned_to, cid))
    elif req.action in amap:
        for cid in req.comment_ids: db_execute("UPDATE ig_comments SET status=%s WHERE id=%s", (amap[req.action], cid))
    else: raise HTTPException(status_code=400, detail=f"Invalid: {req.action}")
    return {"success": True, "updated": len(req.comment_ids)}

@router.get("/commenters")
async def list_commenters(
    filter_type: Optional[str] = Query(default=None, regex="^(superfan|potential_client|repeat)$"),
    page: int = Query(default=1, ge=1), per_page: int = Query(default=20, le=50)
):
    where = ""
    if filter_type == "superfan": where = "WHERE is_superfan = TRUE"
    elif filter_type == "potential_client": where = "WHERE is_potential_client = TRUE"
    elif filter_type == "repeat": where = "WHERE is_repeat_commenter = TRUE"
    total = db_count(f"SELECT COUNT(*) as count FROM ig_commenters {where}")
    offset = (page - 1) * per_page
    data = db_query(f"SELECT * FROM ig_commenters {where} ORDER BY total_comments DESC LIMIT %s OFFSET %s", (per_page, offset))
    return {"data": data, "total": total, "page": page}
