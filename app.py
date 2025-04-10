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

# ====== GPT 回應處理（ChatCompletion + stream） ======
def run_assistant_with_chatcompletion(messages):
    try:
        print(f"🚀 使用 ChatCompletion 模式 stream=True 處理訊息...")

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            stream=True
            max_tokens=300
        )

        full_reply = ""
        for chunk in response:
            delta = chunk.choices[0].delta
            if delta and delta.content:
                full_reply += delta.content

        print("✅ ChatCompletion 回應完成")
        return full_reply.strip()

    except Exception as e:
        print(f"❌ ChatCompletion 執行錯誤: {traceback.format_exc()}")
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

    if user_id in user_lock and user_lock[user_id].is_alive():
        print(f"⚠️ 忽略 {user_id} 的訊息：{user_message}（上一個請求尚未完成）")
        return

    user_lock[user_id] = threading.Thread(target=process_message, args=(user_id, user_message, event))
    user_lock[user_id].start()

# ====== 核心訊息處理邏輯 ======
def process_message(user_id, user_message, event):
    print(f"📩 開始處理訊息：user_id={user_id}, message={user_message}")

    try:
        user_ref = db.collection("users").document(user_id)
        user_doc = user_ref.get()

        if user_doc.exists:
            print("✅ 找到用戶對話歷史")
            user_data = user_doc.to_dict()
            messages = user_data.get("messages", [])
        else:
            print("🆕 新用戶，建立對話紀錄")
            messages = []

        # 加入這次 user 訊息
        messages.append({"role": "user", "content": user_message})

        # 準備 GPT 對話內容（包含角色設定）
        system_prompt = {
            "role": "system",
            "content": "你是安昕，一位親切的睡眠拖延治療機器人，請用200~300字回覆，語氣溫和、實用，結尾包含提問。"
        }
        history_for_chat = [system_prompt] + [{"role": m["role"], "content": m["content"]} for m in messages[-6:]]

        # 取得 GPT 回覆
        assistant_reply = run_assistant_with_chatcompletion(history_for_chat)
        assistant_reply = remove_markdown(assistant_reply)

        # 儲存回覆到 Firestore
        messages.append({"role": "assistant", "content": assistant_reply})
        user_ref.set({"messages": messages})

        # 傳送至 LINE（最多每段 200 字）
        max_length = 200
        reply_messages = [TextSendMessage(text=assistant_reply[i:i+max_length]) for i in range(0, len(assistant_reply), max_length)]
        line_bot_api.reply_message(event.reply_token, reply_messages)

    except Exception as e:
        print(f"❌ 錯誤: {traceback.format_exc()}")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❗安昕暫時無法使用，請稍後再試"))
    finally:
        if user_id in user_lock:
            del user_lock[user_id]

# ====== 啟動應用程式 ======
if __name__ == "__main__":
    port = int(os.getenv('PORT', 8080))
    print(f"🚀 應用程式啟動中，監聽埠號 {port}...")
    app.run(host='0.0.0.0', port=port)
