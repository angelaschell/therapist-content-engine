"""
Viral Content Scraper - Reddit + Instagram via Apify
Pulls large volume, ranks by relevance + virality
"""

import httpx
import json
import os
import time
import re

APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "")
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

SUBREDDITS = ["GriefSupport", "MotherlessDaughters", "CPTSD", "raisedbynarcissists", "emotionalneglect", "EstrangedAdultKids", "ChildrenofDeadParents", "griefsupport", "ptsd", "attachment_theory"]

INSTAGRAM_ACCOUNTS = ["nedratawwab", "therapyjeff", "the.holistic.psychologist", "lisaoliveratherapy", "lori.gottlieb", "estherperel", "drnicolelepera", "silvy.khoucasian", "attachmentnerd", "rising.woman", "mindfulmft", "thebraincoach"]

PATTERNS = {
    "naming unnamed grief": ["guilty", "guilt", "no one talks", "nobody talks", "never told", "unnamed", "words for"],
    "challenging platitudes": ["stay strong", "better place", "at least", "everything happens", "move on", "get over", "time heals"],
    "milestone grief": ["wedding", "birthday", "graduation", "pregnant", "baby", "mother's day", "holiday", "anniversary", "christmas"],
    "living loss": ["still alive", "estranged", "no contact", "alive but", "living parent", "functional family"],
    "parentification": ["parenting my parent", "caretaker", "took care of", "raised myself", "role reversal", "emotional support"],
    "somatic awareness": ["body remembers", "flinch", "nervous system", "freeze", "fight or flight", "triggered", "hypervigilant"],
    "community identification": ["things nobody tells", "does anyone else", "am I the only", "if you know you know"],
    "grief has no timeline": ["years later", "still cry", "out of nowhere", "thought I was over", "ambush", "wave of grief"],
    "attachment wounds": ["attachment", "anxious", "avoidant", "clingy", "too much", "not enough", "abandonment"],
    "frozen grief": ["numb", "can't cry", "shut down", "disconnected", "autopilot", "going through the motions"],
}

CACHE_FILE = "viral_cache.json"


def detect_pattern(text):
    text_lower = text.lower()
    scores = {}
    for pattern, keywords in PATTERNS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0: scores[pattern] = score
    return max(scores, key=scores.get) if scores else "general grief"


def extract_hook(title):
    text = title.strip()
    if len(text) > 80: text = text[:77] + "..."
    return text


def topic_to_reddit_queries(topic):
    skip = {"the","and","that","this","with","from","your","when","what","have","been","they","their","there","about","just","like","does","anyone","else","feel","still","know","talks","talk","nobody","ever","really","very","much","also","into","not","you","can","but","for","how","why","all","was","were","are","its"}
    words = [w for w in topic.lower().split() if w not in skip and len(w) > 2]
    primary = " ".join(words[:4]) if words else "grief"
    queries = [primary]
    tl = topic.lower()
    if "mother" in tl or "mom" in tl: queries.append("losing mother grief"); queries.append("motherless daughter")
    if "trauma" in tl: queries.append("childhood trauma healing"); queries.append("trauma nobody talks about")
    if "attachment" in tl: queries.append("attachment wound relationships"); queries.append("anxious attachment")
    if "guilt" in tl: queries.append("grief guilt laughing"); queries.append("survivor guilt")
    if "alive" in tl: queries.append("grieving parent still alive"); queries.append("estranged parent grief")
    if "neglect" in tl: queries.append("emotional neglect childhood"); queries.append("invisible child")
    if "shame" in tl: queries.append("shame childhood trauma"); queries.append("toxic shame healing")
    if "emdr" in tl: queries.append("emdr therapy experience"); queries.append("emdr changed my life")
    if "horse" in tl or "equine" in tl: queries.append("equine therapy grief"); queries.append("horse therapy healing")
    if "body" in tl or "somatic" in tl: queries.append("body keeps the score"); queries.append("somatic healing trauma")
    if len(queries) == 1: queries.append("grief trauma healing"); queries.append("childhood wounds adult")
    return queries[:5]


def topic_to_filter_words(topic):
    skip = {"the","and","that","this","with","from","your","when","what","have","been","they","their","there","about","just","like","does","anyone","feel","still","know","talks","talk","nobody","ever","really","very","into","not","you","can","but","for","how","why","all"}
    words = [w for w in topic.lower().split() if w not in skip and len(w) > 2]
    extras = []
    tl = topic.lower()
    if "mother" in tl or "mom" in tl: extras += ["mother","mom","parent","daughter","maternal","motherless"]
    if "trauma" in tl: extras += ["trauma","wound","hurt","pain","heal","traumatic","ptsd","cptsd"]
    if "grief" in tl: extras += ["grief","loss","grieve","lost","miss","grieving","mourn"]
    if "attachment" in tl: extras += ["attachment","attach","bond","connect","secure","insecure","anxious","avoidant"]
    if "shame" in tl: extras += ["shame","worth","enough","worthy","deserving"]
    if "neglect" in tl: extras += ["neglect","absent","invisible","unseen","ignored","overlooked"]
    if "body" in tl or "somatic" in tl: extras += ["body","somatic","nervous","system","sensation","feel"]
    return list(set(words + extras))


def calculate_relevance(text, filter_words):
    """Score how relevant a post is to the topic. Higher = more relevant."""
    text_lower = text.lower()
    score = 0
    for word in filter_words:
        count = text_lower.count(word)
        if count > 0:
            score += count
    # Bonus for longer matches (phrases)
    for i in range(len(filter_words)):
        for j in range(i+1, min(i+3, len(filter_words))):
            phrase = filter_words[i] + " " + filter_words[j]
            if phrase in text_lower:
                score += 3
    return score


def calculate_virality(engagement_str, src="reddit"):
    """Extract engagement number and normalize to a virality score."""
    numbers = re.findall(r'[\d,]+', engagement_str)
    if not numbers: return 0
    primary = int(numbers[0].replace(",", ""))
    # Normalize: Reddit upvotes and IG likes are on different scales
    if src == "instagram":
        return primary  # IG likes as-is
    return primary * 5  # Reddit upvotes weighted higher (more meaningful engagement)


# ─── REDDIT ───

def search_reddit(queries, filter_words, limit=15):
    posts = []
    relevant_subs = set(s.lower() for s in SUBREDDITS)
    relevant_subs.update(["grief","trauma","ptsd","mentalhealth","therapy","loss","bereavement","anxiety","depression","selfhelp"])

    for query in queries:
        try:
            print(f"  Reddit: '{query}'")
            url = f"https://www.reddit.com/search.json?q={query}&sort=relevance&t=month&limit={limit}"
            resp = httpx.get(url, headers=HEADERS, timeout=15, follow_redirects=True)
            if resp.status_code == 200:
                for child in resp.json().get("data",{}).get("children",[]):
                    p = child.get("data",{})
                    if p.get("stickied"): continue
                    sub = p.get("subreddit","")
                    if not any(r in sub.lower() for r in relevant_subs): continue
                    ups = p.get("ups",0)
                    if ups < 3: continue
                    title = p.get("title","")
                    selftext = (p.get("selftext","") or "")[:400]
                    full_text = title + " " + selftext
                    rel = calculate_relevance(full_text, filter_words)
                    vir = ups * 5
                    posts.append({
                        "src":"reddit","sub":f"r/{sub}","title":title,
                        "stats":f"{ups:,} upvotes · {p.get('num_comments',0):,} comments",
                        "excerpt":selftext[:200] if selftext else title,
                        "tag":detect_pattern(full_text),
                        "engagement":ups,
                        "relevance_score":rel,
                        "virality_score":vir,
                        "combined_score":rel*10 + vir,
                    })
            time.sleep(2)
        except Exception as e:
            print(f"  Reddit error: {e}")

    # Fallback
    if len(posts) < 5:
        print("  Reddit fallback: top posts from subs...")
        for sub in SUBREDDITS[:6]:
            try:
                url = f"https://www.reddit.com/r/{sub}/top.json?t=week&limit=8"
                resp = httpx.get(url, headers=HEADERS, timeout=15, follow_redirects=True)
                if resp.status_code == 200:
                    for child in resp.json().get("data",{}).get("children",[]):
                        p = child.get("data",{})
                        if p.get("stickied"): continue
                        ups = p.get("ups",0)
                        if ups < 3: continue
                        title = p.get("title","")
                        selftext = (p.get("selftext","") or "")[:400]
                        full_text = title + " " + selftext
                        rel = calculate_relevance(full_text, filter_words)
                        vir = ups * 5
                        posts.append({
                            "src":"reddit","sub":f"r/{sub}","title":title,
                            "stats":f"{ups:,} upvotes · {p.get('num_comments',0):,} comments",
                            "excerpt":selftext[:200] if selftext else title,
                            "tag":detect_pattern(full_text),
                            "engagement":ups,
                            "relevance_score":rel,
                            "virality_score":vir,
                            "combined_score":rel*10 + vir,
                        })
                time.sleep(2)
            except: pass

    # Deduplicate
    seen = set()
    unique = []
    for p in posts:
        if p["title"] not in seen: seen.add(p["title"]); unique.append(p)
    return unique


# ─── INSTAGRAM via Apify ───

def scrape_instagram_accounts_filtered(topic, filter_words, accounts=None):
    if not APIFY_TOKEN: return []
    if accounts is None: accounts = INSTAGRAM_ACCOUNTS[:8]
    print(f"  Instagram: {len(accounts)} accounts, filter: {filter_words[:6]}")
    all_posts = []
    try:
        url = f"https://api.apify.com/v2/acts/apify~instagram-post-scraper/run-sync-get-dataset-items?token={APIFY_TOKEN}"
        resp = httpx.post(url, json={"username": accounts, "resultsLimit": 12}, timeout=180)
        if resp.status_code in [200, 201]:
            items = resp.json()
            if isinstance(items, list):
                print(f"  Apify returned {len(items)} total posts")
                for item in items:
                    caption = item.get("caption","") or ""
                    likes = item.get("likesCount",0) or 0
                    comments = item.get("commentsCount",0) or 0
                    owner = item.get("ownerUsername","") or ""
                    if not caption or len(caption.strip()) < 30: continue

                    rel = calculate_relevance(caption, filter_words)
                    if rel == 0: continue  # Must be at least somewhat relevant

                    first_line = caption.split("\n")[0].strip()
                    title = first_line[:120]+("..." if len(first_line)>120 else "")
                    vir = likes
                    all_posts.append({
                        "src":"instagram",
                        "sub":f"@{owner}" if owner else "Instagram",
                        "title":title,
                        "stats":f"{likes:,} likes · {comments:,} comments",
                        "excerpt":caption[:200].replace("\n"," "),
                        "tag":detect_pattern(caption),
                        "engagement":likes,
                        "relevance_score":rel,
                        "virality_score":vir,
                        "combined_score":rel*10 + vir,
                    })
        else:
            print(f"  Apify error: {resp.status_code}")
    except Exception as e:
        print(f"  Apify exception: {e}")
    return all_posts


# ─── COMBINED + RANKED ───

def scrape_by_topic(topic="grief"):
    print(f"\n{'='*50}\nSCRAPING: {topic}\n{'='*50}")

    filter_words = topic_to_filter_words(topic)
    reddit_queries = topic_to_reddit_queries(topic)
    print(f"Queries: {reddit_queries}")
    print(f"Filter words: {filter_words}")

    reddit_posts = search_reddit(reddit_queries, filter_words)
    ig_posts = scrape_instagram_accounts_filtered(topic, filter_words)
    print(f"Raw: {len(reddit_posts)} Reddit, {len(ig_posts)} Instagram")

    all_posts = reddit_posts + ig_posts

    # Rank by combined score (relevance * 10 + virality)
    all_posts.sort(key=lambda x: x.get("combined_score", 0), reverse=True)

    # Assign rank labels
    for i, p in enumerate(all_posts):
        # Determine rank category
        if p.get("relevance_score", 0) >= 5 and p.get("engagement", 0) >= 100:
            p["rank"] = "highly relevant + viral"
        elif p.get("relevance_score", 0) >= 3:
            p["rank"] = "highly relevant"
        elif p.get("engagement", 0) >= 500:
            p["rank"] = "viral"
        elif p.get("relevance_score", 0) >= 1:
            p["rank"] = "relevant"
        else:
            p["rank"] = "general"

    # Extract hooks from top posts
    hooks = []
    for p in all_posts[:10]:
        hook = extract_hook(p["title"])
        if hook and len(hook) > 15:
            hooks.append(hook)

    # Clean internal fields for frontend
    for p in all_posts:
        p.pop("engagement", None)
        p.pop("combined_score", None)

    # Keep top 25 ranked posts
    top = all_posts[:25]
    print(f"RANKED: {len(top)} posts, {len(hooks)} hooks")

    # Print ranking summary
    ranks = {}
    for p in top:
        r = p.get("rank", "general")
        ranks[r] = ranks.get(r, 0) + 1
    for r, c in sorted(ranks.items(), key=lambda x: -x[1]):
        print(f"  {r}: {c}")

    return top, hooks[:8]


def run_scraper(topic="grief"):
    posts, hooks = scrape_by_topic(topic)
    cache = {
        "scraped_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "topic": topic,
        "total_found": len(posts),
        "posts": posts,
        "hooks": hooks,
    }
    with open(CACHE_FILE, "w") as f: json.dump(cache, f, indent=2)
    print(f"Saved {len(posts)} posts, {len(hooks)} hooks")
    return cache


def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r") as f: return json.load(f)
        except: return None
    return None


if __name__ == "__main__":
    import sys
    run_scraper(sys.argv[1] if len(sys.argv) > 1 else "grief mother loss")
