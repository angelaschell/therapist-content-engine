"""
Viral Content Scraper - Reddit (free) + Instagram via Apify ($49/mo)
"""

import httpx
import json
import os
import time

APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "")
HEADERS = {"User-Agent": "ContentEngine/1.0 (grief-therapy-research)"}

SUBREDDITS = [
    "GriefSupport", "MotherlessDaughters", "CPTSD",
    "raisedbynarcissists", "emotionalneglect",
    "EstrangedAdultKids", "ChildrenofDeadParents",
]

INSTAGRAM_ACCOUNTS = [
    "nedratawwab", "therapyjeff", "the.holistic.psychologist",
    "lisaoliveratherapy", "lori.gottlieb",
]

TOPIC_HASHTAGS = {
    "grief": ["grief", "griefjourney", "griefandloss", "grieving"],
    "mother": ["motherlessdaughters", "motherhunger", "motherloss", "losingamom"],
    "trauma": ["trauma", "traumahealing", "childhoodtrauma", "cptsd"],
    "attachment": ["attachmenttheory", "anxiousattachment", "attachmentwounds"],
    "emdr": ["emdr", "emdrtherapy", "emdrhealing"],
    "equine": ["equinetherapy", "horsetherapy", "healingwithhorses"],
    "narcissist": ["narcissisticmother", "raisedbynarcissists", "toxicparents"],
    "loss": ["childloss", "parentloss", "bereavement"],
    "healing": ["healingjourney", "innerchildhealing", "therapyworks"],
    "shame": ["toxicshame", "shame", "shamehealing"],
}

PATTERNS = {
    "naming unnamed grief": ["guilty", "guilt", "no one talks", "nobody talks", "never told", "unnamed"],
    "challenging platitudes": ["stay strong", "better place", "at least", "everything happens", "move on", "get over"],
    "milestone grief": ["wedding", "birthday", "graduation", "pregnant", "baby", "mother's day", "holiday"],
    "living loss": ["still alive", "estranged", "no contact", "alive but"],
    "parentification": ["parenting my parent", "caretaker", "took care of", "raised myself"],
    "somatic awareness": ["body remembers", "flinch", "nervous system", "freeze", "triggered"],
    "community identification": ["things nobody tells", "does anyone else", "am I the only"],
    "grief has no timeline": ["years later", "still cry", "out of nowhere", "thought I was over"],
    "attachment wounds": ["attachment", "anxious", "avoidant", "clingy", "too much", "abandonment"],
}

CACHE_FILE = "viral_cache.json"


def detect_pattern(text):
    text_lower = text.lower()
    scores = {}
    for pattern, keywords in PATTERNS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[pattern] = score
    return max(scores, key=scores.get) if scores else "general grief"


def topic_to_keywords(topic):
    words = [w for w in topic.lower().split() if len(w) > 3 and w not in ["about", "that", "this", "with", "from", "your", "when", "what", "have", "been", "they", "their", "there", "nobody", "talks", "still"]]
    return " ".join(words[:5]) if words else topic[:50]


def topic_to_hashtags(topic):
    topic_lower = topic.lower()
    hashtags = set()
    for key, tags in TOPIC_HASHTAGS.items():
        if key in topic_lower:
            hashtags.update(tags)
    if not hashtags:
        hashtags.update(["grief", "traumatherapy", "grieftherapist"])
    return list(hashtags)[:6]


# ─── REDDIT (free) ───

def search_reddit(query, limit=20):
    posts = []
    try:
        url = f"https://www.reddit.com/search.json?q={query}&sort=top&t=week&limit={limit}"
        resp = httpx.get(url, headers=HEADERS, timeout=15, follow_redirects=True)
        if resp.status_code == 200:
            data = resp.json()
            for child in data.get("data", {}).get("children", []):
                p = child.get("data", {})
                if p.get("stickied"):
                    continue
                sub = p.get("subreddit", "")
                relevant = [s.lower() for s in SUBREDDITS] + ["grief", "trauma", "ptsd", "mentalhealth", "therapy", "loss"]
                if not any(r in sub.lower() for r in relevant):
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
                    "tag": detect_pattern(title + " " + selftext),
                    "score": ups,
                })
        time.sleep(1)
    except Exception as e:
        print(f"Reddit search error: {e}")

    for sub in SUBREDDITS[:4]:
        try:
            url = f"https://www.reddit.com/r/{sub}/search.json?q={query}&restrict_sr=on&sort=top&t=month&limit=5"
            resp = httpx.get(url, headers=HEADERS, timeout=15, follow_redirects=True)
            if resp.status_code == 200:
                for child in resp.json().get("data", {}).get("children", []):
                    p = child.get("data", {})
                    if p.get("stickied"):
                        continue
                    ups = p.get("ups", 0)
                    if ups < 5:
                        continue
                    title = p.get("title", "")
                    selftext = (p.get("selftext", "") or "")[:300]
                    posts.append({
                        "src": "reddit",
                        "sub": f"r/{sub}",
                        "title": title,
                        "stats": f"{ups:,} upvotes · {p.get('num_comments', 0):,} comments",
                        "excerpt": selftext[:200] if selftext else title,
                        "tag": detect_pattern(title + " " + selftext),
                        "score": ups,
                    })
            time.sleep(1)
        except Exception as e:
            print(f"Reddit sub error r/{sub}: {e}")

    seen = set()
    unique = []
    for p in posts:
        if p["title"] not in seen:
            seen.add(p["title"])
            unique.append(p)
    unique.sort(key=lambda x: x.get("score", 0), reverse=True)
    return unique[:12]


# ─── INSTAGRAM via Apify ───

def parse_ig_post(item):
    """Extract post data from any Apify Instagram actor response format."""
    # Try multiple possible field names
    caption = item.get("caption", "") or item.get("text", "") or ""
    likes = item.get("likesCount", 0) or item.get("likes", 0) or item.get("dipimaticaCount", 0) or 0
    comments = item.get("commentsCount", 0) or item.get("comments", 0) or 0
    owner = item.get("ownerUsername", "") or ""
    if not owner:
        owner_obj = item.get("owner", {})
        if isinstance(owner_obj, dict):
            owner = owner_obj.get("username", "") or ""
    if not owner:
        owner = item.get("user", {}).get("username", "") if isinstance(item.get("user"), dict) else ""

    # Build a clean title from caption
    title = caption[:120].replace("\n", " ").strip()
    if len(caption) > 120:
        title += "..."

    return {
        "src": "instagram",
        "sub": f"@{owner}" if owner else "Instagram",
        "title": title,
        "stats": f"{likes:,} likes · {comments:,} comments",
        "excerpt": caption[:200].replace("\n", " ") if caption else "",
        "tag": detect_pattern(caption),
        "score": likes if isinstance(likes, int) else 0,
    }


def scrape_instagram_hashtags(hashtags):
    if not APIFY_TOKEN:
        print("No APIFY_TOKEN, skipping Instagram hashtags")
        return []

    posts = []
    try:
        print(f"  Apify hashtag scraper: {hashtags}")
        url = f"https://api.apify.com/v2/acts/apify~instagram-hashtag-scraper/run-sync-get-dataset-items?token={APIFY_TOKEN}"
        body = {
            "hashtags": hashtags,
            "resultsLimit": 5,
        }
        resp = httpx.post(url, json=body, timeout=120)
        print(f"  Apify hashtag response: {resp.status_code}, items: {len(resp.json()) if resp.status_code == 200 else 'N/A'}")

        if resp.status_code == 200:
            items = resp.json()
            if isinstance(items, list):
                for item in items:
                    post = parse_ig_post(item)
                    posts.append(post)
            print(f"  Parsed {len(posts)} hashtag posts")
        else:
            print(f"  Apify hashtag error: {resp.status_code} - {resp.text[:300]}")
    except Exception as e:
        print(f"  Apify hashtag exception: {e}")

    return posts


def scrape_instagram_accounts(usernames):
    if not APIFY_TOKEN:
        print("No APIFY_TOKEN, skipping Instagram accounts")
        return []

    posts = []
    try:
        print(f"  Apify post scraper: {usernames}")
        url = f"https://api.apify.com/v2/acts/apify~instagram-post-scraper/run-sync-get-dataset-items?token={APIFY_TOKEN}"
        body = {
            "username": usernames,
            "resultsLimit": 3,
        }
        resp = httpx.post(url, json=body, timeout=120)
        print(f"  Apify post response: {resp.status_code}, items: {len(resp.json()) if resp.status_code == 200 else 'N/A'}")

        if resp.status_code == 200:
            items = resp.json()
            if isinstance(items, list):
                for item in items:
                    post = parse_ig_post(item)
                    posts.append(post)
            print(f"  Parsed {len(posts)} account posts")
        else:
            print(f"  Apify post error: {resp.status_code} - {resp.text[:300]}")
    except Exception as e:
        print(f"  Apify post exception: {e}")

    return posts


# ─── COMBINED ───

def scrape_by_topic(topic="grief"):
    print(f"\n{'='*50}")
    print(f"SCRAPING: {topic}")
    print(f"{'='*50}")

    query = topic_to_keywords(topic)
    hashtags = topic_to_hashtags(topic)

    print(f"Reddit query: {query}")
    print(f"Instagram hashtags: {hashtags}")

    # Reddit
    reddit_posts = search_reddit(query)
    print(f"Reddit results: {len(reddit_posts)}")

    # Instagram
    ig_hashtag_posts = scrape_instagram_hashtags(hashtags)
    ig_account_posts = scrape_instagram_accounts(INSTAGRAM_ACCOUNTS[:3])
    ig_posts = ig_hashtag_posts + ig_account_posts
    print(f"Instagram results: {len(ig_posts)} ({len(ig_hashtag_posts)} hashtag + {len(ig_account_posts)} account)")

    # Combine
    all_posts = reddit_posts + ig_posts
    all_posts.sort(key=lambda x: x.get("score", 0), reverse=True)

    # Clean score field
    for p in all_posts:
        p.pop("score", None)

    print(f"TOTAL: {len(all_posts)} posts")
    return all_posts[:20]


def run_scraper(topic="grief"):
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
