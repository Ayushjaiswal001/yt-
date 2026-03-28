#!/usr/bin/env python3
"""
YTAutoPilot — Shorts Pipeline
Generates a vertical short-form video and uploads to YouTube as a Short.
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import edge_tts
import google.generativeai as genai
import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


def pick_topic(topic_arg: str, niche: str) -> str:
    """Pick a topic from CLI arg or topics file."""
    if topic_arg and topic_arg.strip():
        return topic_arg.strip()

    topics_file = os.environ.get("TOPICS_FILE", "topics.json")
    if not os.path.exists(topics_file):
        return f"Quick {niche} tip"

    with open(topics_file, "r") as f:
        topics = json.load(f)

    for t in topics:
        if not t.get("used_short", False):
            t["used_short"] = True
            with open(topics_file, "w") as f2:
                json.dump(topics, f2, indent=2, ensure_ascii=False)
            return t.get("topic", f"Quick {niche} tip")

    return f"Quick {niche} tip"


def generate_short_script(topic: str, niche: str) -> dict:
    """Generate a short-form script (30-60 seconds) with Gemini."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: GEMINI_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    genai.configure(api_key=api_key.strip(), transport="rest")
    model = genai.GenerativeModel("gemini-1.5-flash")

    prompt = f"""Create a YouTube Shorts script about: {topic}
Niche: {niche}

Requirements:
- Must be 30-60 seconds when read aloud
- Hook in the first 3 seconds
- Punchy, fast-paced delivery
- End with a call-to-action

Return ONLY a JSON object:
- "title": catchy title with #Shorts (max 100 chars)
- "description": short description with hashtags
- "tags": array of 5 tags
- "hook": the opening hook line (read in 3 seconds)
- "body": the main content (20-40 seconds)
- "cta": closing call-to-action (5 seconds)
- "search_query": Pexels search query for background footage

Raw JSON only, no markdown."""

    response = model.generate_content(prompt)
    text = response.text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]

    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        return {
            "title": f"{topic} #Shorts",
            "description": f"Quick take on {topic}! #{niche} #Shorts",
            "tags": [niche, "shorts"],
            "hook": f"Did you know this about {topic}?",
            "body": f"Here's a quick breakdown of {topic} that everyone should know.",
            "cta": "Follow for more! Like and subscribe!",
            "search_query": topic,
        }


async def generate_tts(text: str, output_path: str, voice: str, rate: str) -> None:
    """Generate TTS audio."""
    communicate = edge_tts.Communicate(text, voice, rate=rate)
    await communicate.save(output_path)


def get_duration(file_path: str) -> float:
    """Get media duration in seconds."""
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", file_path],
        capture_output=True, text=True
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 10.0


def download_pexels_video(query: str, work_dir: str) -> str | None:
    """Download a vertical stock video from Pexels."""
    api_key = os.environ.get("PEXELS_API_KEY")
    if not api_key:
        return None

    headers = {"Authorization": api_key}
    params = {"query": query, "per_page": 5, "orientation": "portrait"}
    try:
        resp = requests.get("https://api.pexels.com/videos/search", headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"WARN: Pexels search failed: {e}", file=sys.stderr)
        return None

    videos = data.get("videos", [])
    if not videos:
        return None

    video = videos[0]
    video_files = video.get("video_files", [])
    # Prefer vertical/portrait files
    portrait = [v for v in video_files if v.get("height", 0) > v.get("width", 0)]
    chosen = portrait[0] if portrait else video_files[0] if video_files else None
    if not chosen:
        return None

    out_path = os.path.join(work_dir, "bg_clip.mp4")
    try:
        r = requests.get(chosen["link"], timeout=60)
        r.raise_for_status()
        with open(out_path, "wb") as f:
            f.write(r.content)
        return out_path
    except Exception:
        return None


def assemble_short(clip_path: str | None, audio_path: str, output_path: str) -> None:
    """Assemble a vertical short video (1080x1920)."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    audio_duration = get_duration(audio_path)

    if clip_path and os.path.exists(clip_path):
        # Use stock footage as background, loop to audio length
        subprocess.run([
            "ffmpeg", "-y",
            "-stream_loop", "-1", "-i", clip_path,
            "-i", audio_path,
            "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
            "-c:v", "libx264", "-preset", "fast",
            "-c:a", "aac", "-shortest",
            "-t", str(min(audio_duration + 1, 60)),
            "-movflags", "+faststart", output_path
        ], check=True, capture_output=True)
    else:
        # Black background with audio
        subprocess.run([
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c=black:s=1080x1920:d={audio_duration}",
            "-i", audio_path,
            "-c:v", "libx264", "-c:a", "aac", "-shortest", output_path
        ], check=True, capture_output=True)

    print(f"  Short assembled: {output_path}")


def upload_to_youtube(video_path: str, title: str, description: str, tags: list[str]) -> str | None:
    """Upload short to YouTube."""
    creds_json = os.environ.get("YT_CREDENTIALS_JSON")
    if not creds_json:
        print("WARN: YT_CREDENTIALS_JSON not set, skipping upload", file=sys.stderr)
        return None

    try:
        creds_data = json.loads(creds_json)
        creds = Credentials.from_authorized_user_info(creds_data)
        youtube = build("youtube", "v3", credentials=creds)

        body = {
            "snippet": {
                "title": title[:100],
                "description": description[:5000],
                "tags": tags[:30],
                "categoryId": "28",
            },
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False,
            },
        }

        media = MediaFileUpload(video_path, chunksize=10 * 1024 * 1024, resumable=True)
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                print(f"  Upload: {int(status.progress() * 100)}%")

        video_id = response.get("id", "unknown")
        print(f"  ✓ Uploaded Short: https://youtube.com/shorts/{video_id}")
        return video_id
    except Exception as e:
        print(f"ERROR: Upload failed: {e}", file=sys.stderr)
        return None


def main():
    parser = argparse.ArgumentParser(description="YTAutoPilot Shorts Pipeline")
    parser.add_argument("--topic", default="", help="Topic (blank=auto)")
    parser.add_argument("--niche", default="tech", help="Content niche")
    args = parser.parse_args()

    output_dir = os.environ.get("OUTPUT_DIR", "./output/shorts")
    voice = os.environ.get("TTS_VOICE", "en-US-ChristopherNeural")
    rate = os.environ.get("TTS_RATE", "+10%")
    os.makedirs(output_dir, exist_ok=True)

    topic = pick_topic(args.topic, args.niche)
    print(f"\n{'='*50}")
    print(f"  YTAutoPilot — Shorts Pipeline")
    print(f"  Topic: {topic}")
    print(f"{'='*50}\n")

    print("[1/4] Generating short script...")
    script = generate_short_script(topic, args.niche)
    title = script.get("title", f"{topic} #Shorts")
    description = script.get("description", "")
    tags = script.get("tags", [])
    narration = f"{script.get('hook', '')} {script.get('body', '')} {script.get('cta', '')}"
    search_query = script.get("search_query", topic)

    with tempfile.TemporaryDirectory() as work_dir:
        print("[2/4] Generating narration...")
        audio_path = os.path.join(work_dir, "short_audio.mp3")
        asyncio.run(generate_tts(narration, audio_path, voice, rate))

        print("[3/4] Downloading background footage...")
        clip = download_pexels_video(search_query, work_dir)

        print("[4/4] Assembling short...")
        video_path = os.path.join(output_dir, "short.mp4")
        assemble_short(clip, audio_path, video_path)

        print("\nUploading to YouTube...")
        upload_to_youtube(video_path, title, description, tags)

    print("\n✓ Shorts pipeline complete!")


if __name__ == "__main__":
    main()
