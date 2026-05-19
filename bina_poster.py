import os
import json
import time
import datetime
import threading
import requests
import uuid
from flask import Flask, request, jsonify, redirect
from flask_cors import CORS
from urllib.parse import urlencode

app = Flask(__name__)
CORS(app)

BINA_URL = os.environ.get('BINA_URL', 'https://my-ai-agent-production-5e17.up.railway.app')
TIKTOK_CLIENT_KEY = os.environ.get('TIKTOK_CLIENT_KEY', 'sbaw221skbq4k750t5')
TIKTOK_CLIENT_SECRET = os.environ.get('TIKTOK_CLIENT_SECRET', 'ZgYdZqlIT37G2vT3ioHsD8kaZi4vQSZm')
POSTER_URL = os.environ.get('POSTER_URL', '')

ACCOUNTS_FILE = '/tmp/tiktok_accounts.json'
QUEUE_FILE = '/tmp/post_queue.json'
POSTED_FILE = '/tmp/posted_log.json'
UPLOAD_DIR = '/tmp/uploads'
os.makedirs(UPLOAD_DIR, exist_ok=True)

POSTING_SCHEDULE = [
    {'hour': 7, 'minute': 0},
    {'hour': 12, 'minute': 0},
    {'hour': 15, 'minute': 0},
    {'hour': 18, 'minute': 0},
    {'hour': 21, 'minute': 0},
    {'hour': 23, 'minute': 0},
]

print("Bina Poster starting v2...")

def load_accounts():
    try:
        if os.path.exists(ACCOUNTS_FILE):
            with open(ACCOUNTS_FILE, 'r') as f:
                return json.load(f)
    except: pass
    return []

def save_accounts(accounts):
    with open(ACCOUNTS_FILE, 'w') as f:
        json.dump(accounts, f)

def load_queue():
    try:
        if os.path.exists(QUEUE_FILE):
            with open(QUEUE_FILE, 'r') as f:
                return json.load(f)
    except: pass
    return []

def save_queue(queue):
    with open(QUEUE_FILE, 'w') as f:
        json.dump(queue, f)

def load_posted():
    try:
        if os.path.exists(POSTED_FILE):
            with open(POSTED_FILE, 'r') as f:
                return json.load(f)
    except: pass
    return []

def save_posted(posted):
    with open(POSTED_FILE, 'w') as f:
        json.dump(posted[-200:], f)

def push_to_bina(subject, body):
    try:
        requests.post(f'{BINA_URL}/internal/add-notification', json={
            'id': f'post-{int(time.time())}-{uuid.uuid4().hex[:6]}',
            'type': 'post', 'subject': subject, 'from': 'Bina Poster',
            'body': body, 'read': False, 'timestamp': time.time()
        }, timeout=15)
    except Exception as e:
        print(f"Push error: {str(e)}")

@app.route('/tiktok/login')
def tiktok_login():
    account_label = request.args.get('account', 'account1')
    state = f"{account_label}-{uuid.uuid4().hex[:8]}"
    params = {
        'client_key': TIKTOK_CLIENT_KEY,
        'scope': 'user.info.basic,video.upload',
        'response_type': 'code',
        'redirect_uri': f'{POSTER_URL}/tiktok/callback',
        'state': state
    }
    return redirect('https://www.tiktok.com/v2/auth/authorize/?' + urlencode(params))

@app.route('/tiktok/callback')
def tiktok_callback():
    code = request.args.get('code')
    state = request.args.get('state', '')
    error = request.args.get('error')
    if error:
        return f'<h2 style="color:red;font-family:monospace;padding:40px">Error: {error}</h2>'
    if not code:
        return '<h2 style="color:red;font-family:monospace;padding:40px">No code</h2>'
    try:
        token_response = requests.post(
            'https://open.tiktokapis.com/v2/oauth/token/',
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            data={
                'client_key': TIKTOK_CLIENT_KEY,
                'client_secret': TIKTOK_CLIENT_SECRET,
                'code': code,
                'grant_type': 'authorization_code',
                'redirect_uri': f'{POSTER_URL}/tiktok/callback'
            },
            timeout=15
        )
        token_data = token_response.json()
        if 'access_token' not in token_data:
            return f'<h2 style="color:red;font-family:monospace;padding:40px">Token error: {token_data}</h2>'
        account_label = state.split('-')[0] if '-' in state else 'account1'
        user_response = requests.get(
            'https://open.tiktokapis.com/v2/user/info/',
            params={'fields': 'open_id,display_name'},
            headers={'Authorization': f"Bearer {token_data['access_token']}"},
            timeout=10
        )
        user_data = user_response.json().get('data', {}).get('user', {})
        accounts = load_accounts()
        accounts = [a for a in accounts if a.get('label') != account_label]
        accounts.append({
            'label': account_label,
            'display_name': user_data.get('display_name', account_label),
            'open_id': user_data.get('open_id', ''),
            'access_token': token_data['access_token'],
            'refresh_token': token_data.get('refresh_token', ''),
            'expires_in': token_data.get('expires_in', 86400),
            'token_saved_at': time.time(),
            'posts_today': 0,
            'last_post': 0
        })
        save_accounts(accounts)
        push_to_bina(
            f"✅ TikTok Connected: @{user_data.get('display_name', account_label)}",
            f"Account **@{user_data.get('display_name', account_label)}** connected. Ready to post."
        )
        return f'''<html><body style="font-family:monospace;padding:40px;background:#000;color:#0f0;">
        <h2>✅ Connected!</h2>
        <p>@{user_data.get('display_name', account_label)} is ready to post.</p>
        <p style="color:#888;margin-top:20px">You can close this tab.</p>
        </body></html>'''
    except Exception as e:
        return f'<h2 style="color:red;font-family:monospace;padding:40px">Error: {str(e)}</h2>'

def refresh_token(account):
    if not account.get('refresh_token'):
        return account
    try:
        response = requests.post(
            'https://open.tiktokapis.com/v2/oauth/token/',
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            data={
                'client_key': TIKTOK_CLIENT_KEY,
                'client_secret': TIKTOK_CLIENT_SECRET,
                'grant_type': 'refresh_token',
                'refresh_token': account['refresh_token']
            }, timeout=15
        )
        data = response.json()
        if 'access_token' in data:
            account['access_token'] = data['access_token']
            account['refresh_token'] = data.get('refresh_token', account['refresh_token'])
            account['token_saved_at'] = time.time()
            account['expires_in'] = data.get('expires_in', 86400)
    except Exception as e:
        print(f"Token refresh error: {str(e)}")
    return account

def get_valid_token(account):
    saved_at = account.get('token_saved_at', 0)
    expires_in = account.get('expires_in', 86400)
    if time.time() > saved_at + expires_in - 300:
        account = refresh_token(account)
    return account

def post_to_tiktok(account, video_path, caption, hashtags):
    account = get_valid_token(account)
    token = account.get('access_token')
    if not token:
        return False, "No access token"
    full_caption = f"{caption}\n\n{hashtags}"
    try:
        file_size = os.path.getsize(video_path)
        init_response = requests.post(
            'https://open.tiktokapis.com/v2/post/publish/video/init/',
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
            json={
                'post_info': {
                    'title': full_caption[:2200],
                    'privacy_level': 'PUBLIC_TO_EVERYONE',
                    'disable_duet': False,
                    'disable_comment': False,
                    'disable_stitch': False,
                    'video_cover_timestamp_ms': 1000
                },
                'source_info': {
                    'source': 'FILE_UPLOAD',
                    'video_size': file_size,
                    'chunk_size': file_size,
                    'total_chunk_count': 1
                }
            }, timeout=30
        )
        init_data = init_response.json()
        if init_data.get('error', {}).get('code') != 'ok':
            return False, f"Init error: {init_data}"
        publish_id = init_data['data']['publish_id']
        upload_url = init_data['data']['upload_url']
        with open(video_path, 'rb') as f:
            video_data = f.read()
        upload_response = requests.put(
            upload_url,
            headers={
                'Content-Range': f'bytes 0-{file_size-1}/{file_size}',
                'Content-Length': str(file_size),
                'Content-Type': 'video/mp4'
            },
            data=video_data, timeout=300
        )
        if upload_response.status_code not in [200, 201, 206]:
            return False, f"Upload failed: {upload_response.status_code}"
        for i in range(10):
            time.sleep(5)
            status_response = requests.post(
                'https://open.tiktokapis.com/v2/post/publish/status/fetch/',
                headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
                json={'publish_id': publish_id}, timeout=15
            )
            status = status_response.json().get('data', {}).get('status', '')
            if status == 'PUBLISH_COMPLETE':
                return True, publish_id
            elif status in ['FAILED', 'PUBLISH_FAILED']:
                return False, f"Publish failed"
        return False, "Timeout"
    except Exception as e:
        return False, str(e)

@app.route('/upload-clip', methods=['POST'])
def upload_clip():
    if 'video' not in request.files:
        return jsonify({'error': 'No video file'}), 400
    file = request.files['video']
    caption = request.form.get('caption', '')
    hashtags = request.form.get('hashtags', '#fyp #viral #streamer #kick')
    account_label = request.form.get('account', 'account1')
    streamer = request.form.get('streamer', '')
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    file_id = str(uuid.uuid4())
    file_path = f"{UPLOAD_DIR}/{file_id}.mp4"
    file.save(file_path)
    queue = load_queue()
    item = {
        'id': file_id, 'video_path': file_path, 'caption': caption,
        'hashtags': hashtags, 'account_label': account_label,
        'added_at': time.time(), 'status': 'queued',
        'streamer': streamer, 'filename': file.filename
    }
    queue.append(item)
    save_queue(queue)
    push_to_bina(
        f"📥 Clip Queued: {file.filename}",
        f"Caption: {caption}\nStreamer: {streamer}\nAccount: {account_label}\nQueue position: #{len(queue)}"
    )
    return jsonify({'success': True, 'file_id': file_id, 'queue_position': len(queue)})

@app.route('/queue', methods=['GET'])
def get_queue():
    return jsonify({'queue': load_queue()})

@app.route('/accounts', methods=['GET'])
def get_accounts():
    accounts = load_accounts()
    safe = [{k: v for k, v in a.items() if k not in ['access_token', 'refresh_token']} for a in accounts]
    return jsonify({'accounts': safe, 'count': len(accounts)})

@app.route('/posted', methods=['GET'])
def get_posted():
    return jsonify({'posted': load_posted()})

@app.route('/status', methods=['GET'])
def status():
    la_time = datetime.datetime.utcnow() + datetime.timedelta(hours=-7)
    return jsonify({
        'status': 'online',
        'accounts': len(load_accounts()),
        'queue': len([i for i in load_queue() if i.get('status') == 'queued']),
        'posted': len(load_posted()),
        'la_time': la_time.strftime('%Y-%m-%d %H:%M'),
        'schedule': [f"{s['hour']:02d}:{s['minute']:02d}" for s in POSTING_SCHEDULE]
    })

@app.route('/')
def index():
    accounts = load_accounts()
    queue = load_queue()
    posted = load_posted()
    pending = [i for i in queue if i.get('status') == 'queued']
    acct_html = ''.join([f"<li>@{a.get('display_name','?')} ({a.get('label','?')}) ✅</li>" for a in accounts]) or '<li>No accounts connected</li>'
    return f'''<html><body style="font-family:monospace;padding:40px;background:#000;color:#0f0;max-width:800px">
    <h1>🎬 Bina Poster</h1>
    <p>Status: <strong>ONLINE</strong> | Queue: {len(pending)} | Posted: {len(posted)}</p>
    <h2>TikTok Accounts</h2>
    <ul>{acct_html}</ul>
    <a href="/tiktok/login?account=account1" style="color:#0f0">+ Connect Account 1</a> |
    <a href="/tiktok/login?account=account2" style="color:#0f0">+ Connect Account 2</a>
    <h2>Posting Times (LA)</h2>
    <p>7am · 12pm · 3pm · 6pm · 9pm · 11pm</p>
    <h2>Upload Edited Clip</h2>
    <form action="/upload-clip" method="post" enctype="multipart/form-data">
        <input type="file" name="video" accept="video/*" style="color:#0f0;background:#111;padding:8px"><br><br>
        <input type="text" name="caption" placeholder="Caption" style="width:400px;background:#111;color:#0f0;padding:8px;border:1px solid #0f0"><br><br>
        <input type="text" name="hashtags" placeholder="#fyp #viral #n3on #kick" style="width:400px;background:#111;color:#0f0;padding:8px;border:1px solid #0f0"><br><br>
        <input type="text" name="streamer" placeholder="Streamer name" style="width:200px;background:#111;color:#0f0;padding:8px;border:1px solid #0f0"><br><br>
        <select name="account" style="background:#111;color:#0f0;padding:8px;border:1px solid #0f0">
            <option value="account1">Account 1</option>
            <option value="account2">Account 2</option>
        </select><br><br>
        <button type="submit" style="background:#0f0;color:#000;padding:10px 20px;border:none;cursor:pointer;font-weight:bold">Queue Clip →</button>
    </form>
    </body></html>'''

def should_post_now(la_time):
    for slot in POSTING_SCHEDULE:
        if la_time.hour == slot['hour'] and la_time.minute in [slot['minute'], slot['minute'] + 1]:
            return True
    return False

def run_poster():
    print("Poster thread started")
    last_post_minute = -1
    while True:
        try:
            la_time = datetime.datetime.utcnow() + datetime.timedelta(hours=-7)
            current_minute = la_time.hour * 60 + la_time.minute
            if should_post_now(la_time) and current_minute != last_post_minute:
                last_post_minute = current_minute
                queue = load_queue()
                pending = [i for i in queue if i.get('status') == 'queued']
                if not pending:
                    print(f"Post slot {la_time.strftime('%H:%M')} — queue empty")
                    time.sleep(60)
                    continue
                item = pending[0]
                accounts = load_accounts()
                account = next((a for a in accounts if a.get('label') == item.get('account_label', 'account1')), accounts[0] if accounts else None)
                if not account:
                    print("No TikTok account connected")
                    time.sleep(60)
                    continue
                video_path = item.get('video_path', '')
                if not os.path.exists(video_path):
                    for q in queue:
                        if q['id'] == item['id']:
                            q['status'] = 'failed_missing_file'
                    save_queue(queue)
                    time.sleep(60)
                    continue
                print(f"Posting at {la_time.strftime('%H:%M')}: {item.get('caption','')[:40]}")
                success, result = post_to_tiktok(account, video_path, item.get('caption', ''), item.get('hashtags', ''))
                for q in queue:
                    if q['id'] == item['id']:
                        q['status'] = 'posted' if success else 'failed'
                        q['posted_at'] = time.time()
                        q['result'] = str(result)
                save_queue(queue)
                if success:
                    posted = load_posted()
                    posted.append({
                        'id': item['id'], 'caption': item.get('caption', ''),
                        'streamer': item.get('streamer', ''),
                        'account': account.get('display_name', '?'),
                        'posted_at': time.time(), 'publish_id': str(result)
                    })
                    save_posted(posted)
                    push_to_bina(
                        f"✅ Posted — {la_time.strftime('%H:%M')}",
                        f"**Posted to @{account.get('display_name','?')}**\nCaption: {item.get('caption','')}\nStreamer: {item.get('streamer','')}\nRemaining: {len(pending)-1}"
                    )
                else:
                    push_to_bina(f"❌ Post Failed — {la_time.strftime('%H:%M')}", f"Error: {result}\nCaption: {item.get('caption','')}")
            time.sleep(30)
        except Exception as e:
            print(f"Poster error: {str(e)}")
            time.sleep(60)

poster_thread = threading.Thread(target=run_poster, daemon=True)
poster_thread.start()

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=int(os.environ.get('PORT', 5001)))
