from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from anthropic import Anthropic
from dotenv import load_dotenv
from ddgs import DDGS
from twilio.rest import Client as TwilioClient
from twilio.twiml.messaging_response import MessagingResponse
import os
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

load_dotenv()

app = Flask(__name__)
CORS(app)
client = Anthropic()
twilio_client = TwilioClient(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))

conversation_history = []
whatsapp_history = []
MEMORY_FILE = "memory.json"

SYSTEM_PROMPT = """You are Bina, a personal AI assistant and autonomous agent.
You are proactive, organized, and remember context throughout conversations.
You have access to web search. When asked about current events, news, prices,
or anything requiring up-to-date information, use the search results provided.
You have a warm, professional personality like a trusted personal assistant.
Your name Bina (בינה) means intelligence and wisdom in Hebrew.
You can send emails on behalf of Nathaniel Werner using the send email command."""

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

def send_email(to_email, subject, body):
    try:
        gmail_address = os.getenv("GMAIL_ADDRESS")
        gmail_password = os.getenv("GMAIL_APP_PASSWORD")
        msg = MIMEMultipart()
        msg['From'] = gmail_address
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        server.login(gmail_address, gmail_password)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"Email error: {e}")
        return False

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
    return jsonify({"response": assistant_message})

@app.route('/whatsapp', methods=['POST'])
def whatsapp():
    global whatsapp_history
    incoming_msg = request.values.get('Body', '').strip()

    # EMAIL SEND COMMAND — checked FIRST before AI sees the message
    if incoming_msg.lower().startswith('send email'):
        parts = incoming_msg.split('|')
        if len(parts) == 4:
            to_email = parts[1].strip()
            subject = parts[2].strip()
            body = parts[3].strip()
            success = send_email(to_email, subject, body)
            reply = f"✅ Email sent to {to_email}!" if success else "❌ Failed to send email. Please try again."
        else:
            reply = "To send an email use this format:\nsend email | recipient@email.com | Subject Here | Email body here"
        resp = MessagingResponse()
        resp.message(reply)
        return str(resp)

    # WEB SEARCH
    search_keywords = ['latest', 'news', 'today', 'current', 'price', 'weather', 'who is', 'what is', 'when is', 'search']
    should_search = any(k in incoming_msg.lower() for k in search_keywords)
    enhanced_message = incoming_msg
    if should_search:
        search_results = search_web(incoming_msg)
        if search_results:
            enhanced_message = incoming_msg + "\n\n[SEARCH RESULTS]\n" + search_results

    # EMAIL DRAFT REQUEST
    if 'draft email' in incoming_msg.lower() or 'write email' in incoming_msg.lower() or 'email to' in incoming_msg.lower():
        enhanced_message = incoming_msg + "\n\nPlease draft a professional email for Nathaniel Werner. Format your response as:\nTO: [email]\nSUBJECT: [subject]\nBODY:\n[email body]\n\nThen tell the user to reply with:\nsend email | to@email.com | Subject | Body\nto actually send it."

    # AI RESPONSE
    whatsapp_history.append({"role": "user", "content": enhanced_message})
    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=1000,
        system=SYSTEM_PROMPT,
        messages=whatsapp_history
    )
    reply = response.content[0].text
    whatsapp_history.append({"role": "assistant", "content": reply})
    resp = MessagingResponse()
    resp.message(reply)
    return str(resp)

@app.route('/send-email', methods=['POST'])
def send_email_route():
    data = request.json
    to_email = data.get('to', '')
    subject = data.get('subject', '')
    body = data.get('body', '')
    if not to_email or not subject or not body:
        return jsonify({"status": "error", "message": "Missing required fields"})
    success = send_email(to_email, subject, body)
    if success:
        return jsonify({"status": "success", "message": f"Email sent to {to_email}"})
    else:
        return jsonify({"status": "error", "message": "Failed to send email"})

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
    app.run(debug=True, port=5000)
