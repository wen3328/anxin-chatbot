# Flask import
from flask import Flask, request, abort

# Environment import
import os
from dotenv import load_dotenv
import traceback
from datetime import datetime

# LineBot import
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (MessageEvent, TextMessage, TextSendMessage, PostbackEvent)

# OpenAI import
from openai import OpenAI

# Other modules import
import time
import re

# Load environment variables
load_dotenv()

# Validate environment variables before initializing
def validate_env_vars():
    required_vars = [
        'CHANNEL_ACCESS_TOKEN',
        'CHANNEL_SECRET',
        'OPENAI_API_KEY',
        'ASSISTANT_ID'
    ]
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    if missing_vars:
        raise ValueError(
            f"Missing required environment variables: {', '.join(missing_vars)}\n"
            f"Please make sure you have a .env file with these variables set."
        )

# Validate environment variables first
validate_env_vars()

# Flask App Initialization
app = Flask(__name__)

# LineBot Initialization
channel_access_token = os.getenv('CHANNEL_ACCESS_TOKEN')
channel_secret = os.getenv('CHANNEL_SECRET')

if not channel_access_token or not channel_secret:
    raise ValueError(
        "LINE Bot credentials are not properly configured. "
        "Please check CHANNEL_ACCESS_TOKEN and CHANNEL_SECRET in your .env file."
    )

line_bot_api = LineBotApi(channel_access_token)
handler = WebhookHandler(channel_secret)

# OpenAI API Initialization
client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
ASSISTANT_ID = os.getenv('ASSISTANT_ID')  # Set your Assistant ID in environment variables

# 用於儲存用戶的對話歷史
user_histories = {}

# ====== GPT Assistant Functions ======
def create_thread(user_id):
    """Create a new conversation Thread"""
    thread = client.beta.threads.create()
    user_histories[user_id] = {"thread_id": thread.id, "messages": []}
    return thread.id


def add_message_to_thread(thread_id, role, content):
    """Add a message to the thread"""
    client.beta.threads.messages.create(
        thread_id=thread_id,
        role=role,
        content=content
    )


def run_assistant(thread_id):
    """Run Assistant and get reply"""
    run = client.beta.threads.runs.create(
        thread_id=thread_id,
        assistant_id=ASSISTANT_ID
    )

    # Poll for Run completion
    timeout_counter = 0
    MAX_RETRIES = 10
    while True:
        run_status = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id)
        if run_status.status == 'completed':
            break
        elif run_status.status in ['failed', 'cancelled']:
            raise Exception("Assistant run failed or was cancelled.")
        time.sleep(1)
        timeout_counter += 1
        if timeout_counter > MAX_RETRIES:
            raise Exception("Assistant run timeout")
    
    # Get reply message
    messages = client.beta.threads.messages.list(thread_id=thread_id)
    return messages.data[0].content[0].text.value


def remove_markdown(text):
    """Turn markdown to plain text"""
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)  # Bold
    text = re.sub(r'\*(.*?)\*', r'\1', text)      # Italic
    text = re.sub(r'^#+\s', '', text, flags=re.MULTILINE)  # Titles
    text = re.sub(r'\[(.*?)\]\((.*?)\)', r'\1 (\2)', text)  # Links
    text = re.sub(r'```(.*?)```', r'\1', text, flags=re.DOTALL)  # Code blocks
    text = re.sub(r'`(.*?)`', r'\1', text)  # Inline code
    text = re.sub(r'^>\s', '', text, flags=re.MULTILINE)  # Quotes
    return text


# ====== LineBot Callback ======
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        print(traceback.format_exc())
        print("Invalid signature. Please check your channel access token and secret.")
        abort(400)
    except Exception as e:
        print(traceback.format_exc())
        print(f"An error occurred: {e}")
        abort(500)
    return 'OK'


# ====== Handle User Message ======
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_message = event.message.text

    try:
        # Get or create thread for the user
        if user_id not in user_histories:
            thread_id = create_thread(user_id)
        else:
            thread_id = user_histories[user_id]["thread_id"]

        # Add user message to the history
        user_histories[user_id]["messages"].append({"role": "user", "content": user_message})
        add_message_to_thread(thread_id, "user", user_message)

        # Get assistant reply
        assistant_reply = run_assistant(thread_id)
        assistant_reply = remove_markdown(assistant_reply)

        # Add assistant reply to the history
        user_histories[user_id]["messages"].append({"role": "assistant", "content": assistant_reply})

        # Send reply to the user
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=assistant_reply)
        )
    except Exception as e:
        print(traceback.format_exc())
        error_message = "❗ 糖安心小助手暫時無法使用，請稍後再試"
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=error_message)
        )


# ====== Handle Postback Event ======
@handler.add(PostbackEvent)
def handle_postback(event):
    print(event.postback.data)


# ====== Start Flask App ======
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
