"""
Viral Content Scraper - Reddit + Instagram Hashtags via Apify
Hashtag-first approach: recommends hashtags, scrapes them, ranks by relevance + virality
"""

import httpx
import json
import os
import time
import re

APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "")
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

SUBREDDITS = ["GriefSupport", "MotherlessDaughters", "CPTSD", "raisedbynarcissists", "emotionalneglect", "EstrangedAdultKids", "ChildrenofDeadParents", "griefsupport", "ptsd", "attachment_theory"]

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

# Hashtag library organized by therapy topic
HASHTAG_LIBRARY = {
    "grief": ["grief", "griefjourney", "griefandloss", "grieving", "griefquotes", "griefrecovery", "griefwork", "griefawareness"],
    "mother": ["motherlessdaughters", "motherhunger", "motherloss", "losingamom", "momgrief", "motherwound", "maternalloss"],
    "mom": ["motherlessdaughters", "motherloss", "losingamom", "momgrief", "motherwound"],
    "parent": ["parentloss", "losingaparent", "griefandloss", "adultorphan", "parentaldeath"],
    "trauma": ["trauma", "traumahealing", "childhoodtrauma", "cptsd", "traumatherapy", "traumarecovery", "traumainformed", "healingtrauma"],
    "attachment": ["attachmenttheory", "anxiousattachment", "attachmentwounds", "attachmentstyle", "secureattachment", "avoidantattachment", "disorganizedattachment"],
    "emdr": ["emdr", "emdrtherapy", "emdrhealing", "traumatherapy"],
    "equine": ["equinetherapy", "horsetherapy", "healingwithhorses", "equineassisted", "horsesofinstagram"],
    "narcissist": ["narcissisticmother", "raisedbynarcissists", "toxicparents", "narcissisticabuse", "narcissisticparent"],
    "shame": ["toxicshame", "shameresilience", "shamework", "innercritic", "worthiness"],
    "neglect": ["emotionalneglect", "childhoodneglect", "invisiblechild", "emotionalabuse"],
    "body": ["somatichealing", "somatictherapy", "nervoussystem", "nervoussystemregulation", "bodykeepsthescore", "polyvagaltheory"],
    "somatic": ["somatichealing", "somatictherapy", "somaticexperiencing", "bodywork", "nervoussystemregulation"],
    "healing": ["healingjourney", "innerchildhealing", "therapyworks", "mentalhealth", "mentalhealthawareness"],
    "therapy": ["therapistsofinstagram", "therapyiscool", "therapyworks", "mentalhealththerapist", "onlinetherapy"],
    "inner child": ["innerchild", "innerchildhealing", "innerchildwork", "reparenting"],
    "anxiety": ["anxietyrelief", "anxietyhelp", "anxietytips", "healinganxiety"],
    "nervous system": ["nervoussystem", "nervoussystemregulation", "polyvagaltheory", "vagusnerve", "dysregulation"],
    "boundaries": ["boundaries", "healthyboundaries", "boundarywork", "settingboundaries"],
    "codependency": ["codependency", "codependencyrecovery", "codependent", "peoplepleasing"],
    "dissociation": ["dissociation", "freezeresponse", "shutdown", "emotionalnumbness"],
    "generational": ["generationaltrauma", "intergenerationaltrauma", "breakingthecycle", "generationalhealing"],
}

# Base therapy hashtags always included
BASE_THERAPY_HASHTAGS = ["therapistsofinstagram", "therapyworks", "mentalhealth", "healingjourney"]

CACHE_FILE = "viral_cache.json"


def detect_pattern(text):
    text_lower = text.lower()
    scores = {}
    for pattern, keywords in PATTERNS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[pattern] = score
    return max(scores, key=scores.get) if scores else "general grief"


def extract_hook(title):
    text = title.strip()
    if len(text) > 80:
        text = text[:77] + "..."
    return text


def recommend_hashtags(topic):
    """Given a topic string, return recommended hashtags to search."""
    topic_lower = topic.lower()
    recommended = set()

    # Match topic words against hashtag library
    for key, tags in HASHTAG_LIBRARY.items():
        if key in topic_lower:
            recommended.update(tags)

    # If nothing matched, use broad therapy hashtags
    if not recommended:
        recommended.update(["grief", "trauma", "healingjourney", "therapistsofinstagram", "mentalhealth", "childhoodtrauma"])

    # Always include base therapy hashtags
    recommended.update(BASE_THERAPY_HASHTAGS)

    # Cap at 15 hashtags
    return list(recommended)[:15]


def topic_to_reddit_queries(topic):
    skip = {"the", "and", "that", "this", "with", "from", "your", "when", "what", "have", "been", "they", "their", "there", "about", "just", "like", "does", "anyone", "else", "feel", "still", "know", "talks", "talk", "nobody", "ever", "really", "very", "much", "also", "into", "not", "you", "can", "but", "for", "how", "why", "all", "was", "were", "are", "its"}
    words = [w for w in topic.lower().split() if w not in skip and len(w) > 2]
    primary = " ".join(words[:4]) if words else "grief"
    queries = [primary]
    tl = topic.lower()
    if "mother" in tl or "mom" in tl:
        queries += ["losing mother grief", "motherless daughter"]
    if "trauma" in tl:
        queries += ["childhood trauma healing", "trauma nobody talks about"]
    if "attachment" in tl:
        queries += ["attachment wound relationships", "anxious attachment"]
    if "guilt" in tl:
        queries += ["grief guilt laughing", "survivor guilt"]
    if "alive" in tl:
        queries += ["grieving parent still alive", "estranged parent grief"]
    if "neglect" in tl:
        queries += ["emotional neglect childhood", "invisible child"]
    if "shame" in tl:
        queries += ["shame childhood trauma", "toxic shame healing"]
    if "emdr" in tl:
        queries += ["emdr therapy experience", "emdr changed my life"]
    if "horse" in tl or "equine" in tl:
        queries += ["equine therapy grief", "horse therapy healing"]
    if "body" in tl or "somatic" in tl:
        queries += ["body keeps the score", "somatic healing trauma"]
    if len(queries) == 1:
        queries += ["grief trauma healing", "childhood wounds adult"]
    return queries[:5]


def topic_to_filter_words(topic):
    skip = {"the", "and", "that", "this", "with", "from", "your", "when", "what", "have", "been", "they", "their", "there", "about", "just", "like", "does", "anyone", "feel", "still", "know", "talks", "talk", "nobody", "ever", "really", "very", "into", "not", "you", "can", "but", "for", "how", "why", "all"}
    words = [w for w in topic.lower().split() if w not in skip and len(w) > 2]
    extras = []
    tl = topic.lower()
    if "mother" in tl or "mom" in tl:
        extras += ["mother", "mom", "parent", "daughter", "maternal", "motherless"]
    if "trauma" in tl:
        extras += ["trauma", "wound", "hurt", "pain", "heal", "traumatic", "ptsd", "cptsd"]
    if "grief" in tl:
        extras += ["grief", "loss", "grieve", "lost", "miss", "grieving", "mourn"]
    if "attachment" in tl:
        extras += ["attachment", "attach", "bond", "connect", "secure", "insecure", "anxious", "avoidant"]
    if "shame" in tl:
        extras += ["shame", "worth", "enough", "worthy", "deserving"]
    if "neglect" in tl:
        extras += ["neglect", "absent", "invisible", "unseen", "ignored", "overlooked"]
    if "body" in tl or "somatic" in tl:
        extras += ["body", "somatic", "nervous", "system", "sensation", "feel"]
    return list(set(words + extras))


def calculate_relevance(text, filter_words):
    """Score how relevant a post is to the topic."""
    text_lower = text.lower()
    score = 0
    for word in filter_words:
        count = text_lower.count(word)
        if count > 0:
            score += count
    for i in range(len(filter_words)):
        for j in range(i + 1, min(i + 3, len(filter_words))):
            phrase = filter_words[i] + " " + filter_words[j]
            if phrase in text_lower:
                score += 3
    return score


# ─── REDDIT ───

def search_reddit(queries, filter_words, limit=15):
    posts = []
    relevant_subs = set(s.lower() for s in SUBREDDITS)
    relevant_subs.update(["grief", "trauma", "ptsd", "mentalhealth", "therapy", "loss", "bereavement", "anxiety", "depression", "selfhelp"])

    for query in queries:
        try:
            print(f"  Reddit: '{query}'")
            url = f"https://www.reddit.com/search.json?q={query}&sort=relevance&t=month&limit={limit}"
            resp = httpx.get(url, headers=HEADERS, timeout=15, follow_redirects=True)
            if resp.status_code == 200:
                for child in resp.json().get("data", {}).get("children", []):
                    p = child.get("data", {})
                    if p.get("stickied"):
                        continue
                    sub = p.get("subreddit", "")
                    if not any(r in sub.lower() for r in relevant_subs):
                        continue
                    ups = p.get("ups", 0)
                    if ups < 3:
                        continue
                    title = p.get("title", "")
                    selftext = (p.get("selftext", "") or "")[:400]
                    full_text = title + " " + selftext
                    rel = calculate_relevance(full_text, filter_words)
                    vir = ups * 5
                    posts.append({
                        "src": "reddit", "sub": f"r/{sub}", "title": title,
                        "stats": f"{ups:,} upvotes . {p.get('num_comments', 0):,} comments",
                        "excerpt": selftext[:200] if selftext else title,
                        "tag": detect_pattern(full_text),
                        "engagement": ups,
                        "relevance_score": rel,
                        "virality_score": vir,
                        "combined_score": rel * 10 + vir,
                    })
            time.sleep(2)
        except Exception as e:
            print(f"  Reddit error: {e}")

    # Fallback if low results
    if len(posts) < 5:
        print("  Reddit fallback: top posts from subs...")
        for sub in SUBREDDITS[:6]:
            try:
                url = f"https://www.reddit.com/r/{sub}/top.json?t=week&limit=8"
                resp = httpx.get(url, headers=HEADERS, timeout=15, follow_redirects=True)
                if resp.status_code == 200:
                    for child in resp.json().get("data", {}).get("children", []):
                        p = child.get("data", {})
                        if p.get("stickied"):
                            continue
                        ups = p.get("ups", 0)
                        if ups < 3:
                            continue
                        title = p.get("title", "")
                        selftext = (p.get("selftext", "") or "")[:400]
                        full_text = title + " " + selftext
                        rel = calculate_relevance(full_text, filter_words)
                        vir = ups * 5
                        posts.append({
                            "src": "reddit", "sub": f"r/{sub}", "title": title,
                            "stats": f"{ups:,} upvotes . {p.get('num_comments', 0):,} comments",
                            "excerpt": selftext[:200] if selftext else title,
                            "tag": detect_pattern(full_text),
                            "engagement": ups,
                            "relevance_score": rel,
                            "virality_score": vir,
                            "combined_score": rel * 10 + vir,
                        })
                time.sleep(2)
            except:
                pass

    # Deduplicate
    seen = set()
    unique = []
    for p in posts:
        if p["title"] not in seen:
            seen.add(p["title"])
            unique.append(p)
    return unique


# ─── INSTAGRAM via Apify HASHTAG SCRAPER ───

def scrape_instagram_hashtags(hashtags, filter_words, min_likes=1000):
    """Scrape Instagram by hashtags. Only returns posts with 1000+ likes."""
    if not APIFY_TOKEN:
        print("  No APIFY_TOKEN set, skipping Instagram")
        return []
    if not hashtags:
        return []

    print(f"  Instagram hashtags: {hashtags[:10]}")
    all_posts = []

    try:
        # Use Apify Instagram Hashtag Scraper
        url = f"https://api.apify.com/v2/acts/apify~instagram-hashtag-scraper/run-sync-get-dataset-items?token={APIFY_TOKEN}"
        payload = {
            "hashtags": hashtags[:10],
            "resultsLimit": 30,
            "resultsType": "posts",
        }
        print(f"  Calling Apify hashtag scraper...")
        resp = httpx.post(url, json=payload, timeout=180)
        print(f"  Apify response: {resp.status_code}")

        if resp.status_code in [200, 201]:
            items = resp.json()
            if isinstance(items, list):
                print(f"  Apify returned {len(items)} total posts")
                for item in items:
                    # Try multiple field names (different Apify actors use different names)
                    caption = item.get("caption", "") or item.get("text", "") or ""
                    likes = item.get("likesCount", 0) or item.get("likes", 0) or 0
                    comments = item.get("commentsCount", 0) or item.get("comments", 0) or 0
                    owner = item.get("ownerUsername", "") or item.get("username", "") or ""

                    # Try nested owner object
                    if not owner and isinstance(item.get("owner"), dict):
                        owner = item["owner"].get("username", "")

                    # Skip posts under 1000 likes
                    if likes < min_likes:
                        continue

                    # Skip empty captions
                    if not caption or len(caption.strip()) < 20:
                        continue

                    # Calculate relevance to topic
                    rel = calculate_relevance(caption, filter_words)

                    first_line = caption.split("\n")[0].strip()
                    title = first_line[:120] + ("..." if len(first_line) > 120 else "")

                    all_posts.append({
                        "src": "instagram",
                        "sub": f"@{owner}" if owner else "Instagram",
                        "title": title,
                        "stats": f"{likes:,} likes . {comments:,} comments",
                        "excerpt": caption[:200].replace("\n", " "),
                        "tag": detect_pattern(caption),
                        "engagement": likes,
                        "relevance_score": rel,
                        "virality_score": likes,
                        "combined_score": rel * 10 + likes,
                    })
            else:
                print(f"  Apify returned non-list: {type(items)}")
        else:
            print(f"  Apify error: {resp.status_code}")
            try:
                print(f"  Apify body: {resp.text[:500]}")
            except:
                pass
    except Exception as e:
        print(f"  Apify exception: {e}")

    print(f"  Instagram after 1000+ filter: {len(all_posts)} posts")
    return all_posts


# ─── COMBINED + RANKED ───

def scrape_by_topic(topic="grief", hashtags=None):
    """Main scrape function. If hashtags provided, use those. Otherwise auto-recommend."""
    print(f"\n{'=' * 50}\nSCRAPING: {topic}\n{'=' * 50}")

    filter_words = topic_to_filter_words(topic)
    reddit_queries = topic_to_reddit_queries(topic)

    # Use provided hashtags or auto-recommend
    if not hashtags:
        hashtags = recommend_hashtags(topic)

    print(f"Queries: {reddit_queries}")
    print(f"Hashtags: {hashtags}")
    print(f"Filter words: {filter_words}")

    reddit_posts = search_reddit(reddit_queries, filter_words)
    ig_posts = scrape_instagram_hashtags(hashtags, filter_words)
    print(f"Raw: {len(reddit_posts)} Reddit, {len(ig_posts)} Instagram")

    all_posts = reddit_posts + ig_posts

    # Sort: topic-matching posts float to top, then by virality
    all_posts.sort(key=lambda x: x.get("combined_score", 0), reverse=True)

    # Assign rank labels
    for p in all_posts:
        rel = p.get("relevance_score", 0)
        eng = p.get("engagement", 0)
        if rel >= 5 and eng >= 100:
            p["rank"] = "highly relevant + viral"
        elif rel >= 3:
            p["rank"] = "highly relevant"
        elif eng >= 500:
            p["rank"] = "viral"
        elif rel >= 1:
            p["rank"] = "relevant"
        else:
            p["rank"] = "general"

    # Extract hooks from top posts
    hooks = []
    for p in all_posts[:10]:
        hook = extract_hook(p["title"])
        if hook and len(hook) > 15:
            hooks.append(hook)

    # Clean internal fields
    for p in all_posts:
        p.pop("engagement", None)
        p.pop("combined_score", None)

    top = all_posts[:30]
    print(f"RANKED: {len(top)} posts, {len(hooks)} hooks")

    ranks = {}
    for p in top:
        r = p.get("rank", "general")
        ranks[r] = ranks.get(r, 0) + 1
    for r, c in sorted(ranks.items(), key=lambda x: -x[1]):
        print(f"  {r}: {c}")

    return top, hooks[:8]


def run_scraper(topic="grief", hashtags=None):
    posts, hooks = scrape_by_topic(topic, hashtags)
    cache = {
        "scraped_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "topic": topic,
        "total_found": len(posts),
        "posts": posts,
        "hooks": hooks,
    }
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)
    print(f"Saved {len(posts)} posts, {len(hooks)} hooks")
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
    run_scraper(sys.argv[1] if len(sys.argv) > 1 else "grief mother loss")
