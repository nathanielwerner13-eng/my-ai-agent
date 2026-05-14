from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from anthropic import Anthropic
from dotenv import load_dotenv
from ddgs import DDGS
import os
import json

load_dotenv()

app = Flask(__name__)
CORS(app)
client = Anthropic()

conversation_history = []
MEMORY_FILE = "memory.json"

SYSTEM_PROMPT = """You are Alfred, a personal AI assistant and autonomous agent.
You are proactive, organized, and remember context throughout conversations.
You have access to web search. When asked about current events, news, prices, 
or anything requiring up-to-date information, use the search results provided.
You have a warm, professional personality like a trusted personal assistant."""

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
