"""
Real-time Claude reply + lead routing for ManyChat flows.

Wiring in ManyChat:
  1. Inside any flow, add a "User Input" step to capture a free-text reply.
  2. Add an "External Request" node:
       - Method: POST
       - URL: https://<render-url>/api/manychat/claude-reply
       - Header: X-ManyChat-Secret: <MANYCHAT_WEBHOOK_SECRET>
       - Body:
         {
           "contact_id":  "{{contact_id}}",
           "user_message":"{{last_input_text}}",
           "keyword":     "{{last_keyword}}",
           "first_name":  "{{first_name}}"
         }
  3. Map the response fields into custom fields:
       - claude_category, claude_product_fit, claude_heat_score,
         claude_last_reply, claude_reasoning
  4. Add a Condition node branching on `claude_category`:
       - ready_to_buy / hot → "Angela will DM you personally" flow + tag lead:hot
       - warm               → product-fit specific flow (fit:retreat, fit:1on1, ...)
       - warming            → 3-day nurture sequence
       - cold               → top-of-funnel freebie

The endpoint returns JSON in ManyChat's External Request v2 format so ManyChat
sends Claude's reply as the next message and updates custom fields + tags
in a single round trip.
"""

import os
import json
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()

DATABASE_URL = os.environ.get("DATABASE_URL", "")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MC_WEBHOOK_SECRET = os.environ.get("MANYCHAT_WEBHOOK_SECRET", "")
CLAUDE_MODEL = os.environ.get("CLAUDE_MANYCHAT_MODEL", "claude-sonnet-4-20250514")


# ───────────────── DB HELPERS ─────────────────
def get_conn():
    if not DATABASE_URL:
        raise Exception("DATABASE_URL not configured")
    return psycopg2.connect(DATABASE_URL)


def _clean(row):
    if not row:
        return None
    d = dict(row)
    for k, v in d.items():
        if hasattr(v, "isoformat"):
            d[k] = v.isoformat()
    return d


def query(sql, params=None):
    conn = get_conn()
    try:
        conn.autocommit = True
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params or ())
        rows = [_clean(r) for r in cur.fetchall()]
        cur.close()
        return rows
    finally:
        conn.close()


def execute(sql, params=None):
    conn = get_conn()
    try:
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(sql, params or ())
        cur.close()
    finally:
        conn.close()


# ───────────────── TAXONOMY ─────────────────
READINESS_LEVELS = ("cold", "warming", "warm", "hot", "ready_to_buy")

READINESS_HEAT = {
    "cold": 15,
    "warming": 35,
    "warm": 55,
    "hot": 75,
    "ready_to_buy": 95,
}

PRODUCT_FITS = (
    "starter_kit",      # WORTHY — Emotional Starter Kit
    "1on1",             # HEAL, UNTANGLE, STEADY — 1:1 Therapy Session
    "malibu_retreat",   # MALIBURETREAT — Healing with Horses Retreat
    "mother_hunger",    # UNLEARN — Mother Hunger Course
    "grief_relief",     # GRIEFRELIEF, GRIEFTOOLS — Grief Relief Video Series
    "emdr",             # EMDR — EMDR Therapy Sessions
    "community",        # MOM, COMMUNITYCALL — Thursday Group
    "none",
)

# Human-readable tags applied in ManyChat (match your existing tag taxonomy)
LEAD_TAG = {
    "cold": "lead:cold",
    "warming": "lead:warming",
    "warm": "lead:warm",
    "hot": "lead:hot",
    "ready_to_buy": "lead:hot",
}

FIT_TAG = {
    "starter_kit": "fit:starter",
    "1on1": "fit:1on1",
    "malibu_retreat": "fit:retreat",
    "mother_hunger": "fit:course",
    "grief_relief": "fit:video",
    "emdr": "fit:emdr",
    "community": "fit:community",
    "none": "",
}

INTEREST_FROM_CATEGORY = {
    "cold": "new",
    "warming": "new",
    "warm": "warm",
    "hot": "hot",
    "ready_to_buy": "hot",
}


# ───────────────── PROMPT BUILDER ─────────────────
ANGELA_VOICE_RULES = """VOICE:
- Short punchy lines. Rhythm over grammar.
- Second person. Spacious. Regulated. Invitational.
- SHOW don't tell. Hyper-specific over abstract.
- Zero advice. No lecturing. No urgency or scarcity.
- NEVER use em dashes, "healing era", "holding space", "do the work",
  "you are not broken", "hold space", or other wellness cliches.
- 1-3 sentences maximum. The reply is a door, not a monologue."""


def build_prompt(lead: dict, user_message: str, keyword: str, notes: list, triggers: list) -> str:
    fn = (lead.get("first_name") or "").strip()
    ln = (lead.get("last_name") or "").strip()
    name = f"{fn} {ln}".strip() or (lead.get("ig_username") or "") or "friend"

    trigger_list = "\n".join(
        f"- {t['keyword']}: {t.get('label','')} — {t.get('description','')}"
        for t in (triggers or [])
    )

    note_list = "\n".join(f"- {n.get('note','')}" for n in (notes or []))
    note_block = note_list if note_list else "(no prior notes)"

    return f"""You are Angela Schellenberg's AI assistant replying to someone in her ManyChat inbox.
Your job has two parts: (1) write a short reply in Angela's voice, (2) categorize this lead
so the flow can route them to the right next step.

{ANGELA_VOICE_RULES}

SUBSCRIBER CONTEXT:
- Name: {name}
- Instagram: @{lead.get('ig_username','')}
- Email on file: {lead.get('email','') or 'none'}
- Keyword they triggered: {keyword or lead.get('keyword','')}
- Grief type: {lead.get('grief_type','') or 'unknown'}
- Location: {lead.get('user_location_state','') or 'unknown'}
- Segment: {lead.get('audience_segment','') or 'unknown'}
- Current heat score: {lead.get('heat_score', 0)}/100
- Current interest level: {lead.get('interest_level','new')}
- Prior notes:
{note_block}

THE SUBSCRIBER'S LATEST MESSAGE:
\"\"\"{user_message}\"\"\"

AVAILABLE MANYCHAT TRIGGERS YOU CAN RECOMMEND:
{trigger_list}

CATEGORIZATION RULES:
- category ∈ [cold, warming, warm, hot, ready_to_buy]
  - cold: venting, lurker energy, no engagement signals
  - warming: curious but not ready, asking light questions
  - warm: real emotional disclosure, specific pain named
  - hot: asking about cost, logistics, timing, or "how do I start"
  - ready_to_buy: explicit intent to purchase/book/join NOW
- product_fit ∈ [starter_kit, 1on1, malibu_retreat, mother_hunger, grief_relief, emdr, community, none]
  - starter_kit: hasn't opted in yet, needs a free doorway
  - 1on1: wants personal therapy, asking about sessions
  - malibu_retreat: horses, retreat, in-person, California
  - mother_hunger: mom-related grief, Kelly McDaniel references, UNLEARN
  - grief_relief: wants self-study video tools
  - emdr: specifically asked about EMDR or trauma processing
  - community: wants a group, sisterhood, belonging
  - none: no clear fit yet
- heat_score ∈ [0, 100] — your best estimate
- suggested_trigger: pick ONE keyword from the AVAILABLE MANYCHAT TRIGGERS list above that
  should fire next, or empty string if none fit.

RETURN STRICT JSON (no backticks, no markdown, no commentary) with these exact keys:
{{
  "reply": "<Angela-voice reply, 1-3 sentences>",
  "category": "<one of cold|warming|warm|hot|ready_to_buy>",
  "product_fit": "<one of starter_kit|1on1|malibu_retreat|mother_hunger|grief_relief|emdr|community|none>",
  "heat_score": <integer 0-100>,
  "suggested_trigger": "<keyword or empty string>",
  "reasoning": "<one short sentence explaining the category/fit call>"
}}"""


# ───────────────── ENDPOINT ─────────────────
def _verify_secret(request: Request) -> bool:
    if not MC_WEBHOOK_SECRET:
        return True
    provided = (
        request.headers.get("x-manychat-secret")
        or request.headers.get("X-ManyChat-Secret")
        or ""
    )
    return provided == MC_WEBHOOK_SECRET


def _safe_category(value: str) -> str:
    v = (value or "").strip().lower()
    return v if v in READINESS_LEVELS else "warming"


def _safe_product_fit(value: str) -> str:
    v = (value or "").strip().lower()
    return v if v in PRODUCT_FITS else "none"


def _safe_int(value, default=0, lo=0, hi=100) -> int:
    try:
        n = int(float(value))
    except Exception:
        n = default
    return max(lo, min(hi, n))


def _manychat_v2_response(
    reply: str,
    category: str,
    product_fit: str,
    heat_score: int,
    reasoning: str,
    suggested_trigger: str,
) -> dict:
    """Shape the response so ManyChat's External Request v2 accepts it directly."""
    actions = [
        {"action": "set_field_value", "field_name": "claude_category", "value": category},
        {"action": "set_field_value", "field_name": "claude_product_fit", "value": product_fit},
        {"action": "set_field_value", "field_name": "claude_heat_score", "value": heat_score},
        {"action": "set_field_value", "field_name": "claude_last_reply", "value": reply},
        {"action": "set_field_value", "field_name": "claude_reasoning", "value": reasoning},
        {"action": "set_field_value", "field_name": "claude_suggested_trigger", "value": suggested_trigger},
    ]

    lead_tag = LEAD_TAG.get(category, "")
    if lead_tag:
        actions.append({"action": "add_tag", "tag_name": lead_tag})
    fit_tag = FIT_TAG.get(product_fit, "")
    if fit_tag:
        actions.append({"action": "add_tag", "tag_name": fit_tag})

    return {
        "version": "v2",
        "content": {
            "messages": [{"type": "text", "text": reply}],
            "actions": actions,
        },
    }


def _fallback_response(first_name: str) -> dict:
    """When Claude is unreachable or returns junk, still keep the flow moving."""
    name = (first_name or "").strip() or "friend"
    reply = f"thank you for reaching out, {name}. I see you. I'll be in touch soon."
    return _manychat_v2_response(
        reply=reply,
        category="warming",
        product_fit="none",
        heat_score=35,
        reasoning="Claude unavailable; default warming classification.",
        suggested_trigger="",
    )


@router.post("/api/manychat/claude-reply")
async def claude_reply(request: Request):
    """Real-time: ManyChat calls this mid-flow, gets back Claude's reply + routing data."""
    if not _verify_secret(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    contact_id = str(
        body.get("contact_id")
        or body.get("id")
        or body.get("subscriber_id")
        or ""
    ).strip()
    user_message = (body.get("user_message") or body.get("last_input_text") or "").strip()
    keyword = (body.get("keyword") or body.get("last_keyword") or "").strip()
    first_name_hint = (body.get("first_name") or "").strip()

    if not contact_id:
        return JSONResponse({"error": "contact_id is required"}, status_code=400)

    # Load lead, notes, active triggers
    try:
        rows = query("SELECT * FROM manychat_leads_clean WHERE contact_id = %s", (contact_id,))
        lead = rows[0] if rows else {
            "contact_id": contact_id,
            "first_name": first_name_hint,
            "keyword": keyword,
            "heat_score": 0,
            "interest_level": "new",
        }
        notes = query(
            "SELECT note FROM subscriber_notes WHERE contact_id = %s ORDER BY created_at DESC LIMIT 10",
            (contact_id,),
        ) or []
        triggers = query(
            "SELECT keyword, label, description FROM manychat_triggers WHERE is_active = true"
        ) or []
    except Exception as e:
        print(f"[CLAUDE REPLY] DB load error: {e}")
        return JSONResponse(_fallback_response(first_name_hint))

    # If user didn't send a message yet, fall back to the existing analyze path
    if not user_message:
        user_message = "(no free-text message yet — categorize based on their keyword trigger alone)"

    # Call Claude
    if not ANTHROPIC_KEY:
        print("[CLAUDE REPLY] ANTHROPIC_API_KEY not set; returning fallback")
        return JSONResponse(_fallback_response(lead.get("first_name") or first_name_hint))

    raw_text = ""
    try:
        import anthropic

        prompt = build_prompt(lead, user_message, keyword, notes, triggers)
        client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_text = (resp.content[0].text or "").strip()
        if raw_text.startswith("```"):
            # Strip code fences if Claude slipped up
            raw_text = raw_text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        parsed = json.loads(raw_text)
    except json.JSONDecodeError as e:
        print(f"[CLAUDE REPLY] JSON parse failed: {e}; raw={raw_text[:200]}")
        return JSONResponse(_fallback_response(lead.get("first_name") or first_name_hint))
    except Exception as e:
        print(f"[CLAUDE REPLY] Claude call failed: {e}")
        return JSONResponse(_fallback_response(lead.get("first_name") or first_name_hint))

    reply = (parsed.get("reply") or "").strip() or (
        f"thank you for reaching out. I see you."
    )
    category = _safe_category(parsed.get("category"))
    product_fit = _safe_product_fit(parsed.get("product_fit"))
    heat_score = _safe_int(
        parsed.get("heat_score"),
        default=READINESS_HEAT.get(category, 35),
    )
    reasoning = (parsed.get("reasoning") or "").strip()[:500]
    suggested_trigger = (parsed.get("suggested_trigger") or "").strip().upper()

    # Persist everything back to the lead
    analysis_blob = {
        "category": category,
        "product_fit": product_fit,
        "heat_score": heat_score,
        "reasoning": reasoning,
        "suggested_trigger": suggested_trigger,
        "last_reply": reply,
        "last_user_message": user_message,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        execute(
            """UPDATE manychat_leads_clean
               SET heat_score = %s,
                   interest_level = %s,
                   analysis = %s,
                   last_interaction = now(),
                   updated_at = now()
               WHERE contact_id = %s""",
            (
                heat_score,
                INTEREST_FROM_CATEGORY.get(category, "new"),
                json.dumps(analysis_blob),
                contact_id,
            ),
        )
    except Exception as e:
        print(f"[CLAUDE REPLY] lead update error: {e}")

    # Surface hot leads in the CRM dashboard's recommendation feed
    if category in ("hot", "ready_to_buy"):
        try:
            fn = (lead.get("first_name") or first_name_hint or "").strip()
            ln = (lead.get("last_name") or "").strip()
            name = f"{fn} {ln}".strip() or (lead.get("ig_username") or "") or contact_id
            execute(
                """INSERT INTO subscriber_recommendations
                   (contact_id, subscriber_name, title, priority, category,
                    description, suggested_action, suggested_flow, data_points)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    contact_id,
                    name,
                    f"Hot lead: {product_fit.replace('_',' ')}",
                    1 if category == "ready_to_buy" else 2,
                    "high_intent",
                    reasoning or "Flagged hot by real-time Claude classification.",
                    "Send a personal DM today.",
                    suggested_trigger,
                    json.dumps({
                        "category": category,
                        "product_fit": product_fit,
                        "heat_score": heat_score,
                        "last_user_message": user_message,
                    }),
                ),
            )
        except Exception as e:
            print(f"[CLAUDE REPLY] recommendation insert error: {e}")

    # Audit trail — reuse the existing webhook_dashboard helper
    try:
        from webhook_dashboard import log_webhook

        fn = (lead.get("first_name") or first_name_hint or "").strip()
        ln = (lead.get("last_name") or "").strip()
        sub_name = f"{fn} {ln}".strip() or (lead.get("ig_username") or "")
        log_webhook(
            event_type="claude_reply",
            source="manychat",
            mc_id=contact_id,
            keyword=keyword,
            subscriber_name=sub_name,
            status="success",
            payload_preview=json.dumps({
                "user_message": user_message,
                "category": category,
                "product_fit": product_fit,
                "heat_score": heat_score,
            })[:500],
        )
    except Exception as e:
        # Audit is best-effort, never fail the ManyChat response on a log write
        print(f"[CLAUDE REPLY] webhook log error: {e}")

    return JSONResponse(
        _manychat_v2_response(
            reply=reply,
            category=category,
            product_fit=product_fit,
            heat_score=heat_score,
            reasoning=reasoning,
            suggested_trigger=suggested_trigger,
        )
    )
