"""
Viral Content Scraper for Angela Schellenberg's Content Engine
- Reddit: searches by keyword using public JSON API (free)
- Instagram: scrapes hashtags + competitor accounts via Apify ($49/mo)
No Reddit API keys needed. Apify token required for Instagram.
"""

import httpx
import json
import os
import time

APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "")

HEADERS = {"User-Agent": "ContentEngine/1.0 (grief-therapy-research)"}

# Default subreddits to search within
SUBREDDITS = [
    "GriefSupport", "MotherlessDaughters", "CPTSD",
    "raisedbynarcissists", "emotionalneglect",
    "EstrangedAdultKids", "ChildrenofDeadParents",
]

# Instagram accounts in Angela's niche (competitor/inspiration)
INSTAGRAM_ACCOUNTS = [
    "nedratawwab", "therapyjeff", "the.holistic.psychologist",
    "lisaoliveratherapy", "lori.gottlieb", "estherperel",
]

# Topic to hashtag mapping for Instagram
TOPIC_HASHTAGS = {
    "grief": ["grief", "griefjourney", "griefandloss", "grieving", "griefquotes"],
    "mother": ["motherlessdaughters", "motherhunger", "motherloss", "losingamom", "momgrief"],
    "trauma": ["trauma", "traumahealing", "childhoodtrauma", "cptsd", "traumatherapy"],
    "attachment": ["attachmenttheory", "anxiousattachment", "attachmentstyle", "attachmentwounds"],
    "emdr": ["emdr", "emdrtherapy", "emdrhealing", "traumatherapy"],
    "equine": ["equinetherapy", "horsetherapy", "equineassisted", "healingwithhorses"],
    "narcissist": ["narcissisticmother", "raisedbynarcissists", "narcissisticabuse", "toxicparents"],
    "loss": ["childloss", "parentloss", "bereavement", "griefisnotlinear"],
    "healing": ["healingjourney", "innerchildhealing", "therapyworks", "mentalhealthmatters"],
}

# Pattern detection
PATTERNS = {
    "naming unnamed grief": ["guilty", "guilt", "no one talks", "nobody talks", "never told", "unnamed", "can't explain"],
    "challenging platitudes": ["stay strong", "better place", "at least", "everything happens", "move on", "get over", "time heals"],
    "milestone grief": ["wedding", "birthday", "graduation", "pregnant", "baby", "mother's day", "holiday", "christmas", "anniversary"],
    "living loss": ["still alive", "estranged", "no contact", "alive but", "living parent"],
    "parentification": ["parenting my parent", "caretaker", "took care of", "raised myself", "role reversal"],
    "somatic awareness": ["body remembers", "flinch", "nervous system", "freeze", "fight or flight", "triggered", "hypervigilant"],
    "community identification": ["things nobody tells", "only people who", "if you know", "does anyone else", "am I the only"],
    "grief has no timeline": ["years later", "still cry", "out of nowhere", "thought I was over", "ambush", "wave of grief"],
    "frozen grief": ["numb", "can't cry", "shut down", "disconnected", "going through the motions"],
    "attachment wounds": ["attachment", "anxious", "avoidant", "clingy", "too much", "not enough", "abandonment"],
}

CACHE_FILE = "viral_cache.json"


def detect_pattern(title, text):
    combined = (title + " " + text).lower()
    scores = {}
    for pattern, keywords in PATTERNS.items():
        score = sum(1 for kw in keywords if kw.lower() in combined)
        if score > 0:
            scores[pattern] = score
    return max(scores, key=scores.get) if scores else "general grief"


def topic_to_keywords(topic):
    """Convert a topic string into search keywords."""
    topic_lower = topic.lower()
    keywords = []

    # Extract core words
    for word in topic_lower.split():
        if len(word) > 3 and word not in ["about", "that", "this", "with", "from", "your", "when", "what", "have", "been", "they", "their", "there", "nobody", "talks", "still"]:
            keywords.append(word)

    return " ".join(keywords[:5]) if keywords else topic[:50]


def topic_to_hashtags(topic):
    """Convert a topic to relevant Instagram hashtags."""
    topic_lower = topic.lower()
    hashtags = set()

    for key, tags in TOPIC_HASHTAGS.items():
        if key in topic_lower:
            hashtags.update(tags)

    # Always include base grief/therapy hashtags
    hashtags.update(["grief", "traumatherapy", "grieftherapist"])

    return list(hashtags)[:8]


# ─── REDDIT SCRAPER (free, no API key) ───

def search_reddit(query, limit=20):
    """Search Reddit for posts matching a query."""
    posts = []

    # Search across all grief subreddits
    search_url = f"https://www.reddit.com/search.json?q={query}&sort=top&t=week&limit={limit}"
    try:
        resp = httpx.get(search_url, headers=HEADERS, timeout=15, follow_redirects=True)
        if resp.status_code == 200:
            data = resp.json()
            for child in data.get("data", {}).get("children", []):
                p = child.get("data", {})
                if p.get("stickied"):
                    continue
                sub = p.get("subreddit", "")
                # Filter to grief/trauma related subs
                relevant_subs = [s.lower() for s in SUBREDDITS] + ["grief", "trauma", "ptsd", "mentalhealth", "therapy", "loss", "bereavement"]
                if not any(rs in sub.lower() for rs in relevant_subs):
                    continue

                ups = p.get("ups", 0)
                if ups < 20:
                    continue

                title = p.get("title", "")
                selftext = (p.get("selftext", "") or "")[:300]
                posts.append({
                    "src": "reddit",
                    "sub": f"r/{sub}",
                    "title": title,
                    "stats": f"{ups:,} upvotes · {p.get('num_comments', 0):,} comments",
                    "excerpt": selftext[:200] if selftext else title,
                    "tag": detect_pattern(title, selftext),
                    "score": ups,
                })
        time.sleep(1)
    except Exception as e:
        print(f"Reddit search error: {e}")

    # Also search within specific subreddits
    for sub in SUBREDDITS[:4]:
        try:
            url = f"https://www.reddit.com/r/{sub}/search.json?q={query}&restrict_sr=on&sort=top&t=month&limit=5"
            resp = httpx.get(url, headers=HEADERS, timeout=15, follow_redirects=True)
            if resp.status_code == 200:
                data = resp.json()
                for child in data.get("data", {}).get("children", []):
                    p = child.get("data", {})
                    if p.get("stickied"):
                        continue
                    ups = p.get("ups", 0)
                    if ups < 10:
                        continue
                    title = p.get("title", "")
                    selftext = (p.get("selftext", "") or "")[:300]
                    posts.append({
                        "src": "reddit",
                        "sub": f"r/{sub}",
                        "title": title,
                        "stats": f"{ups:,} upvotes · {p.get('num_comments', 0):,} comments",
                        "excerpt": selftext[:200] if selftext else title,
                        "tag": detect_pattern(title, selftext),
                        "score": ups,
                    })
            time.sleep(1)
        except Exception as e:
            print(f"Reddit sub search error for r/{sub}: {e}")

    # Deduplicate by title
    seen = set()
    unique = []
    for p in posts:
        if p["title"] not in seen:
            seen.add(p["title"])
            unique.append(p)

    unique.sort(key=lambda x: x.get("score", 0), reverse=True)
    return unique[:15]


# ─── INSTAGRAM SCRAPER via Apify ───

def scrape_instagram_hashtags(hashtags, max_posts=5):
    """Scrape Instagram posts by hashtag using Apify."""
    if not APIFY_TOKEN:
        print("No APIFY_TOKEN set, skipping Instagram")
        return []

    posts = []
    try:
        # Use Instagram Hashtag Scraper actor
        url = f"https://api.apify.com/v2/acts/apify~instagram-hashtag-scraper/run-sync-get-dataset-items?token={APIFY_TOKEN}"
        body = {
            "hashtags": hashtags[:5],
            "resultsLimit": max_posts,
        }
        resp = httpx.post(url, json=body, timeout=120)
        if resp.status_code == 200:
            items = resp.json()
            for item in items:
                caption = item.get("caption", "") or ""
                likes = item.get("likesCount", 0) or item.get("likes", 0) or 0
                comments = item.get("commentsCount", 0) or item.get("comments", 0) or 0
                owner = item.get("ownerUsername", "") or item.get("owner", {}).get("username", "") or ""

                if likes < 100:
                    continue

                posts.append({
                    "src": "instagram",
                    "sub": f"@{owner}" if owner else "Instagram",
                    "title": caption[:120] + ("..." if len(caption) > 120 else ""),
                    "stats": f"{likes:,} likes · {comments:,} comments",
                    "excerpt": caption[:200] if caption else "",
                    "tag": detect_pattern(caption, ""),
                    "score": likes,
                })
        else:
            print(f"Apify hashtag scraper returned {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        print(f"Apify hashtag error: {e}")

    return posts


def scrape_instagram_accounts(usernames, max_posts=3):
    """Scrape recent posts from specific Instagram accounts using Apify."""
    if not APIFY_TOKEN:
        return []

    posts = []
    try:
        url = f"https://api.apify.com/v2/acts/apify~instagram-post-scraper/run-sync-get-dataset-items?token={APIFY_TOKEN}"
        body = {
            "username": usernames[:4],
            "resultsLimit": max_posts,
        }
        resp = httpx.post(url, json=body, timeout=120)
        if resp.status_code == 200:
            items = resp.json()
            for item in items:
                caption = item.get("caption", "") or ""
                likes = item.get("likesCount", 0) or item.get("likes", 0) or 0
                comments = item.get("commentsCount", 0) or item.get("comments", 0) or 0
                owner = item.get("ownerUsername", "") or ""

                posts.append({
                    "src": "instagram",
                    "sub": f"@{owner}" if owner else "Instagram",
                    "title": caption[:120] + ("..." if len(caption) > 120 else ""),
                    "stats": f"{likes:,} likes · {comments:,} comments",
                    "excerpt": caption[:200] if caption else "",
                    "tag": detect_pattern(caption, ""),
                    "score": likes,
                })
        else:
            print(f"Apify post scraper returned {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        print(f"Apify account error: {e}")

    return posts


# ─── COMBINED SCRAPER ───

def scrape_by_topic(topic="grief"):
    """Search Reddit + Instagram based on a topic. Returns combined results."""
    print(f"Scraping for topic: {topic}")

    query = topic_to_keywords(topic)
    hashtags = topic_to_hashtags(topic)

    print(f"  Reddit query: {query}")
    print(f"  Instagram hashtags: {hashtags}")

    # Reddit (always runs, free)
    reddit_posts = search_reddit(query)
    print(f"  Reddit: {len(reddit_posts)} posts")

    # Instagram via Apify (runs if token exists)
    ig_hashtag_posts = scrape_instagram_hashtags(hashtags, max_posts=5)
    ig_account_posts = scrape_instagram_accounts(INSTAGRAM_ACCOUNTS[:3], max_posts=2)
    ig_posts = ig_hashtag_posts + ig_account_posts
    print(f"  Instagram: {len(ig_posts)} posts")

    # Combine and sort by engagement
    all_posts = reddit_posts + ig_posts
    all_posts.sort(key=lambda x: x.get("score", 0), reverse=True)

    # Clean up score field
    for p in all_posts:
        p.pop("score", None)

    return all_posts[:20]


def run_scraper(topic="grief"):
    """Run full scraper and save to cache."""
    posts = scrape_by_topic(topic)

    cache = {
        "scraped_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "topic": topic,
        "total_found": len(posts),
        "posts": posts,
    }

    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)

    print(f"Saved {len(posts)} posts to {CACHE_FILE}")
    return cache


def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r") as f:
                return json.load(f)
        except:
            return None
    return None


if __name__ == "__main__":
    import sys
    topic = sys.argv[1] if len(sys.argv) > 1 else "grief mother loss"
    run_scraper(topic)
