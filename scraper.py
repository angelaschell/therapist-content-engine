"""
Viral Content Scraper
Runs daily via Render cron job
Sources: Reddit, Apify (TikTok/Instagram), YouTube transcripts
"""

import os
import praw
import uuid
import httpx
from datetime import datetime
from database import save_viral_content

APIFY_TOKEN = os.environ.get("APIFY_TOKEN")
REDDIT_CLIENT_ID = os.environ.get("REDDIT_CLIENT_ID")
REDDIT_CLIENT_SECRET = os.environ.get("REDDIT_CLIENT_SECRET")


# ─── Category Config ──────────────────────────────────────────────────────────

CATEGORY_CONFIG = {
    "grief": {
        "reddit_subs": ["grief", "motherlessdaughters", "CPTSD", "GriefSupport"],
        "tiktok_hashtags": ["#grief", "#griefhealing", "#grieving", "#grieftok"],
        "instagram_hashtags": ["grief", "griefhealing", "motherlessdaughter"],
        "youtube_queries": ["grief therapy explained", "processing grief"],
    },
    "anxiety": {
        "reddit_subs": ["Anxiety", "anxietyadvice", "mentalhealth"],
        "tiktok_hashtags": ["#anxiety", "#anxietyrelief", "#anxietytok"],
        "instagram_hashtags": ["anxiety", "anxietyhealing", "anxietysupport"],
        "youtube_queries": ["anxiety therapy techniques", "managing anxiety"],
    },
    "trauma": {
        "reddit_subs": ["CPTSD", "traumarecovery", "raisedbynarcissists"],
        "tiktok_hashtags": ["#trauma", "#traumahealing", "#cptsd", "#traumatok"],
        "instagram_hashtags": ["traumahealing", "traumarecovery", "emdr"],
        "youtube_queries": ["trauma therapy explained", "healing from trauma"],
    },
    "attachment": {
        "reddit_subs": ["attachment", "anxiousattachment", "relationships"],
        "tiktok_hashtags": ["#attachmentstyles", "#anxiousattachment", "#healingattachment"],
        "instagram_hashtags": ["attachmentstyles", "attachmenthealing"],
        "youtube_queries": ["attachment theory therapy", "healing attachment wounds"],
    },
}


# ─── Reddit Scraper ───────────────────────────────────────────────────────────

def scrape_reddit(category: str) -> list:
    if not REDDIT_CLIENT_ID:
        print("Reddit credentials not set, skipping")
        return []

    reddit = praw.Reddit(
        client_id=REDDIT_CLIENT_ID,
        client_secret=REDDIT_CLIENT_SECRET,
        user_agent="TherapistContentEngine/1.0"
    )

    config = CATEGORY_CONFIG.get(category, CATEGORY_CONFIG["grief"])
    items = []

    for subreddit_name in config["reddit_subs"]:
        try:
            subreddit = reddit.subreddit(subreddit_name)
            for post in subreddit.hot(limit=10):
                if post.score < 50:
                    continue
                items.append({
                    "id": str(uuid.uuid4()),
                    "platform": "reddit",
                    "category": category,
                    "hook": post.title,
                    "format": "text_post",
                    "source_url": f"https://reddit.com{post.permalink}",
                    "engagement_score": post.score,
                    "engagement_summary": f"{post.score} upvotes, {post.num_comments} comments",
                    "subreddit": subreddit_name,
                    "scraped_at": datetime.utcnow().isoformat(),
                })
        except Exception as e:
            print(f"Reddit error for {subreddit_name}: {e}")

    return items


# ─── Apify Scraper (TikTok + Instagram) ──────────────────────────────────────

async def scrape_tiktok(category: str) -> list:
    if not APIFY_TOKEN:
        print("Apify token not set, skipping TikTok")
        return []

    config = CATEGORY_CONFIG.get(category, CATEGORY_CONFIG["grief"])
    hashtags = config["tiktok_hashtags"][:2]  # Limit to save Apify credits
    items = []

    async with httpx.AsyncClient(timeout=60) as client:
        # Start Apify actor run
        run_response = await client.post(
            f"https://api.apify.com/v2/acts/clockworks~tiktok-scraper/runs",
            headers={"Authorization": f"Bearer {APIFY_TOKEN}"},
            json={
                "hashtags": hashtags,
                "resultsPerPage": 20,
                "shouldDownloadVideos": False,
                "shouldDownloadCovers": False,
            }
        )

        if run_response.status_code != 201:
            print(f"Apify TikTok error: {run_response.text}")
            return []

        run_id = run_response.json()["data"]["id"]

        # Wait for completion (poll every 5 seconds, max 60 seconds)
        import asyncio
        for _ in range(12):
            await asyncio.sleep(5)
            status_response = await client.get(
                f"https://api.apify.com/v2/acts/clockworks~tiktok-scraper/runs/{run_id}",
                headers={"Authorization": f"Bearer {APIFY_TOKEN}"}
            )
            status = status_response.json()["data"]["status"]
            if status == "SUCCEEDED":
                break
            elif status in ["FAILED", "ABORTED"]:
                return []

        # Get results
        results_response = await client.get(
            f"https://api.apify.com/v2/acts/clockworks~tiktok-scraper/runs/{run_id}/dataset/items",
            headers={"Authorization": f"Bearer {APIFY_TOKEN}"}
        )

        for video in results_response.json():
            plays = video.get("playCount", 0)
            if plays < 10000:
                continue

            items.append({
                "id": str(uuid.uuid4()),
                "platform": "tiktok",
                "category": category,
                "hook": video.get("text", "")[:200],
                "format": "short_video",
                "source_url": video.get("webVideoUrl", ""),
                "engagement_score": plays,
                "engagement_summary": f"{plays:,} plays, {video.get('diggCount', 0):,} likes",
                "scraped_at": datetime.utcnow().isoformat(),
            })

    return items


# ─── Main Scraper Runner ──────────────────────────────────────────────────────

async def run_scraper(categories: list = None) -> dict:
    if not categories:
        categories = ["grief", "trauma", "attachment"]

    all_items = []

    for category in categories:
        print(f"Scraping {category}...")

        # Reddit (sync, fast)
        reddit_items = scrape_reddit(category)
        all_items.extend(reddit_items)
        print(f"  Reddit: {len(reddit_items)} items")

        # TikTok via Apify (async)
        tiktok_items = await scrape_tiktok(category)
        all_items.extend(tiktok_items)
        print(f"  TikTok: {len(tiktok_items)} items")

    # Save everything to database
    if all_items:
        await save_viral_content(all_items)

    print(f"Scraper complete. Saved {len(all_items)} items.")
    return {"count": len(all_items), "categories": categories}


# ─── Scheduled Entry Point ────────────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio
    asyncio.run(run_scraper(["grief", "trauma", "attachment"]))
