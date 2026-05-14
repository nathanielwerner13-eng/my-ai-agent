import os
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

client = Anthropic()

conversation_history = []

SYSTEM_PROMPT = """You are a personal AI assistant and autonomous agent. 
You help manage tasks, communications, and information on behalf of your user.
You are proactive, organized, and remember context throughout our conversation.
When asked to do tasks, break them into clear steps and execute them methodically."""

def chat(user_message):
    conversation_history.append({
        "role": "user",
        "content": user_message
    })
    
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=8096,
        system=SYSTEM_PROMPT,
        messages=conversation_history
    )
    
    assistant_message = response.content[0].text
    conversation_history.append({
        "role": "assistant", 
        "content": assistant_message
    })
    
    return assistant_message

def main():
    print("🤖 Your Personal AI Agent is running. Type 'quit' to exit.\n")
    while True:
        user_input = input("You: ").strip()
        if user_input.lower() in ['quit', 'exit']:
            break
        if not user_input:
            continue
        response = chat(user_input)
        print(f"\nAgent: {response}\n")

if __name__ == "__main__":
    main()
    
