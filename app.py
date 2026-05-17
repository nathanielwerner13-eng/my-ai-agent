import os
import json
import re
import base64
import requests
import threading
import time
import datetime
import uuid
from urllib.parse import urlencode
from email.mime.text import MIMEText
from flask import Flask, request, jsonify, session, redirect
from flask_cors import CORS
from anthropic import Anthropic
from duckduckgo_search import DDGS
from pywebpush import webpush, WebPushException
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from pinecone import Pinecone
from openai import OpenAI

app = Flask(__name__, static_folder='.', static_url_path='')
app.secret_key = os.environ.get('SECRET_KEY', 'bina-secret-key-2024')
CORS(app)

client = Anthropic()
openai_client = OpenAI(api_key=os.environ.get('ANTHROPIC_API_KEY'))

# Pinecone setup
pc = Pinecone(api_key=os.environ.get('PINECONE_API_KEY', ''))
PINECONE_INDEX = os.environ.get('PINECONE_INDEX', 'bina-memory')

PASSPHRASE = 'bina2024'
NOTIFICATIONS_FILE = 'notifications.json'
SEEN_EMAILS_FILE = 'seen_emails.json'
SUBSCRIPTIONS_FILE = 'subscriptions.json'
GMAIL_SCOPES = 'https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/calendar'
REDIRECT_URI = 'https://my-ai-agent-production-5e17.up.railway.app/oauth/callback'
VAPID_PUBLIC_KEY = os.environ.get('VAPID_PUBLIC_KEY', '')
VAPID_PRIVATE_KEY = os.environ.get('VAPID_PRIVATE_KEY', '')
VAPID_CLAIMS_EMAIL = os.environ.get('VAPID_CLAIMS_EMAIL', 'mailto:nathanielwerner13@gmail.com')
BINA_URL = 'https://my-ai-agent-production-5e17.up.railway.app'
SERPER_API_KEY = os.environ.get('SERPER_API_KEY', '')

SYSTEM_PROMPT = """You are Bina (בינה), a fully autonomous AI agent and personal chief of staff for Nathaniel Werner.

ABOUT NATHANIEL:
- 18 years old, college student in Beverly Hills
- Entrepreneur focused on building autonomous income systems
- Interested in crypto, investments, Polymarket, TikTok/content, business automation
- Building toward financial freedom and passive income
- Direct, ambitious, moves fast, hates wasted time
- Jewish background (uses Hebrew greetings occasionally)

YOUR ROLE:
You are Nathaniel's personal chief of staff, business partner, and autonomous agent. You think ahead, spot opportunities, and help him execute fast. You treat him as a capable adult and never add unnecessary caveats or warnings.

YOUR CAPABILITIES:
- Send emails: SEND_EMAIL|to@email.com|Subject|Body END_EMAIL
- Create calendar events: CREATE_EVENT|Title|2026-05-16T10:00:00|2026-05-16T11:00:00|Description END_EVENT
- Deep web search with full article content (automatically triggered)
- Persistent memory across ALL conversations — you remember everything
- Research people, companies, investments, opportunities
- Help with business strategy, outreach, scheduling, and execution

MEMORY INSTRUCTIONS:
You have access to Nathaniel's memory from past conversations. When relevant memories are shown, reference them naturally — like a person who genuinely remembers past conversations. Say things like "You mentioned this last week..." or "Based on what you told me about X..." Don't make it robotic.

PERSONALITY:
- Sharp, direct, no fluff
- Proactive — tell him what he should know before he asks
- Think like a brilliant chief of staff who is always one step ahead
- When you find something interesting or an opportunity, flag it
- Be concise unless depth is needed
- Feel like a real relationship, not a tool

The current date and time in Los Angeles will be injected into every message."""


# ── Memory (Pinecone) ─────────────────────────────────────────────────────────

def get_embedding(text):
    """Get embedding using OpenAI's API."""
    try:
        # Use Anthropic API key won't work for OpenAI embeddings
        # Use a simple hash-based approach as fallback, or use OpenAI
        response = requests.post(
            'https://api.openai.com/v1/embeddings',
            headers={
                'Authorization': f'Bearer {os.environ.get("OPENAI_API_KEY", "")}',
                'Content-Type': 'application/json'
            },
            json={
                'model': 'text-embedding-ada-002',
                'input': text[:8000]
            }
        )
        if response.status_code == 200:
            return response.json()['data'][0]['embedding']
        return None
    except Exception as e:
        print(f"Embedding error: {str(e)}")
        return None

def save_memory(text, memory_type='conversation', metadata=None):
    """Save a memory to Pinecone."""
    try:
        embedding = get_embedding(text)
        if not embedding:
            return False
        index = pc.Index(PINECONE_INDEX)
        memory_id = str(uuid.uuid4())
        meta = {
            'text': text[:1000],
            'type': memory_type,
            'timestamp': time.time(),
            'date': datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
        }
        if metadata:
            meta.update(metadata)
        index.upsert(vectors=[{
            'id': memory_id,
            'values': embedding,
            'metadata': meta
        }])
        print(f"Memory saved: {text[:50]}...")
        return True
    except Exception as e:
        print(f"Save memory error: {str(e)}")
        return False

def search_memories(query, top_k=5):
    """Search for relevant memories."""
    try:
        embedding = get_embedding(query)
        if not embedding:
            return []
        index = pc.Index(PINECONE_INDEX)
        results = index.query(
            vector=embedding,
            top_k=top_k,
            include_metadata=True
        )
        memories = []
        for match in results.matches:
            if match.score > 0.75:  # Only highly relevant memories
                memories.append({
                    'text': match.metadata.get('text', ''),
                    'date': match.metadata.get('date', ''),
                    'type': match.metadata.get('type', ''),
                    'score': match.score
                })
        return memories
    except Exception as e:
        print(f"Search memory error: {str(e)}")
        return []

def format_memories(memories):
    """Format memories for injection into context."""
    if not memories:
        return ""
    output = "\n\nRelevant memories from past conversations:\n"
    for m in memories:
        output += f"[{m['date']}] {m['text']}\n"
    return output


# ── Push Subscriptions ────────────────────────────────────────────────────────

def load_subscriptions():
    if os.path.exists(SUBSCRIPTIONS_FILE):
        with open(SUBSCRIPTIONS_FILE, 'r') as f:
            return json.load(f)
    return []

def save_subscriptions(subs):
    with open(SUBSCRIPTIONS_FILE, 'w') as f:
        json.dump(subs, f)

def send_push(title, body, url=None):
    if url is None:
        url = BINA_URL
    if not VAPID_PRIVATE_KEY or not VAPID_PUBLIC_KEY:
        return
    subscriptions = load_subscriptions()
    if not subscriptions:
        return
    payload = json.dumps({'title': title, 'body': body, 'url': url})
    good_subs = []
    for sub in subscriptions:
        try:
            webpush(
                subscription_info=sub,
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={'sub': VAPID_CLAIMS_EMAIL}
            )
            good_subs.append(sub)
        except WebPushException as e:
            if '400' not in str(e) and '410' not in str(e):
                good_subs.append(sub)
    save_subscriptions(good_subs)


# ── Notifications ─────────────────────────────────────────────────────────────

def load_notifications():
    if os.path.exists(NOTIFICATIONS_FILE):
        with open(NOTIFICATIONS_FILE, 'r') as f:
            return json.load(f)
    return []

def save_notifications(notifications):
    with open(NOTIFICATIONS_FILE, 'w') as f:
        json.dump(notifications, f)

def add_notification(notif):
    notifications = load_notifications()
    notifications.insert(0, notif)
    notifications = notifications[:50]
    save_notifications(notifications)


# ── Seen Emails ───────────────────────────────────────────────────────────────

def load_seen_emails():
    if os.path.exists(SEEN_EMAILS_FILE):
        with open(SEEN_EMAILS_FILE, 'r') as f:
            return set(json.load(f))
    return set()

def save_seen_emails(seen):
    with open(SEEN_EMAILS_FILE, 'w') as f:
        json.dump(list(seen), f)


# ── Junk Filter ───────────────────────────────────────────────────────────────

def is_important_email(email):
    junk_keywords = [
        'noreply', 'no-reply', 'donotreply', 'newsletter', 'notifications@',
        'mailer', 'updates@', 'info@', 'support@', 'hello@', 'team@',
        'news@', 'digest', 'unsubscribe', 'marketing', 'promo', 'offer',
        'notification', 'alert@', 'automated', 'bounce', 'postmaster',
        'do-not-reply', 'system@', 'admin@', 'billing@', 'invoice@',
        'receipt@', 'confirm', 'verify', 'activate', 'password',
        'security alert', 'sign-in', 'signin', 'account activity',
        'canvas@', 'canvasemail', 'instructure', 'campusdpp'
    ]
    sender = email['from'].lower()
    subject = email['subject'].lower()
    for word in junk_keywords:
        if word in sender or word in subject:
            return False
    return True


# ── Web Search ────────────────────────────────────────────────────────────────

def web_search(query, num_results=5):
    if SERPER_API_KEY:
        try:
            response = requests.post(
                'https://google.serper.dev/search',
                headers={'X-API-KEY': SERPER_API_KEY, 'Content-Type': 'application/json'},
                json={'q': query, 'num': num_results}
            )
            data = response.json()
            output = f"Search results for '{query}':\n\n"
            if data.get('knowledgeGraph'):
                kg = data['knowledgeGraph']
                output += f"Quick answer: {kg.get('title', '')} — {kg.get('description', '')}\n\n"
            if data.get('answerBox'):
                ab = data['answerBox']
                answer = ab.get('answer') or ab.get('snippet') or ''
                if answer:
                    output += f"Direct answer: {answer}\n\n"
            for i, r in enumerate(data.get('organic', [])[:num_results], 1):
                output += f"{i}. {r.get('title', '')}\n   {r.get('link', '')}\n   {r.get('snippet', '')}\n\n"
            if data.get('news'):
                output += "Latest news:\n"
                for n in data['news'][:3]:
                    output += f"• {n.get('title', '')} — {n.get('date', '')}\n  {n.get('snippet', '')}\n\n"
            return output
        except Exception as e:
            print(f"Serper error: {str(e)}")
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=num_results))
            if results:
                output = f"Search results for '{query}':\n\n"
                for i, r in enumerate(results, 1):
                    output += f"{i}. {r['title']}\n{r['href']}\n{r['body']}\n\n"
                return output
        return "No results found."
    except Exception as e:
        return f"Search error: {str(e)}"


# ── Google Auth ───────────────────────────────────────────────────────────────

def get_access_token():
    response = requests.post('https://oauth2.googleapis.com/token', data={
        'refresh_token': os.environ.get('GOOGLE_REFRESH_TOKEN'),
        'client_id': os.environ.get('GOOGLE_CLIENT_ID'),
        'client_secret': os.environ.get('GOOGLE_CLIENT_SECRET'),
        'grant_type': 'refresh_token'
    })
    data = response.json()
    if 'error' in data:
        raise Exception(f"Token refresh failed: {data}")
    return data['access_token']


# ── Gmail ─────────────────────────────────────────────────────────────────────

def send_email(to, subject, body):
    try:
        access_token = get_access_token()
        message = MIMEText(body)
        message['to'] = to
        message['subject'] = subject
        message['from'] = os.environ.get('GMAIL_ADDRESS')
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        response = requests.post(
            'https://gmail.googleapis.com/gmail/v1/users/me/messages/send',
            headers={'Authorization': f'Bearer {access_token}', 'Content-Type': 'application/json'},
            json={'raw': raw}
        )
        if response.status_code == 200:
            return True, None
        else:
            return False, str(response.json())
    except Exception as e:
        return False, str(e)

def get_inbox_emails(max_results=10):
    try:
        access_token = get_access_token()
        response = requests.get(
            'https://gmail.googleapis.com/gmail/v1/users/me/messages',
            headers={'Authorization': f'Bearer {access_token}'},
            params={'maxResults': max_results, 'labelIds': 'INBOX', 'q': 'is:unread'}
        )
        data = response.json()
        messages = data.get('messages', [])
        emails = []
        for msg in messages:
            msg_response = requests.get(
                f'https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg["id"]}',
                headers={'Authorization': f'Bearer {access_token}'},
                params={'format': 'full'}
            )
            msg_data = msg_response.json()
            headers = {h['name']: h['value'] for h in msg_data.get('payload', {}).get('headers', [])}
            body = ''
            payload = msg_data.get('payload', {})
            if payload.get('body', {}).get('data'):
                body = base64.urlsafe_b64decode(payload['body']['data']).decode('utf-8', errors='ignore')
            elif payload.get('parts'):
                for part in payload['parts']:
                    if part.get('mimeType') == 'text/plain' and part.get('body', {}).get('data'):
                        body = base64.urlsafe_b64decode(part['body']['data']).decode('utf-8', errors='ignore')
                        break
            emails.append({
                'id': msg['id'],
                'from': headers.get('From', 'Unknown'),
                'subject': headers.get('Subject', '(no subject)'),
                'date': headers.get('Date', ''),
                'body': body[:2000]
            })
        return emails
    except Exception as e:
        print(f"Inbox error: {str(e)}")
        return []

def draft_reply(email):
    try:
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=500,
            system="You are Bina, drafting a reply on behalf of Nathaniel Werner, 18-year-old entrepreneur in Beverly Hills. Write a concise, professional reply. Just write the reply body only — no subject line, no greeting header, just the message text and sign off with Nathaniel Werner.",
            messages=[{"role": "user", "content": f"Draft a reply to this email:\n\nFrom: {email['from']}\nSubject: {email['subject']}\n\n{email['body']}"}]
        )
        return response.content[0].text
    except Exception as e:
        return f"Could not draft reply: {str(e)}"


# ── Google Calendar ───────────────────────────────────────────────────────────

def get_upcoming_events(max_results=10):
    try:
        access_token = get_access_token()
        now = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
        response = requests.get(
            'https://www.googleapis.com/calendar/v3/calendars/primary/events',
            headers={'Authorization': f'Bearer {access_token}'},
            params={'timeMin': now, 'maxResults': max_results, 'singleEvents': True, 'orderBy': 'startTime'}
        )
        data = response.json()
        events = []
        for item in data.get('items', []):
            start = item['start'].get('dateTime', item['start'].get('date', ''))
            end = item['end'].get('dateTime', item['end'].get('date', ''))
            events.append({
                'id': item['id'],
                'title': item.get('summary', '(no title)'),
                'start': start,
                'end': end,
                'description': item.get('description', '')
            })
        return events
    except Exception as e:
        print(f"Calendar read error: {str(e)}")
        return []

def create_calendar_event(title, start, end, description=''):
    try:
        access_token = get_access_token()
        event = {
            'summary': title,
            'description': description,
            'start': {'dateTime': start, 'timeZone': 'America/Los_Angeles'},
            'end': {'dateTime': end, 'timeZone': 'America/Los_Angeles'}
        }
        response = requests.post(
            'https://www.googleapis.com/calendar/v3/calendars/primary/events',
            headers={'Authorization': f'Bearer {access_token}', 'Content-Type': 'application/json'},
            json=event
        )
        if response.status_code in [200, 201]:
            return True, response.json().get('htmlLink', '')
        else:
            return False, str(response.json())
    except Exception as e:
        return False, str(e)

def process_calendar_commands(text):
    pattern = r'CREATE_EVENT\|([^\|]+)\|([^\|]+)\|([^\|]+)\|?([^E]*)END_EVENT'
    matches = re.findall(pattern, text, re.DOTALL)
    results = []
    for match in matches:
        title = match[0].strip()
        start = match[1].strip()
        end = match[2].strip()
        description = match[3].strip() if match[3] else ''
        success, link = create_calendar_event(title, start, end, description)
        results.append({'title': title, 'success': success, 'link': link})
    return results


# ── Morning Briefing ──────────────────────────────────────────────────────────

def generate_morning_briefing():
    try:
        events = get_upcoming_events(max_results=5)
        emails = get_inbox_emails(max_results=3)
        news = web_search("business crypto AI news today", num_results=3)
        events_text = "\n".join([f"- {e['title']} at {e['start']}" for e in events]) or "No upcoming events"
        emails_text = "\n".join([f"- From {e['from']}: {e['subject']}" for e in emails]) or "No unread emails"
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=500,
            system="You are Bina, Nathaniel's AI chief of staff. Give a sharp, energizing morning briefing covering his schedule, emails, and relevant news. Be concise and actionable. End with one key focus for the day.",
            messages=[{"role": "user", "content": f"Generate morning briefing.\n\nSchedule:\n{events_text}\n\nEmails:\n{emails_text}\n\nNews:\n{news}"}]
        )
        return response.content[0].text
    except Exception as e:
        return f"Briefing error: {str(e)}"


# ── Inbox Monitor ─────────────────────────────────────────────────────────────

def monitor_inbox():
    print("Inbox monitor started")
    last_briefing_day = -1
    while True:
        try:
            la_time = datetime.datetime.utcnow() + datetime.timedelta(hours=-7)
            la_hour = la_time.hour
            la_day = la_time.timetuple().tm_yday

            if la_hour == 7 and la_day != last_briefing_day:
                briefing = generate_morning_briefing()
                add_notification({
                    'id': f'briefing-{la_day}',
                    'type': 'briefing',
                    'subject': '☀️ Morning Briefing',
                    'from': 'Bina',
                    'body': briefing,
                    'draft_reply': '',
                    'read': False,
                    'timestamp': time.time()
                })
                send_push('☀️ Bina', 'Your morning briefing is ready.', BINA_URL)
                last_briefing_day = la_day

            seen = load_seen_emails()
            emails = get_inbox_emails(max_results=10)
            for email in emails:
                if email['id'] not in seen:
                    seen.add(email['id'])
                    if is_important_email(email):
                        draft = draft_reply(email)
                        sender = email['from'].split('<')[0].strip()
                        add_notification({
                            'id': email['id'],
                            'type': 'email',
                            'from': email['from'],
                            'subject': email['subject'],
                            'body': email['body'][:500],
                            'draft_reply': draft,
                            'read': False,
                            'timestamp': time.time()
                        })
                        save_memory(
                            f"Received email from {email['from']} about: {email['subject']}",
                            memory_type='email'
                        )
                        send_push('Bina — Review Required', f'New message from {sender}.', BINA_URL)
                        print(f"Important + push: {email['from']}")
                    else:
                        print(f"Filtered: {email['from']} - {email['subject']}")
            save_seen_emails(seen)
        except Exception as e:
            print(f"Monitor error: {str(e)}")
        time.sleep(60)


# ── Email Command Processing ──────────────────────────────────────────────────

def process_email_commands(text):
    pattern = r'SEND_EMAIL\|(.*?)\|(.*?)\|(.*?)END_EMAIL'
    matches = re.findall(pattern, text, re.DOTALL)
    results = []
    for to, subject, body in matches:
        success, error = send_email(to.strip(), subject.strip(), body.strip())
        results.append({'to': to.strip(), 'subject': subject.strip(), 'success': success, 'error': error})
    return results

def clean_response(text):
    cleaned = re.sub(r'SEND_EMAIL\|.*?END_EMAIL', '', text, flags=re.DOTALL)
    cleaned = re.sub(r'CREATE_EVENT\|.*?END_EVENT', '', cleaned, flags=re.DOTALL)
    return cleaned.strip()


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return app.send_static_file('index.html')

@app.route('/manifest.json')
def manifest():
    return app.send_static_file('manifest.json')

@app.route('/sw.js')
def service_worker():
    return app.send_static_file('sw.js')

@app.route('/verify-passphrase', methods=['POST'])
def verify_passphrase():
    data = request.json
    if data.get('passphrase') == PASSPHRASE:
        session['authenticated'] = True
        return jsonify({'success': True})
    return jsonify({'success': False}), 401

@app.route('/clear-subs')
def clear_subs():
    save_subscriptions([])
    return '<h2 style="font-family:monospace;color:green;padding:40px">✅ Subscriptions cleared!</h2>'

@app.route('/subscribe', methods=['POST'])
def subscribe():
    sub = request.json
    subs = load_subscriptions()
    if sub not in subs:
        subs.append(sub)
        save_subscriptions(subs)
    print(f"Subscription saved. Total: {len(subs)}")
    return jsonify({'success': True})

@app.route('/vapid-public-key')
def vapid_key():
    return jsonify({'key': VAPID_PUBLIC_KEY})

@app.route('/generate-vapid')
def generate_vapid():
    try:
        private_key = ec.generate_private_key(ec.SECP256R1())
        pub_bytes = private_key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        priv_numbers = private_key.private_numbers()
        priv_raw = priv_numbers.private_value.to_bytes(32, 'big')
        pub_b64 = base64.urlsafe_b64encode(pub_bytes).rstrip(b"=").decode()
        priv_b64 = base64.urlsafe_b64encode(priv_raw).rstrip(b"=").decode()
        keys_match = pub_b64.endswith(priv_b64) or priv_b64 in pub_b64
        return f"""<html><body style="font-family:monospace;padding:40px;background:#000;color:#0f0;">
        <h2>✅ Fresh VAPID Keys</h2>
        <p>Public: {len(pub_b64)} | Private: {len(priv_b64)}</p>
        <p style="color:{'red' if keys_match else '#0f0'}">{'OVERLAPPING - REFRESH' if keys_match else 'distinct ✅'}</p>
        <p><b>PUBLIC:</b></p>
        <textarea onclick="this.select()" style="width:100%;height:60px;background:#111;color:#0f0;padding:8px;">{pub_b64}</textarea>
        <p><b>PRIVATE:</b></p>
        <textarea onclick="this.select()" style="width:100%;height:60px;background:#111;color:#0f0;padding:8px;">{priv_b64}</textarea>
        </body></html>"""
    except Exception as e:
        import traceback
        return f'<pre style="color:red;padding:40px">{traceback.format_exc()}</pre>'

@app.route('/notifications', methods=['GET'])
def get_notifications():
    notifications = load_notifications()
    unread = [n for n in notifications if not n.get('read')]
    return jsonify({'notifications': notifications, 'unread_count': len(unread)})

@app.route('/notifications/read/<notif_id>', methods=['POST'])
def mark_read(notif_id):
    notifications = load_notifications()
    for n in notifications:
        if n['id'] == notif_id:
            n['read'] = True
    save_notifications(notifications)
    return jsonify({'success': True})

@app.route('/send-draft', methods=['POST'])
def send_draft():
    data = request.json
    to = data.get('to')
    subject = data.get('subject')
    body = data.get('body')
    success, error = send_email(to, f"Re: {subject}", body)
    if success:
        notifications = load_notifications()
        for n in notifications:
            if n.get('subject') == subject:
                n['replied'] = True
        save_notifications(notifications)
        send_push('Bina ✅', 'Reply sent successfully.', BINA_URL)
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': error})

@app.route('/calendar', methods=['GET'])
def get_calendar():
    return jsonify({'events': get_upcoming_events(max_results=10)})

@app.route('/calendar/create', methods=['POST'])
def create_event_route():
    data = request.json
    success, link = create_calendar_event(
        data.get('title'), data.get('start'), data.get('end'), data.get('description', ''))
    return jsonify({'success': success, 'link': link})

@app.route('/test-push')
def test_push():
    send_push('Bina 🔔', 'Test notification.', BINA_URL)
    return '<h2 style="color:green;font-family:monospace;padding:40px">✅ Push sent!</h2>'

@app.route('/test-email')
def test_email():
    success, error = send_email('iirawgunzsii@gmail.com', 'Test from Bina', 'Hey! Bina testing.')
    if success:
        return '<h2 style="color:green;font-family:monospace;padding:40px">✅ Email sent!</h2>'
    return f'<h2 style="color:red;font-family:monospace;padding:40px">❌ Failed: {error}</h2>'

@app.route('/memories', methods=['GET'])
def get_memories():
    """Debug route to see stored memories."""
    query = request.args.get('q', 'Nathaniel')
    memories = search_memories(query, top_k=10)
    return jsonify({'memories': memories})

@app.route('/chat', methods=['POST'])
def chat():
    if not session.get('authenticated'):
        return jsonify({'error': 'Not authenticated'}), 401

    data = request.json
    user_message = data.get('message', '')
    conversation_history = data.get('history', [])

    la_time = datetime.datetime.utcnow() + datetime.timedelta(hours=-7)
    la_time_str = la_time.strftime('%A, %B %d, %Y %I:%M %p')
    user_message_with_context = f"[Current date and time in Los Angeles: {la_time_str}]\n\n{user_message}"

    # Search relevant memories
    memories = search_memories(user_message, top_k=5)
    memory_context = format_memories(memories)

    # Web search triggers
    search_triggers = ['search', 'look up', 'find', 'what is', 'who is', 'latest', 'news',
                       'current', 'price', 'stock', 'weather', 'research', 'tell me about',
                       'what happened', 'crypto', 'bitcoin', 'market', 'today', 'trending',
                       'recent', 'update', 'best', 'top', 'review', 'how is', 'how are']
    deep_triggers = ['research', 'deep dive', 'everything about', 'full report',
                     'analyze', 'investigate', 'background on', 'who is', 'tell me about']

    msg_lower = user_message.lower()

    if any(word in msg_lower for word in deep_triggers):
        search_results = web_search(user_message, num_results=5)
        user_message_with_context += f"\n\nDeep research:\n{search_results}"
    elif any(word in msg_lower for word in search_triggers):
        search_results = web_search(user_message)
        user_message_with_context += f"\n\nSearch results:\n{search_results}"

    # Calendar context
    calendar_triggers = ['schedule', 'calendar', 'event', 'meeting', 'appointment',
                         'tomorrow', 'next week', 'briefing', 'what do i have', 'today']
    if any(word in msg_lower for word in calendar_triggers):
        events = get_upcoming_events(max_results=5)
        if events:
            events_text = "\n".join([f"- {e['title']} at {e['start']}" for e in events])
            user_message_with_context += f"\n\nUpcoming events:\n{events_text}"
        else:
            user_message_with_context += "\n\nCalendar is currently empty."

    messages = conversation_history + [{"role": "user", "content": user_message_with_context}]

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=2048,
        system=SYSTEM_PROMPT + memory_context,
        messages=messages
    )

    assistant_message = response.content[0].text
    email_results = process_email_commands(assistant_message)
    calendar_results = process_calendar_commands(assistant_message)
    display_message = clean_response(assistant_message)

    # Save conversation to memory automatically
    memory_text = f"Nathaniel said: {user_message} | Bina responded: {display_message[:200]}"
    save_memory(memory_text, memory_type='conversation')

    # Save explicit memories
    memory_triggers = ['remember', 'save', 'note', 'important', "don't forget", 'keep in mind', 'remind me']
    if any(word in msg_lower for word in memory_triggers):
        save_memory(f"IMPORTANT - Nathaniel said to remember: {user_message}", memory_type='explicit')

    result = {'response': display_message}
    if email_results:
        sent = [e for e in email_results if e['success']]
        failed = [e for e in email_results if not e['success']]
        if sent:
            result['email_sent'] = f"✅ Email sent to {sent[0]['to']}"
            send_push('Bina ✅', f'Message sent to {sent[0]["to"]}', BINA_URL)
            save_memory(f"Sent email to {sent[0]['to']} with subject: {sent[0]['subject']}", memory_type='email')
        if failed:
            result['email_error'] = f"❌ Email failed: {failed[0]['error']}"
    if calendar_results:
        created = [e for e in calendar_results if e['success']]
        if created:
            result['event_created'] = f"📅 Event created: {created[0]['title']}"
            send_push('Bina 📅', f'"{created[0]["title"]}" added to your calendar', BINA_URL)
            save_memory(f"Created calendar event: {created[0]['title']}", memory_type='calendar')

    return jsonify(result)


# ── OAuth ─────────────────────────────────────────────────────────────────────

@app.route('/authorize')
def authorize():
    params = {
        'client_id': os.environ.get('GOOGLE_CLIENT_ID'),
        'redirect_uri': REDIRECT_URI,
        'response_type': 'code',
        'scope': GMAIL_SCOPES,
        'access_type': 'offline',
        'prompt': 'consent'
    }
    url = 'https://accounts.google.com/o/oauth2/auth?' + urlencode(params)
    return redirect(url)

@app.route('/oauth/callback')
def oauth_callback():
    code = request.args.get('code')
    error = request.args.get('error')
    if error:
        return f'<h2 style="color:red">Error: {error}</h2>', 400
    if not code:
        return '<h2 style="color:red">Error: no code returned</h2>', 400
    token_response = requests.post('https://oauth2.googleapis.com/token', data={
        'code': code,
        'client_id': os.environ.get('GOOGLE_CLIENT_ID'),
        'client_secret': os.environ.get('GOOGLE_CLIENT_SECRET'),
        'redirect_uri': REDIRECT_URI,
        'grant_type': 'authorization_code'
    })
    tokens = token_response.json()
    refresh_token = tokens.get('refresh_token', '')
    note = "✅ Copy token to GOOGLE_REFRESH_TOKEN in Railway." if refresh_token else "⚠️ No refresh token."
    return f"""<html><body style="font-family:monospace;padding:40px;background:#000;color:#0f0;">
    <h2>OAuth Callback</h2>
    <textarea style="width:100%;height:80px;background:#111;color:#0f0;padding:8px;">{refresh_token}</textarea>
    <p>{note}</p>
    <pre style="background:#111;padding:10px;color:#ff0;">{json.dumps(tokens, indent=2)}</pre>
    </body></html>"""


# ── Start monitor thread ──────────────────────────────────────────────────────

monitor_thread = threading.Thread(target=monitor_inbox, daemon=True)
monitor_thread.start()

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
