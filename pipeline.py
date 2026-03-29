#!/usr/bin/env python3
"""YTAutoPilot - Long Video Pipeline. Works with or without Gemini."""
import argparse, asyncio, json, os, subprocess, sys, tempfile, random
from pathlib import Path
import edge_tts, requests

def pick_topic(topic_arg, niche):
    if topic_arg and topic_arg.strip(): return topic_arg.strip()
    tf = os.environ.get("TOPICS_FILE", "topics.json")
    if not os.path.exists(tf): return f"Latest {niche} trends"
    topics = json.load(open(tf))
    for t in topics:
        if not t.get("used"):
            t["used"] = True
            json.dump(topics, open(tf,"w"), indent=2, ensure_ascii=False)
            return t.get("topic", f"Latest {niche} trends")
    return f"Latest {niche} trends"

def generate_script(topic, niche):
    api_key = os.environ.get("GEMINI_API_KEY","").strip()
    if api_key:
        try:
            import google.generativeai as genai
            genai.configure(api_key=api_key, transport="rest")
            model = genai.GenerativeModel("gemini-1.5-flash")
            prompt = f'Create YouTube script about: {topic}. Niche: {niche}. Return JSON: "title","description","tags"(10),"sections"(5-8 with "narration","search_query"). Raw JSON only.'
            text = model.generate_content(prompt).text.strip()
            if text.startswith("\`"): text = text.split("\n",1)[1] if "\n" in text else text[3:]
            if text.endswith("\`\`\`"): text = text.rsplit("\`\`\`",1)[0]
            return json.loads(text.strip())
        except Exception as e:
            print(f"  Gemini failed ({type(e).__name__}), using template", file=sys.stderr)
    w = topic.split()
    return {
        "title": topic[:100],
        "description": f"Everything about {topic}. #{niche} #trending #2026",
        "tags": [niche, w[0] if w else niche, "trending", "2026", "explained"],
        "sections": [
            {"narration": f"Welcome! Today we dive deep into {topic}. This topic has been making waves recently.", "search_query": f"{niche} technology"},
            {"narration": f"Lets start with the basics. {topic} has transformed how we think about {niche}.", "search_query": f"{niche} innovation"},
            {"narration": f"The most exciting part about {topic} is the real-world impact. People worldwide already see benefits.", "search_query": f"{niche} people working"},
            {"narration": f"Experts predict {topic} will continue to evolve rapidly in the coming years.", "search_query": f"{niche} future"},
            {"narration": f"Thats all for today on {topic}. If you found this helpful, please like and subscribe!", "search_query": f"{niche} success"},
        ]
    }

async def tts(text, path, voice, rate):
    await edge_tts.Communicate(text, voice, rate=rate).save(path)

def get_dur(f):
    r = subprocess.run(["ffprobe","-v","quiet","-show_entries","format=duration","-of","csv=p=0",f], capture_output=True, text=True)
    try: return float(r.stdout.strip())
    except: return 10.0

def dl_pexels(query, work, idx):
    key = os.environ.get("PEXELS_API_KEY","")
    if not key: return None
    try:
        r = requests.get("https://api.pexels.com/videos/search", headers={"Authorization":key}, params={"query":query,"per_page":3,"orientation":"landscape"}, timeout=30)
        vids = r.json().get("videos",[])
        if not vids: return None
        files = vids[idx%len(vids)].get("video_files",[])
        hd = [v for v in files if v.get("height",0)>=720]
        chosen = (hd or files)[0] if files else None
        if not chosen: return None
        out = os.path.join(work, f"clip_{idx}.mp4")
        data = requests.get(chosen["link"], timeout=60).content
        open(out,"wb").write(data)
        return out
    except: return None

def assemble(clips, audio, output):
    os.makedirs(os.path.dirname(output), exist_ok=True)
    dur = get_dur(audio)
    if not clips:
        subprocess.run(["ffmpeg","-y","-f","lavfi","-i",f"color=c=black:s=1920x1080:d={dur}","-i",audio,"-c:v","libx264","-c:a","aac","-shortest",output], check=True, capture_output=True)
        return
    cpd = max(dur/len(clips), 3)
    trimmed = []
    for i,c in enumerate(clips):
        tp = os.path.join(os.path.dirname(output), f"t_{i}.mp4")
        subprocess.run(["ffmpeg","-y","-i",c,"-t",str(cpd),"-vf","scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2","-c:v","libx264","-preset","fast","-an",tp], check=True, capture_output=True)
        trimmed.append(tp)
    cf = os.path.join(os.path.dirname(output), "concat.txt")
    open(cf,"w").write("\n".join(f"file '{t}'" for t in trimmed))
    subprocess.run(["ffmpeg","-y","-f","concat","-safe","0","-i",cf,"-i",audio,"-c:v","libx264","-c:a","aac","-shortest","-movflags","+faststart",output], check=True, capture_output=True)

def upload_yt(path, title, desc, tags):
    creds_json = os.environ.get("YT_CREDENTIALS_JSON","")
    if not creds_json:
        print("  WARN: YT_CREDENTIALS_JSON not set, skipping upload"); return
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        creds = Credentials.from_authorized_user_info(json.loads(creds_json))
        yt = build("youtube","v3",credentials=creds)
        body = {"snippet":{"title":title[:100],"description":desc[:5000],"tags":tags[:30],"categoryId":"28"},"status":{"privacyStatus":"public","selfDeclaredMadeForKids":False}}
        req = yt.videos().insert(part="snippet,status", body=body, media_body=MediaFileUpload(path, chunksize=10*1024*1024, resumable=True))
        resp = None
        while resp is None:
            st, resp = req.next_chunk()
            if st: print(f"  Upload: {int(st.progress()*100)}%")
        print(f"  Done: https://youtube.com/watch?v={resp.get('id','?')}")
    except Exception as e:
        print(f"  Upload error: {e}", file=sys.stderr)

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--topic", default="")
    p.add_argument("--niche", default="tech")
    args = p.parse_args()
    out_dir = os.environ.get("OUTPUT_DIR","./output/long")
    voice = os.environ.get("TTS_VOICE","en-US-ChristopherNeural")
    rate = os.environ.get("TTS_RATE","+5%")
    os.makedirs(out_dir, exist_ok=True)
    topic = pick_topic(args.topic, args.niche)
    print(f"\n  YTAutoPilot - {topic}\n")
    print("[1/5] Script...")
    script = generate_script(topic, args.niche)
    sections = script.get("sections",[])
    narration = " ".join(s["narration"] for s in sections)
    with tempfile.TemporaryDirectory() as wd:
        print("[2/5] Audio...")
        ap = os.path.join(wd,"narration.mp3")
        asyncio.run(tts(narration, ap, voice, rate))
        print("[3/5] Stock footage...")
        clips = []
        for i,s in enumerate(sections):
            c = dl_pexels(s.get("search_query",args.niche), wd, i)
            if c: clips.append(c)
        print(f"  {len(clips)} clips")
        print("[4/5] Assembling...")
        vp = os.path.join(out_dir,"video.mp4")
        assemble(clips, ap, vp)
        print("[5/5] Uploading...")
        upload_yt(vp, script.get("title",topic), script.get("description",""), script.get("tags",[]))
    print("\nDone!")

if __name__=="__main__": main()
