#!/usr/bin/env python3
"""Generate trending topics using Gemini AI and save to a JSON file."""

import argparse
import json
import os
import sys
import google.generativeai as genai


def generate_topics(niche: str, count: int) -> list[dict]:
    """Use Gemini to generate trending topics for the given niche."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: GEMINI_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    genai.configure(api_key=api_key.strip(), transport="rest")
    model = genai.GenerativeModel("gemini-2.0-flash")

    prompt = f"""Generate {count} trending YouTube video topics for the "{niche}" niche.
Return ONLY a JSON array of objects, each with these fields:
- "topic": a specific, engaging video title/topic
- "description": one sentence describing the video angle
- "tags": array of 5 relevant YouTube tags

Return raw JSON only, no markdown fences, no extra text."""

    response = model.generate_content(prompt)
    text = response.text.strip()
    # Strip markdown fences if present
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    text = text.strip()

    try:
        topics = json.loads(text)
    except json.JSONDecodeError:
        print(f"ERROR: Could not parse Gemini response as JSON:\n{text[:500]}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(topics, list):
        topics = [topics]

    return topics[:count]


def main():
    parser = argparse.ArgumentParser(description="Generate trending topics with Gemini AI")
    parser.add_argument("--niche", default="tech", help="Content niche (default: tech)")
    parser.add_argument("--count", type=int, default=10, help="Number of topics (default: 10)")
    args = parser.parse_args()

    topics_file = os.environ.get("TOPICS_FILE", "topics.json")

    # Load existing topics if file exists
    existing = []
    if os.path.exists(topics_file):
        try:
            with open(topics_file, "r") as f:
                existing = json.load(f)
            if not isinstance(existing, list):
                existing = []
        except (json.JSONDecodeError, IOError):
            existing = []

    # Filter out already-used topics
    used_titles = {t.get("topic", "").lower() for t in existing if t.get("used")}

    print(f"Generating {args.count} topics for niche: {args.niche}")
    new_topics = generate_topics(args.niche, args.count)

    # Mark new, deduplicate
    fresh = []
    for t in new_topics:
        if t.get("topic", "").lower() not in used_titles:
            t["used"] = False
            t["niche"] = args.niche
            fresh.append(t)
            used_titles.add(t.get("topic", "").lower())

    # Combine: keep used history + add fresh
    combined = existing + fresh

    with open(topics_file, "w") as f:
        json.dump(combined, f, indent=2, ensure_ascii=False)

    print(f"Saved {len(fresh)} new topics to {topics_file} (total: {len(combined)})")


if __name__ == "__main__":
    main()
