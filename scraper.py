"""
Viral Content Scraper - Reddit + Instagram TOP posts via Apify
Uses instagram-scraper with hashtag URLs to get TOP posts (not recent)
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
    topic_lower = topic.lower()
    recommended = set()
    for key, tags in HASHTAG_LIBRARY.items():
        if key in topic_lower:
            recommended.update(tags)
    if not recommended:
        recommended.update(["grief", "trauma", "healingjourney", "therapistsofinstagram", "mentalhealth", "childhoodtrauma"])
    recommended.update(BASE_THERAPY_HASHTAGS)
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


# --- REDDIT ---

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
            except Exception as e:
                print(f"  Reddit fallback error for r/{sub}: {e}")

    seen = set()
    unique = []
    for p in posts:
        if p["title"] not in seen:
            seen.add(p["title"])
            unique.append(p)
    return unique


# --- INSTAGRAM via Apify - TOP POSTS from hashtag pages ---

def scrape_instagram_top_posts(hashtags, filter_words, min_likes=500):
    """
    Scrape Instagram TOP posts by feeding hashtag page URLs to apify~instagram-scraper.
    Instagram explore/tags/ page shows top/popular posts by default.
    """
    if not APIFY_TOKEN:
        print("  No APIFY_TOKEN set, skipping Instagram")
        return []
    if not hashtags:
        return []

    hashtag_urls = [f"https://www.instagram.com/explore/tags/{tag}/" for tag in hashtags[:10]]
    print(f"  Instagram: scraping TOP posts from {len(hashtag_urls)} hashtag pages")
    print(f"  URLs: {hashtag_urls[:3]}...")

    all_posts = []

    try:
        url = f"https://api.apify.com/v2/acts/apify~instagram-scraper/run-sync-get-dataset-items?token={APIFY_TOKEN}"
        payload = {
            "directUrls": hashtag_urls,
            "resultsLimit": 20,
            "resultsType": "posts",
            "searchType": "hashtag",
        }
        print(f"  Calling Apify instagram-scraper with hashtag URLs...")
        resp = httpx.post(url, json=payload, timeout=240)
        print(f"  Apify response: {resp.status_code}")

        if resp.status_code in [200, 201]:
            items = resp.json()
            if isinstance(items, list):
                print(f"  Apify returned {len(items)} total posts from hashtag pages")

                for item in items:
                    caption = item.get("caption", "") or item.get("text", "") or item.get("alt", "") or ""
                    likes = item.get("likesCount", 0) or item.get("likes", 0) or 0
                    comments = item.get("commentsCount", 0) or item.get("comments", 0) or 0
                    owner = item.get("ownerUsername", "") or item.get("username", "") or ""

                    if not owner and isinstance(item.get("owner"), dict):
                        owner = item["owner"].get("username", "")
                    if not caption and isinstance(item.get("edge_media_to_caption"), dict):
                        edges = item["edge_media_to_caption"].get("edges", [])
                        if edges:
                            caption = edges[0].get("node", {}).get("text", "")
                    if not likes and isinstance(item.get("edge_liked_by"), dict):
                        likes = item["edge_liked_by"].get("count", 0)
                    if not likes and isinstance(item.get("edge_media_preview_like"), dict):
                        likes = item["edge_media_preview_like"].get("count", 0)

                    if likes < min_likes:
                        continue
                    if not caption or len(caption.strip()) < 20:
                        continue

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

        # Fallback to hashtag-scraper with higher limit if nothing found
        if len(all_posts) == 0:
            print("  Fallback: trying instagram-hashtag-scraper with higher limit...")
            url2 = f"https://api.apify.com/v2/acts/apify~instagram-hashtag-scraper/run-sync-get-dataset-items?token={APIFY_TOKEN}"
            payload2 = {
                "hashtags": hashtags[:10],
                "resultsLimit": 100,
                "resultsType": "posts",
            }
            resp2 = httpx.post(url2, json=payload2, timeout=240)
            print(f"  Fallback Apify response: {resp2.status_code}")

            if resp2.status_code in [200, 201]:
                items2 = resp2.json()
                if isinstance(items2, list):
                    print(f"  Fallback returned {len(items2)} posts")
                    for item in items2:
                        caption = item.get("caption", "") or item.get("text", "") or ""
                        likes = item.get("likesCount", 0) or item.get("likes", 0) or 0
                        comments = item.get("commentsCount", 0) or item.get("comments", 0) or 0
                        owner = item.get("ownerUsername", "") or item.get("username", "") or ""
                        if not owner and isinstance(item.get("owner"), dict):
                            owner = item["owner"].get("username", "")
                        if likes < 100:
                            continue
                        if not caption or len(caption.strip()) < 20:
                            continue
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

    except Exception as e:
        print(f"  Apify exception: {e}")
        import traceback
        traceback.print_exc()

    seen = set()
    unique = []
    for p in all_posts:
        if p["title"] not in seen:
            seen.add(p["title"])
            unique.append(p)

    print(f"  Instagram final: {len(unique)} posts (500+ likes from TOP posts)")
    return unique


# --- COMBINED + RANKED ---

def scrape_by_topic(topic="grief", hashtags=None):
    print(f"\n{'=' * 50}\nSCRAPING: {topic}\n{'=' * 50}")

    filter_words = topic_to_filter_words(topic)
    reddit_queries = topic_to_reddit_queries(topic)

    if not hashtags:
        hashtags = recommend_hashtags(topic)

    print(f"Queries: {reddit_queries}")
    print(f"Hashtags: {hashtags}")
    print(f"Filter words: {filter_words}")

    reddit_posts = search_reddit(reddit_queries, filter_words)
    ig_posts = scrape_instagram_top_posts(hashtags, filter_words)
    print(f"Raw: {len(reddit_posts)} Reddit, {len(ig_posts)} Instagram")

    all_posts = reddit_posts + ig_posts
    all_posts.sort(key=lambda x: x.get("combined_score", 0), reverse=True)

    for p in all_posts:
        rel = p.get("relevance_score", 0)
        eng = p.get("engagement", 0)
        if rel >= 5 and eng >= 500:
            p["rank"] = "highly relevant + viral"
        elif rel >= 3:
            p["rank"] = "highly relevant"
        elif eng >= 1000:
            p["rank"] = "viral"
        elif rel >= 1:
            p["rank"] = "relevant"
        else:
            p["rank"] = "general"

    hooks = []
    for p in all_posts[:10]:
        hook = extract_hook(p["title"])
        if hook and len(hook) > 15:
            hooks.append(hook)

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
