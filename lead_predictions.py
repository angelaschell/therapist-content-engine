# Lead Score Predictions - Visual dashboard with conversion probability estimates
import os
import json
import psycopg2
import psycopg2.extras
import httpx
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()
DATABASE_URL = os.environ.get("DATABASE_URL", "")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")


def get_conn():
    if not DATABASE_URL:
        raise Exception("DATABASE_URL not configured")
    return psycopg2.connect(DATABASE_URL)

def clean(row):
    if not row:
        return None
    d = dict(row)
    for k, v in d.items():
        if isinstance(v, datetime):
            d[k] = v.isoformat()
        elif hasattr(v, 'isoformat'):
            d[k] = v.isoformat()
    return d

def query(sql, params=None):
    conn = get_conn()
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(sql, params or ())
    result = [clean(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return result


@router.get("/api/predictions/leads")
async def predict_lead_conversions():
    """Calculate conversion probability for hot leads based on behavior signals."""
    subs = query("""
        SELECT s.*,
            (SELECT COUNT(*) FROM subscriber_triggers WHERE mc_id = s.mc_id) as actual_triggers,
            (SELECT MAX(fired_at) FROM subscriber_triggers WHERE mc_id = s.mc_id) as latest_trigger
        FROM manychat_subscribers s
        WHERE s.do_not_contact = false AND s.heat_score >= 20
        ORDER BY s.heat_score DESC LIMIT 50
    """)

    predictions = []
    now = datetime.now(timezone.utc)

    for sub in subs:
        # Calculate conversion probability based on signals
        prob = 0
        signals = []

        # Heat score contribution (0-30 points)
        heat = sub.get("heat_score", 0) or 0
        heat_points = min(heat * 0.3, 30)
        prob += heat_points

        # Trigger count (0-25 points, each trigger = 8 points, max 25)
        triggers = sub.get("actual_triggers", 0) or sub.get("trigger_count", 0) or 0
        trigger_points = min(triggers * 8, 25)
        prob += trigger_points
        if triggers >= 3:
            signals.append(f"Fired {triggers} triggers (high intent)")
        elif triggers >= 1:
            signals.append(f"Fired {triggers} trigger(s)")

        # Has email (10 points)
        if sub.get("email"):
            prob += 10
            signals.append("Has email on file")

        # Conversation count (0-15 points)
        convos = sub.get("conversation_count", 0) or 0
        prob += min(convos * 5, 15)
        if convos >= 2:
            signals.append(f"{convos} conversations (engaged)")

        # Recency of last trigger (0-15 points)
        latest = sub.get("latest_trigger") or sub.get("last_interaction")
        if latest:
            try:
                if isinstance(latest, str):
                    latest_dt = datetime.fromisoformat(latest.replace("Z", "+00:00"))
                else:
                    latest_dt = latest
                if latest_dt.tzinfo is None:
                    latest_dt = latest_dt.replace(tzinfo=timezone.utc)
                days_ago = (now - latest_dt).days
                if days_ago <= 3:
                    prob += 15
                    signals.append("Active in last 3 days")
                elif days_ago <= 7:
                    prob += 10
                    signals.append("Active this week")
                elif days_ago <= 14:
                    prob += 5
                elif days_ago > 30:
                    prob -= 10
                    signals.append("Inactive 30+ days")
            except Exception:
                pass

        # Funnel stage bonus
        stage = sub.get("funnel_stage", "subscriber")
        if stage == "booked":
            prob += 10
            signals.append("Funnel: booked")
        elif stage == "conversation":
            prob += 5
            signals.append("Funnel: in conversation")

        # Cap at 95
        prob = max(5, min(int(prob), 95))

        # Recommended action
        if prob >= 70:
            action = "Send personal DM this week. This person is ready."
        elif prob >= 50:
            action = "Nurture with a targeted flow. They need one more touch."
        elif prob >= 30:
            action = "Keep engaging. Reply to their comments, show up in their stories."
        else:
            action = "Low priority. Let content do the work."

        predictions.append({
            "mc_id": sub.get("mc_id"),
            "name": sub.get("full_name", "Unknown"),
            "ig_username": sub.get("ig_username", ""),
            "email": bool(sub.get("email")),
            "heat_score": heat,
            "trigger_count": triggers,
            "funnel_stage": stage,
            "conversion_probability": prob,
            "signals": signals,
            "recommended_action": action,
            "last_active": sub.get("last_interaction"),
        })

    predictions.sort(key=lambda x: x["conversion_probability"], reverse=True)

    # Summary stats
    high_prob = len([p for p in predictions if p["conversion_probability"] >= 70])
    mid_prob = len([p for p in predictions if 40 <= p["conversion_probability"] < 70])
    avg_prob = round(sum(p["conversion_probability"] for p in predictions) / len(predictions), 1) if predictions else 0

    return JSONResponse({
        "predictions": predictions,
        "summary": {
            "total_scored": len(predictions),
            "high_probability": high_prob,
            "medium_probability": mid_prob,
            "average_probability": avg_prob,
        }
    })
