from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from anthropic import Anthropic
from dotenv import load_dotenv
from ddgs import DDGS
from twilio.rest import Client as TwilioClient
from twilio.twiml.messaging_response import MessagingResponse
import os
import json
import sendgrid
from sendgrid.helpers.mail import Mail
import re

load_dotenv()

app = Flask(__name__)
CORS(app)
client = Anthropic()
twilio_client = TwilioClient(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))

conversation_history = []
whatsapp_history = []
MEMORY_FILE = "memory.json"

SYSTEM_PROMPT = """You are Bina, a personal AI assistant and autonomous agent for Nathaniel Werner.
You are proactive, organized, and remember context throughout conversations.
You have access to web search. When asked about current events, news, prices,
or anything requiring up-to-date information, use the search results provided.
You have a warm, professional personality like a trusted personal assistant.
Your name Bina (בינה) means intelligence and wisdom in Hebrew.

EMAIL CAPABILITY:
You can send emails on behalf of Nathaniel Werner from nathaniel@nathanielwerner.org.
When asked to send or write an email, extract the following and respond in this EXACT format:

SEND_EMAIL
TO: [email address]
SUBJECT: [subject line]
BODY: [full email body]
END_EMAIL

Always write professional, personalized emails. Sign off as Nathaniel Werner.
If the user doesn't provide an email address, ask for it before drafting.
If the user says something like "email Jason at jason@gmail.com about lunch", 
extract the email, write a professional message, and use the SEND_EMAIL format.
After sending confirm with a friendly message."""

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
        sg = sendgrid.SendGridAPIClient(api_key=os.getenv("SENDGRID_API_KEY"))
        from_email = os.getenv("SENDGRID_FROM_EMAIL")
        message = Mail(
            from_email=from_email,
            to_emails=to_email,
            subject=subject,
            plain_text_content=body
        )
        response = sg.send(message)
        print(f"Email sent! Status: {response.status_code}")
        return True
    except Exception as e:
        print(f"Email error: {e}")
        return False

def parse_and_send_email(text):
    """Parse Bina's response for email commands and send them"""
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

    # WEB SEARCH
    search_keywords = ['latest', 'news', 'today', 'current', 'price', 'weather', 'who is', 'what is', 'when is', 'search']
    should_search = any(k in incoming_msg.lower() for k in search_keywords)
    enhanced_message = incoming_msg
    if should_search:
        search_results = search_web(incoming_msg)
        if search_results:
            enhanced_message = incoming_msg + "\n\n[SEARCH RESULTS]\n" + search_results

    # AI RESPONSE
    whatsapp_history.append({"role": "user", "content": enhanced_message})
    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=1500,
        system=SYSTEM_PROMPT,
        messages=whatsapp_history
    )
    reply = response.content[0].text
    whatsapp_history.append({"role": "assistant", "content": reply})

    # CHECK IF BINA WANTS TO SEND AN EMAIL
    if 'SEND_EMAIL' in reply:
        success, to_email = parse_and_send_email(reply)
        # Clean up the reply to remove the technical block
        clean_reply = reply.split('SEND_EMAIL')[0].strip()
        if success:
            clean_reply += f"\n\n✅ Email sent to {to_email}!"
        else:
            clean_reply += f"\n\n❌ Failed to send email. Please try again."
        reply = clean_reply

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
