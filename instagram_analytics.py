"""
Instagram Analytics API Routes - FIXED WITH PAGE TOKEN
──────────────────────────────────────────────────────────
The User token can discover the IG account but can't pull media.
This version auto-fetches the Page token and uses it for all IG calls.
"""

import os
import json
import httpx
import asyncio
import logging
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, Query
from typing import Optional

logger = logging.getLogger("analytics")

router = APIRouter(prefix="/api/analytics", tags=["analytics"])

GRAPH_API_BASE = "https://graph.facebook.com/v19.0"


class TokenManager:
    def __init__(self):
        self.user_token = os.getenv("INSTAGRAM_ACCESS_TOKEN", "")
        self.app_id = os.getenv("FB_APP_ID", "")
        self.app_secret = os.getenv("FB_APP_SECRET", "")
        self.page_token = None  # Auto-fetched from user token
        self.page_id = None
        self.ig_account_id = None
        self.token_file = "/tmp/ig_token.json"
        self.last_refresh = None
        self.expires_at = None
        self.days_remaining = None
        self._load_from_file()

    def _load_from_file(self):
        try:
            if os.path.exists(self.token_file):
                with open(self.token_file, 'r') as f:
                    data = json.load(f)
                    file_token = data.get("token", "")
                    if file_token:
                        self.user_token = file_token
                        self.last_refresh = data.get("refreshed_at", "")
        except Exception as e:
            logger.warning(f"Could not load token file: {e}")

    def _save_to_file(self):
        try:
            with open(self.token_file, 'w') as f:
                json.dump({
                    "token": self.user_token,
                    "refreshed_at": datetime.utcnow().isoformat(),
                    "expires_at": self.expires_at.isoformat() if self.expires_at else None,
                }, f)
        except Exception as e:
            logger.warning(f"Could not save token file: {e}")

    async def get_page_token(self):
        """Fetch the Page token from the User token. This is the key fix."""
        if self.page_token:
            return self.page_token

        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                f"{GRAPH_API_BASE}/me/accounts",
                params={
                    "access_token": self.user_token,
                    "fields": "id,name,access_token,instagram_business_account{id,username}"
                }
            )
            if r.status_code != 200:
                raise HTTPException(status_code=r.status_code, detail="Could not fetch page token")

            data = r.json()
            for page in data.get("data", []):
                ig = page.get("instagram_business_account")
                if ig:
                    self.page_token = page["access_token"]  # This is the Page token
                    self.page_id = page["id"]
                    self.ig_account_id = ig["id"]
                    logger.info(f"Got page token for {page.get('name')} / @{ig.get('username')}")
                    return self.page_token

            raise HTTPException(status_code=404, detail="No IG business account found on any page")

    async def check_token(self) -> dict:
        if not self.user_token:
            return {"valid": False, "error": "No token configured"}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(
                    f"{GRAPH_API_BASE}/debug_token",
                    params={
                        "input_token": self.user_token,
                        "access_token": f"{self.app_id}|{self.app_secret}" if self.app_id and self.app_secret else self.user_token,
                    }
                )
                data = r.json()
                token_data = data.get("data", {})
                is_valid = token_data.get("is_valid", False)
                expires_at = token_data.get("expires_at", 0)
                if expires_at and expires_at > 0:
                    self.expires_at = datetime.utcfromtimestamp(expires_at)
                    self.days_remaining = (self.expires_at - datetime.utcnow()).days
                else:
                    self.expires_at = None
                    self.days_remaining = 999
                return {
                    "valid": is_valid,
                    "expires_at": self.expires_at.isoformat() if self.expires_at else "never",
                    "days_remaining": self.days_remaining,
                    "scopes": token_data.get("scopes", []),
                    "type": token_data.get("type", "unknown"),
                    "app_id": token_data.get("app_id", ""),
                }
        except Exception as e:
            return {"valid": False, "error": str(e)}

    async def refresh_token(self) -> dict:
        if not self.app_id or not self.app_secret:
            return {"success": False, "error": "FB_APP_ID and FB_APP_SECRET required"}
        if not self.user_token:
            return {"success": False, "error": "No current token to refresh"}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(
                    f"{GRAPH_API_BASE}/oauth/access_token",
                    params={
                        "grant_type": "fb_exchange_token",
                        "client_id": self.app_id,
                        "client_secret": self.app_secret,
                        "fb_exchange_token": self.user_token,
                    }
                )
                if r.status_code != 200:
                    error = r.json().get("error", {}).get("message", "Unknown error")
                    return {"success": False, "error": error}
                data = r.json()
                new_token = data.get("access_token", "")
                if not new_token:
                    return {"success": False, "error": "No token in response"}
                self.user_token = new_token
                self.page_token = None  # Reset so it re-fetches with new user token
                self.last_refresh = datetime.utcnow().isoformat()
                self._save_to_file()
                status = await self.check_token()
                return {
                    "success": True,
                    "refreshed_at": self.last_refresh,
                    "days_remaining": status.get("days_remaining", "unknown"),
                    "expires_at": status.get("expires_at", "unknown"),
                    "new_token": new_token,
                }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def auto_refresh_if_needed(self):
        status = await self.check_token()
        if not status.get("valid"):
            return await self.refresh_token()
        days = status.get("days_remaining", 999)
        if days is not None and days < 7:
            return await self.refresh_token()
        return {"action": "none", "days_remaining": days}


token_mgr = TokenManager()

_refresh_task = None

async def token_refresh_loop():
    while True:
        try:
            result = await token_mgr.auto_refresh_if_needed()
            logger.info(f"Token check: {result}")
        except Exception as e:
            logger.error(f"Token refresh error: {e}")
        await asyncio.sleep(12 * 60 * 60)

def start_refresh_loop():
    global _refresh_task
    if _refresh_task is None:
        _refresh_task = asyncio.create_task(token_refresh_loop())


# ─────────────────────────────────────────────
# API HELPER - NOW USES PAGE TOKEN
# ─────────────────────────────────────────────

async def graph_request(endpoint: str, params: dict = None, use_page_token: bool = True) -> dict:
    """Make Graph API request. Uses Page token by default for IG calls."""
    if use_page_token:
        token = await token_mgr.get_page_token()
    else:
        token = token_mgr.user_token

    if not token:
        raise HTTPException(status_code=500, detail="No access token available")

    default_params = {"access_token": token}
    if params:
        default_params.update(params)

    async with httpx.AsyncClient(timeout=30.0) as client:
        url = f"{GRAPH_API_BASE}/{endpoint}"
        response = await client.get(url, params=default_params)

        if response.status_code != 200:
            error_data = response.json()
            error_msg = error_data.get("error", {}).get("message", "Graph API error")

            # If token expired, try refresh
            if "expired" in error_msg.lower() or "invalid" in error_msg.lower():
                result = await token_mgr.refresh_token()
                if result.get("success"):
                    token_mgr.page_token = None  # Reset page token
                    new_token = await token_mgr.get_page_token() if use_page_token else token_mgr.user_token
                    default_params["access_token"] = new_token
                    response = await client.get(url, params=default_params)
                    if response.status_code == 200:
                        return response.json()

            raise HTTPException(status_code=response.status_code, detail=error_msg)
        return response.json()


# ─────────────────────────────────────────────
# TOKEN ENDPOINTS
# ─────────────────────────────────────────────

@router.get("/token/status")
async def get_token_status():
    status = await token_mgr.check_token()
    status["last_refresh"] = token_mgr.last_refresh
    return status

@router.post("/token/refresh")
async def manual_refresh():
    return await token_mgr.refresh_token()


# ─────────────────────────────────────────────
# DISCOVER
# ─────────────────────────────────────────────

@router.get("/discover")
async def discover_ig_account():
    pages = await graph_request("me/accounts", {
        "fields": "id,name,instagram_business_account{id,username,name,profile_picture_url,followers_count,follows_count,media_count}"
    }, use_page_token=False)  # Use user token for discovery
    results = []
    for page in pages.get("data", []):
        ig = page.get("instagram_business_account")
        if ig:
            results.append({
                "page_id": page["id"],
                "page_name": page.get("name", ""),
                "ig_account_id": ig["id"],
                "ig_username": ig.get("username", ""),
                "ig_followers": ig.get("followers_count", 0),
                "ig_following": ig.get("follows_count", 0),
                "ig_media_count": ig.get("media_count", 0),
            })
    return {"accounts": results}


# ─────────────────────────────────────────────
# ACCOUNT INFO
# ─────────────────────────────────────────────

@router.get("/account/{ig_account_id}")
async def get_account_info(ig_account_id: str):
    return await graph_request(ig_account_id, {
        "fields": "id,username,name,biography,followers_count,follows_count,media_count,profile_picture_url,website"
    })


# ─────────────────────────────────────────────
# MEDIA
# ─────────────────────────────────────────────

@router.get("/media/{ig_account_id}")
async def get_media_list(ig_account_id: str, limit: int = Query(50, ge=1, le=100), after: Optional[str] = None):
    fields = "id,caption,media_type,media_url,thumbnail_url,permalink,timestamp,like_count,comments_count"
    params = {"fields": fields, "limit": str(limit)}
    if after:
        params["after"] = after
    return await graph_request(f"{ig_account_id}/media", params)


# ─────────────────────────────────────────────
# SUMMARY
# ─────────────────────────────────────────────

@router.get("/summary/{ig_account_id}")
async def get_performance_summary(ig_account_id: str, days: int = Query(30, ge=1, le=365)):
    account = await graph_request(ig_account_id, {"fields": "followers_count,media_count,username"})
    followers = account.get("followers_count", 1)

    fields = "id,caption,media_type,media_url,thumbnail_url,permalink,timestamp,like_count,comments_count"
    all_posts = []
    next_cursor = None
    cutoff = datetime.utcnow() - timedelta(days=days)

    for _ in range(10):
        params = {"fields": fields, "limit": "100"}
        if next_cursor:
            params["after"] = next_cursor
        data = await graph_request(f"{ig_account_id}/media", params)
        posts = data.get("data", [])
        if not posts:
            break
        hit_cutoff = False
        for post in posts:
            pt = datetime.fromisoformat(post["timestamp"].replace("+0000", "+00:00").replace("Z", "+00:00"))
            if pt.replace(tzinfo=None) < cutoff:
                hit_cutoff = True
                break
            all_posts.append(post)
        if hit_cutoff:
            break
        paging = data.get("paging", {}).get("cursors", {})
        next_cursor = paging.get("after")
        if not next_cursor:
            break

    if not all_posts:
        return {"period_days": days, "total_posts": 0, "followers": followers}

    n = len(all_posts)
    total_likes = sum(p.get("like_count", 0) for p in all_posts)
    total_comments = sum(p.get("comments_count", 0) for p in all_posts)
    total_engagement = total_likes + total_comments

    type_breakdown = {}
    for post in all_posts:
        mt = post.get("media_type", "UNKNOWN")
        if mt not in type_breakdown:
            type_breakdown[mt] = {"count": 0, "likes": 0, "comments": 0}
        type_breakdown[mt]["count"] += 1
        type_breakdown[mt]["likes"] += post.get("like_count", 0)
        type_breakdown[mt]["comments"] += post.get("comments_count", 0)

    for mt, s in type_breakdown.items():
        if s["count"] > 0:
            avg = (s["likes"] + s["comments"]) / s["count"]
            s["avg_engagement_per_post"] = round(avg, 1)
            s["avg_engagement_rate"] = round((avg / followers) * 100, 3)

    day_dist = {i: {"count": 0, "eng": 0} for i in range(7)}
    hour_dist = {i: {"count": 0, "eng": 0} for i in range(24)}
    for post in all_posts:
        pt = datetime.fromisoformat(post["timestamp"].replace("+0000", "+00:00").replace("Z", "+00:00"))
        d = pt.weekday()
        h = pt.hour
        eng = post.get("like_count", 0) + post.get("comments_count", 0)
        day_dist[d]["count"] += 1
        day_dist[d]["eng"] += eng
        hour_dist[h]["count"] += 1
        hour_dist[h]["eng"] += eng

    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    best_days = [{"day": day_names[i], "posts": s["count"], "avg_engagement": round(s["eng"]/s["count"], 1)} for i, s in day_dist.items() if s["count"] > 0]
    best_days.sort(key=lambda x: x["avg_engagement"], reverse=True)

    best_hours = [{"hour": h, "posts": s["count"], "avg_engagement": round(s["eng"]/s["count"], 1)} for h, s in hour_dist.items() if s["count"] > 0]
    best_hours.sort(key=lambda x: x["avg_engagement"], reverse=True)

    top_post = max(all_posts, key=lambda p: p.get("like_count", 0) + p.get("comments_count", 0))

    return {
        "period_days": days,
        "username": account.get("username", ""),
        "followers": followers,
        "total_posts": n,
        "total_likes": total_likes,
        "total_comments": total_comments,
        "total_engagement": total_engagement,
        "avg_likes_per_post": round(total_likes / n, 1),
        "avg_comments_per_post": round(total_comments / n, 1),
        "avg_engagement_per_post": round(total_engagement / n, 1),
        "avg_engagement_rate": round(((total_engagement / n) / followers) * 100, 3),
        "type_breakdown": type_breakdown,
        "best_days": best_days,
        "best_hours": best_hours[:5],
        "top_post": top_post,
        "posts": all_posts,
    }


# ─────────────────────────────────────────────
# COMMENTS
# ─────────────────────────────────────────────

@router.get("/comments/{media_id}")
async def get_media_comments(media_id: str, limit: int = Query(50, ge=1, le=100)):
    fields = "id,text,timestamp,username,like_count,replies{id,text,timestamp,username}"
    return await graph_request(f"{media_id}/comments", {"fields": fields, "limit": str(limit)})


# ─────────────────────────────────────────────
# ADS
# ─────────────────────────────────────────────

@router.get("/ads/campaigns")
async def get_ad_campaigns(date_preset: str = Query("last_30d")):
    me_data = await graph_request("me", {"fields": "adaccounts{account_id,name,account_status}"}, use_page_token=False)
    ad_accounts = me_data.get("adaccounts", {}).get("data", [])
    if not ad_accounts:
        return {"data": [], "message": "No ad accounts found"}
    ad_account_id = ad_accounts[0]["account_id"]
    fields = "campaign_name,objective,status,impressions,reach,clicks,cpc,cpm,ctr,spend,actions"
    return await graph_request(f"act_{ad_account_id}/insights", {"fields": fields, "date_preset": date_preset, "level": "campaign"}, use_page_token=False)


# ─────────────────────────────────────────────
# HEALTH
# ─────────────────────────────────────────────

@router.get("/health")
async def analytics_health():
    results = {"token": await token_mgr.check_token(), "ig_accounts": []}
    try:
        pages = await graph_request("me/accounts", {
            "fields": "id,name,instagram_business_account{id,username}"
        }, use_page_token=False)
        for page in pages.get("data", []):
            ig = page.get("instagram_business_account")
            if ig:
                results["ig_accounts"].append({"page": page.get("name", ""), "ig_id": ig["id"], "ig_username": ig.get("username", "")})
    except Exception as e:
        results["error"] = str(e)
    return results
