#!/usr/bin/env python3
"""Generate topics — works with or without Gemini API."""
import argparse, json, os, sys, random, datetime

BUILT_IN = {
    "tech": [
        {"topic": "AI Agents Are Replacing SaaS", "description": "How autonomous AI agents disrupt traditional software", "tags": ["ai", "saas", "automation", "tech", "future"]},
        {"topic": "Why Rust Is Taking Over Systems Programming", "description": "Rust memory safety winning over C++ devs", "tags": ["rust", "programming", "systems", "coding", "developer"]},
        {"topic": "The Hidden Cost of Cloud Computing in 2026", "description": "Cloud bills exploding — strategies to cut costs", "tags": ["cloud", "aws", "costs", "devops", "infrastructure"]},
        {"topic": "Edge Computing vs Cloud — Which Wins", "description": "Processing data closer to users", "tags": ["edge", "cloud", "latency", "iot", "computing"]},
        {"topic": "How GitHub Copilot Changed Coding Forever", "description": "AI pair programming real impact", "tags": ["copilot", "ai", "coding", "github", "productivity"]},
        {"topic": "Quantum Computing Breakthroughs Explained", "description": "Latest quantum milestones in plain English", "tags": ["quantum", "computing", "physics", "future", "science"]},
        {"topic": "Why Every Developer Should Learn Docker", "description": "Containerization skills are table stakes", "tags": ["docker", "containers", "devops", "development", "skills"]},
        {"topic": "The Rise of Local AI on Your Phone", "description": "On-device AI models getting good", "tags": ["ai", "local", "mobile", "llm", "privacy"]},
        {"topic": "Web Assembly Is Revolutionizing the Web", "description": "WASM bringing desktop performance to browsers", "tags": ["wasm", "web", "performance", "browsers", "coding"]},
        {"topic": "5 Programming Languages to Learn in 2026", "description": "Languages hiring managers want", "tags": ["programming", "career", "languages", "jobs", "coding"]},
    ],
    "kids": [
        {"topic": "The Magical Forest Where Animals Talk", "description": "A brave rabbit discovers a talking forest", "tags": ["kids", "story", "animals", "magic", "adventure"]},
        {"topic": "The Little Star Who Wanted to Shine Brightest", "description": "A tiny star learns kindness shines brighter", "tags": ["kids", "moral", "stars", "kindness", "bedtime"]},
        {"topic": "The Elephant Who Forgot His Birthday", "description": "Friends help an elephant remember", "tags": ["kids", "elephant", "birthday", "friendship", "funny"]},
        {"topic": "Why the Moon Changes Shape Every Night", "description": "Fun moon phases explanation for kids", "tags": ["kids", "science", "moon", "educational", "space"]},
        {"topic": "The Brave Little Boat on the Big Ocean", "description": "A small boat on a big adventure", "tags": ["kids", "adventure", "ocean", "courage", "boats"]},
        {"topic": "The Fox and the Clever Crow", "description": "Classic Panchatantra tale retold", "tags": ["kids", "panchatantra", "fable", "indian", "moral"]},
        {"topic": "The Garden Where Vegetables Come Alive", "description": "Veggies go on a midnight adventure", "tags": ["kids", "vegetables", "garden", "funny", "healthy"]},
        {"topic": "The Rainbow Bridge Between Two Villages", "description": "Two villages learn to share", "tags": ["kids", "sharing", "rainbow", "villages", "cooperation"]},
        {"topic": "The Little Train That Climbed the Mountain", "description": "Perseverance story of a brave train", "tags": ["kids", "train", "mountain", "perseverance", "hindi"]},
        {"topic": "How Butterflies Get Their Beautiful Colors", "description": "Magical origin story of butterfly wings", "tags": ["kids", "butterflies", "colors", "nature", "educational"]},
    ],
    "health": [
        {"topic": "Cold Plunge Science — What Happens to Your Body", "description": "Real science behind cold water immersion", "tags": ["health", "coldplunge", "science", "biohacking", "recovery"]},
        {"topic": "Zone 2 Cardio — Exercise Doctors Recommend Most", "description": "Why low-intensity cardio is the longevity sweet spot", "tags": ["health", "cardio", "exercise", "longevity", "fitness"]},
        {"topic": "Sleep Optimization — 5 Habits Backed by Research", "description": "Evidence-based sleep improvement", "tags": ["health", "sleep", "habits", "research", "wellness"]},
        {"topic": "Intermittent Fasting New Research", "description": "Latest studies on fasting and metabolism", "tags": ["health", "fasting", "metabolism", "research", "nutrition"]},
        {"topic": "Gut Microbiome — The Second Brain", "description": "How gut bacteria influence mood and weight", "tags": ["health", "gut", "microbiome", "nutrition", "science"]},
        {"topic": "Morning Sunlight — The Free Health Hack", "description": "Why 10 min of morning sun changes your day", "tags": ["health", "sunlight", "circadian", "energy", "free"]},
        {"topic": "Creatine Beyond Muscle — Brain Benefits", "description": "New research on creatine for cognition", "tags": ["health", "creatine", "brain", "supplements", "cognition"]},
        {"topic": "Walking 10K Steps — Overhyped or Underrated", "description": "What research says about daily steps", "tags": ["health", "walking", "steps", "exercise", "research"]},
        {"topic": "Sauna Benefits — Heat Therapy for Longevity", "description": "Finnish studies on sauna and heart health", "tags": ["health", "sauna", "longevity", "heart", "therapy"]},
        {"topic": "Magnesium — The Most Deficient Mineral", "description": "Why most people are deficient", "tags": ["health", "magnesium", "minerals", "supplements", "deficiency"]},
    ],
}

def try_gemini(niche, count):
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key: return None
    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key, transport="rest")
        model = genai.GenerativeModel("gemini-1.5-flash")
        prompt = f'Generate {count} YouTube topics for "{niche}" niche. Return JSON array with "topic", "description", "tags" (5 tags). Raw JSON only.'
        resp = model.generate_content(prompt)
        text = resp.text.strip()
        if text.startswith("\`\`\`"): text = text.split("\n",1)[1]
        if text.endswith("\`\`\`"): text = text.rsplit("\`\`\`",1)[0]
        return json.loads(text.strip())[:count]
    except Exception as e:
        print(f"  Gemini unavailable ({type(e).__name__}), using built-in topics", file=sys.stderr)
        return None

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--niche", default="tech")
    parser.add_argument("--count", type=int, default=10)
    args = parser.parse_args()
    topics_file = os.environ.get("TOPICS_FILE", "topics.json")
    existing = []
    if os.path.exists(topics_file):
        try:
            existing = json.load(open(topics_file))
            if not isinstance(existing, list): existing = []
        except: existing = []
    used = {t.get("topic","").lower() for t in existing if t.get("used")}
    print(f"Generating {args.count} topics for: {args.niche}")
    new_topics = try_gemini(args.niche, args.count)
    if not new_topics:
        pool = BUILT_IN.get(args.niche, BUILT_IN["tech"])
        random.shuffle(pool)
        new_topics = pool[:args.count]
        print(f"  Using {len(new_topics)} built-in topics")
    fresh = []
    for t in new_topics:
        if t.get("topic","").lower() not in used:
            t["used"] = False
            t["niche"] = args.niche
            fresh.append(t)
            used.add(t.get("topic","").lower())
    combined = existing + fresh
    with open(topics_file, "w") as f:
        json.dump(combined, f, indent=2, ensure_ascii=False)
    print(f"  Saved {len(fresh)} new topics (total: {len(combined)})")

if __name__ == "__main__":
    main()
