#!/usr/bin/env python3
"""YTAutoPilot - Shorts Pipeline. Works with or without Gemini."""
import argparse, asyncio, json, os, subprocess, sys, tempfile
import edge_tts, requests

def pick_topic(topic_arg, niche):
    if topic_arg and topic_arg.strip(): return topic_arg.strip()
    tf = os.environ.get("TOPICS_FILE","topics.json")
    if not os.path.exists(tf): return f"Quick {niche} tip"
    topics = json.load(open(tf))
    for t in topics:
        if not t.get("used_short"):
            t["used_short"]=True
            json.dump(topics, open(tf,"w"), indent=2, ensure_ascii=False)
            return t.get("topic", f"Quick {niche} tip")
    return f"Quick {niche} tip"

def gen_short_script(topic, niche):
    api_key = os.environ.get("GEMINI_API_KEY","").strip()
    if api_key:
        try:
            import google.generativeai as genai
            genai.configure(api_key=api_key, transport="rest")
            model = genai.GenerativeModel("gemini-1.5-flash")
            prompt = f'YouTube Shorts script (30-60s) about: {topic}. Niche: {niche}. Return JSON: "title"(with #Shorts),"description","tags"(5),"hook"(3s),"body"(30s),"cta"(5s),"search_query". Raw JSON only.'
            text = model.generate_content(prompt).text.strip()
            if text.startswith("\`"): text = text.split("\n",1)[1] if "\n" in text else text[3:]
            if text.endswith("\`\`\`"): text = text.rsplit("\`\`\`",1)[0]
            return json.loads(text.strip())
        except Exception as e:
            print(f"  Gemini failed ({type(e).__name__}), using template", file=sys.stderr)
    return {
        "title": f"{topic} #Shorts", "description": f"Quick take on {topic}! #{niche} #Shorts",
        "tags": [niche,"shorts","trending"], "search_query": topic,
        "hook": f"Did you know this about {topic}?",
        "body": f"Here is a quick breakdown of {topic} that everyone should know. The facts might surprise you.",
        "cta": "Follow for more! Like and subscribe!",
    }

def get_dur(f):
    r = subprocess.run(["ffprobe","-v","quiet","-show_entries","format=duration","-of","csv=p=0",f], capture_output=True, text=True)
    try: return float(r.stdout.strip())
    except: return 10.0

def dl_pexels_portrait(q, wd):
    key = os.environ.get("PEXELS_API_KEY","")
    if not key: return None
    try:
        r = requests.get("https://api.pexels.com/videos/search", headers={"Authorization":key}, params={"query":q,"per_page":5,"orientation":"portrait"}, timeout=30)
        vids = r.json().get("videos",[])
        if not vids: return None
        files = vids[0].get("video_files",[])
        port = [v for v in files if v.get("height",0)>v.get("width",0)]
        chosen = (port or files)[0] if files else None
        if not chosen: return None
        out = os.path.join(wd,"bg.mp4")
        open(out,"wb").write(requests.get(chosen["link"],timeout=60).content)
        return out
    except: return None

def assemble_short(clip, audio, output):
    os.makedirs(os.path.dirname(output), exist_ok=True)
    dur = get_dur(audio)
    if clip and os.path.exists(clip):
        subprocess.run(["ffmpeg","-y","-stream_loop","-1","-i",clip,"-i",audio,"-vf","scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2","-c:v","libx264","-preset","fast","-c:a","aac","-shortest","-t",str(min(dur+1,60)),"-movflags","+faststart",output], check=True, capture_output=True)
    else:
        subprocess.run(["ffmpeg","-y","-f","lavfi","-i",f"color=c=black:s=1080x1920:d={dur}","-i",audio,"-c:v","libx264","-c:a","aac","-shortest",output], check=True, capture_output=True)

def upload_yt(path, title, desc, tags):
    creds = os.environ.get("YT_CREDENTIALS_JSON","")
    if not creds: print("  WARN: No YT creds, skip upload"); return
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        c = Credentials.from_authorized_user_info(json.loads(creds))
        yt = build("youtube","v3",credentials=c)
        body = {"snippet":{"title":title[:100],"description":desc[:5000],"tags":tags[:30],"categoryId":"28"},"status":{"privacyStatus":"public","selfDeclaredMadeForKids":False}}
        req = yt.videos().insert(part="snippet,status",body=body,media_body=MediaFileUpload(path,chunksize=10*1024*1024,resumable=True))
        resp=None
        while resp is None: st,resp=req.next_chunk()
        print(f"  Done: https://youtube.com/shorts/{resp.get('id','?')}")
    except Exception as e: print(f"  Upload error: {e}", file=sys.stderr)

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--topic",default="")
    p.add_argument("--niche",default="tech")
    args = p.parse_args()
    out = os.environ.get("OUTPUT_DIR","./output/shorts")
    voice = os.environ.get("TTS_VOICE","en-US-ChristopherNeural")
    rate = os.environ.get("TTS_RATE","+10%")
    os.makedirs(out, exist_ok=True)
    topic = pick_topic(args.topic, args.niche)
    print(f"\n  Shorts - {topic}\n")
    script = gen_short_script(topic, args.niche)
    narration = f"{script.get('hook','')} {script.get('body','')} {script.get('cta','')}"
    with tempfile.TemporaryDirectory() as wd:
        ap = os.path.join(wd,"audio.mp3")
        asyncio.run(edge_tts.Communicate(narration,voice,rate=rate).save(ap))
        clip = dl_pexels_portrait(script.get("search_query",topic), wd)
        vp = os.path.join(out,"short.mp4")
        assemble_short(clip, ap, vp)
        upload_yt(vp, script.get("title",topic), script.get("description",""), script.get("tags",[]))
    print("Done!")

if __name__=="__main__": main()
