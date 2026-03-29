"""
Instagram Publisher & Scheduler with Supabase Storage
─────────────────────────────────────────────────────
Flow: Carousel Builder → Upload slides to Supabase → Publish to Instagram

Env vars needed:
    INSTAGRAM_ACCESS_TOKEN
    SUPABASE_URL
    SUPABASE_SERVICE_KEY
"""

import os
import json
import httpx
import asyncio
import logging
import base64
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Request
from typing import Optional

logger = logging.getLogger("publisher")

router = APIRouter(prefix="/api/publish", tags=["publisher"])

GRAPH_API_BASE = "https://graph.facebook.com/v19.0"
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
STORAGE_BUCKET = "carousel-slides"
SCHEDULE_FILE = "/tmp/ig_scheduled_posts.json"


# ─────────────────────────────────────────────
# SUPABASE STORAGE
# ─────────────────────────────────────────────

async def ensure_bucket():
    """Create the storage bucket if it doesn't exist."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        # Check if bucket exists
        r = await client.get(
            f"{SUPABASE_URL}/storage/v1/bucket/{STORAGE_BUCKET}",
            headers={"Authorization": f"Bearer {SUPABASE_KEY}", "apikey": SUPABASE_KEY}
        )
        if r.status_code == 200:
            return True
        
        # Create bucket (public so Instagram can fetch the images)
        r = await client.post(
            f"{SUPABASE_URL}/storage/v1/bucket",
            headers={
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "apikey": SUPABASE_KEY,
                "Content-Type": "application/json"
            },
            json={"id": STORAGE_BUCKET, "name": STORAGE_BUCKET, "public": True}
        )
        if r.status_code in (200, 201):
            logger.info(f"Created storage bucket: {STORAGE_BUCKET}")
            return True
        else:
            logger.error(f"Could not create bucket: {r.text}")
            return False


async def upload_to_supabase(image_data_base64: str, filename: str) -> str:
    """
    Upload a base64 image to Supabase Storage.
    Returns the public URL.
    """
    await ensure_bucket()
    
    # Decode base64 (strip data URL prefix if present)
    if "," in image_data_base64:
        image_data_base64 = image_data_base64.split(",")[1]
    
    image_bytes = base64.b64decode(image_data_base64)
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            f"{SUPABASE_URL}/storage/v1/object/{STORAGE_BUCKET}/{filename}",
            headers={
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "apikey": SUPABASE_KEY,
                "Content-Type": "image/png",
                "x-upsert": "true",
            },
            content=image_bytes
        )
        
        if r.status_code not in (200, 201):
            raise HTTPException(status_code=500, detail=f"Supabase upload failed: {r.text}")
    
    # Return public URL
    public_url = f"{SUPABASE_URL}/storage/v1/object/public/{STORAGE_BUCKET}/{filename}"
    return public_url


@router.post("/upload-slides")
async def upload_slides(req: Request):
    """
    Upload carousel slide images to Supabase.
    Body: { "slides": ["data:image/png;base64,...", "data:image/png;base64,..."] }
    Returns: { "urls": ["https://supabase.../slide-1.png", ...] }
    """
    data = await req.json()
    slides = data.get("slides", [])
    
    if not slides:
        raise HTTPException(status_code=400, detail="No slides provided")
    
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    
    # Generate unique folder for this carousel
    batch_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    
    urls = []
    for i, slide_data in enumerate(slides):
        filename = f"{batch_id}/slide-{i+1}.png"
        url = await upload_to_supabase(slide_data, filename)
        urls.append(url)
    
    return {"success": True, "urls": urls, "batch_id": batch_id, "count": len(urls)}


# ─────────────────────────────────────────────
# GRAPH API HELPERS
# ─────────────────────────────────────────────

async def get_page_token_and_ig_id():
    from instagram_analytics import token_mgr
    token = token_mgr.user_token
    
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(
            f"{GRAPH_API_BASE}/me/accounts",
            params={
                "access_token": token,
                "fields": "id,name,access_token,instagram_business_account{id,username}"
            }
        )
        if r.status_code != 200:
            raise HTTPException(status_code=r.status_code, detail="Could not fetch page token")
        
        data = r.json()
        for page in data.get("data", []):
            ig = page.get("instagram_business_account")
            if ig:
                return page["access_token"], ig["id"], ig.get("username", "")
        
        raise HTTPException(status_code=404, detail="No IG business account found")


async def graph_post(endpoint, params):
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(f"{GRAPH_API_BASE}/{endpoint}", data=params)
        if r.status_code != 200:
            error = r.json().get("error", {}).get("message", "Unknown error")
            raise HTTPException(status_code=r.status_code, detail=error)
        return r.json()


async def graph_get(endpoint, params):
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(f"{GRAPH_API_BASE}/{endpoint}", params=params)
        if r.status_code != 200:
            error = r.json().get("error", {}).get("message", "Unknown error")
            raise HTTPException(status_code=r.status_code, detail=error)
        return r.json()


async def wait_for_container(container_id, token, max_wait=60, interval=3):
    for _ in range(max_wait // interval):
        status = await graph_get(container_id, {
            "fields": "status_code,status",
            "access_token": token,
        })
        code = status.get("status_code", "")
        if code == "FINISHED":
            return True
        if code == "ERROR":
            raise HTTPException(status_code=500, detail=f"Container error: {status.get('status', 'unknown')}")
        await asyncio.sleep(interval)
    raise HTTPException(status_code=504, detail="Container processing timed out")


# ─────────────────────────────────────────────
# PUBLISH PHOTO
# ─────────────────────────────────────────────

@router.post("/photo")
async def publish_photo(req: Request):
    """
    Publish a single photo.
    Body: { "image_url": "https://...", "caption": "..." }
    """
    data = await req.json()
    image_url = data.get("image_url", "")
    caption = data.get("caption", "")
    
    if not image_url:
        raise HTTPException(status_code=400, detail="image_url is required")
    
    page_token, ig_id, username = await get_page_token_and_ig_id()
    
    container = await graph_post(f"{ig_id}/media", {
        "image_url": image_url,
        "caption": caption,
        "access_token": page_token,
    })
    await wait_for_container(container["id"], page_token)
    
    result = await graph_post(f"{ig_id}/media_publish", {
        "creation_id": container["id"],
        "access_token": page_token,
    })
    
    return {"success": True, "media_id": result.get("id"), "username": username}


# ─────────────────────────────────────────────
# PUBLISH CAROUSEL (the main one)
# ─────────────────────────────────────────────

@router.post("/carousel")
async def publish_carousel(req: Request):
    """
    Publish a carousel to Instagram.
    Body: {
        "images": ["https://supabase-url/slide-1.png", ...],
        "caption": "Your caption"
    }
    """
    data = await req.json()
    images = data.get("images", [])
    caption = data.get("caption", "")
    
    if len(images) < 2:
        raise HTTPException(status_code=400, detail="Carousel needs at least 2 images")
    if len(images) > 10:
        raise HTTPException(status_code=400, detail="Carousel max is 10 images")
    
    page_token, ig_id, username = await get_page_token_and_ig_id()
    
    # Create child containers
    child_ids = []
    for img_url in images:
        child = await graph_post(f"{ig_id}/media", {
            "image_url": img_url,
            "is_carousel_item": "true",
            "access_token": page_token,
        })
        child_ids.append(child["id"])
    
    for cid in child_ids:
        await wait_for_container(cid, page_token)
    
    # Create carousel container
    container = await graph_post(f"{ig_id}/media", {
        "media_type": "CAROUSEL",
        "children": ",".join(child_ids),
        "caption": caption,
        "access_token": page_token,
    })
    await wait_for_container(container["id"], page_token)
    
    # Publish
    result = await graph_post(f"{ig_id}/media_publish", {
        "creation_id": container["id"],
        "access_token": page_token,
    })
    
    return {
        "success": True,
        "media_id": result.get("id"),
        "username": username,
        "slide_count": len(images),
        "message": f"Carousel ({len(images)} slides) published to @{username}"
    }


# ─────────────────────────────────────────────
# FULL FLOW: Upload slides + Publish in one call
# ─────────────────────────────────────────────

@router.post("/carousel-from-slides")
async def publish_carousel_from_slides(req: Request):
    """
    The big one. Takes base64 slide images straight from the builder,
    uploads to Supabase, publishes to Instagram.
    Body: {
        "slides": ["data:image/png;base64,...", ...],
        "caption": "Your caption"
    }
    """
    data = await req.json()
    slides = data.get("slides", [])
    caption = data.get("caption", "")
    
    if len(slides) < 2:
        raise HTTPException(status_code=400, detail="Need at least 2 slides")
    
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    
    # Step 1: Upload all slides to Supabase
    batch_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    image_urls = []
    
    for i, slide_data in enumerate(slides):
        filename = f"{batch_id}/slide-{i+1}.png"
        url = await upload_to_supabase(slide_data, filename)
        image_urls.append(url)
    
    # Step 2: Publish carousel to Instagram
    page_token, ig_id, username = await get_page_token_and_ig_id()
    
    child_ids = []
    for img_url in image_urls:
        child = await graph_post(f"{ig_id}/media", {
            "image_url": img_url,
            "is_carousel_item": "true",
            "access_token": page_token,
        })
        child_ids.append(child["id"])
    
    for cid in child_ids:
        await wait_for_container(cid, page_token)
    
    container = await graph_post(f"{ig_id}/media", {
        "media_type": "CAROUSEL",
        "children": ",".join(child_ids),
        "caption": caption,
        "access_token": page_token,
    })
    await wait_for_container(container["id"], page_token)
    
    result = await graph_post(f"{ig_id}/media_publish", {
        "creation_id": container["id"],
        "access_token": page_token,
    })
    
    return {
        "success": True,
        "media_id": result.get("id"),
        "username": username,
        "slide_count": len(slides),
        "image_urls": image_urls,
        "message": f"Carousel ({len(slides)} slides) published to @{username}"
    }


# ─────────────────────────────────────────────
# PUBLISH REEL
# ─────────────────────────────────────────────

@router.post("/reel")
async def publish_reel(req: Request):
    data = await req.json()
    video_url = data.get("video_url", "")
    caption = data.get("caption", "")
    cover_url = data.get("cover_url", "")
    
    if not video_url:
        raise HTTPException(status_code=400, detail="video_url is required")
    
    page_token, ig_id, username = await get_page_token_and_ig_id()
    
    params = {
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption,
        "share_to_feed": "true",
        "access_token": page_token,
    }
    if cover_url:
        params["cover_url"] = cover_url
    
    container = await graph_post(f"{ig_id}/media", params)
    await wait_for_container(container["id"], page_token, max_wait=120, interval=5)
    
    result = await graph_post(f"{ig_id}/media_publish", {
        "creation_id": container["id"],
        "access_token": page_token,
    })
    
    return {"success": True, "media_id": result.get("id"), "username": username}


# ─────────────────────────────────────────────
# SCHEDULER
# ─────────────────────────────────────────────

def load_schedule():
    try:
        if os.path.exists(SCHEDULE_FILE):
            with open(SCHEDULE_FILE, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return []

def save_schedule(schedule):
    try:
        with open(SCHEDULE_FILE, 'w') as f:
            json.dump(schedule, f, indent=2)
    except Exception as e:
        logger.error(f"Could not save schedule: {e}")


@router.post("/schedule")
async def schedule_post(req: Request):
    """
    Schedule a post. Same body as publish endpoints, plus publish_at.
    Body: {
        "post_type": "photo" | "carousel" | "carousel_slides",
        "publish_at": "2026-04-01T14:00:00Z",
        "caption": "...",
        "image_url": "..." (photo),
        "images": [...] (carousel with URLs),
        "slides": [...] (carousel with base64, will upload to Supabase first)
    }
    """
    data = await req.json()
    post_type = data.get("post_type", "photo")
    publish_at = data.get("publish_at", "")
    
    if not publish_at:
        raise HTTPException(status_code=400, detail="publish_at required")
    
    try:
        pub_time = datetime.fromisoformat(publish_at.replace("Z", "+00:00"))
        if pub_time <= datetime.now(timezone.utc):
            raise HTTPException(status_code=400, detail="publish_at must be in the future")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")
    
    scheduled = {
        "id": f"sched_{int(datetime.now(timezone.utc).timestamp())}",
        "post_type": post_type,
        "publish_at": publish_at,
        "caption": data.get("caption", ""),
        "status": "scheduled",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    
    # If carousel_slides, upload to Supabase now and store URLs
    if post_type == "carousel_slides":
        slides = data.get("slides", [])
        if len(slides) < 2:
            raise HTTPException(status_code=400, detail="Need at least 2 slides")
        
        batch_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
        image_urls = []
        for i, slide_data in enumerate(slides):
            filename = f"scheduled/{batch_id}/slide-{i+1}.png"
            url = await upload_to_supabase(slide_data, filename)
            image_urls.append(url)
        
        scheduled["post_type"] = "carousel"
        scheduled["images"] = image_urls
    elif post_type == "carousel":
        scheduled["images"] = data.get("images", [])
    elif post_type == "photo":
        scheduled["image_url"] = data.get("image_url", "")
    elif post_type == "reel":
        scheduled["video_url"] = data.get("video_url", "")
        scheduled["cover_url"] = data.get("cover_url", "")
    
    schedule = load_schedule()
    schedule.append(scheduled)
    save_schedule(schedule)
    
    return {"success": True, "scheduled": scheduled}


@router.get("/schedule")
async def get_schedule():
    return {"scheduled": load_schedule()}


@router.delete("/schedule/{post_id}")
async def cancel_scheduled(post_id: str):
    schedule = load_schedule()
    new_schedule = [p for p in schedule if p.get("id") != post_id]
    if len(new_schedule) == len(schedule):
        raise HTTPException(status_code=404, detail="Not found")
    save_schedule(new_schedule)
    return {"success": True}


# ─────────────────────────────────────────────
# SCHEDULER LOOP
# ─────────────────────────────────────────────

async def execute_scheduled_post(post):
    try:
        page_token, ig_id, username = await get_page_token_and_ig_id()
        post_type = post.get("post_type", "photo")
        
        if post_type == "photo":
            container = await graph_post(f"{ig_id}/media", {
                "image_url": post["image_url"],
                "caption": post.get("caption", ""),
                "access_token": page_token,
            })
            await wait_for_container(container["id"], page_token)
            result = await graph_post(f"{ig_id}/media_publish", {
                "creation_id": container["id"],
                "access_token": page_token,
            })
            
        elif post_type == "carousel":
            child_ids = []
            for img_url in post["images"]:
                child = await graph_post(f"{ig_id}/media", {
                    "image_url": img_url,
                    "is_carousel_item": "true",
                    "access_token": page_token,
                })
                child_ids.append(child["id"])
            for cid in child_ids:
                await wait_for_container(cid, page_token)
            container = await graph_post(f"{ig_id}/media", {
                "media_type": "CAROUSEL",
                "children": ",".join(child_ids),
                "caption": post.get("caption", ""),
                "access_token": page_token,
            })
            await wait_for_container(container["id"], page_token)
            result = await graph_post(f"{ig_id}/media_publish", {
                "creation_id": container["id"],
                "access_token": page_token,
            })
            
        elif post_type == "reel":
            params = {
                "media_type": "REELS",
                "video_url": post["video_url"],
                "caption": post.get("caption", ""),
                "share_to_feed": "true",
                "access_token": page_token,
            }
            if post.get("cover_url"):
                params["cover_url"] = post["cover_url"]
            container = await graph_post(f"{ig_id}/media", params)
            await wait_for_container(container["id"], page_token, max_wait=120, interval=5)
            result = await graph_post(f"{ig_id}/media_publish", {
                "creation_id": container["id"],
                "access_token": page_token,
            })
        
        return {"success": True, "media_id": result.get("id")}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def scheduler_loop():
    while True:
        try:
            schedule = load_schedule()
            now = datetime.now(timezone.utc)
            updated = False
            
            for post in schedule:
                if post.get("status") != "scheduled":
                    continue
                pub_time = datetime.fromisoformat(post["publish_at"].replace("Z", "+00:00"))
                if pub_time <= now:
                    post["status"] = "publishing"
                    save_schedule(schedule)
                    result = await execute_scheduled_post(post)
                    if result.get("success"):
                        post["status"] = "published"
                        post["media_id"] = result.get("media_id")
                        post["published_at"] = now.isoformat()
                    else:
                        post["status"] = "failed"
                        post["error"] = result.get("error", "Unknown")
                    updated = True
            
            if updated:
                save_schedule(schedule)
        except Exception as e:
            logger.error(f"Scheduler error: {e}")
        
        await asyncio.sleep(60)


_scheduler_task = None

def start_scheduler():
    global _scheduler_task
    if _scheduler_task is None:
        _scheduler_task = asyncio.create_task(scheduler_loop())
        logger.info("Scheduler started")
