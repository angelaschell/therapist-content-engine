"""
Viral Content Scraper - Reddit (free) + Instagram hashtags via Apify ($49/mo)
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

# Map topics to the best hashtags for Instagram search
TOPIC_HASHTAGS = {
    "grief": ["grief", "griefjourney", "griefandloss", "grieving", "griefquotes"],
    "mother": ["motherlessdaughters", "motherhunger", "motherloss", "losingamom", "momgrief"],
    "mom": ["motherlessdaughters", "motherloss", "losingamom", "momgrief"],
    "parent": ["parentloss", "losingaparent", "griefandloss", "orphanadult"],
    "trauma": ["trauma", "traumahealing", "childhoodtrauma", "cptsd", "traumatherapy"],
    "attachment": ["attachmenttheory", "anxiousattachment", "attachmentwounds", "attachmentstyle"],
    "emdr": ["emdr", "emdrtherapy", "emdrhealing", "traumatherapy"],
    "equine": ["equinetherapy", "horsetherapy", "healingwithhorses", "equineassisted"],
    "horse": ["equinetherapy", "horsetherapy", "healingwithhorses"],
    "narcissist": ["narcissisticmother", "raisedbynarcissists", "narcissisticabuse", "toxicparents"],
    "toxic": ["toxicparents", "toxicmother", "toxicfamily", "emotionalabuse"],
    "loss": ["childloss", "parentloss", "bereavement", "griefisnotlinear"],
    "healing": ["healingjourney", "innerchildhealing", "therapyworks", "mentalhealthmatters"],
    "shame": ["toxicshame", "shame", "shamehealing", "worthiness"],
    "neglect": ["emotionalneglect", "childhoodneglect", "neglecttrauma"],
    "abandon": ["abandonmentissues", "abandonmentwound", "fearofabandonment"],
    "inner child": ["innerchild", "innerchildhealing", "innerchildwork"],
    "nervous": ["nervoussystem", "nervoussystemregulation", "somatichealing", "polyvagal"],
    "somatic": ["somatichealing", "somaticexperiencing", "bodykeepsthescore"],
    "frozen": ["frozengrief", "complicatedgrief", "stuckgrief"],
    "guilt": ["griefguilt", "survivorsguilt", "guilt", "griefandguilt"],
    "anger": ["griefandanger", "angrygrief", "griefrage"],
    "wedding": ["griefandweddings", "motherlessbride", "weddinginheaven"],
    "holiday": ["griefduringholidays", "holidaygrief", "firstholidaywithoutmom"],
    "mother's day": ["mothersdaygrief", "motherlessdaughter", "mothersday"],
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


def topic_to_reddit_queries(topic):
    """Convert topic to multiple Reddit search queries for better results."""
    topic_lower = topic.lower()

    # Build primary query from meaningful words
    skip = {"the", "and", "that", "this", "with", "from", "your", "when", "what",
            "have", "been", "they", "their", "there", "about", "just", "like",
            "does", "anyone", "else", "feel", "still", "know", "talks", "talk",
            "nobody", "ever", "really", "very", "much", "also", "into"}

    words = [w for w in topic_lower.split() if w not in skip and len(w) > 2]
    primary = " ".join(words[:4]) if words else "grief"

    # Generate additional focused queries
    queries = [primary]

    # Add topic-specific queries
    if "mother" in topic_lower or "mom" in topic_lower:
        queries.append("losing mother grief")
    if "trauma" in topic_lower:
        queries.append("childhood trauma healing")
    if "attachment" in topic_lower:
        queries.append("attachment style relationships")
    if "guilt" in topic_lower:
        queries.append("grief guilt laughing")
    if "alive" in topic_lower:
        queries.append("grieving parent still alive")
    if "neglect" in topic_lower:
        queries.append("emotional neglect childhood")
    if "emdr" in topic_lower:
        queries.append("emdr therapy experience")
    if "shame" in topic_lower:
        queries.append("shame childhood trauma")

    return queries[:3]


def topic_to_hashtags(topic):
    """Convert topic to Instagram hashtags."""
    topic_lower = topic.lower()
    hashtags = set()

    for key, tags in TOPIC_HASHTAGS.items():
        if key in topic_lower:
            hashtags.update(tags)

    # Always include base tags if nothing matched
    if not hashtags:
        hashtags.update(["grief", "griefjourney", "traumatherapy", "grieftherapist"])

    return list(hashtags)[:8]


# ─── REDDIT (free) ───

def search_reddit(queries, limit=10):
    """Search Reddit with multiple queries for better coverage."""
    posts = []
    relevant_subs = set(s.lower() for s in SUBREDDITS)
    relevant_subs.update(["grief", "trauma", "ptsd", "mentalhealth", "therapy",
                          "loss", "bereavement", "anxiety", "depression"])

    for query in queries:
        try:
            print(f"  Reddit searching: '{query}'")
            url = f"https://www.reddit.com/search.json?q={query}&sort=relevance&t=month&limit={limit}"
            resp = httpx.get(url, headers=HEADERS, timeout=15, follow_redirects=True)
            print(f"  Reddit response: {resp.status_code}")

            if resp.status_code == 200:
                data = resp.json()
                for child in data.get("data", {}).get("children", []):
                    p = child.get("data", {})
                    if p.get("stickied"):
                        continue
                    sub = p.get("subreddit", "")
                    # Filter to relevant subs
                    if not any(r in sub.lower() for r in relevant_subs):
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
            elif resp.status_code == 429:
                print(f"  Reddit rate limited, waiting...")
                time.sleep(5)
            else:
                print(f"  Reddit returned {resp.status_code}")

            time.sleep(2)
        except Exception as e:
            print(f"  Reddit search error: {e}")

    # If global search returned nothing, try top posts from subreddits directly
    if len(posts) < 3:
        print("  Reddit search low results, trying top posts from subreddits...")
        for sub in SUBREDDITS[:5]:
            try:
                url = f"https://www.reddit.com/r/{sub}/top.json?t=week&limit=5"
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
                time.sleep(2)
            except Exception as e:
                print(f"  Reddit sub error r/{sub}: {e}")

    # Deduplicate
    seen = set()
    unique = []
    for p in posts:
        if p["title"] not in seen:
            seen.add(p["title"])
            unique.append(p)
    unique.sort(key=lambda x: x.get("score", 0), reverse=True)
    print(f"  Reddit total: {len(unique)} unique posts")
    return unique[:12]


# ─── INSTAGRAM via Apify (hashtag search only, topic-relevant) ───

def parse_ig_post(item):
    """Parse Instagram post from Apify response."""
    caption = item.get("caption", "") or item.get("text", "") or ""
    likes = item.get("likesCount", 0) or item.get("likes", 0) or 0
    comments = item.get("commentsCount", 0) or item.get("comments", 0) or 0
    owner = item.get("ownerUsername", "") or ""
    if not owner:
        owner_obj = item.get("owner", {})
        if isinstance(owner_obj, dict):
            owner = owner_obj.get("username", "") or ""

    # Skip posts with no real content
    if not caption or len(caption.strip()) < 20:
        return None

    # Build title from first line of caption
    first_line = caption.split("\n")[0].strip()
    title = first_line[:120]
    if len(first_line) > 120:
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
    """Scrape Instagram by hashtag only. This keeps results topic-relevant."""
    if not APIFY_TOKEN:
        print("  No APIFY_TOKEN, skipping Instagram")
        return []

    posts = []
    try:
        print(f"  Apify hashtag search: {hashtags}")
        url = f"https://api.apify.com/v2/acts/apify~instagram-hashtag-scraper/run-sync-get-dataset-items?token={APIFY_TOKEN}"
        body = {
            "hashtags": hashtags,
            "resultsLimit": 10,
        }
        resp = httpx.post(url, json=body, timeout=120)
        print(f"  Apify response: {resp.status_code}")

        if resp.status_code in [200, 201]:
            items = resp.json()
            if isinstance(items, list):
                for item in items:
                    post = parse_ig_post(item)
                    if post:
                        posts.append(post)
                print(f"  Parsed {len(posts)} Instagram posts from {len(items)} results")
            else:
                print(f"  Apify returned non-list: {type(items)}")
        else:
            print(f"  Apify error {resp.status_code}: {resp.text[:300]}")
    except Exception as e:
        print(f"  Apify exception: {e}")

    # Sort by engagement
    posts.sort(key=lambda x: x.get("score", 0), reverse=True)
    return posts[:10]


# ─── COMBINED ───

def scrape_by_topic(topic="grief"):
    print(f"\n{'='*50}")
    print(f"SCRAPING: {topic}")
    print(f"{'='*50}")

    reddit_queries = topic_to_reddit_queries(topic)
    hashtags = topic_to_hashtags(topic)

    print(f"Reddit queries: {reddit_queries}")
    print(f"Instagram hashtags: {hashtags}")

    # Reddit
    reddit_posts = search_reddit(reddit_queries)
    print(f"Reddit final: {len(reddit_posts)} posts")

    # Instagram (hashtag only, so results match the topic)
    ig_posts = scrape_instagram_hashtags(hashtags)
    print(f"Instagram final: {len(ig_posts)} posts")

    # Combine, Reddit first then Instagram
    all_posts = reddit_posts + ig_posts

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
