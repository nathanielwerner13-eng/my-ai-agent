import os
import json
import re
import base64
import requests
import threading
import time
from urllib.parse import urlencode
from email.mime.text import MIMEText
from flask import Flask, request, jsonify, session, redirect
from flask_cors import CORS
from anthropic import Anthropic
from duckduckgo_search import DDGS

app = Flask(__name__, static_folder='.', static_url_path='')
app.secret_key = os.environ.get('SECRET_KEY', 'bina-secret-key-2024')
CORS(app)

client = Anthropic()

PASSPHRASE = 'bina2024'
MEMORY_FILE = 'memory.json'
NOTIFICATIONS_FILE = 'notifications.json'
SEEN_EMAILS_FILE = 'seen_emails.json'
GMAIL_SCOPES = 'https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/calendar'
REDIRECT_URI = 'https://my-ai-agent-production-5e17.up.railway.app/oauth/callback'

SYSTEM_PROMPT = """You are Bina (בינה), a fully autonomous AI agent and personal chief of staff for Nathaniel Werner, an 18-year-old entrepreneur and college student in Beverly Hills.

You manage his personal life, business, investments, education, and help build toward his goal of creating autonomous income systems.

You are proactive, intelligent, direct, and treat Nathaniel as a capable adult. You never add unnecessary caveats. You think ahead, spot opportunities, and help him move fast.

You can:
- Send emails by outputting exactly: SEND_EMAIL|to@email.com|Subject Line|Body text here END_EMAIL
- Create calendar events by outputting exactly: CREATE_EVENT|Title|2026-05-16T10:00:00|2026-05-16T11:00:00|Description END_EVENT
- Search the web when needed for current information
- Remember important information across conversations
- Help with business strategy, investments, scheduling, research, and execution

Always be concise unless depth is needed. Think like a brilliant chief of staff who is always one step ahead."""


# ── Memory ───────────────────────────────────────────────────────────────────

def load_memory():
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, 'r') as f:
            return json.load(f)
    return []

def save_memory(memories):
    with open(MEMORY_FILE, 'w') as f:
        json.dump(memories, f)


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


# ── Web Search ────────────────────────────────────────────────────────────────

def web_search(query):
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=5))
            if results:
                output = f"Web search results for '{query}':\n\n"
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
            system="You are Bina, drafting a reply on behalf of Nathaniel Werner, 18-year-old entrepreneur in Beverly Hills. Write a concise, professional reply. Just write the reply body — no subject, no greeting header, just the message text.",
            messages=[{"role": "user", "content": f"Draft a reply to this email:\n\nFrom: {email['from']}\nSubject: {email['subject']}\n\n{email['body']}"}]
        )
        return response.content[0].text
    except Exception as e:
        return f"Could not draft reply: {str(e)}"


# ── Google Calendar ───────────────────────────────────────────────────────────

def get_upcoming_events(max_results=10):
    try:
        access_token = get_access_token()
        now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        response = requests.get(
            'https://www.googleapis.com/calendar/v3/calendars/primary/events',
            headers={'Authorization': f'Bearer {access_token}'},
            params={
                'timeMin': now,
                'maxResults': max_results,
                'singleEvents': True,
                'orderBy': 'startTime'
            }
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
        print(f"Calendar error: {str(e)}")
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
    pattern = r'CREATE_EVENT\|(.*?)\|(.*?)\|(.*?)\|(.*?)END_EVENT'
    matches = re.findall(pattern, text, re.DOTALL)
    results = []
    for title, start, end, description in matches:
        success, link = create_calendar_event(title.strip(), start.strip(), end.strip(), description.strip())
        results.append({'title': title.strip(), 'success': success, 'link': link})
    return results


# ── Morning Briefing ──────────────────────────────────────────────────────────

def generate_morning_briefing():
    try:
        events = get_upcoming_events(max_results=5)
        emails = get_inbox_emails(max_results=3)
        events_text = "\n".join([f"- {e['title']} at {e['start']}" for e in events]) or "No upcoming events"
        emails_text = "\n".join([f"- From {e['from']}: {e['subject']}" for e in emails]) or "No unread emails"
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=400,
            system="You are Bina, Nathaniel's AI chief of staff. Give a sharp, energizing morning briefing. Be concise and actionable.",
            messages=[{"role": "user", "content": f"Generate a morning briefing.\n\nUpcoming events:\n{events_text}\n\nUnread emails:\n{emails_text}"}]
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
            # Morning briefing at 7am LA time
            la_hour = (time.gmtime().tm_hour - 7) % 24
            la_day = time.gmtime().tm_yday
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
                last_briefing_day = la_day

            # Check inbox
            seen = load_seen_emails()
            emails = get_inbox_emails(max_results=10)
            for email in emails:
                if email['id'] not in seen:
                    seen.add(email['id'])
                    draft = draft_reply(email)
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
                    print(f"New email from {email['from']}: {email['subject']}")
            save_seen_emails(seen)
        except Exception as e:
            print(f"Monitor error: {str(e)}")
        time.sleep(60)


# ── Email/Calendar Command Processing ────────────────────────────────────────

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

@app.route('/verify-passphrase', methods=['POST'])
def verify_passphrase():
    data = request.json
    if data.get('passphrase') == PASSPHRASE:
        session['authenticated'] = True
        return jsonify({'success': True})
    return jsonify({'success': False}), 401

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
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': error})

@app.route('/calendar', methods=['GET'])
def get_calendar():
    events = get_upcoming_events(max_results=10)
    return jsonify({'events': events})

@app.route('/calendar/create', methods=['POST'])
def create_event():
    data = request.json
    success, link = create_calendar_event(
        data.get('title'),
        data.get('start'),
        data.get('end'),
        data.get('description', '')
    )
    return jsonify({'success': success, 'link': link})

@app.route('/test-email')
def test_email():
    token = os.environ.get('GOOGLE_REFRESH_TOKEN', 'NOT SET')
    success, error = send_email('iirawgunzsii@gmail.com', 'Test from Bina', 'Hey! This is Bina testing email directly.')
    if success:
        return f'<h2 style="color:green;font-family:monospace">✅ Email sent!</h2><p style="font-family:monospace">Token: {token[:30]}...</p>'
    else:
        return f'<h2 style="color:red;font-family:monospace">❌ Failed: {error}</h2><p style="font-family:monospace">Token: {token[:30]}...</p>'

@app.route('/chat', methods=['POST'])
def chat():
    if not session.get('authenticated'):
        return jsonify({'error': 'Not authenticated'}), 401

    data = request.json
    user_message = data.get('message', '')
    conversation_history = data.get('history', [])

    search_triggers = ['search', 'look up', 'find', 'what is', 'who is', 'latest', 'news', 'current', 'today', 'price', 'stock', 'weather']
    if any(word in user_message.lower() for word in search_triggers):
        search_results = web_search(user_message)
        user_message_with_context = f"{user_message}\n\nSearch results:\n{search_results}"
    else:
        user_message_with_context = user_message

    # Inject calendar context if scheduling related
    calendar_triggers = ['schedule', 'calendar', 'event', 'meeting', 'appointment', 'tomorrow', 'next week', 'briefing']
    if any(word in user_message.lower() for word in calendar_triggers):
        events = get_upcoming_events(max_results=5)
        if events:
            events_text = "\n".join([f"- {e['title']} at {e['start']}" for e in events])
            user_message_with_context += f"\n\nUpcoming calendar events:\n{events_text}"

    memories = load_memory()
    memory_context = ""
    if memories:
        memory_context = "\n\nRelevant memories:\n" + "\n".join(memories[-10:])

    current_time = time.strftime('%A, %B %d, %Y %I:%M %p', time.gmtime())
la_offset = -7
import datetime
la_time = datetime.datetime.utcnow() + datetime.timedelta(hours=la_offset)
la_time_str = la_time.strftime('%A, %B %d, %Y %I:%M %p')
user_message_with_context = f"[Current date and time in Los Angeles: {la_time_str}]\n\n" + user_message_with_context
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

    if any(word in user_message.lower() for word in ['remember', 'save', 'note', 'important']):
        memories.append(f"User said: {user_message}")
        save_memory(memories)

    result = {'response': display_message}
    if email_results:
        sent = [e for e in email_results if e['success']]
        failed = [e for e in email_results if not e['success']]
        if sent:
            result['email_sent'] = f"✅ Email sent to {sent[0]['to']}"
        if failed:
            result['email_error'] = f"❌ Email failed: {failed[0]['error']}"
    if calendar_results:
        created = [e for e in calendar_results if e['success']]
        if created:
            result['event_created'] = f"📅 Event created: {created[0]['title']}"

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
    if not refresh_token:
        note = "⚠️ No refresh token. Revoke access at myaccount.google.com/permissions then try again."
    else:
        note = "✅ Copy the token and set it as GOOGLE_REFRESH_TOKEN in Railway."
    return f"""
    <html><body style="font-family:monospace;padding:40px;background:#000;color:#0f0;">
    <h2>OAuth Callback</h2>
    <p><b>Refresh Token:</b></p>
    <textarea style="width:100%;height:80px;background:#111;color:#0f0;font-size:13px;padding:8px;">{refresh_token}</textarea>
    <br><br><p>{note}</p>
    <p><b>Full response:</b></p>
    <pre style="background:#111;padding:10px;color:#ff0;">{json.dumps(tokens, indent=2)}</pre>
    </body></html>
    """


# ── Start monitor thread ──────────────────────────────────────────────────────

monitor_thread = threading.Thread(target=monitor_inbox, daemon=True)
monitor_thread.start()

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
