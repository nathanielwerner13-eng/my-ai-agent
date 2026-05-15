import os
import json
import re
import base64
import requests
from urllib.parse import urlencode
from email.mime.text import MIMEText
from flask import Flask, request, jsonify, session, redirect
from flask_cors import CORS
from anthropic import Anthropic
from duckduckgo_search import DDGS
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

app = Flask(__name__, static_folder='.', static_url_path='')
app.secret_key = os.environ.get('SECRET_KEY', 'bina-secret-key-2024')
CORS(app)

client = Anthropic()
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

PASSPHRASE = 'bina2024'
MEMORY_FILE = 'memory.json'
GMAIL_SCOPES = 'https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/gmail.readonly'
REDIRECT_URI = 'https://my-ai-agent-production-5e17.up.railway.app/oauth/callback'

SYSTEM_PROMPT = """You are Bina (בינה), a fully autonomous AI agent and personal chief of staff for Nathaniel Werner, an 18-year-old entrepreneur and college student in Beverly Hills.

You manage his personal life, business, investments, education, and help build toward his goal of creating autonomous income systems.

You are proactive, intelligent, direct, and treat Nathaniel as a capable adult. You never add unnecessary caveats. You think ahead, spot opportunities, and help him move fast.

You can:
- Send emails by outputting exactly: SEND_EMAIL|to@email.com|Subject Line|Body text here END_EMAIL
- Search the web when needed for current information
- Remember important information across conversations
- Help with business strategy, investments, scheduling, research, and execution

Always be concise unless depth is needed. Think like a brilliant chief of staff who is always one step ahead."""


# ── Memory ──────────────────────────────────────────────────────────────────

def load_memory():
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, 'r') as f:
            return json.load(f)
    return []

def save_memory(memories):
    with open(MEMORY_FILE, 'w') as f:
        json.dump(memories, f)


# ── Web Search ───────────────────────────────────────────────────────────────

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


# ── Gmail ────────────────────────────────────────────────────────────────────

def get_gmail_service():
    creds = Credentials(
        token=None,
        refresh_token=os.environ.get('GOOGLE_REFRESH_TOKEN'),
        token_uri='https://oauth2.googleapis.com/token',
        client_id=os.environ.get('GOOGLE_CLIENT_ID'),
        client_secret=os.environ.get('GOOGLE_CLIENT_SECRET'),
        scopes=GMAIL_SCOPES.split()
    )
    if not creds.valid:
        creds.refresh(Request())
    return build('gmail', 'v1', credentials=creds)

def send_email(to, subject, body):
    try:
        service = get_gmail_service()
        message = MIMEText(body)
        message['to'] = to
        message['subject'] = subject
        message['from'] = os.environ.get('GMAIL_ADDRESS')
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        service.users().messages().send(userId='me', body={'raw': raw}).execute()
        return True, None
    except Exception as e:
        return False, str(e)

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
    return cleaned.strip()


# ── Routes ───────────────────────────────────────────────────────────────────

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

@app.route('/chat', methods=['POST'])
def chat():
    if not session.get('authenticated'):
        return jsonify({'error': 'Not authenticated'}), 401

    data = request.json
    user_message = data.get('message', '')
    conversation_history = data.get('history', [])

    # Auto web search triggers
    search_triggers = ['search', 'look up', 'find', 'what is', 'who is',
                       'latest', 'news', 'current', 'today', 'price', 'stock', 'weather']
    if any(word in user_message.lower() for word in search_triggers):
        search_results = web_search(user_message)
        user_message_with_context = f"{user_message}\n\nSearch results:\n{search_results}"
    else:
        user_message_with_context = user_message

    # Load memory context
    memories = load_memory()
    memory_context = ""
    if memories:
        memory_context = "\n\nRelevant memories:\n" + "\n".join(memories[-10:])

    messages = conversation_history + [{"role": "user", "content": user_message_with_context}]

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=2048,
        system=SYSTEM_PROMPT + memory_context,
        messages=messages
    )

    assistant_message = response.content[0].text
    email_results = process_email_commands(assistant_message)
    display_message = clean_response(assistant_message)

    # Auto-save to memory
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
        note = "⚠️ No refresh token returned. Go to https://myaccount.google.com/permissions, revoke access for this app, then try /authorize again."
    else:
        note = "✅ Copy the token above and set it as GOOGLE_REFRESH_TOKEN in Railway variables."

    return f"""
    <html>
    <body style="font-family: monospace; padding: 40px; background: #000; color: #0f0;">
    <h2>OAuth Callback</h2>
    <p><b>Refresh Token:</b></p>
    <textarea style="width:100%;height:80px;background:#111;color:#0f0;font-size:13px;padding:8px;">{refresh_token}</textarea>
    <br><br>
    <p>{note}</p>
    <br>
    <p><b>Full response (for debugging):</b></p>
    <pre style="background:#111;padding:10px;color:#ff0;">{json.dumps(tokens, indent=2)}</pre>
    </body>
    </html>
    """


if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
