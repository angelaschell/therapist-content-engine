"""
Viral Content Scraper - Reddit (free) + Instagram top accounts via Apify ($49/mo)
Instagram: Scrapes big therapy accounts, filters posts by topic keywords = high engagement + relevant
"""

import httpx
import json
import os
import time

APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "")
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

SUBREDDITS = [
    "GriefSupport", "MotherlessDaughters", "CPTSD",
    "raisedbynarcissists", "emotionalneglect",
    "EstrangedAdultKids", "ChildrenofDeadParents",
]

# Big therapy/grief accounts that actually get engagement
INSTAGRAM_ACCOUNTS = [
    "nedratawwab", "therapyjeff", "the.holistic.psychologist",
    "lisaoliveratherapy", "lori.gottlieb", "estherperel",
    "drnicolelepera", "silvy.khoucasian", "attachmentnerd",
    "the.holistic.psychologist", "rising.woman",
]

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


def topic_to_reddit_queries(topic):
    skip = {"the", "and", "that", "this", "with", "from", "your", "when", "what",
            "have", "been", "they", "their", "there", "about", "just", "like",
            "does", "anyone", "else", "feel", "still", "know", "talks", "talk",
            "nobody", "ever", "really", "very", "much", "also", "into"}
    words = [w for w in topic.lower().split() if w not in skip and len(w) > 2]
    primary = " ".join(words[:4]) if words else "grief"
    queries = [primary]
    tl = topic.lower()
    if "mother" in tl or "mom" in tl: queries.append("losing mother grief")
    if "trauma" in tl: queries.append("childhood trauma nobody talks about")
    if "attachment" in tl: queries.append("attachment style wound")
    if "guilt" in tl: queries.append("grief guilt")
    if "alive" in tl: queries.append("grieving parent still alive")
    if "neglect" in tl: queries.append("emotional neglect childhood")
    if "shame" in tl: queries.append("shame childhood trauma")
    if len(queries) == 1: queries.append("grief trauma healing")
    return queries[:3]


def topic_to_filter_words(topic):
    """Extract meaningful words from topic to filter Instagram posts."""
    skip = {"the", "and", "that", "this", "with", "from", "your", "when", "what",
            "have", "been", "they", "their", "there", "about", "just", "like",
            "does", "anyone", "feel", "still", "know", "talks", "talk",
            "nobody", "ever", "really", "very", "into", "not", "you"}
    words = [w for w in topic.lower().split() if w not in skip and len(w) > 2]
    # Also add related terms
    extras = []
    tl = topic.lower()
    if "mother" in tl or "mom" in tl: extras += ["mother", "mom", "parent", "daughter"]
    if "trauma" in tl: extras += ["trauma", "wound", "hurt", "pain", "heal"]
    if "grief" in tl: extras += ["grief", "loss", "grieve", "lost", "miss"]
    if "attachment" in tl: extras += ["attachment", "attach", "bond", "connect"]
    if "shame" in tl: extras += ["shame", "worth", "enough"]
    if "neglect" in tl: extras += ["neglect", "absent", "invisible", "unseen"]
    return list(set(words + extras))


# ─── REDDIT (free) ───

def search_reddit(queries, limit=10):
    posts = []
    relevant_subs = set(s.lower() for s in SUBREDDITS)
    relevant_subs.update(["grief", "trauma", "ptsd", "mentalhealth", "therapy", "loss", "bereavement", "anxiety"])

    for query in queries:
        try:
            print(f"  Reddit: '{query}'")
            url = f"https://www.reddit.com/search.json?q={query}&sort=relevance&t=month&limit={limit}"
            resp = httpx.get(url, headers=HEADERS, timeout=15, follow_redirects=True)
            if resp.status_code == 200:
                for child in resp.json().get("data", {}).get("children", []):
                    p = child.get("data", {})
                    if p.get("stickied"): continue
                    sub = p.get("subreddit", "")
                    if not any(r in sub.lower() for r in relevant_subs): continue
                    ups = p.get("ups", 0)
                    if ups < 5: continue
                    title = p.get("title", "")
                    selftext = (p.get("selftext", "") or "")[:300]
                    posts.append({
                        "src": "reddit", "sub": f"r/{sub}", "title": title,
                        "stats": f"{ups:,} upvotes · {p.get('num_comments', 0):,} comments",
                        "excerpt": selftext[:200] if selftext else title,
                        "tag": detect_pattern(title + " " + selftext), "score": ups,
                    })
            time.sleep(2)
        except Exception as e:
            print(f"  Reddit error: {e}")

    # Fallback: top posts from subreddits if search returned little
    if len(posts) < 3:
        print("  Reddit: low results, pulling top posts from subs...")
        for sub in SUBREDDITS[:5]:
            try:
                url = f"https://www.reddit.com/r/{sub}/top.json?t=week&limit=5"
                resp = httpx.get(url, headers=HEADERS, timeout=15, follow_redirects=True)
                if resp.status_code == 200:
                    for child in resp.json().get("data", {}).get("children", []):
                        p = child.get("data", {})
                        if p.get("stickied"): continue
                        ups = p.get("ups", 0)
                        if ups < 5: continue
                        title = p.get("title", "")
                        selftext = (p.get("selftext", "") or "")[:300]
                        posts.append({
                            "src": "reddit", "sub": f"r/{sub}", "title": title,
                            "stats": f"{ups:,} upvotes · {p.get('num_comments', 0):,} comments",
                            "excerpt": selftext[:200] if selftext else title,
                            "tag": detect_pattern(title + " " + selftext), "score": ups,
                        })
                time.sleep(2)
            except Exception as e:
                print(f"  Reddit sub error: {e}")

    seen = set()
    unique = []
    for p in posts:
        if p["title"] not in seen:
            seen.add(p["title"])
            unique.append(p)
    unique.sort(key=lambda x: x.get("score", 0), reverse=True)
    print(f"  Reddit total: {len(unique)}")
    return unique[:12]


# ─── INSTAGRAM via Apify (scrape big accounts, filter by topic) ───

def scrape_instagram_accounts_filtered(topic, accounts=None):
    """Scrape recent posts from big therapy accounts, then filter by topic relevance."""
    if not APIFY_TOKEN:
        print("  No APIFY_TOKEN, skipping Instagram")
        return []

    if accounts is None:
        accounts = INSTAGRAM_ACCOUNTS[:6]

    filter_words = topic_to_filter_words(topic)
    print(f"  Instagram: scraping {accounts}")
    print(f"  Filter words: {filter_words}")

    all_posts = []
    try:
        url = f"https://api.apify.com/v2/acts/apify~instagram-post-scraper/run-sync-get-dataset-items?token={APIFY_TOKEN}"
        body = {
            "username": accounts,
            "resultsLimit": 8,
        }
        resp = httpx.post(url, json=body, timeout=180)
        print(f"  Apify response: {resp.status_code}")

        if resp.status_code in [200, 201]:
            items = resp.json()
            if isinstance(items, list):
                print(f"  Apify returned {len(items)} total posts")
                for item in items:
                    caption = item.get("caption", "") or ""
                    likes = item.get("likesCount", 0) or 0
                    comments = item.get("commentsCount", 0) or 0
                    owner = item.get("ownerUsername", "") or ""

                    if not caption or len(caption.strip()) < 30:
                        continue

                    # Check if post is relevant to the topic
                    caption_lower = caption.lower()
                    relevance = sum(1 for w in filter_words if w in caption_lower)

                    if relevance == 0:
                        continue

                    first_line = caption.split("\n")[0].strip()
                    title = first_line[:120] + ("..." if len(first_line) > 120 else "")

                    all_posts.append({
                        "src": "instagram",
                        "sub": f"@{owner}" if owner else "Instagram",
                        "title": title,
                        "stats": f"{likes:,} likes · {comments:,} comments",
                        "excerpt": caption[:200].replace("\n", " "),
                        "tag": detect_pattern(caption),
                        "score": likes,
                        "relevance": relevance,
                    })

                # Sort by relevance first, then engagement
                all_posts.sort(key=lambda x: (x.get("relevance", 0), x.get("score", 0)), reverse=True)
                print(f"  Instagram relevant: {len(all_posts)} posts (from {len(items)} total)")
        else:
            print(f"  Apify error: {resp.status_code}")
    except Exception as e:
        print(f"  Apify exception: {e}")

    # Clean up extra fields
    for p in all_posts:
        p.pop("relevance", None)

    return all_posts[:8]


# ─── COMBINED ───

def scrape_by_topic(topic="grief"):
    print(f"\n{'='*50}")
    print(f"SCRAPING: {topic}")
    print(f"{'='*50}")

    reddit_queries = topic_to_reddit_queries(topic)
    print(f"Reddit queries: {reddit_queries}")

    reddit_posts = search_reddit(reddit_queries)
    ig_posts = scrape_instagram_accounts_filtered(topic)

    print(f"Reddit: {len(reddit_posts)}, Instagram: {len(ig_posts)}")

    all_posts = reddit_posts + ig_posts
    for p in all_posts:
        p.pop("score", None)

    print(f"TOTAL: {len(all_posts)}")
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
