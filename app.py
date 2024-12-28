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
from linebot.models import (MessageEvent, TextMessage, TextSendMessage,PostbackEvent)
# OpenAI import
from openai import OpenAI

# Firebase import
import firebase_admin
from firebase_admin import credentials, firestore

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

# Initialize Firebase
#cred = credentials.Certificate(
   # {
     #   "type": os.getenv('FIREBASE_CREDENTIALS_TYPE'),
     #  "project_id": os.getenv('FIREBASE_CREDENTIALS_PROJECT_ID'),
     #   "private_key_id": os.getenv('FIREBASE_CREDENTIALS_PRIVATE_KEY_ID'),
     #   "private_key": os.getenv('FIREBASE_CREDENTIALS_PRIVATE_KEY').replace('\\n', '\n'),
      #  "client_email": os.getenv('FIREBASE_CREDENTIALS_CLIENT_EMAIL'),
      #  "client_id": os.getenv('FIREBASE_CREDENTIALS_CLIENT_ID'),
      #  "auth_uri": os.getenv('FIREBASE_CREDENTIALS_AUTH_URI'),
       # "token_uri": os.getenv('FIREBASE_CREDENTIALS_TOKEN_URI'),
        #"auth_provider_x509_cert_url": os.getenv('FIREBASE_CREDENTIALS_AUTH_PROVIDER_X509_CERT_URL'),
       # "client_x509_cert_url": os.getenv('FIREBASE_CREDENTIALS_CLIENT_X509_CERT_URL'),
        #"universe_domain": os.getenv('FIREBASE_CREDENTIALS_UNIVERSE_DOMAIN')
    #}
#)
#firebase_admin.initialize_app(cred)
#db = firestore.client()


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
        elif run_status.status in ['failed']:
            raise Exception("Assistant run failed.")
        elif run_status.status == 'cancelled':
            print(f"Run {run.id} was cancelled.")
            return "CANCELLED"
        time.sleep(1)  # Add delay to avoid excessive requests
        timeout_counter += 1
        if timeout_counter > MAX_RETRIES:
            raise Exception("Assistant run timeout")
    
    # Get reply message
    messages = client.beta.threads.messages.list(thread_id=thread_id)
    return messages.data[0].content[0].text.value


def cancel_run(thread_id):
    """Cancel an ongoing Assistant Run"""
    try:
        runs = client.beta.threads.runs.list(thread_id=thread_id)
        active_runs = [run for run in runs.data if run.status in ['in_progress', 'queued']]

        for run in active_runs:
            client.beta.threads.runs.cancel(thread_id=thread_id, run_id=run.id)
            print(f"Cancelled run {run.id} in thread {thread_id}")
    except Exception as e:
        print(f"Failed to cancel ongoing run: {e}")


def remove_markdown(text):
    # Turn markdown to plain text
    # Remove bold and italic tags
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)  # **粗體**
    text = re.sub(r'\*(.*?)\*', r'\1', text)      # *斜體*
    
    # Remove title tags
    text = re.sub(r'^#+\s', '', text, flags=re.MULTILINE)  # # 標題
    
    # Remove link tags
    text = re.sub(r'\[(.*?)\]\((.*?)\)', r'\1 (\2)', text)  # [文字](連結)
    
    # Remove code block tags
    text = re.sub(r'```(.*?)```', r'\1', text, flags=re.DOTALL)  # ```程式碼區塊```
    text = re.sub(r'`(.*?)`', r'\1', text)  # `行內程式碼`
    
    # Remove quote tags
    text = re.sub(r'^>\s', '', text, flags=re.MULTILINE)  # > 引用
    
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
    profile = line_bot_api.get_profile(user_id)
    display_name = profile.display_name
    user_message = event.message.text
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    current_date = datetime.now().strftime("%Y/%m/%d")

    try:
        user_ref = db.collection('users').document(user_id)
        user_doc = user_ref.get()
        
        # Initialize messages list
        messages = []
        is_processing = False
        previous_unprocessed_message = ""
        
        if user_doc.exists:
            # If user exists, get thread_id and update messages
            user_data = user_doc.to_dict()
            thread_id = user_data.get('thread_id')
            is_processing = user_data.get('is_processing', False)
            messages = user_data.get('messages', [])

            if is_processing:
                cancel_run(thread_id)
                previous_messages = [msg['content'] for msg in messages if msg['role'] == 'user']
                previous_unprocessed_message = previous_messages[-1] if previous_messages else ""
                # Add previous unprocessed message to user message
                user_message = f"{previous_unprocessed_message}\n{user_message}"
        else:
            # If user does not exist, create a new thread
            thread_id = create_thread()
            user_ref.set({
                'thread_id': thread_id,
                'is_processing': False,
                'last_active': firestore.SERVER_TIMESTAMP,
                'create_at': firestore.SERVER_TIMESTAMP,
                'user_info': {
                    'display_name': display_name,
                    'language': profile.language if hasattr(profile, 'language') else 'zh-Hant'
                },
                'messages': []
            })

        # Update user message status
        user_ref.update({
            'is_processing': True,
            'last_active': firestore.SERVER_TIMESTAMP,
        })
        
        # Add user message
        messages.append({
            'role': 'user',
            'content': user_message,
            'create_at': current_time
        })
        
        # Immediately update Firestore with user message
        user_ref.update({
            'messages': messages,
            'last_active': firestore.SERVER_TIMESTAMP
        })
        
        # Send user message to Assistant
        add_message_to_thread(thread_id, user_message)
        assistant_reply = run_assistant(thread_id)
        assistant_reply = remove_markdown(assistant_reply)

        if assistant_reply == "CANCELLED":
            return
        
        # Add assistant reply
        messages.append({
            'role': 'assistant',
            'content': assistant_reply,
            'create_at': current_time
        })

        # Update with new message
        user_ref.update({
            'messages': messages,
            'last_active': firestore.SERVER_TIMESTAMP,
            'is_processing': False
        })

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=assistant_reply)
        )
    except Exception as e:
        print(traceback.format_exc())
        print(f"An error occurred: {e}")
        error_message = "❗ 安昕暫時無法使用，請聯絡研究人員"
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=error_message)
        )
        # Update with error message
        if 'user_ref' in locals():
            try:
                updated_doc = user_ref.get().to_dict()
                messages = updated_doc.get('messages', [])
                messages.append({
                    'role': 'assistant',
                    'content': error_message,
                    'create_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                })
                user_ref.update({
                    'messages': messages,
                    'last_active': firestore.SERVER_TIMESTAMP,
                    'is_processing': False  # Reset processing status
                })
            except Exception as e:
                print(f"Error updating error message: {e}")


# ====== Handle Postback Event ======
@handler.add(PostbackEvent)
def handle_postback(event):
    print(event.postback.data)


# ====== Start Flask App ======
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
