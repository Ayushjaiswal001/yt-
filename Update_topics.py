"""
update_topics.py — Auto Topic Generator
=========================================
Runs inside GitHub Actions before each video pipeline.
Sources: Google Trends RSS → Reddit → Gemini 1.5 Flash
Output: Overwrites topics.json with 10 fresh trend-based titles.

Usage:
    python update_topics.py --niche tech --count 10
    python update_topics.py --niche health --count 10
    python update_topics.py --niche kids --count 10

Env: GEMINI_API_KEY, TOPICS_FILE (optional, default: topics.json)
"""

import os, sys, json, time, argparse, logging, random
from pathlib import Path
import xml.etree.ElementTree as ET
import urllib.request
import urllib.error

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("TopicEngine")

GEMINI_KEY  = os.environ.get("GEMINI_API_KEY", "")
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
MODELS      = [
    "gemini-1.5-flash",
    "gemini-1.5-pro",
    "gemini-1.5-flash-001",
    "gemini-1.5-pro-001",
    "gemini-1.5-flash-8b"
]

NICHE_CONFIG = {
    "tech": {
        "trends_geo":   "IN",
        "reddit_subs":  ["technology", "artificial", "MachineLearning", "singularity"],
        "yt_searches":  ["AI tools 2025", "ChatGPT tricks", "tech news India"],
        "prompt_hint":  "Tech/AI YouTube channel targeting Indian audience aged 18-35. Topics: AI tools, gadgets, coding tips, tech news.",
        "topics_file":  "topics.json"
    },
    "health": {
        "trends_geo":   "IN",
        "reddit_subs":  ["Biohackers", "longevity", "nutrition", "nootropics"],
        "yt_searches":  ["biohacking tips", "longevity science", "health optimization"],
        "prompt_hint":  "Health/Biohacking YouTube channel targeting educated Indian professionals. Topics: longevity, nutrition, sleep, performance optimization.",
        "topics_file":  "biohacker_topics.json"
    },
    "kids": {
        "trends_geo":   "IN",
        "reddit_subs":  ["IndianParenting", "hindi", "learnhindi"],
        "yt_searches":  ["Hindi stories for kids", "Panchatantra 2025", "moral stories Hindi"],
        "prompt_hint":  "Kids Hindi Stories YouTube channel for Indian children aged 3-10. Topics: Panchatantra stories, moral lessons, educational content in Hindi.",
        "topics_file":  "kids_topics.json"
    }
}

FALLBACK_TOPICS = {
    "tech":   ["Top 10 AI Tools Taking Over India", "ChatGPT Secret Features Nobody Uses",
               "Best Free AI Tools for Students India", "How AI is Changing Indian Jobs",
               "5 Gadgets Under 1000 Rupees 2025", "Google vs ChatGPT Which is Better",
               "AI Makes 10000 Per Day Passive Income", "Best Coding Languages to Learn 2025",
               "How to Use Gemini AI Free in India", "Top 5 Tech Skills for High Paying Jobs"],
    "health": ["10 Biohacks That Doubled My Energy", "Science of Intermittent Fasting India",
               "Top Supplements for Brain Power", "Why Indians Sleep Less and How to Fix",
               "Cold Shower Benefits 30 Day Challenge", "Best Foods for Longevity Science",
               "5 Breathing Techniques for Anxiety", "How to Optimize Your Morning Routine",
               "Blue Light and Sleep Quality Truth", "Gut Health Complete Guide India 2025"],
    "kids":   ["Panchatantra Ki Sarvashresth Kahaniyan", "Akbar Birbal Ki Mazedar Kahani",
               "Tenali Raman Ki Chalak Kahaniyan", "Moral Stories for Kids Hindi 2025",
               "Jadui Chirag Ki Kahaani Hindi", "Honest Woodcutter Story Hindi",
               "Greedy King Moral Story Hindi", "Clever Farmer Hindi Kahani",
               "Friendship Story for Kids Hindi", "Brave Girl Hindi Moral Story"]
}


# ── HTTP helper (no external deps) ───────────────────────────────────────
def http_get(url: str, headers: dict = None, timeout: int = 10) -> str:
    """Simple GET using stdlib urllib."""
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "YTAutoPilot/2.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="ignore")
    except Exception as e:
        log.warning(f"GET {url[:60]}... failed: {e}")
        return ""


def http_post(url: str, payload: dict, headers: dict = None, timeout: int = 30) -> dict:
    """Simple POST using stdlib urllib."""
    import json as _json
    data = _json.dumps(payload).encode()
    h = {"Content-Type": "application/json", "User-Agent": "YTAutoPilot/2.0"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return _json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        log.warning(f"POST {url[:60]}... HTTP {e.code}: {e.read().decode()[:200]}")
        return {}
    except Exception as e:
        log.warning(f"POST failed: {e}")
        return {}


# ── Step 1: Fetch Google Trends ───────────────────────────────────────────
def fetch_google_trends(geo: str = "IN") -> list[str]:
    """Fetch top 10 trending searches from Google Trends RSS."""
    url  = f"https://trends.google.com/trends/trendingsearches/daily/rss?geo={geo}"
    data = http_get(url, timeout=15)
    if not data:
        return []
    try:
        root  = ET.fromstring(data)
        items = root.findall(".//item/title")
        trends = [t.text.strip() for t in items if t.text][:10]
        log.info(f"[TRENDS] {geo}: {trends[:3]}...")
        return trends
    except Exception as e:
        log.warning(f"[TRENDS] Parse error: {e}")
        return []


# ── Step 2: Fetch Reddit hot posts ────────────────────────────────────────
def fetch_reddit_signals(subreddits: list[str]) -> list[str]:
    """Fetch hot post titles from subreddits."""
    titles = []
    for sub in subreddits[:3]:  # max 3 subs to avoid rate limit
        url  = f"https://www.reddit.com/r/{sub}/hot.json?limit=5"
        data = http_get(url, headers={"User-Agent": "YTAutoPilot/2.0 bot"}, timeout=10)
        if not data:
            continue
        try:
            posts = json.loads(data)
            for post in posts.get("data", {}).get("children", []):
                title = post.get("data", {}).get("title", "")
                if title and len(title) > 10:
                    titles.append(title)
        except Exception as e:
            log.warning(f"[REDDIT] r/{sub} parse error: {e}")
        time.sleep(1)  # rate limit respect
    log.info(f"[REDDIT] Got {len(titles)} post signals")
    return titles[:15]


# ── Step 3: Call Gemini ───────────────────────────────────────────────────
def gemini_generate_topics(
    niche: str,
    trend_signals: list[str],
    reddit_signals: list[str],
    count: int,
    config: dict
) -> list[str]:
    """Ask Gemini to generate trend-based video topics."""
    if not GEMINI_KEY:
        log.error("[GEMINI] No GEMINI_API_KEY set — using fallbacks")
        return []

    trend_str  = ", ".join(trend_signals[:8]) if trend_signals else "general trending topics India"
    reddit_str = " | ".join(reddit_signals[:8]) if reddit_signals else "popular tech discussions"

    prompt = f"""You are a viral YouTube content strategist. Generate {count} high-CTR video titles.

Channel type: {config['prompt_hint']}
Today's Google Trends India: {trend_str}
Reddit hot discussions: {reddit_str}

STRICT RULES:
1. Every title MUST reference or be inspired by the trending signals above
2. Each title 45-65 characters max
3. Use number hooks (Top 5, 10 Ways, 3 Secrets...) or How-To format
4. High curiosity gap — make viewers NEED to click
5. No lies, no pure clickbait
6. For kids/Hindi niche: mix Hindi and English naturally
7. Return ONLY a JSON array, no markdown, no explanation

Return exactly {count} titles as JSON array:
["title 1", "title 2", "title 3", ...]"""

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": 1024,
            "temperature":     0.85,
            "topP":            0.95
        }
    }

    for model in MODELS:
        url  = f"{GEMINI_BASE}/models/{model}:generateContent?key={GEMINI_KEY}"
        resp = http_post(url, payload, timeout=30)
        if not resp:
            continue

        try:
            raw = resp["candidates"][0]["content"]["parts"][0]["text"].strip()
            # Strip markdown fences if present
            if "```" in raw:
                for chunk in raw.split("```"):
                    chunk = chunk.strip()
                    if chunk.startswith("json"):
                        chunk = chunk[4:].strip()
                    if chunk.startswith("["):
                        raw = chunk
                        break
            # Find the JSON array
            start = raw.find("[")
            end   = raw.rfind("]")
            if start != -1 and end > start:
                raw = raw[start:end+1]
            topics = json.loads(raw)
            topics = [str(t).strip() for t in topics if t and len(str(t)) > 10][:count]
            if topics:
                log.info(f"[GEMINI] {model} → {len(topics)} topics generated")
                return topics
        except Exception as e:
            log.warning(f"[GEMINI] {model} parse error: {e}")
            if "429" in str(resp):
                time.sleep(30)

    log.warning("[GEMINI] All models failed")
    return []


# ── Step 4: Load + merge + save topics.json ───────────────────────────────
def update_topics_file(new_topics: list[str], topics_file: str, niche: str) -> None:
    """Merge new topics with existing, keep newest 50, save to file."""
    path = Path(topics_file)

    existing = []
    if path.exists():
        try:
            data     = json.loads(path.read_text())
            existing = data if isinstance(data, list) else data.get("topics", [])
            log.info(f"[TOPICS] Loaded {len(existing)} existing topics")
        except Exception as e:
            log.warning(f"[TOPICS] Could not load existing: {e}")

    # Merge: new topics first, then existing (deduped), keep 50 max
    seen   = set()
    merged = []
    for t in new_topics + existing:
        t_clean = t.strip()
        if t_clean and t_clean.lower() not in seen:
            seen.add(t_clean.lower())
            merged.append(t_clean)
        if len(merged) >= 50:
            break

    path.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    log.info(f"[TOPICS] Saved {len(merged)} topics to {topics_file}")
    log.info(f"[TOPICS] Top 3 new: {merged[:3]}")


# ── Main ──────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Auto-update YouTube topics from trends")
    parser.add_argument("--niche",  default="tech",  choices=["tech", "health", "kids"])
    parser.add_argument("--count",  default=10,      type=int)
    parser.add_argument("--output", default="",      help="Override output file path")
    args = parser.parse_args()

    cfg        = NICHE_CONFIG[args.niche]
    out_file   = args.output or os.environ.get("TOPICS_FILE", cfg["topics_file"])
    count      = args.count

    log.info("=" * 55)
    log.info(f"  Auto Topic Engine | {args.niche.upper()} | count={count}")
    log.info("=" * 55)

    # Step 1: Google Trends
    log.info("[STEP 1/4] Fetching Google Trends India...")
    trends_in  = fetch_google_trends("IN")
    trends_us  = fetch_google_trends("US")
    all_trends = list(dict.fromkeys(trends_in + trends_us))  # deduped
    log.info(f"[STEP 1/4] {len(all_trends)} trend signals fetched")

    # Step 2: Reddit
    log.info("[STEP 2/4] Fetching Reddit signals...")
    reddit_signals = fetch_reddit_signals(cfg["reddit_subs"])

    # Step 3: Gemini
    log.info("[STEP 3/4] Calling Gemini for topic generation...")
    new_topics = gemini_generate_topics(
        niche          = args.niche,
        trend_signals  = all_trends,
        reddit_signals = reddit_signals,
        count          = count,
        config         = cfg
    )

    # Fallback if Gemini failed
    if not new_topics:
        log.warning("[STEP 3/4] Gemini failed — using curated fallbacks")
        fallbacks  = FALLBACK_TOPICS.get(args.niche, [])
        new_topics = random.sample(fallbacks, min(count, len(fallbacks)))

    # Step 4: Save
    log.info(f"[STEP 4/4] Updating {out_file}...")
    update_topics_file(new_topics, out_file, args.niche)

    log.info("=" * 55)
    log.info(f"  DONE — {len(new_topics)} topics updated in {out_file}")
    log.info("=" * 55)

    # Print for GitHub Actions logs
    print("\n📊 TOPICS GENERATED:")
    for i, t in enumerate(new_topics, 1):
        print(f"  {i}. {t}")


if __name__ == "__main__":
    main()
