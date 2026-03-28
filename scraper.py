"""
Viral Content Scraper for Angela Schellenberg's Content Engine
Scrapes top posts from grief/trauma subreddits using Reddit's public JSON API.
No API keys required.

Run manually: python scraper.py
Or via Render cron job (daily at 11pm PT / 6am UTC)
"""

import httpx
import json
import os
import time

SUBREDDITS = [
    "GriefSupport",
    "MotherlessDaughters",
    "CPTSD",
    "raisedbynarcissists",
    "emotionalneglect",
    "EstrangedAdultKids",
    "ChildrenofDeadParents",
    "griefsupport",
]

# Pattern detection keywords
PATTERNS = {
    "naming unnamed grief": ["guilty", "guilt", "no one talks", "nobody talks", "never told", "didn't know", "unnamed", "can't explain"],
    "challenging platitudes": ["stay strong", "she's in a better place", "at least", "everything happens", "move on", "get over", "time heals"],
    "milestone grief": ["wedding", "birthday", "graduation", "pregnant", "baby", "mother's day", "holiday", "christmas", "thanksgiving", "anniversary"],
    "living loss": ["still alive", "estranged", "no contact", "alive but", "living parent", "might as well be dead"],
    "parentification": ["parenting my parent", "caretaker", "took care of", "raised myself", "emotional support", "parent to my parent", "role reversal"],
    "somatic awareness": ["body remembers", "flinch", "nervous system", "freeze", "fight or flight", "triggered", "panic", "hypervigilant"],
    "community identification": ["things nobody tells", "only people who", "if you know you know", "does anyone else", "am I the only"],
    "grief has no timeline": ["years later", "still cry", "out of nowhere", "thought I was over", "hit me", "ambush", "wave of grief"],
    "frozen grief": ["numb", "can't cry", "shut down", "disconnected", "going through the motions", "autopilot"],
    "attachment wounds": ["attachment", "anxious", "avoidant", "secure", "clingy", "too much", "not enough", "abandonment"],
}

CACHE_FILE = "viral_cache.json"
HEADERS = {
    "User-Agent": "ContentEngine/1.0 (grief-therapy-research)"
}


def detect_pattern(title, text):
    """Detect which viral pattern a post matches."""
    combined = (title + " " + text).lower()
    scores = {}
    for pattern, keywords in PATTERNS.items():
        score = sum(1 for kw in keywords if kw.lower() in combined)
        if score > 0:
            scores[pattern] = score
    if scores:
        return max(scores, key=scores.get)
    return "general grief"


def scrape_subreddit(sub, timeframe="week", limit=10):
    """Scrape top posts from a subreddit using public JSON API."""
    url = f"https://www.reddit.com/r/{sub}/top.json?t={timeframe}&limit={limit}"
    try:
        resp = httpx.get(url, headers=HEADERS, timeout=15, follow_redirects=True)
        if resp.status_code != 200:
            print(f"  [!] r/{sub} returned {resp.status_code}")
            return []

        data = resp.json()
        posts = []
        for child in data.get("data", {}).get("children", []):
            p = child.get("data", {})
            if p.get("stickied"):
                continue

            title = p.get("title", "")
            selftext = p.get("selftext", "")[:300]
            ups = p.get("ups", 0)
            num_comments = p.get("num_comments", 0)

            if ups < 50:
                continue

            pattern = detect_pattern(title, selftext)

            posts.append({
                "src": "reddit",
                "sub": f"r/{sub}",
                "title": title,
                "stats": f"{ups:,} upvotes · {num_comments:,} comments",
                "excerpt": selftext[:200] if selftext else title,
                "tag": pattern,
                "score": ups,
                "comments": num_comments,
                "url": f"https://reddit.com{p.get('permalink', '')}",
            })

        return posts

    except Exception as e:
        print(f"  [!] Error scraping r/{sub}: {e}")
        return []


def run_scraper():
    """Scrape all subreddits and save results."""
    print("=" * 50)
    print("VIRAL CONTENT SCRAPER")
    print("=" * 50)

    all_posts = []

    for sub in SUBREDDITS:
        print(f"\nScraping r/{sub}...")
        posts = scrape_subreddit(sub, timeframe="week", limit=15)
        print(f"  Found {len(posts)} qualifying posts")
        all_posts.extend(posts)
        time.sleep(2)  # Be nice to Reddit

    # Sort by engagement score
    all_posts.sort(key=lambda x: x.get("score", 0), reverse=True)

    # Take top 20
    top_posts = all_posts[:20]

    # Remove score field (not needed in frontend)
    for p in top_posts:
        p.pop("score", None)
        p.pop("comments", None)
        p.pop("url", None)

    # Save to cache file
    cache = {
        "scraped_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "total_found": len(all_posts),
        "posts": top_posts,
    }

    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)

    print(f"\nDone. {len(top_posts)} posts saved to {CACHE_FILE}")
    print(f"Scraped at: {cache['scraped_at']}")

    # Print pattern breakdown
    patterns = {}
    for p in top_posts:
        tag = p.get("tag", "unknown")
        patterns[tag] = patterns.get(tag, 0) + 1
    print("\nPattern breakdown:")
    for pat, count in sorted(patterns.items(), key=lambda x: -x[1]):
        print(f"  {pat}: {count}")

    return cache


def load_cache():
    """Load cached viral posts. Returns None if no cache exists."""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r") as f:
                return json.load(f)
        except:
            return None
    return None


if __name__ == "__main__":
    run_scraper()
