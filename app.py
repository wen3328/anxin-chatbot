from flask import Flask, request, abort
import os
import json
from dotenv import load_dotenv
from google.cloud import secretmanager
import traceback
import time
import re

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

import firebase_admin
from firebase_admin import credentials, firestore

from openai import OpenAI

# 環境變數
load_dotenv()

# 初始化 Flask App 和 LINE Bot
app = Flask(__name__)
line_bot_api = LineBotApi(os.getenv('CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('CHANNEL_SECRET'))

# ====== 從 Secret Manager 取得 Firebase 金鑰 ======
def get_firebase_credentials_from_secret(secret_name="firebase"):
    client = secretmanager.SecretManagerServiceClient()
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
    secret_path = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
    response = client.access_secret_version(request={"name": secret_path})

    # 解碼並讀取 Secret 內容
    secret_payload = response.payload.data.decode("UTF-8")
    service_account_info = json.loads(secret_payload)
    return credentials.Certificate(service_account_info)

# 使用 Secret 初始化 Firebase
firebase_cred = get_firebase_credentials_from_secret()
firebase_admin.initialize_app(firebase_cred)
db = firestore.client()

# OpenAI API 初始化
client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
ASSISTANT_ID = os.getenv('ASSISTANT_ID')

# ====== GPT Functions ======
def create_thread(user_id):
    thread = client.beta.threads.create()
    return thread.id

def add_message_to_thread(thread_id, role, content):
    client.beta.threads.messages.create(thread_id=thread_id, role=role, content=content)

def run_assistant(thread_id):
    run = client.beta.threads.runs.create(thread_id=thread_id, assistant_id=ASSISTANT_ID)
    while True:
        run_status = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id)
        if run_status.status == 'completed':
            break
        time.sleep(1)
    messages = client.beta.threads.messages.list(thread_id=thread_id)
    return messages.data[0].content[0].text.value

def remove_markdown(text):
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)  # Bold
    text = re.sub(r'\*(.*?)\*', r'\1', text)      # Italic
    text = re.sub(r'`(.*?)`', r'\1', text)        # Inline code
    return text

def trim_message_history(messages, max_length=10):
    if len(messages) > max_length:
        return messages[-max_length:]
    return messages

# ====== LINE Bot Webhook ======
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_message = event.message.text

    try:
        # 查詢 Firestore
        user_ref = db.collection("users").document(user_id)
        user_doc = user_ref.get()

        if user_doc.exists:
            user_data = user_doc.to_dict()
            thread_id = user_data.get("thread_id")
            messages = user_data.get("messages", [])
        else:
            thread_id = create_thread(user_id)
            messages = []

        messages.append({"role": "user", "content": user_message})
        add_message_to_thread(thread_id, "user", user_message)

        assistant_reply = run_assistant(thread_id)
        assistant_reply = remove_markdown(assistant_reply)

        messages.append({"role": "assistant", "content": assistant_reply})
        messages = trim_message_history(messages)

        user_ref.set({"thread_id": thread_id, "messages": messages})

        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=assistant_reply))

    except Exception as e:
        print(traceback.format_exc())
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❗ 糖安心小助手暫時無法使用"))

if __name__ == "__main__":
    port = int(os.getenv('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
