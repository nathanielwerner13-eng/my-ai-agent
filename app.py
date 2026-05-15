from flask import Flask, request, jsonify, send_from_directory, redirect
from flask_cors import CORS
from anthropic import Anthropic
from dotenv import load_dotenv
from duckduckgo_search import DDGS
import os
import json
import re
import base64
from email.mime.text import MIMEText
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

load_dotenv()

app = Flask(__name__)
CORS(app)
client = Anthropic()

conversation_history = []
MEMORY_FILE = "memory.json"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify"
]

SYSTEM_PROMPT = """You are Bina, a personal AI assistant and autonomous agent for Nathaniel Werner.
You are proactive, organized, and remember context throughout conversations.
You have access to web search. When asked about current events, news, prices,
or anything requiring up-to-date information, use the search results provided.
You have a warm, professional personality like a trusted personal assistant.
Your name Bina (בינה) means intelligence and wisdom in Hebrew.

EMAIL CAPABILITY:
You can send emails on behalf of Nathaniel Werner from nathanielwerner13@gmail.com.
When asked to send or write an email, respond in this EXACT format:

SEND_EMAIL
TO: [email address]
SUBJECT: [subject line]
BODY: [full email body]
END_EMAIL

Always write professional, personalized emails. Sign off as Nathaniel Werner.
If the user doesn't provide an email address, ask for it before drafting.
After sending confirm with a friendly message."""

def get_gmail_service():
    creds = Credentials(
        token=None,
        refresh_token=os.environ.get("GOOGLE_REFRESH_TOKEN"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ.get("GOOGLE_CLIENT_ID"),
        client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
        scopes=SCOPES
    )
    creds.refresh(Request())
    return build("gmail", "v1", credentials=creds)

def send_email(to_email, subject, body):
    try:
        service = get_gmail_service()
        message = MIMEText(body)
        message["to"] = to_email
        message["from"] = "nathanielwerner13@gmail.com"
        message["subject"] = subject
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        service.users().messages().send(userId="me", body={"raw": raw}).execute()
        print(f"Email sent to {to_email}")
        return True
    except Exception as e:
        print(f"Gmail error: {e}")
        return False

def parse_and_send_email(text):
    if 'SEND_EMAIL' in text and 'END_EMAIL' in text:
        try:
            email_block = text.split('SEND_EMAIL')[1].split('END_EMAIL')[0]
            to_match = re.search(r'TO:\s*(.+)', email_block)
            subject_match = re.search(r'SUBJECT:\s*(.+)', email_block)
            body_match = re.search(r'BODY:\s*([\s\S]+)', email_block)
            if to_match and subject_match and body_match:
                to_email = to_match.group(1).strip()
                subject = subject_match.group(1).strip()
                body = body_match.group(1).strip()
                success = send_email(to_email, subject, body)
                return success, to_email
        except Exception as e:
            print(f"Parse error: {e}")
    return False, None

def clean_response(text):
    if 'SEND_EMAIL' in text and 'END_EMAIL' in text:
        before = text.split('SEND_EMAIL')[0].strip()
        after = text.split('END_EMAIL')[-1].strip()
        return (before + "\n\n" + after).strip()
    return text

def search_web(query):
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=3))
            if results:
                summary = "Web search results for: " + query + "\n"
                for r in results:
                    summary += "- " + r['title'] + ": " + r['body'][:200] + "\n"
                return summary
    except:
        pass
    return ""

def load_memory():
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, 'r') as f:
            return json.load(f)
    return []

def save_memory(history):
    with open(MEMORY_FILE, 'w') as f:
        json.dump(history, f)

@app.route('/')
def home():
    return send_from_directory('.', 'index.html')

@app.route('/manifest.json')
def manifest():
    return send_from_directory('.', 'manifest.json')

@app.route('/authorize')
def authorize():
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": os.environ.get("GOOGLE_CLIENT_ID"),
                "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET"),
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["https://my-ai-agent-production-5e17.up.railway.app/oauth/callback"]
            }
        },
        scopes=SCOPES
    )
    flow.redirect_uri = "https://my-ai-agent-production-5e17.up.railway.app/oauth/callback"
    auth_url, state = flow.authorization_url(access_type='offline', prompt='consent')
    return redirect(auth_url)

@app.route('/oauth/callback')
def oauth_callback():
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": os.environ.get("GOOGLE_CLIENT_ID"),
                "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET"),
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["https://my-ai-agent-production-5e17.up.railway.app/oauth/callback"]
            }
        },
        scopes=SCOPES
    )
    flow.redirect_uri = "https://my-ai-agent-production-5e17.up.railway.app/oauth/callback"
    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials
    print("NEW REFRESH TOKEN:", creds.refresh_token)
    return f"<h1>Success!</h1><p>Refresh token: {creds.refresh_token}</p><p>Copy this and add it to Railway as GOOGLE_REFRESH_TOKEN</p>"

@app.route('/chat', methods=['POST'])
def chat():
    global conversation_history
    data = request.json
    user_message = data.get('message', '')

    search_keywords = ['latest', 'news', 'today', 'current', 'price', 'weather', 'who is', 'what is', 'when is', 'search']
    should_search = any(k in user_message.lower() for k in search_keywords)
    enhanced_message = user_message
    if should_search:
        search_results = search_web(user_message)
        if search_results:
            enhanced_message = user_message + "\n\n[SEARCH RESULTS]\n" + search_results

    conversation_history.append({"role": "user", "content": enhanced_message})

    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=8096,
        system=SYSTEM_PROMPT,
        messages=conversation_history
    )

    assistant_message = response.content[0].text
    conversation_history.append({"role": "assistant", "content": assistant_message})
    save_memory(conversation_history)

    display_message = assistant_message
    if 'SEND_EMAIL' in assistant_message:
        success, to_email = parse_and_send_email(assistant_message)
        display_message = clean_response(assistant_message)
        if success:
            display_message += f"\n\n✅ Email sent to {to_email}!"
        else:
            display_message += f"\n\n❌ Email failed. Check Railway logs."

    return jsonify({"response": display_message})

@app.route('/history', methods=['GET'])
def get_history():
    return jsonify({"history": conversation_history})

@app.route('/clear', methods=['POST'])
def clear_history():
    global conversation_history
    conversation_history = []
    save_memory([])
    return jsonify({"status": "cleared"})

if __name__ == '__main__':
    conversation_history = load_memory()
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)
