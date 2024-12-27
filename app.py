# Flask import
from flask import Flask, request, abort

# Environment import
import os
from dotenv import load_dotenv
import traceback
from datetime import datetime
import time

# LineBot import
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage,
    PostbackEvent
)

# OpenAI import
from openai import OpenAI

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

static_tmp_path = os.path.join(os.path.dirname(__file__), 'static', 'tmp')

# Initialize Flask App
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


# ====== GPT Assistant Functions ======
def create_thread():
    """Create a new conversation Thread"""
    thread = client.beta.threads.create()
    return thread.id


def add_message_to_thread(thread_id, user_message):
    """Add user message to Thread"""
    client.beta.threads.messages.create(
        thread_id=thread_id,
        role="user",
        content=user_message
    )


def run_assistant(thread_id):
    """Run Assistant and get reply"""
    run = client.beta.threads.runs.create(
        thread_id=thread_id,
        assistant_id=ASSISTANT_ID
    )

    # Poll for Run completion
    timeout_counter = 0
    MAX_RETRIES = 10  # Set maximum retry count
    while True:
        run_status = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id)
        if run_status.status == 'completed':
            break
        elif run_status.status in ['failed', 'cancelled']:
            raise Exception("Assistant run failed or was cancelled.")
        time.sleep(1)  # Add delay to avoid excessive requests
        timeout_counter += 1
        if timeout_counter > MAX_RETRIES:
            raise Exception("Assistant run timeout")
    
    # Get reply message
    messages = client.beta.threads.messages.list(thread_id=thread_id)
    return messages.data[0].content[0].text.value


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
    profile = line_bot_api.get_profile(user_id)
    display_name = profile.display_name
    user_message = event.message.text
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    current_date = datetime.now().strftime("%Y/%m/%d")

    # Check if user message is "今日飲食規劃" or "今日飲食記錄"
    if user_message == "今日飲食規劃":
        user_message = f"今日飲食規劃 - {current_date}"
    elif user_message == "今日飲食記錄":
        user_message = f"今日飲食記錄 - {current_date}"

    try:
        # Simplified behavior without Firebase
        thread_id = create_thread()
        add_message_to_thread(thread_id, user_message)
        assistant_reply = run_assistant(thread_id)

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=assistant_reply)
        )
    except Exception as e:
        print(traceback.format_exc())
        print(f"An error occurred: {e}")
        error_message = "噢！糖安心小幫手暫時無法使用，請稍後再試"
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
