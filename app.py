import os
import json
import re
from flask import Flask, request, jsonify, session, redirect, url_for
from flask_cors import CORS
from anthropic import Anthropic
from duckduckgo_search import DDGS
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import Flow
import base64
from email.mime.text import MIMEText

app = Flask(__name__, static_folder='.', static_url_path='')
app.secret_key = os.environ.get('SECRET_KEY', 'bina-secret-key-2024')
CORS(app)

client = Anthropic()
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

PASSPHRASE = 'bina2024'
MEMORY_FILE = 'memory.json'

GMAIL_SCOPES = [
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/gmail.readonly'
]

SYSTEM_PROMPT = """You are Bina (בינה), a fully autonomous AI agent and personal chief of staff for Nathaniel Werner, an 18-year-old entrepreneur and college student in Beverly Hills.

You manage his personal life, business, investments, education, and help build toward his goal of creating autonomous income systems.

You are proactive, intelligent, direct, and treat Nathaniel as a capable adult. You never add unnecessary caveats. You think ahead, spot opportunities, and help him move fast.

You can:
- Send emails by outputting: SEND_EMAIL|to@email.com|Subject|Body text END_EMAIL
- Search the web when needed for current information
- Remember important information across conversations
- Help with business strategy, investments, scheduling, research, and execution

Always be concise unless depth is needed. Think like a brilliant chief of staff who is always one step ahead."""

def load_memory():
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, 'r') as f:
            return json.load(f)
    return []

def save_memory(memories):
    with open(MEMORY_FILE, 'w') as f:
        json.dump(memories, f)

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

def get_gmail_service():
    creds = Credentials(
        token=None,
        refresh_token=os.environ.get('GOOGLE_REFRESH_TOKEN'),
        token_uri='https://oauth2.googleapis.com/token',
        client_id=os.environ.get('GOOGLE_CLIENT_ID'),
        client_secret=os.environ.get('GOOGLE_CLIENT_SECRET'),
        scopes=GMAIL_SCOPES
    )
    if creds.expired or not creds.valid:
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
        return True
    except Exception as e:
        print(f"Email error: {str(e)}")
        return False

def clean_response(text):
    cleaned = re.sub(r'SEND_EMAIL\|.*?END_EMAIL', '', text, flags=re.DOTALL)
    return cleaned.strip()

def process_email_commands(text):
    pattern = r'SEND_EMAIL\|(.*?)\|(.*?)\|(.*?)END_EMAIL'
    matches = re.findall(pattern, text, re.DOTALL)
    results = []
    for to, subject, body in matches:
        success = send_email(to.strip(), subject.strip(), body.strip())
        results.append({'to': to.strip(), 'subject': subject.strip(), 'success': success})
    return results

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

    # Handle web search
    if any(word in user_message.lower() for word in ['search', 'look up', 'find', 'what is', 'who is', 'latest', 'news', 'current', 'today', 'price', 'stock']):
        search_query = user_message.replace('search', '').replace('look up', '').strip()
        search_results = web_search(search_query)
        user_message_with_context = f"{user_message}\n\nSearch results:\n{search_results}"
    else:
        user_message_with_context = user_message

    # Load memory
    memories = load_memory()
    memory_context = ""
    if memories:
        memory_context = "\n\nRelevant memories:\n" + "\n".join(memories[-10:])

    messages = conversation_history + [{"role": "user", "content": user_message_with_context}]

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2048,
        system=SYSTEM_PROMPT + memory_context,
        messages=messages
    )

    assistant_message = response.content[0].text

    # Process any email commands
    email_results = process_email_commands(assistant_message)

    # Clean response for display
    display_message = clean_response(assistant_message)

    # Auto-save important info to memory
    if any(word in user_message.lower() for word in ['remember', 'save', 'note', 'important']):
        memories.append(f"User said: {user_message}")
        save_memory(memories)

    result = {'response': display_message}
    if email_results:
        sent = [e for e in email_results if e['success']]
        if sent:
            result['email_sent'] = f"✅ Email sent to {sent[0]['to']}"

    return jsonify(result)

@app.route('/authorize')
def authorize():
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": os.environ.get('GOOGLE_CLIENT_ID'),
                "client_secret": os.environ.get('GOOGLE_CLIENT_SECRET'),
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["https://my-ai-agent-production-5e17.up.railway.app/oauth/callback"]
            }
        },
        scopes=GMAIL_SCOPES
    )
    flow.redirect_uri = "https://my-ai-agent-production-5e17.up.railway.app/oauth/callback"
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        prompt='consent',
        include_granted_scopes='true'
    )
    session['oauth_state'] = state
    return redirect(authorization_url)

@app.route('/oauth/callback')
def oauth_callback():
    state = session.get('oauth_state')
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": os.environ.get('GOOGLE_CLIENT_ID'),
                "client_secret": os.environ.get('GOOGLE_CLIENT_SECRET'),
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["https://my-ai-agent-production-5e17.up.railway.app/oauth/callback"]
            }
        },
        scopes=GMAIL_SCOPES,
        state=state
    )
    flow.redirect_uri = "https://my-ai-agent-production-5e17.up.railway.app/oauth/callback"
    flow.fetch_token(authorization_response=request.url)
    credentials = flow.credentials
    refresh_token = credentials.refresh_token

    return f"""
    <html><body style="font-family: monospace; padding: 40px; background: #000; color: #0f0;">
    <h2>✅ OAuth Success!</h2>
    <p>Copy this refresh token and add it to Railway as GOOGLE_REFRESH_TOKEN:</p>
    <textarea style="width:100%; height:100px; background:#111; color:#0f0; font-size:14px; padding:10px;">{refresh_token}</textarea>
    <br><br>
    <p>Once saved in Railway, email sending will work!</p>
    </body></html>
    """

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
