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
 
app = Flask(__name__, static_folder='.', static_url_path='')
app.secret_key = os.environ.get('SECRET_KEY', 'bina-secret-key-2024')
CORS(app)
 
client = Anthropic()
pc = Pinecone(api_key=os.environ.get('PINECONE_API_KEY', ''))
PINECONE_INDEX = os.environ.get('PINECONE_INDEX', 'bina-memory')
 
NOTIFICATIONS_FILE = 'notifications.json'
SEEN_EMAILS_FILE = 'seen_emails.json'
SUBSCRIPTIONS_FILE = 'subscriptions.json'
OVERNIGHT_REPORT_FILE = 'overnight_report.json'
GMAIL_SCOPES = 'https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/calendar'
REDIRECT_URI = 'https://my-ai-agent-production-5e17.up.railway.app/oauth/callback'
VAPID_PUBLIC_KEY = os.environ.get('VAPID_PUBLIC_KEY', '')
VAPID_PRIVATE_KEY = os.environ.get('VAPID_PRIVATE_KEY', '')
VAPID_CLAIMS_EMAIL = os.environ.get('VAPID_CLAIMS_EMAIL', 'mailto:nathanielwerner13@gmail.com')
BINA_URL = 'https://my-ai-agent-production-5e17.up.railway.app'
SERPER_API_KEY = os.environ.get('SERPER_API_KEY', '')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', '')
ALPHA_VANTAGE_KEY = os.environ.get('ALPHA_VANTAGE_KEY', '')
FRED_API_KEY = os.environ.get('FRED_API_KEY', '')
NEWS_API_KEY = os.environ.get('NEWS_API_KEY', '')
 
SYSTEM_PROMPT = """You are Bina (בינה), a fully autonomous AI agent and personal chief of staff for Nathaniel Werner.
 
ABOUT NATHANIEL:
- 18 years old, college student in Beverly Hills
- Entrepreneur focused on building autonomous income systems
- Interested in crypto, investments, energy infrastructure stocks, world politics
- Watches: Bitcoin, Ethereum, Solana, Chainlink, Render
- Tracks: Energy stocks (XOM, CVX, NEE, CEG, KMI, COP, OXY, FSLR, VST, WMB), utilities, renewables, nuclear
- Interested in US politics, world politics, daily investment opportunities
- Building toward financial freedom and passive income
- Direct, ambitious, moves fast, hates wasted time
- Jewish background (uses Hebrew greetings occasionally)
 
YOUR ROLE:
You are Nathaniel's personal chief of staff, business partner, and autonomous agent. You think ahead, spot opportunities, and help him execute fast.
 
YOUR CAPABILITIES:
- Send emails: SEND_EMAIL|to@email.com|Subject|Body END_EMAIL
- Create calendar events: CREATE_EVENT|Title|2026-05-17T10:00:00|2026-05-17T11:00:00|Description END_EVENT
- Deep web search, persistent memory, live crypto/commodities/economic data
- Real-time energy/stock intelligence via dedicated research worker (reports at 7am, 1pm, 8pm LA)
 
CRITICAL MEMORY INSTRUCTIONS:
Reference memories naturally. Never ignore relevant memories.
 
PERSONALITY:
- Sharp, direct, no fluff
- Write like a smart friend texting — not a formal report
- Be concise unless depth is needed
- Only recommend positions when you have verified real data and understand the situation
 
The current date and time in Los Angeles will be injected into every message."""
 
 
# ── Memory ────────────────────────────────────────────────────────────────────
 
def get_embedding(text):
    try:
        response = requests.post(
            'https://api.openai.com/v1/embeddings',
            headers={'Authorization': f'Bearer {OPENAI_API_KEY}', 'Content-Type': 'application/json'},
            json={'model': 'text-embedding-ada-002', 'input': text[:8000]}
        )
        if response.status_code == 200:
            return response.json()['data'][0]['embedding']
        return None
    except Exception as e:
        print(f"Embedding error: {str(e)}")
        return None
 
def save_memory(text, memory_type='conversation', metadata=None):
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
        index.upsert(vectors=[{'id': memory_id, 'values': embedding, 'metadata': meta}])
        return True
    except Exception as e:
        print(f"Save memory error: {str(e)}")
        return False
 
def search_memories(query, top_k=8, threshold=0.5):
    try:
        embedding = get_embedding(query)
        if not embedding:
            return []
        index = pc.Index(PINECONE_INDEX)
        results = index.query(vector=embedding, top_k=top_k, include_metadata=True)
        memories = []
        for match in results.matches:
            if match.score > threshold:
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
 
def get_all_context_memories(user_message):
    msg_memories = search_memories(user_message, top_k=8, threshold=0.5)
    personal_memories = search_memories("Nathaniel family mother father friends personal life", top_k=5, threshold=0.4)
    business_memories = search_memories("Nathaniel business goals investments ideas plans", top_k=5, threshold=0.4)
    seen_texts = set()
    all_memories = []
    for m in msg_memories + personal_memories + business_memories:
        if m['text'] not in seen_texts:
            seen_texts.add(m['text'])
            all_memories.append(m)
    all_memories.sort(key=lambda x: x['score'], reverse=True)
    return all_memories[:12]
 
def format_memories(memories):
    if not memories:
        return ""
    output = "\n\nWhat you remember about Nathaniel:\n"
    for m in memories:
        output += f"• [{m['date']}] {m['text']}\n"
    return output
 
 
# ── Push ──────────────────────────────────────────────────────────────────────
 
def load_subscriptions():
    if os.path.exists(SUBSCRIPTIONS_FILE):
        with open(SUBSCRIPTIONS_FILE, 'r') as f:
            return json.load(f)
    return []
 
def save_subscriptions(subs):
    with open(SUBSCRIPTIONS_FILE, 'w') as f:
        json.dump(subs, f)
 
def send_push(title, body, url=None, notif_type='feed'):
    if url is None:
        url = BINA_URL
    if not VAPID_PRIVATE_KEY or not VAPID_PUBLIC_KEY:
        return
    subscriptions = load_subscriptions()
    if not subscriptions:
        return
    payload = json.dumps({'title': title, 'body': body, 'url': url, 'type': notif_type})
    good_subs = []
    for sub in subscriptions:
        try:
            webpush(subscription_info=sub, data=payload,
                    vapid_private_key=VAPID_PRIVATE_KEY,
                    vapid_claims={'sub': VAPID_CLAIMS_EMAIL})
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
            output = f"Search: '{query}':\n"
            if data.get('answerBox'):
                answer = data['answerBox'].get('answer') or data['answerBox'].get('snippet') or ''
                if answer:
                    output += f"Answer: {answer}\n"
            for r in data.get('organic', [])[:num_results]:
                output += f"• {r.get('title', '')}: {r.get('snippet', '')}\n"
            for n in data.get('news', [])[:3]:
                output += f"• NEWS ({n.get('date', '')}): {n.get('title', '')} — {n.get('snippet', '')}\n"
            return output
        except Exception as e:
            print(f"Serper error: {str(e)}")
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=num_results))
            return "\n".join([f"• {r['title']}: {r['body']}" for r in results])
    except Exception as e:
        return f"Search error: {str(e)}"
 
 
# ── Crypto ────────────────────────────────────────────────────────────────────
 
def get_crypto_data():
    try:
        response = requests.get(
            'https://api.coingecko.com/api/v3/simple/price',
            params={
                'ids': 'bitcoin,ethereum,solana,chainlink,render-token',
                'vs_currencies': 'usd',
                'include_24hr_change': 'true',
                'include_24hr_vol': 'true',
            },
            timeout=10
        )
        if response.status_code == 200:
            return response.json()
        return {}
    except:
        return {}
 
def get_fear_greed_index():
    try:
        response = requests.get('https://api.alternative.me/fng/', timeout=10)
        if response.status_code == 200:
            return response.json()['data'][0]
        return {}
    except:
        return {}
 
def format_crypto_report(crypto_data, fear_greed):
    if not crypto_data:
        return "Crypto data unavailable."
    coin_names = {
        'bitcoin': 'Bitcoin (BTC)',
        'ethereum': 'Ethereum (ETH)',
        'solana': 'Solana (SOL)',
        'chainlink': 'Chainlink (LINK)',
        'render-token': 'Render (RNDR)'
    }
    output = "**Crypto Prices**\n"
    if fear_greed:
        output += f"Fear & Greed: **{fear_greed.get('value')} — {fear_greed.get('value_classification')}**\n\n"
    for coin_id, name in coin_names.items():
        if coin_id in crypto_data:
            d = crypto_data[coin_id]
            price = d.get('usd', 0)
            change = d.get('usd_24h_change', 0) or 0
            arrow = '📈' if change > 0 else '📉'
            output += f"{arrow} **{name}**: ${price:,.2f} | {change:+.2f}% 24h\n"
    return output
 
 
# ── Commodities ───────────────────────────────────────────────────────────────
 
def get_real_commodity_prices():
    output = "**Commodities**\n"
    if ALPHA_VANTAGE_KEY:
        try:
            gold_response = requests.get(
                'https://www.alphavantage.co/query',
                params={'function': 'CURRENCY_EXCHANGE_RATE', 'from_currency': 'XAU',
                        'to_currency': 'USD', 'apikey': ALPHA_VANTAGE_KEY},
                timeout=10
            )
            if gold_response.status_code == 200:
                rate = gold_response.json().get('Realtime Currency Exchange Rate', {})
                gold_price = rate.get('5. Exchange Rate', None)
                if gold_price:
                    output += f"🥇 **Gold**: ${float(gold_price):,.2f}/oz\n"
        except:
            pass
        try:
            oil_response = requests.get(
                'https://www.alphavantage.co/query',
                params={'function': 'BRENT', 'interval': 'daily', 'apikey': ALPHA_VANTAGE_KEY},
                timeout=10
            )
            if oil_response.status_code == 200:
                series = oil_response.json().get('data', [])
                if series:
                    output += f"🛢️ **Brent Oil**: ${float(series[0].get('value', 0)):,.2f}/barrel\n"
        except:
            pass
    else:
        output += web_search("gold price oil price today USD", num_results=2)[:200]
    return output
 
 
# ── FRED ──────────────────────────────────────────────────────────────────────
 
def get_fred_data():
    if not FRED_API_KEY:
        return "FRED key not configured."
    indicators = {
        'FEDFUNDS': 'Fed Funds Rate',
        'CPIAUCSL': 'CPI Inflation',
        'UNRATE': 'Unemployment Rate',
        'DGS10': '10-Year Treasury',
        'DCOILWTICO': 'WTI Oil'
    }
    output = "**FRED Economic Data**\n"
    for series_id, name in indicators.items():
        try:
            response = requests.get(
                'https://api.stlouisfed.org/fred/series/observations',
                params={'series_id': series_id, 'api_key': FRED_API_KEY,
                        'file_type': 'json', 'limit': 1, 'sort_order': 'desc'},
                timeout=10
            )
            if response.status_code == 200:
                obs = response.json().get('observations', [])
                if obs and obs[0].get('value') != '.':
                    output += f"• **{name}**: {obs[0]['value']} ({obs[0]['date']})\n"
            time.sleep(0.3)
        except:
            pass
    return output
 
 
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
        emails = []
        for msg in response.json().get('messages', []):
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
            system="You are Bina, drafting a reply on behalf of Nathaniel Werner, 18-year-old entrepreneur in Beverly Hills. Concise, professional. Reply body only, sign off as Nathaniel Werner.",
            messages=[{"role": "user", "content": f"Draft reply:\nFrom: {email['from']}\nSubject: {email['subject']}\n\n{email['body']}"}]
        )
        return response.content[0].text
    except Exception as e:
        return f"Could not draft: {str(e)}"
 
 
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
        events = []
        for item in response.json().get('items', []):
            start = item['start'].get('dateTime', item['start'].get('date', ''))
            end = item['end'].get('dateTime', item['end'].get('date', ''))
            events.append({'id': item['id'], 'title': item.get('summary', '(no title)'), 'start': start, 'end': end})
        return events
    except:
        return []
 
def create_calendar_event(title, start, end, description=''):
    try:
        access_token = get_access_token()
        event = {
            'summary': title, 'description': description,
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
        return False, str(response.json())
    except Exception as e:
        return False, str(e)
 
def process_calendar_commands(text):
    pattern = r'CREATE_EVENT\|([^\|]+)\|([^\|]+)\|([^\|]+)\|?([^E]*)END_EVENT'
    results = []
    for match in re.findall(pattern, text, re.DOTALL):
        title, start, end = match[0].strip(), match[1].strip(), match[2].strip()
        description = match[3].strip() if match[3] else ''
        success, link = create_calendar_event(title, start, end, description)
        results.append({'title': title, 'success': success, 'link': link})
    return results
 
 
# ── Master Monitor ────────────────────────────────────────────────────────────
 
def master_monitor():
    print("Master monitor started")
    last_briefing_day = -1
 
    while True:
        try:
            la_time = datetime.datetime.utcnow() + datetime.timedelta(hours=-7)
            la_hour = la_time.hour
            la_day = la_time.timetuple().tm_yday
 
            if la_hour == 7 and la_day != last_briefing_day:
                last_briefing_day = la_day
                try:
                    events = get_upcoming_events(max_results=5)
                    emails = get_inbox_emails(max_results=3)
                    events_text = "\n".join([f"- {e['title']} at {e['start']}" for e in events]) or "Nothing scheduled"
                    emails_text = "\n".join([f"- From {e['from']}: {e['subject']}" for e in emails]) or "No unread emails"
                    response = client.messages.create(
                        model="claude-sonnet-4-5",
                        max_tokens=300,
                        system="You are Bina texting Nathaniel his morning briefing. Short, punchy, actionable. **Bold** important things.",
                        messages=[{"role": "user", "content": f"Morning briefing.\nSchedule:\n{events_text}\nEmails:\n{emails_text}"}]
                    )
                    add_notification({
                        'id': f'briefing-{la_day}',
                        'type': 'briefing',
                        'subject': '☀️ Morning Briefing',
                        'from': 'Bina',
                        'body': response.content[0].text,
                        'draft_reply': '',
                        'read': False,
                        'timestamp': time.time()
                    })
                    send_push('☀️ Bina', 'Morning briefing ready.', BINA_URL + '?open=feed', notif_type='feed')
                except Exception as e:
                    print(f"Morning briefing error: {str(e)}")
 
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
                        save_memory(f"Email from {email['from']}: {email['subject']}", memory_type='email')
                        send_push('Bina — New Email', f'From {sender}: {email["subject"]}', BINA_URL + '?open=inbox', notif_type='email')
                    else:
                        print(f"Filtered: {email['from']}")
            save_seen_emails(seen)
 
        except Exception as e:
            print(f"Monitor error: {str(e)}")
        time.sleep(60)
 
 
# ── Email Processing ──────────────────────────────────────────────────────────
 
def process_email_commands(text):
    pattern = r'SEND_EMAIL\|(.*?)\|(.*?)\|(.*?)END_EMAIL'
    results = []
    for to, subject, body in re.findall(pattern, text, re.DOTALL):
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
 
@app.route('/clear-subs')
def clear_subs():
    save_subscriptions([])
    return '<h2 style="color:green;font-family:monospace;padding:40px">✅ Cleared!</h2>'
 
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
 
@app.route('/internal/add-notification', methods=['POST'])
def internal_add_notification():
    """Internal endpoint for research worker to push notifications."""
    data = request.json
    if not data:
        return jsonify({'error': 'No data'}), 400
    add_notification({
        'id': data.get('id', f'research-{int(time.time())}'),
        'type': data.get('type', 'intelligence'),
        'subject': data.get('subject', ''),
        'from': data.get('from', 'Bina Research'),
        'body': data.get('body', ''),
        'draft_reply': '',
        'read': False,
        'timestamp': data.get('timestamp', time.time())
    })
    send_push(
        data.get('subject', 'Bina Intelligence')[:50],
        'New intelligence in your feed.',
        BINA_URL + '?open=feed',
        notif_type='feed'
    )
    return jsonify({'success': True})
 
@app.route('/send-draft', methods=['POST'])
def send_draft():
    data = request.json
    to, subject, body = data.get('to'), data.get('subject'), data.get('body')
    success, error = send_email(to, f"Re: {subject}", body)
    if success:
        notifications = load_notifications()
        for n in notifications:
            if n.get('subject') == subject:
                n['replied'] = True
        save_notifications(notifications)
        send_push('Bina ✅', 'Reply sent.', BINA_URL + '?open=inbox', notif_type='email')
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': error})
 
@app.route('/calendar', methods=['GET'])
def get_calendar():
    return jsonify({'events': get_upcoming_events(max_results=10)})
 
@app.route('/calendar/create', methods=['POST'])
def create_event_route():
    data = request.json
    success, link = create_calendar_event(data.get('title'), data.get('start'), data.get('end'), data.get('description', ''))
    return jsonify({'success': success, 'link': link})
 
@app.route('/test-push')
def test_push():
    send_push('Bina 🔔', 'Test.', BINA_URL + '?open=feed', notif_type='feed')
    return '<h2 style="color:green;font-family:monospace;padding:40px">✅ Push sent!</h2>'
 
@app.route('/test-research')
def test_research():
    """Manual trigger for testing."""
    try:
        response = requests.post(
            f'{BINA_URL}/internal/add-notification',
            json={
                'id': f'test-{int(time.time())}',
                'type': 'intelligence',
                'subject': '🧪 Test — Research Worker Online',
                'from': 'Bina',
                'body': 'Research worker is running. Intel reports delivered at 7am, 1pm, and 8pm LA time.',
                'timestamp': time.time()
            }
        )
    except:
        pass
    return '<h2 style="color:green;font-family:monospace;padding:40px">✅ Research worker running on bina-research service!</h2>'
 
@app.route('/test-email')
def test_email():
    success, error = send_email('iirawgunzsii@gmail.com', 'Test from Bina', 'Hey! Bina testing.')
    if success:
        return '<h2 style="color:green;font-family:monospace;padding:40px">✅ Email sent!</h2>'
    return f'<h2 style="color:red;font-family:monospace;padding:40px">❌ Failed: {error}</h2>'
 
@app.route('/crypto')
def get_crypto_route():
    return jsonify({'crypto': get_crypto_data(), 'fear_greed': get_fear_greed_index()})
 
@app.route('/memories', methods=['GET'])
def get_memories_route():
    query = request.args.get('q', 'Nathaniel')
    memories = search_memories(query, top_k=10, threshold=0.3)
    return jsonify({'memories': memories, 'count': len(memories)})
 
@app.route('/tiktokvqGeSkDedicFPnJCRt89o26iAO5fmlFW.txt')
def tiktok_verify():
    return 'tiktokvqGeSkDedicFPnJCRt89o26iAO5fmlFW', 200, {'Content-Type': 'text/plain'}
 
@app.route('/terms')
def terms():
    return '''<html><body style="font-family:monospace;padding:40px;background:#000;color:#fff;max-width:800px">
    <h1>BinaClips — Terms of Service</h1>
    <p>Last updated: May 2026</p>
    <p>BinaClips is a content scheduling and management tool for creators. By using this service, you agree to use it in accordance with all applicable platform terms and local laws. This service is provided as-is. For questions contact nathanielwerner13@gmail.com</p>
    </body></html>'''
 
@app.route('/privacy')
def privacy():
    return '''<html><body style="font-family:monospace;padding:40px;background:#000;color:#fff;max-width:800px">
    <h1>BinaClips — Privacy Policy</h1>
    <p>Last updated: May 2026</p>
    <p>BinaClips collects only the information necessary to provide scheduling and posting services. We do not sell your data. OAuth tokens are stored securely and used only to post content on your behalf. For questions contact nathanielwerner13@gmail.com</p>
    </body></html>'''
 
@app.route('/generate-vapid')
def generate_vapid():
    try:
        private_key = ec.generate_private_key(ec.SECP256R1())
        pub_bytes = private_key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        priv_raw = private_key.private_numbers().private_value.to_bytes(32, 'big')
        pub_b64 = base64.urlsafe_b64encode(pub_bytes).rstrip(b"=").decode()
        priv_b64 = base64.urlsafe_b64encode(priv_raw).rstrip(b"=").decode()
        return f"""<html><body style="font-family:monospace;padding:40px;background:#000;color:#0f0;">
        <h2>VAPID Keys</h2>
        <p><b>PUBLIC:</b></p><textarea onclick="this.select()" style="width:100%;height:60px;background:#111;color:#0f0;padding:8px;">{pub_b64}</textarea>
        <p><b>PRIVATE:</b></p><textarea onclick="this.select()" style="width:100%;height:60px;background:#111;color:#0f0;padding:8px;">{priv_b64}</textarea>
        </body></html>"""
    except Exception as e:
        import traceback
        return f'<pre style="color:red;padding:40px">{traceback.format_exc()}</pre>'
 
@app.route('/chat', methods=['POST'])
def chat():
    data = request.json
    user_message = data.get('message', '')
    conversation_history = data.get('history', [])
 
    la_time = datetime.datetime.utcnow() + datetime.timedelta(hours=-7)
    la_time_str = la_time.strftime('%A, %B %d, %Y %I:%M %p')
    user_message_with_context = f"[LA Time: {la_time_str}]\n\n{user_message}"
 
    all_memories = get_all_context_memories(user_message)
    memory_context = format_memories(all_memories)
    msg_lower = user_message.lower()
 
    # Crypto trigger
    if any(word in msg_lower for word in ['crypto', 'bitcoin', 'btc', 'ethereum', 'eth', 'solana', 'sol', 'price', 'coin']):
        crypto_data = get_crypto_data()
        fear_greed = get_fear_greed_index()
        user_message_with_context += f"\n\nLive crypto:\n{format_crypto_report(crypto_data, fear_greed)}"
 
    # Commodities trigger
    if any(word in msg_lower for word in ['gold', 'oil', 'commodity', 'commodities']):
        user_message_with_context += f"\n\nCommodities:\n{get_real_commodity_prices()}"
 
    # FRED trigger
    if any(word in msg_lower for word in ['fed', 'federal reserve', 'inflation', 'unemployment', 'economic data']):
        user_message_with_context += f"\n\nFRED Economic Data:\n{get_fred_data()}"
 
    # Search triggers
    search_triggers = ['search', 'look up', 'find', 'what is', 'who is', 'latest', 'news',
                       'current', 'stock', 'weather', 'research', 'tell me about', 'what happened',
                       'today', 'trending', 'recent', 'update', 'best', 'top', 'political', 'politics',
                       'gold', 'oil', 'commodity', 'strait', 'war', 'attack', 'breaking',
                       'energy', 'xom', 'cvx', 'nee', 'ceg', 'kmi', 'cop', 'oxy', 'fslr', 'vst',
                       'solar', 'nuclear', 'pipeline', 'utility', 'renewable']
    deep_triggers = ['research', 'deep dive', 'everything about', 'full report', 'analyze',
                     'investigate', 'background on', 'tell me about']
 
    if any(word in msg_lower for word in deep_triggers):
        user_message_with_context += f"\n\nDeep research:\n{web_search(user_message, num_results=5)}"
    elif any(word in msg_lower for word in search_triggers):
        user_message_with_context += f"\n\nSearch:\n{web_search(user_message)}"
 
    # Calendar trigger
    if any(word in msg_lower for word in ['schedule', 'calendar', 'meeting', 'tomorrow', 'what do i have']):
        events = get_upcoming_events(max_results=5)
        if events:
            user_message_with_context += f"\n\nCalendar:\n" + "\n".join([f"- {e['title']} at {e['start']}" for e in events])
        else:
            user_message_with_context += "\n\nCalendar is empty."
 
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
 
    save_memory(f"Nathaniel: {user_message} | Bina: {display_message[:300]}", memory_type='conversation')
 
    if any(word in msg_lower for word in ['remember', 'save', 'note', 'important', "don't forget", 'remind me']):
        save_memory(f"IMPORTANT — Nathaniel said: {user_message}", memory_type='explicit')
 
    if any(word in msg_lower for word in ['my mom', 'my dad', 'my friend', 'my brother', 'my sister',
                                           'my girlfriend', 'i am', "i'm", 'i have', 'i work',
                                           'i live', 'i want', 'i hate', 'i love', 'my goal']):
        save_memory(f"Personal — Nathaniel: {user_message}", memory_type='personal')
 
    result = {'response': display_message}
    if email_results:
        sent = [e for e in email_results if e['success']]
        failed = [e for e in email_results if not e['success']]
        if sent:
            result['email_sent'] = f"✅ Email sent to {sent[0]['to']}"
            send_push('Bina ✅', f'Sent to {sent[0]["to"]}', BINA_URL + '?open=inbox', notif_type='email')
            save_memory(f"Sent email to {sent[0]['to']} — {sent[0]['subject']}", memory_type='email')
        if failed:
            result['email_error'] = f"❌ Email failed: {failed[0]['error']}"
    if calendar_results:
        created = [e for e in calendar_results if e['success']]
        if created:
            result['event_created'] = f"📅 Event created: {created[0]['title']}"
            send_push('Bina 📅', f'"{created[0]["title"]}" added', BINA_URL + '?open=feed', notif_type='feed')
            save_memory(f"Created event: {created[0]['title']}", memory_type='calendar')
 
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
    return redirect('https://accounts.google.com/o/oauth2/auth?' + urlencode(params))
 
@app.route('/oauth/callback')
def oauth_callback():
    code = request.args.get('code')
    error = request.args.get('error')
    if error:
        return f'<h2 style="color:red">Error: {error}</h2>', 400
    if not code:
        return '<h2 style="color:red">No code</h2>', 400
    token_response = requests.post('https://oauth2.googleapis.com/token', data={
        'code': code,
        'client_id': os.environ.get('GOOGLE_CLIENT_ID'),
        'client_secret': os.environ.get('GOOGLE_CLIENT_SECRET'),
        'redirect_uri': REDIRECT_URI,
        'grant_type': 'authorization_code'
    })
    tokens = token_response.json()
    refresh_token = tokens.get('refresh_token', '')
    note = "✅ Copy to GOOGLE_REFRESH_TOKEN in Railway." if refresh_token else "⚠️ No refresh token."
    return f"""<html><body style="font-family:monospace;padding:40px;background:#000;color:#0f0;">
    <h2>OAuth</h2>
    <textarea style="width:100%;height:80px;background:#111;color:#0f0;padding:8px;">{refresh_token}</textarea>
    <p>{note}</p></body></html>"""
 
 
# ── Start ─────────────────────────────────────────────────────────────────────
 
monitor_thread = threading.Thread(target=master_monitor, daemon=True)
monitor_thread.start()
 
if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
 
