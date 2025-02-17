from flask import Flask, request, abort
import os
import json
from dotenv import load_dotenv
import traceback
import time
import re

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

import firebase_admin
from firebase_admin import credentials, firestore

from openai import OpenAI

# 加載環境變數
load_dotenv()

# 初始化 Flask 和 LINE Bot
app = Flask(__name__)
line_bot_api = LineBotApi(os.getenv('CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('CHANNEL_SECRET'))

# ====== Firebase 初始化 ======
def get_firebase_credentials_from_env():
    try:
        firebase_credentials = os.getenv("FIREBASE_CREDENTIALS")
        if not firebase_credentials:
            raise ValueError("未找到環境變數 FIREBASE_CREDENTIALS，請檢查 Cloud Run 設定")
        service_account_info = json.loads(firebase_credentials)
        print("✅ 成功從環境變數讀取 Firebase 金鑰")
        return credentials.Certificate(service_account_info)
    except Exception as e:
        print(f"❌ Firebase 金鑰讀取失敗: {str(e)}")
        raise

firebase_cred = get_firebase_credentials_from_env()
firebase_admin.initialize_app(firebase_cred)
db = firestore.client()

# 初始化 OpenAI API
client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
ASSISTANT_ID = os.getenv('ASSISTANT_ID')

# ====== GPT Functions ======
def create_thread(user_id):
    print(f"🆕 為用戶 {user_id} 創建新的 OpenAI 對話")
    thread = client.beta.threads.create()
    return thread.id

def add_message_to_thread(thread_id, role, content):
    print(f"📩 新增訊息至 OpenAI 對話 {thread_id}: [{role}] {content}")
    client.beta.threads.messages.create(
        thread_id=thread_id,
        role=role,
        content=content
    )

def run_assistant(thread_id):
    try:
        print(f"🚀 執行 OpenAI Assistant，對話 ID: {thread_id}")
        run = client.beta.threads.runs.create(thread_id=thread_id, assistant_id=ASSISTANT_ID)

        timeout_counter = 0
        while True:
            run_status = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id)
            if run_status.status == 'completed':
                break
            time.sleep(0.5)
            timeout_counter += 1
            if timeout_counter > 10:
                raise TimeoutError("⏳ OpenAI 回應超時")

        messages = client.beta.threads.messages.list(thread_id=thread_id)
        assistant_reply = messages.data[0].content[0].text.value.strip()

        # 🔴 **檢查 API Rate Limit**
        if "Rate limit exceeded" in assistant_reply:
            print("🚨 OpenAI API 達到速率限制，請降低請求頻率")
            return "❗安昕繁忙中，請再重新再傳一次訊息，若持續無回應，請聯絡研究人員"

        return assistant_reply
    except Exception as e:
        print(f"❌ OpenAI Assistant 執行錯誤: {str(e)}")
        return "❗安昕繁忙中，請再重新再傳一次訊息，若持續無回應，請聯絡研究人員"

def remove_markdown(text):
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)  # Bold
    text = re.sub(r'\*(.*?)\*', r'\1', text)      # Italic
    text = re.sub(r'`(.*?)`', r'\1', text)        # Inline code
    return text

# ====== LINE Bot Webhook ======
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        print("📩 接收到 LINE Webhook 請求")
        handler.handle(body, signature)
    except InvalidSignatureError:
        print("❌ 簽名驗證失敗")
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_message = event.message.text.strip()
    print(f"📩 接收到用戶訊息：user_id={user_id}, message={user_message}")

    try:
        # 🔎 查詢 Firestore 紀錄
        user_ref = db.collection("users").document(user_id)
        user_doc = user_ref.get()

        if user_doc.exists:
            print("✅ 找到用戶對話歷史")
            user_data = user_doc.to_dict()
            thread_id = user_data.get("thread_id")
            messages = user_data.get("messages", [])
        else:
            print("🆕 未找到用戶資料，創建新對話")
            thread_id = create_thread(user_id)
            messages = []

        # 📩 **新增用戶訊息並傳送至 OpenAI**
        messages.append({"role": "user", "content": user_message})
        add_message_to_thread(thread_id, "user", user_message)

        assistant_reply = run_assistant(thread_id)
        assistant_reply = remove_markdown(assistant_reply)

        # 🔄 **更新 Firestore 紀錄**
        messages.append({"role": "assistant", "content": assistant_reply})
        user_ref.set({"thread_id": thread_id, "messages": messages})

        # ✅ **確保 LINE 回應長度不超過 400**
        max_length = 200
        reply_messages = [assistant_reply[i:i+max_length] for i in range(0, len(assistant_reply), max_length)]

        # ✅ **逐一發送訊息**
        line_bot_api.reply_message(event.reply_token, [TextSendMessage(text=msg) for msg in reply_messages])

    except Exception as e:
        print(f"❌ 處理訊息時發生錯誤: {traceback.format_exc()}")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❗安昕繁忙中，請再重新再傳一次訊息，若持續無回應，請聯絡研究人員"))

if __name__ == "__main__":
    port = int(os.getenv('PORT', 8080))
    print(f"🚀 應用程式啟動中，監聽埠號 {port}...")
    app.run(host='0.0.0.0', port=port)
