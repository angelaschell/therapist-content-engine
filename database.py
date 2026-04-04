"""
Database layer - uses Supabase (Postgres)
All therapist data is isolated by therapist_id (multi-tenant)
"""

import os
import uuid
import secrets
from supabase import create_client, Client
from typing import Optional

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")

def get_db() -> Client:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise Exception("SUPABASE_URL and SUPABASE_SERVICE_KEY must be configured")
    return create_client(SUPABASE_URL, SUPABASE_KEY)


# ─── Therapist Account ────────────────────────────────────────────────────────

async def create_therapist(data: dict) -> dict:
    db = get_db()
    therapist_id = str(uuid.uuid4())
    api_key = secrets.token_urlsafe(32)

    record = {
        "id": therapist_id,
        "api_key": api_key,
        "email": data["email"],
        "profile": {
            "name": data["name"],
            "practice_name": data["practice_name"],
            "specialties": data["specialties"],
            "target_audience": data["target_audience"],
            "voice_description": data["voice_description"],
            "never_use_words": data["never_use_words"],
            "offers": data["offers"],
            "instagram_handle": data["instagram_handle"],
            "brand_colors": data["brand_colors"],
        }
    }

    result = db.table("therapists").insert(record).execute()
    return result.data[0]


async def get_therapist_by_api_key(api_key: str) -> Optional[dict]:
    db = get_db()
    result = db.table("therapists").select("*").eq("api_key", api_key).execute()
    if result.data:
        return result.data[0]
    return None


async def get_therapist_profile(therapist_id: str) -> Optional[dict]:
    db = get_db()
    result = db.table("therapists").select("profile").eq("id", therapist_id).execute()
    if result.data:
        return result.data[0]["profile"]
    return None


async def update_therapist_profile(therapist_id: str, profile_data: dict) -> dict:
    db = get_db()
    result = db.table("therapists").update({"profile": profile_data}).eq("id", therapist_id).execute()
    return result.data[0]


# ─── Generated Content History ────────────────────────────────────────────────

async def save_generated_content(therapist_id: str, content: dict):
    db = get_db()
    record = {
        "id": str(uuid.uuid4()),
        "therapist_id": therapist_id,
        **content
    }
    db.table("generated_content").insert(record).execute()


async def get_content_history(therapist_id: str, limit: int = 50) -> list:
    db = get_db()
    result = (
        db.table("generated_content")
        .select("*")
        .eq("therapist_id", therapist_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data


# ─── Viral Content ────────────────────────────────────────────────────────────

async def save_viral_content(items: list):
    db = get_db()
    for item in items:
        # Upsert so we don't duplicate if scraper runs twice
        db.table("viral_content").upsert(item, on_conflict="source_url").execute()


async def get_viral_content(category: str = "grief", limit: int = 10) -> list:
    db = get_db()
    result = (
        db.table("viral_content")
        .select("*")
        .eq("category", category)
        .order("engagement_score", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data
