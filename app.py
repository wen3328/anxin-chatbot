from flask import Flask, request, abort
import os
import json
from dotenv import load_dotenv
import traceback
import time
import re
import threading

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

import firebase_admin
from firebase_admin import credentials, firestore

from openai import OpenAI

# ====== 初始化設定 ======
load_dotenv()
app = Flask(__name__)
line_bot_api = LineBotApi(os.getenv('CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('CHANNEL_SECRET'))
client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

user_lock = {}

# ====== Firebase 初始化 ======
def get_firebase_credentials_from_env():
    firebase_credentials = os.getenv("FIREBASE_CREDENTIALS")
    service_account_info = json.loads(firebase_credentials)
    print("✅ 成功從環境變數讀取 Firebase 金鑰")
    return credentials.Certificate(service_account_info)

firebase_cred = get_firebase_credentials_from_env()
firebase_admin.initialize_app(firebase_cred)
db = firestore.client()

# ====== GPT 回應處理（ChatCompletion） ======
def run_chat_completion(messages):
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            max_tokens=300,  # 控制字數落在 200~300 字
            stream=False
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print("❌ ChatCompletion 錯誤：")
        traceback.print_exc()
        return "❗安昕暫時無法使用，請稍後再試"

# ====== 清除 markdown 格式（防止 LINE 亂碼） ======
def remove_markdown(text):
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
    text = re.sub(r'\*(.*?)\*', r'\1', text)
    text = re.sub(r'`(.*?)`', r'\1', text)
    return text

# ====== LINE Webhook 接收點 ======
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
    user_message = event.message.text.strip()

    if user_id in user_lock and user_lock[user_id].is_alive():
        print(f"⚠️ 忽略 {user_id} 的訊息：{user_message}（上一個請求尚未完成）")
        return

    user_lock[user_id] = threading.Thread(target=process_message, args=(user_id, user_message, event))
    user_lock[user_id].start()

# ====== 處理訊息邏輯（快速 ChatGPT 模式） ======
def process_message(user_id, user_message, event):
    print(f"📩 處理訊息：user_id={user_id}, message={user_message}")

    try:
        user_ref = db.collection("users").document(user_id)
        user_doc = user_ref.get()

        if user_doc.exists:
            user_data = user_doc.to_dict()
            messages = user_data.get("messages", [])
        else:
            messages = []

        messages.append({"role": "user", "content": user_message})

        # 建立角色提示與上下文（只保留近 3 則）
        system_prompt = {
            "role": "system",
            "content": "你是安昕，一位親切溫和的睡眠拖延治療機器人，請用 200~300 字純文字回答，結尾要提問。禁止重複問題。"
        }
        history_for_chat = [system_prompt] + [{"role": m["role"], "content": m["content"]} for m in messages[-3:]]

        # 取得 GPT 回應
        assistant_reply = run_chat_completion(history_for_chat)
        assistant_reply = remove_markdown(assistant_reply)

        # 更新 Firestore
        messages.append({"role": "assistant", "content": assistant_reply})
        user_ref.set({"messages": messages})

        # 回傳給 LINE（每段不超過 200 字）
        max_length = 200
        reply_messages = [TextSendMessage(text=assistant_reply[i:i+max_length]) for i in range(0, len(assistant_reply), max_length)]
        line_bot_api.reply_message(event.reply_token, reply_messages)

    except Exception as e:
        print("❌ 發生錯誤：")
        traceback.print_exc()
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❗安昕暫時無法使用，請稍後再試"))
    finally:
        if user_id in user_lock:
            del user_lock[user_id]

# ====== 啟動應用程式 ======
if __name__ == "__main__":
    port = int(os.getenv('PORT', 8080))
    print(f"🚀 應用程式啟動中，監聽埠號 {port}...")
    app.run(host='0.0.0.0', port=port)
