"""
Instagram Publisher & Scheduler with Supabase Storage
─────────────────────────────────────────────────────
Now with single-slide upload to handle large carousels.
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

logger = logging.getLogger("publisher")

router = APIRouter(prefix="/api/publish", tags=["publisher"])

GRAPH_API_BASE = "https://graph.facebook.com/v21.0"
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
STORAGE_BUCKET = "carousel-slides"
SCHEDULE_FILE = "/tmp/ig_scheduled_posts.json"


# ─────────────────────────────────────────────
# SUPABASE STORAGE
# ─────────────────────────────────────────────

async def ensure_bucket():
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(
            f"{SUPABASE_URL}/storage/v1/bucket/{STORAGE_BUCKET}",
            headers={"Authorization": f"Bearer {SUPABASE_KEY}", "apikey": SUPABASE_KEY}
        )
        if r.status_code == 200:
            return True
        r = await client.post(
            f"{SUPABASE_URL}/storage/v1/bucket",
            headers={"Authorization": f"Bearer {SUPABASE_KEY}", "apikey": SUPABASE_KEY, "Content-Type": "application/json"},
            json={"id": STORAGE_BUCKET, "name": STORAGE_BUCKET, "public": True}
        )
        return r.status_code in (200, 201)


async def upload_to_supabase(image_data_base64: str, filename: str) -> str:
    await ensure_bucket()
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

    return f"{SUPABASE_URL}/storage/v1/object/public/{STORAGE_BUCKET}/{filename}"


# ─────────────────────────────────────────────
# SINGLE SLIDE UPLOAD (fixes large carousel issue)
# ─────────────────────────────────────────────

@router.post("/upload-slide")
async def upload_single_slide(req: Request):
    """
    Upload ONE slide at a time.
    Body: { "slide": "data:image/png;base64,...", "batch_id": "abc123", "index": 0 }
    Returns: { "url": "https://..." }
    """
    data = await req.json()
    slide_data = data.get("slide", "")
    batch_id = data.get("batch_id", "")
    index = data.get("index", 0)

    if not slide_data:
        raise HTTPException(status_code=400, detail="No slide data")
    if not batch_id:
        batch_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]

    filename = f"{batch_id}/slide-{index+1}.png"
    url = await upload_to_supabase(slide_data, filename)

    return {"success": True, "url": url, "batch_id": batch_id, "index": index}


@router.post("/upload-slides")
async def upload_slides(req: Request):
    """Bulk upload (kept for small carousels)."""
    data = await req.json()
    slides = data.get("slides", [])
    if not slides:
        raise HTTPException(status_code=400, detail="No slides")

    batch_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    urls = []
    for i, slide_data in enumerate(slides):
        filename = f"{batch_id}/slide-{i+1}.png"
        url = await upload_to_supabase(slide_data, filename)
        urls.append(url)

    return {"success": True, "urls": urls, "batch_id": batch_id}


# ─────────────────────────────────────────────
# GRAPH API HELPERS
# ─────────────────────────────────────────────

async def get_page_token_and_ig_id():
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


async def graph_post(endpoint, params, retries=3):
    last_error = None
    # Separate access_token as query param, send rest as form data body
    form_data = {k: v for k, v in params.items() if k != "access_token"}
    query = {"access_token": params["access_token"]} if "access_token" in params else {}
    for attempt in range(retries):
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(f"{GRAPH_API_BASE}/{endpoint}", params=query, data=form_data)
            if r.status_code in (200, 201):
                return r.json()
            try:
                err_body = r.json()
                error = err_body.get("error", {}).get("message", r.text)
                error_code = err_body.get("error", {}).get("code", 0)
            except Exception:
                error = r.text
                error_code = 0
            last_error = error
            # Retry on transient Instagram errors (code 2 = temporary issue)
            is_transient = error_code == 2 or "unexpected error" in error.lower()
            if is_transient and attempt < retries - 1:
                wait = (attempt + 1) * 3
                logger.warning(f"Transient IG error on {endpoint} (attempt {attempt+1}): {error}. Retrying in {wait}s...")
                await asyncio.sleep(wait)
                continue
            break
    raise HTTPException(status_code=r.status_code, detail=last_error)


async def graph_get(endpoint, params, retries=3):
    last_error = None
    for attempt in range(retries):
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(f"{GRAPH_API_BASE}/{endpoint}", params=params)
            if r.status_code in (200, 201):
                return r.json()
            try:
                err_body = r.json()
                error = err_body.get("error", {}).get("message", r.text)
                error_code = err_body.get("error", {}).get("code", 0)
            except Exception:
                error = r.text
                error_code = 0
            last_error = error
            is_transient = error_code == 2 or "unexpected error" in error.lower()
            if is_transient and attempt < retries - 1:
                wait = (attempt + 1) * 3
                logger.warning(f"Transient IG error on GET {endpoint} (attempt {attempt+1}): {error}. Retrying in {wait}s...")
                await asyncio.sleep(wait)
                continue
            break
    raise HTTPException(status_code=r.status_code, detail=last_error)


async def wait_for_container(container_id, token, max_wait=60, interval=3):
    for _ in range(max_wait // interval):
        status = await graph_get(container_id, {"fields": "status_code,status", "access_token": token})
        code = status.get("status_code", "")
        if code == "FINISHED":
            return True
        if code == "ERROR":
            raise HTTPException(status_code=500, detail=f"Container error: {status.get('status', 'unknown')}")
        await asyncio.sleep(interval)
    raise HTTPException(status_code=504, detail="Container processing timed out")


async def verify_image_url(url: str) -> tuple[bool, str]:
    """Check if an image URL is publicly accessible (as Instagram would fetch it)."""
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            r = await client.head(url)
            if r.status_code == 200:
                return True, "ok"
            # Some servers don't support HEAD, try GET with range
            r = await client.get(url, headers={"Range": "bytes=0-0"})
            if r.status_code in (200, 206):
                return True, "ok"
            return False, f"HTTP {r.status_code}"
    except Exception as e:
        return False, str(e)


@router.post("/preflight")
async def preflight_check(req: Request):
    """Check all prerequisites before publishing. Returns actionable diagnostics."""
    checks = {"token": False, "permissions": False, "images": []}
    data = await req.json()
    images = data.get("images", [])

    # Check token
    try:
        page_token, ig_id, username = await get_page_token_and_ig_id()
        checks["token"] = True
        checks["username"] = username
    except Exception as e:
        checks["token_error"] = str(e)
        return {"success": False, "checks": checks, "detail": f"Token issue: {e}"}

    # Check publish permission by verifying token info
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{GRAPH_API_BASE}/debug_token", params={
                "input_token": page_token,
                "access_token": page_token
            })
            if r.status_code == 200:
                token_data = r.json().get("data", {})
                scopes = token_data.get("scopes", [])
                checks["scopes"] = scopes
                checks["permissions"] = "instagram_content_publish" in scopes or "instagram_basic" in scopes
                if not checks["permissions"]:
                    checks["permission_error"] = f"Missing instagram_content_publish scope. Current scopes: {scopes}"
    except Exception as e:
        checks["permission_error"] = str(e)

    # Check image accessibility
    for url in images:
        ok, msg = await verify_image_url(url)
        checks["images"].append({"url": url[:80], "accessible": ok, "detail": msg})

    all_ok = checks["token"] and all(img["accessible"] for img in checks["images"])
    return {"success": all_ok, "checks": checks}


# ─────────────────────────────────────────────
# PUBLISH PHOTO
# ─────────────────────────────────────────────

@router.post("/photo")
async def publish_photo(req: Request):
    data = await req.json()
    image_url = data.get("image_url", "")
    caption = data.get("caption", "")
    if not image_url:
        raise HTTPException(status_code=400, detail="image_url required")

    page_token, ig_id, username = await get_page_token_and_ig_id()
    container = await graph_post(f"{ig_id}/media", {"image_url": image_url, "caption": caption, "access_token": page_token})
    await wait_for_container(container["id"], page_token)
    result = await graph_post(f"{ig_id}/media_publish", {"creation_id": container["id"], "access_token": page_token})
    return {"success": True, "media_id": result.get("id"), "username": username}


# ─────────────────────────────────────────────
# PUBLISH CAROUSEL FROM URLS
# ─────────────────────────────────────────────

@router.post("/carousel")
async def publish_carousel(req: Request):
    """
    Publish carousel from pre-uploaded image URLs.
    Body: { "images": ["https://url1", "https://url2", ...], "caption": "..." }
    """
    data = await req.json()
    images = data.get("images", [])
    caption = data.get("caption", "")

    if len(images) < 2:
        raise HTTPException(status_code=400, detail="Need at least 2 images")
    if len(images) > 10:
        raise HTTPException(status_code=400, detail="Max 10 images")

    # Verify images are publicly accessible before calling Instagram API
    for i, img_url in enumerate(images):
        ok, msg = await verify_image_url(img_url)
        if not ok:
            raise HTTPException(status_code=400, detail=f"Slide {i+1} image not accessible ({msg}). Instagram must be able to download the image. Check Supabase bucket is public.")

    try:
        page_token, ig_id, username = await get_page_token_and_ig_id()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Token error: {e}")

    child_ids = []
    for i, img_url in enumerate(images):
        try:
            child = await graph_post(f"{ig_id}/media", {"image_url": img_url, "is_carousel_item": "true", "access_token": page_token})
            child_ids.append(child["id"])
        except HTTPException as e:
            raise HTTPException(status_code=e.status_code, detail=f"Slide {i+1} container failed: {e.detail}")
        # Small delay between child containers to avoid rate limits
        if i < len(images) - 1:
            await asyncio.sleep(1)

    for i, cid in enumerate(child_ids):
        try:
            await wait_for_container(cid, page_token)
        except HTTPException as e:
            raise HTTPException(status_code=e.status_code, detail=f"Slide {i+1} processing failed: {e.detail}")

    try:
        container = await graph_post(f"{ig_id}/media", {"media_type": "CAROUSEL", "children": ",".join(child_ids), "caption": caption, "access_token": page_token})
        await wait_for_container(container["id"], page_token)
    except HTTPException as e:
        raise HTTPException(status_code=e.status_code, detail=f"Carousel container failed: {e.detail}")

    try:
        result = await graph_post(f"{ig_id}/media_publish", {"creation_id": container["id"], "access_token": page_token})
    except HTTPException as e:
        raise HTTPException(status_code=e.status_code, detail=f"Publish failed: {e.detail}")

    return {"success": True, "media_id": result.get("id"), "username": username, "slide_count": len(images)}


# ─────────────────────────────────────────────
# PUBLISH CAROUSEL FROM BASE64 SLIDES (small carousels)
# ─────────────────────────────────────────────

@router.post("/carousel-from-slides")
async def publish_carousel_from_slides(req: Request):
    data = await req.json()
    slides = data.get("slides", [])
    caption = data.get("caption", "")

    if len(slides) < 2:
        raise HTTPException(status_code=400, detail="Need at least 2 slides")

    batch_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    image_urls = []
    for i, slide_data in enumerate(slides):
        filename = f"{batch_id}/slide-{i+1}.png"
        url = await upload_to_supabase(slide_data, filename)
        image_urls.append(url)

    try:
        page_token, ig_id, username = await get_page_token_and_ig_id()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Token error: {e}")

    child_ids = []
    for i, img_url in enumerate(image_urls):
        try:
            child = await graph_post(f"{ig_id}/media", {"image_url": img_url, "is_carousel_item": "true", "access_token": page_token})
            child_ids.append(child["id"])
        except HTTPException as e:
            raise HTTPException(status_code=e.status_code, detail=f"Slide {i+1} container failed: {e.detail}")
        if i < len(image_urls) - 1:
            await asyncio.sleep(1)

    for i, cid in enumerate(child_ids):
        try:
            await wait_for_container(cid, page_token)
        except HTTPException as e:
            raise HTTPException(status_code=e.status_code, detail=f"Slide {i+1} processing failed: {e.detail}")

    try:
        container = await graph_post(f"{ig_id}/media", {"media_type": "CAROUSEL", "children": ",".join(child_ids), "caption": caption, "access_token": page_token})
        await wait_for_container(container["id"], page_token)
    except HTTPException as e:
        raise HTTPException(status_code=e.status_code, detail=f"Carousel container failed: {e.detail}")

    try:
        result = await graph_post(f"{ig_id}/media_publish", {"creation_id": container["id"], "access_token": page_token})
    except HTTPException as e:
        raise HTTPException(status_code=e.status_code, detail=f"Publish failed: {e.detail}")

    return {"success": True, "media_id": result.get("id"), "username": username, "slide_count": len(slides), "image_urls": image_urls}


# ─────────────────────────────────────────────
# PUBLISH REEL
# ─────────────────────────────────────────────

@router.post("/reel")
async def publish_reel(req: Request):
    data = await req.json()
    video_url = data.get("video_url", "")
    caption = data.get("caption", "")
    if not video_url:
        raise HTTPException(status_code=400, detail="video_url required")

    page_token, ig_id, username = await get_page_token_and_ig_id()
    params = {"media_type": "REELS", "video_url": video_url, "caption": caption, "share_to_feed": "true", "access_token": page_token}
    if data.get("cover_url"):
        params["cover_url"] = data["cover_url"]

    container = await graph_post(f"{ig_id}/media", params)
    await wait_for_container(container["id"], page_token, max_wait=120, interval=5)
    result = await graph_post(f"{ig_id}/media_publish", {"creation_id": container["id"], "access_token": page_token})
    return {"success": True, "media_id": result.get("id"), "username": username}


# ─────────────────────────────────────────────
# SCHEDULER
# ─────────────────────────────────────────────

def load_schedule():
    try:
        if os.path.exists(SCHEDULE_FILE):
            with open(SCHEDULE_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        print(f"[instagram_publisher] Error loading schedule: {e}")
    return []

def save_schedule(schedule):
    try:
        with open(SCHEDULE_FILE, 'w') as f:
            json.dump(schedule, f, indent=2)
    except Exception as e:
        logger.error(f"Could not save schedule: {e}")


@router.post("/schedule")
async def schedule_post(req: Request):
    data = await req.json()
    publish_at = data.get("publish_at", "")
    if not publish_at:
        raise HTTPException(status_code=400, detail="publish_at required")

    try:
        pub_time = datetime.fromisoformat(publish_at.replace("Z", "+00:00"))
        if pub_time <= datetime.now(timezone.utc):
            raise HTTPException(status_code=400, detail="publish_at must be in the future")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")

    post_type = data.get("post_type", "photo")
    scheduled = {
        "id": f"sched_{int(datetime.now(timezone.utc).timestamp())}",
        "post_type": post_type,
        "publish_at": publish_at,
        "caption": data.get("caption", ""),
        "status": "scheduled",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    # If carousel with URLs already uploaded
    if post_type == "carousel":
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


async def execute_scheduled_post(post):
    try:
        page_token, ig_id, username = await get_page_token_and_ig_id()
        post_type = post.get("post_type", "photo")

        if post_type == "photo":
            container = await graph_post(f"{ig_id}/media", {"image_url": post["image_url"], "caption": post.get("caption", ""), "access_token": page_token})
            await wait_for_container(container["id"], page_token)
            result = await graph_post(f"{ig_id}/media_publish", {"creation_id": container["id"], "access_token": page_token})

        elif post_type == "carousel":
            child_ids = []
            for img_url in post["images"]:
                child = await graph_post(f"{ig_id}/media", {"image_url": img_url, "is_carousel_item": "true", "access_token": page_token})
                child_ids.append(child["id"])
            for cid in child_ids:
                await wait_for_container(cid, page_token)
            container = await graph_post(f"{ig_id}/media", {"media_type": "CAROUSEL", "children": ",".join(child_ids), "caption": post.get("caption", ""), "access_token": page_token})
            await wait_for_container(container["id"], page_token)
            result = await graph_post(f"{ig_id}/media_publish", {"creation_id": container["id"], "access_token": page_token})

        elif post_type == "reel":
            params = {"media_type": "REELS", "video_url": post["video_url"], "caption": post.get("caption", ""), "share_to_feed": "true", "access_token": page_token}
            if post.get("cover_url"):
                params["cover_url"] = post["cover_url"]
            container = await graph_post(f"{ig_id}/media", params)
            await wait_for_container(container["id"], page_token, max_wait=120, interval=5)
            result = await graph_post(f"{ig_id}/media_publish", {"creation_id": container["id"], "access_token": page_token})

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
