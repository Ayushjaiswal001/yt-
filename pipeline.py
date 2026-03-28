#!/usr/bin/env python3
"""
YTAutoPilot — Long Video Pipeline
Generates a narrated video with stock footage and uploads to YouTube.
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


# ──────────────────────────────────────────────────
# 1. TOPIC SELECTION
# ──────────────────────────────────────────────────

def pick_topic(topic_arg: str, niche: str) -> str:
    """Pick a topic: use CLI arg, or pull next unused from topics file."""
    if topic_arg and topic_arg.strip():
        return topic_arg.strip()

    topics_file = os.environ.get("TOPICS_FILE", "topics.json")
    if not os.path.exists(topics_file):
        return f"Latest {niche} trends and innovations"

    with open(topics_file, "r") as f:
        topics = json.load(f)

    for t in topics:
        if not t.get("used", False):
            t["used"] = True
            with open(topics_file, "w") as f2:
                json.dump(topics, f2, indent=2, ensure_ascii=False)
            return t.get("topic", f"Latest {niche} trends")

    return f"Latest {niche} trends and innovations"


# ──────────────────────────────────────────────────
# 2. SCRIPT GENERATION (Gemini)
# ──────────────────────────────────────────────────

def generate_script(topic: str, niche: str) -> dict:
    """Generate video script with Gemini AI."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: GEMINI_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    genai.configure(api_key=api_key.strip(), transport="rest")
    model = genai.GenerativeModel("gemini-2.0-flash")

    prompt = f"""Create a YouTube video script about: {topic}
Niche: {niche}

Return ONLY a JSON object with these fields:
- "title": catchy YouTube title (max 100 chars)
- "description": YouTube description (2-3 paragraphs with hashtags)
- "tags": array of 10 relevant tags
- "sections": array of objects, each with:
  - "narration": the voiceover text (2-4 sentences)
  - "visual": brief description of what visual/b-roll to show
  - "search_query": a Pexels search query for stock footage

Target: 5-8 sections for a 3-5 minute video.
Return raw JSON only, no markdown fences."""

    response = model.generate_content(prompt)
    text = response.text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]

    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        print(f"WARN: Could not parse script JSON, using fallback", file=sys.stderr)
        return {
            "title": topic,
            "description": f"A video about {topic}. #youtube #{niche}",
            "tags": [niche, topic.split()[0]],
            "sections": [
                {"narration": f"Today we explore {topic}.", "visual": topic, "search_query": topic}
            ],
        }


# ──────────────────────────────────────────────────
# 3. TEXT-TO-SPEECH (edge-tts)
# ──────────────────────────────────────────────────

async def generate_tts(text: str, output_path: str, voice: str, rate: str) -> None:
    """Generate speech audio from text."""
    communicate = edge_tts.Communicate(text, voice, rate=rate)
    await communicate.save(output_path)


def generate_all_audio(sections: list, work_dir: str, voice: str, rate: str) -> list[str]:
    """Generate TTS audio for each section."""
    audio_files = []
    full_narration = " ".join(s["narration"] for s in sections)

    audio_path = os.path.join(work_dir, "narration.mp3")
    print(f"  Generating TTS ({voice}, rate={rate})...")
    asyncio.run(generate_tts(full_narration, audio_path, voice, rate))
    audio_files.append(audio_path)
    return audio_files


# ──────────────────────────────────────────────────
# 4. STOCK FOOTAGE (Pexels)
# ──────────────────────────────────────────────────

def download_pexels_video(query: str, work_dir: str, index: int) -> str | None:
    """Download a stock video clip from Pexels."""
    api_key = os.environ.get("PEXELS_API_KEY")
    if not api_key:
        print("WARN: PEXELS_API_KEY not set, skipping stock footage", file=sys.stderr)
        return None

    headers = {"Authorization": api_key}
    params = {"query": query, "per_page": 3, "orientation": "landscape"}
    try:
        resp = requests.get("https://api.pexels.com/videos/search", headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"WARN: Pexels search failed for '{query}': {e}", file=sys.stderr)
        return None

    videos = data.get("videos", [])
    if not videos:
        return None

    # Pick a video, prefer HD
    video = videos[index % len(videos)]
    video_files = video.get("video_files", [])
    hd = [v for v in video_files if v.get("height", 0) >= 720]
    chosen = hd[0] if hd else video_files[0] if video_files else None
    if not chosen:
        return None

    url = chosen["link"]
    out_path = os.path.join(work_dir, f"clip_{index}.mp4")
    try:
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        with open(out_path, "wb") as f:
            f.write(r.content)
        return out_path
    except Exception as e:
        print(f"WARN: Download failed: {e}", file=sys.stderr)
        return None


# ──────────────────────────────────────────────────
# 5. VIDEO ASSEMBLY (ffmpeg)
# ──────────────────────────────────────────────────

def get_duration(file_path: str) -> float:
    """Get media file duration in seconds."""
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", file_path],
        capture_output=True, text=True
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 10.0


def assemble_video(clips: list[str], audio_path: str, output_path: str) -> None:
    """Combine video clips with audio narration."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    audio_duration = get_duration(audio_path)

    if not clips:
        # Generate black screen if no clips
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=black:s=1920x1080:d={audio_duration}",
            "-i", audio_path, "-c:v", "libx264", "-c:a", "aac", "-shortest", output_path
        ], check=True, capture_output=True)
        return

    # Concat clips, loop to match audio length
    clip_duration_per = max(audio_duration / len(clips), 3)
    concat_file = os.path.join(os.path.dirname(output_path), "concat.txt")

    trimmed = []
    for i, clip in enumerate(clips):
        trimmed_path = os.path.join(os.path.dirname(output_path), f"trimmed_{i}.mp4")
        subprocess.run([
            "ffmpeg", "-y", "-i", clip, "-t", str(clip_duration_per),
            "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
            "-c:v", "libx264", "-preset", "fast", "-an", trimmed_path
        ], check=True, capture_output=True)
        trimmed.append(trimmed_path)

    with open(concat_file, "w") as f:
        for t in trimmed:
            f.write(f"file '{t}'\n")

    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_file,
        "-i", audio_path, "-c:v", "libx264", "-c:a", "aac", "-shortest",
        "-movflags", "+faststart", output_path
    ], check=True, capture_output=True)
    print(f"  Video assembled: {output_path}")


# ──────────────────────────────────────────────────
# 6. YOUTUBE UPLOAD
# ──────────────────────────────────────────────────

def upload_to_youtube(video_path: str, title: str, description: str, tags: list[str]) -> str | None:
    """Upload video to YouTube using service credentials."""
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
                "categoryId": "28",  # Science & Technology
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
                print(f"  Upload progress: {int(status.progress() * 100)}%")

        video_id = response.get("id", "unknown")
        print(f"  ✓ Uploaded: https://youtube.com/watch?v={video_id}")
        return video_id
    except Exception as e:
        print(f"ERROR: YouTube upload failed: {e}", file=sys.stderr)
        return None


# ──────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="YTAutoPilot Long Video Pipeline")
    parser.add_argument("--topic", default="", help="Video topic (blank=auto from topics file)")
    parser.add_argument("--niche", default="tech", help="Content niche")
    args = parser.parse_args()

    output_dir = os.environ.get("OUTPUT_DIR", "./output/long")
    voice = os.environ.get("TTS_VOICE", "en-US-ChristopherNeural")
    rate = os.environ.get("TTS_RATE", "+5%")

    os.makedirs(output_dir, exist_ok=True)

    # Step 1: Pick topic
    topic = pick_topic(args.topic, args.niche)
    print(f"\n{'='*60}")
    print(f"  YTAutoPilot — Long Video Pipeline")
    print(f"  Topic: {topic}")
    print(f"  Niche: {args.niche}")
    print(f"{'='*60}\n")

    # Step 2: Generate script
    print("[1/5] Generating script with Gemini...")
    script = generate_script(topic, args.niche)
    title = script.get("title", topic)
    description = script.get("description", "")
    tags = script.get("tags", [args.niche])
    sections = script.get("sections", [])
    print(f"  Title: {title}")
    print(f"  Sections: {len(sections)}")

    with tempfile.TemporaryDirectory() as work_dir:
        # Step 3: Generate audio
        print("\n[2/5] Generating narration audio...")
        audio_files = generate_all_audio(sections, work_dir, voice, rate)
        audio_path = audio_files[0] if audio_files else None

        if not audio_path or not os.path.exists(audio_path):
            print("ERROR: Audio generation failed", file=sys.stderr)
            sys.exit(1)

        # Step 4: Download stock footage
        print("\n[3/5] Downloading stock footage from Pexels...")
        clips = []
        for i, section in enumerate(sections):
            query = section.get("search_query", section.get("visual", args.niche))
            print(f"  Searching: {query}")
            clip = download_pexels_video(query, work_dir, i)
            if clip:
                clips.append(clip)

        print(f"  Downloaded {len(clips)} clips")

        # Step 5: Assemble video
        print("\n[4/5] Assembling video with ffmpeg...")
        video_path = os.path.join(output_dir, "video.mp4")
        assemble_video(clips, audio_path, video_path)

        # Step 6: Upload
        print("\n[5/5] Uploading to YouTube...")
        upload_to_youtube(video_path, title, description, tags)

    print("\n✓ Pipeline complete!")


if __name__ == "__main__":
    main()
