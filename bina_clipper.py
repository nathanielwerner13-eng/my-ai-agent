import os
import json
import time
import datetime
import threading
import requests
import urllib.request
import uuid
import re
from anthropic import Anthropic

client = Anthropic()

BINA_URL = os.environ.get('BINA_URL', 'https://my-ai-agent-production-5e17.up.railway.app')
SERPER_API_KEY = os.environ.get('SERPER_API_KEY', '')

CLIPS_DIR = '/tmp/clips'
CLIP_HISTORY_FILE = '/tmp/clip_history.json'
SCORED_CLIPS_FILE = '/tmp/scored_clips.json'
os.makedirs(CLIPS_DIR, exist_ok=True)

KICK_STREAMERS = ['clavicular', 'n3on', 'adinross', 'deenthegreat', 'lacari', 'tjrplays']
TWITCH_STREAMERS = ['jynxzi', 'marlon', 'adinross']

print("Bina Clipper starting...")

def web_search(query, num_results=5):
    if not SERPER_API_KEY:
        return ""
    try:
        response = requests.post(
            'https://google.serper.dev/search',
            headers={'X-API-KEY': SERPER_API_KEY, 'Content-Type': 'application/json'},
            json={'q': query, 'num': num_results},
            timeout=10
        )
        data = response.json()
        output = ""
        for r in data.get('organic', [])[:num_results]:
            output += f"- {r.get('title','')}: {r.get('snippet','')}\n"
        return output
    except Exception as e:
        return f"Search error: {str(e)}"

def push_to_bina(subject, body):
    try:
        requests.post(
            f'{BINA_URL}/internal/add-notification',
            json={
                'id': f'clip-{int(time.time())}-{uuid.uuid4().hex[:6]}',
                'type': 'clip',
                'subject': subject,
                'from': 'Bina Clipper',
                'body': body,
                'read': False,
                'timestamp': time.time()
            },
            timeout=15
        )
        print(f"Pushed: {subject[:50]}")
    except Exception as e:
        print(f"Push error: {str(e)}")

def load_clip_history():
    try:
        if os.path.exists(CLIP_HISTORY_FILE):
            with open(CLIP_HISTORY_FILE, 'r') as f:
                return json.load(f)
    except:
        pass
    return {'clips': []}

def save_clip_history(history):
    try:
        with open(CLIP_HISTORY_FILE, 'w') as f:
            json.dump(history, f)
    except:
        pass

def load_scored_clips():
    try:
        if os.path.exists(SCORED_CLIPS_FILE):
            with open(SCORED_CLIPS_FILE, 'r') as f:
                return json.load(f)
    except:
        pass
    return []

def save_scored_clips(clips):
    try:
        with open(SCORED_CLIPS_FILE, 'w') as f:
            json.dump(clips, f)
    except:
        pass

# Twitch app credentials (free dev account)
TWITCH_CLIENT_ID = 'kimne78kx3ncx6brgo4mv6wki5h1ko'  # public web client id
TWITCH_USER_AGENT = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'

def get_twitch_token():
    """Get app access token using public Twitch GQL (no auth needed for public clips)"""
    try:
        req = urllib.request.Request(
            'https://id.twitch.tv/oauth2/token',
            data=b'client_id=kimne78kx3ncx6brgo4mv6wki5h1ko&client_secret=&grant_type=client_credentials',
            method='POST'
        )
    except:
        pass
    return None

def get_twitch_clips(streamer, limit=10):
    """Use Twitch GQL API - no auth required for public data"""
    clips = []
    try:
        # Use Twitch GQL which works without OAuth for public clips
        gql_query = {
            "operationName": "ClipsCards__User",
            "variables": {
                "login": streamer,
                "limit": limit,
                "criteria": {
                    "filter": "LAST_WEEK"
                }
            },
            "extensions": {
                "persistedQuery": {
                    "version": 1,
                    "sha256Hash": "b73ad2bfaecfd30a9e6c28fada15bd97032c83ec77a0440766a56fe0bd632777"
                }
            }
        }
        response = requests.post(
            'https://gql.twitch.tv/gql',
            json=gql_query,
            headers={
                'Client-ID': 'kimne78kx3ncx6brgo4mv6wki5h1ko',
                'Content-Type': 'application/json',
                'User-Agent': TWITCH_USER_AGENT
            },
            timeout=15
        )
        if response.status_code == 200:
            data = response.json()
            edges = (data.get('data', {}) or {}).get('user', {}) or {}
            edges = edges.get('clips', {}).get('edges', []) if edges else []
            for edge in edges[:limit]:
                node = edge.get('node', {})
                if not node:
                    continue
                clips.append({
                    'id': node.get('id', str(uuid.uuid4())),
                    'title': node.get('title', f'{streamer} clip'),
                    'url': node.get('url', f'https://www.twitch.tv/{streamer}/clip/{node.get("id","")}'),
                    'views': node.get('viewCount', 0),
                    'duration': node.get('durationSeconds', 0),
                    'streamer': streamer,
                    'platform': 'twitch',
                    'thumbnail': node.get('thumbnailURL', ''),
                    'likes': 0
                })
            print(f"Twitch {streamer}: {len(clips)} clips")
        else:
            print(f"Twitch GQL {streamer}: status {response.status_code}")
    except Exception as e:
        print(f"Twitch error {streamer}: {str(e)}")
    return clips

def get_kick_clips(streamer, limit=10):
    clips = []
    # Try multiple Kick API formats
    urls_to_try = [
        f'https://kick.com/api/v2/channels/{streamer}/clips?sort=views&time=week',
        f'https://kick.com/api/v1/channels/{streamer}/clips?sort=views',
    ]
    for api_url in urls_to_try:
        try:
            response = requests.get(
                api_url,
                headers={
                    'Accept': 'application/json',
                    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
                    'Referer': 'https://kick.com',
                    'Accept-Language': 'en-US,en;q=0.9'
                },
                timeout=15
            )
            print(f"Kick {streamer} ({api_url.split('?')[0].split('/')[-1]}): status {response.status_code}")
            if response.status_code == 200:
                data = response.json()
                clip_list = []
                if isinstance(data, list):
                    clip_list = data
                elif isinstance(data, dict):
                    clip_list = data.get('clips', data.get('data', []))
                for clip in clip_list[:limit]:
                    url = clip.get('clip_url', clip.get('url', clip.get('video_url', '')))
                    if not url:
                        continue
                    clips.append({
                        'id': str(clip.get('id', uuid.uuid4())),
                        'title': clip.get('title', f'{streamer} kick clip'),
                        'url': url,
                        'views': clip.get('views', clip.get('view_count', 0)),
                        'duration': clip.get('duration', 0),
                        'streamer': streamer,
                        'platform': 'kick',
                        'thumbnail': clip.get('thumbnail_url', clip.get('thumbnail', '')),
                        'likes': clip.get('likes', clip.get('like_count', 0))
                    })
                if clips:
                    print(f"Kick {streamer}: {len(clips)} clips")
                    break
        except Exception as e:
            print(f"Kick error {streamer} {api_url}: {str(e)}")
    return clips

def score_clips_with_ai(clips):
    if not clips:
        return []
    descriptions = ""
    for i, clip in enumerate(clips[:20]):
        descriptions += f"""
Clip {i+1}:
- Streamer: {clip.get('streamer','?')} on {clip.get('platform','?')}
- Title: {clip.get('title','No title')}
- Views: {clip.get('views',0):,}
- Duration: {clip.get('duration',0)}s
- Likes: {clip.get('likes',0):,}
"""
    prompt = f"""Score IRL/Just Chatting stream clips for viral TikTok potential 1-10.

HIGH score: unexpected moments, rage, laughter, shock, confrontations, celebrity appearances, 15-60s clips, high views/likes. n3on, clavicular, adinross content performs very well.
LOW score: boring chatting, low views, generic title, over 3 minutes.

CLIPS:
{descriptions}

Return ONLY JSON array:
[{{"clip_index": 1, "score": 8, "reason": "one sentence", "suggested_caption": "short caption", "suggested_hashtags": "#n3on #kick #viral #fyp"}}]

Only clips scoring 6+."""

    try:
        response = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.content[0].text.strip()
        raw = re.sub(r'^```json\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        scored = json.loads(raw)
        result = []
        for item in scored:
            idx = item.get('clip_index', 0) - 1
            if 0 <= idx < len(clips):
                clip = clips[idx].copy()
                clip['score'] = item.get('score', 0)
                clip['reason'] = item.get('reason', '')
                clip['suggested_caption'] = item.get('suggested_caption', '')
                clip['suggested_hashtags'] = item.get('suggested_hashtags', '#fyp #viral #streamer')
                result.append(clip)
        result.sort(key=lambda x: x.get('score', 0), reverse=True)
        return result
    except Exception as e:
        print(f"AI scoring error: {str(e)}")
        for clip in clips:
            clip['score'] = min(10, max(1, clip.get('views', 0) // 1000))
        return sorted(clips, key=lambda x: x.get('score', 0), reverse=True)

def run_clip_farm_cycle():
    now_str = datetime.datetime.now().strftime('%H:%M')
    print(f"\nCLIP FARM CYCLE - {now_str}")
    history = load_clip_history()
    already_seen = set(history.get('clips', []))
    all_clips = []

    print("Scanning Kick...")
    for streamer in KICK_STREAMERS:
        clips = get_kick_clips(streamer, limit=10)
        new_clips = [c for c in clips if c['id'] not in already_seen]
        all_clips.extend(new_clips)
        print(f"  {streamer}: {len(new_clips)} new clips")
        time.sleep(2)

    print("Scanning Twitch...")
    for streamer in TWITCH_STREAMERS:
        clips = get_twitch_clips(streamer, limit=5)
        new_clips = [c for c in clips if c['id'] not in already_seen]
        all_clips.extend(new_clips)
        print(f"  {streamer}: {len(new_clips)} new clips")
        time.sleep(1)

    print(f"Total new clips: {len(all_clips)}")
    if not all_clips:
        push_to_bina("Clip Scan Complete", f"Scanned all streamers at {now_str} - no new clips. Checking again in 30 min.")
        return

    print("Scoring with AI...")
    scored = score_clips_with_ai(all_clips)
    top_clips = [c for c in scored if c.get('score', 0) >= 7]
    print(f"Top clips (7+): {len(top_clips)}")

    for clip in all_clips:
        already_seen.add(clip['id'])
    history['clips'] = list(already_seen)[-2000:]
    save_clip_history(history)

    existing = load_scored_clips()
    existing_ids = {c['id'] for c in existing}
    for clip in top_clips:
        if clip['id'] not in existing_ids:
            clip['status'] = 'pending_edit'
            clip['found_at'] = time.time()
            existing.append(clip)
    save_scored_clips(existing[-50:])

    if top_clips:
        body = f"**{len(top_clips)} clips ready for your review**\n\n"
        body += f"Scanned {len(all_clips)} clips across Kick + Twitch\n\n"
        for i, clip in enumerate(top_clips[:5]):
            body += f"**#{i+1} - Score {clip.get('score','?')}/10**\n"
            body += f"Streamer: {clip.get('streamer','?').upper()} on {clip.get('platform','?').upper()}\n"
            body += f"Title: {clip.get('title','No title')}\n"
            body += f"Views: {clip.get('views',0):,} | Likes: {clip.get('likes',0):,}\n"
            body += f"URL: {clip.get('url','')}\n"
            body += f"Caption: {clip.get('suggested_caption','')}\n"
            body += f"Tags: {clip.get('suggested_hashtags','')}\n"
            body += f"Why: {clip.get('reason','')}\n\n"
        body += "---\nDownload, edit in CapCut, upload to bina-poster"
        push_to_bina(f"Clips Ready - {now_str} ({len(top_clips)} found)", body)
    else:
        push_to_bina(f"Clip Scan - {now_str}", f"Scanned {len(all_clips)} clips, none hit 7+ this cycle.")

def main():
    print("=" * 50)
    print("BINA CLIPPER ONLINE")
    print(f"Started: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Kick: {KICK_STREAMERS}")
    print(f"Twitch: {TWITCH_STREAMERS}")
    print("Scan: every 30 minutes")
    print("=" * 50)
    last_scan = 0
    print("\nRunning initial scan...")
    threading.Thread(target=run_clip_farm_cycle, daemon=True).start()
    last_scan = time.time()
    while True:
        try:
            now = time.time()
            if now - last_scan > 1800:
                last_scan = now
                threading.Thread(target=run_clip_farm_cycle, daemon=True).start()
            time.sleep(60)
        except Exception as e:
            print(f"Scheduler error: {str(e)}")
            time.sleep(60)

if __name__ == '__main__':
    main()

if __name__ == '__main__':
    import threading
    def _main_loop():
        global last_scan
        last_scan = 0
        last_scan = time.time()
        run_clip_farm_cycle()
        while True:
            try:
                now = time.time()
                if now - last_scan > 1800:
                    last_scan = now
                    run_clip_farm_cycle()
                time.sleep(60)
            except Exception as e:
                print(f"Scheduler error: {str(e)}")
                time.sleep(60)
    t = threading.Thread(target=_main_loop, daemon=True)
    t.start()
    port = int(os.environ.get('PORT', 5001))
    flask_app.run(host='0.0.0.0', port=port)
