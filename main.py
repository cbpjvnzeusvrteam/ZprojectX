import os
import requests
import json
import telebot
from flask import Flask, request
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from threading import Thread
from bs4 import BeautifulSoup
import time

TOKEN = "7539540916:AAFH3TBho-13IT6RB_nynN1T9j83GizVDNo"
APP_URL = "https://zproject-111.onrender.com"
ADMIN_ID = 5819094246

bot = telebot.TeleBot(TOKEN, threaded=False)
app = Flask(__name__)

USER_IDS = set()
GROUP_INFOS = []
CHECKED_EMAILS = {}  # Lưu thời gian đã check để chống spam

# Đồng bộ user/group lên PHP API
def sync_chat_to_server(chat):
    if chat.type not in ["private", "group", "supergroup"]:
        return
    try:
        payload = {
            "id": chat.id,
            "type": chat.type,
            "title": getattr(chat, "title", ""),
            "username": getattr(chat, "username", "")
        }
        requests.post("https://zcode.x10.mx/apizproject.php", json=payload, timeout=5)
    except:
        pass

# Cập nhật danh sách ID từ server
def update_id_list_loop():
    global USER_IDS, GROUP_INFOS
    while True:
        try:
            r = requests.get("https://zcode.x10.mx/group-idchat.json", timeout=5)
            data = r.json()
            USER_IDS = set(data.get("users", []))
            GROUP_INFOS = data.get("groups", [])
        except:
            pass
        time.sleep(5)

Thread(target=update_id_list_loop, daemon=True).start()

# START
@bot.message_handler(commands=["start"])
def start_cmd(message):
    sync_chat_to_server(message.chat)
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("👤 Admin", url="https://t.me/zproject2"),
        InlineKeyboardButton("📢 Thông Báo", url="https://t.me/zproject3"),
        InlineKeyboardButton("💬 Chat", url="https://t.me/zproject4")
    )
    bot.send_message(
        message.chat.id,
        "<b>👋 Xin chào!</b>\n\nGửi email bất kỳ hoặc dùng /checkmail để kiểm tra xem địa chỉ đó đã từng bị rò rỉ dữ liệu chưa 🔐",
        reply_markup=markup,
        parse_mode="HTML"
    )

# /checkmail email
@bot.message_handler(commands=["checkmail"])
def checkmail_cmd(msg):
    parts = msg.text.split()
    if len(parts) == 2 and "@" in parts[1]:
        fake = msg
        fake.text = parts[1]
        check_email(fake)
    else:
        bot.reply_to(msg, "📩 Dùng: /checkmail email@example.com")

# Gửi email trực tiếp
@bot.message_handler(func=lambda m: "@" in m.text and "." in m.text)
def check_email(message):
    email = message.text.strip().lower()
    chat_id = message.chat.id

    # Chống spam mỗi 30s
    now = time.time()
    if CHECKED_EMAILS.get(chat_id) and now - CHECKED_EMAILS[chat_id] < 30:
        return
    CHECKED_EMAILS[chat_id] = now

    sync_chat_to_server(message.chat)

    try:
        msg = bot.reply_to(message, f"<b>⏳ Đang kiểm tra:</b> <code>{email}</code>", parse_mode="HTML")
    except:
        msg = None

    try:
        r = requests.get(f"https://haveibeenpwned.com/account/{email}", headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        alert = soup.find("div", {"class": "pwnedSummary"})
        if alert:
            result = f"⚠️ <b><==> ZProject Check <==></b>\n\n<code>{email}</code> đã bị rò rỉ!\n🔗 https://haveibeenpwned.com/account/{email}"
        else:
            result = f"✅ <b><==> ZProject Check <==></b>\n\n<code>{email}</code> chưa từng bị rò rỉ!"
    except Exception as e:
        result = f"🚫 Lỗi kiểm tra: <code>{e}</code>"

    if msg:
        try:
            bot.edit_message_text(result, msg.chat.id, msg.message_id, parse_mode="HTML")
        except:
            bot.send_message(chat_id, result, parse_mode="HTML")
    else:
        bot.send_message(chat_id, result, parse_mode="HTML")

# /noti
@bot.message_handler(commands=["noti"])
def send_noti(message):
    if message.from_user.id != ADMIN_ID:
        return bot.reply_to(message, "🚫 Không có quyền.")
    text = message.text.replace("/noti", "").strip()
    if not text:
        return bot.reply_to(message, "⚠️ Dùng: /noti nội_dung")

    notify = f"<b>[!] THÔNG BÁO</b>\n\n{text}"
    ok, fail = 0, 0
    for uid in USER_IDS.union({g["id"] for g in GROUP_INFOS}):
        try:
            bot.send_message(uid, notify, parse_mode="HTML")
            ok += 1
        except:
            fail += 1
    bot.reply_to(message, f"✅ Gửi: {ok} | ❌ Lỗi: {fail}")

# /sever
@bot.message_handler(commands=["sever"])
def show_groups(message):
    if message.from_user.id != ADMIN_ID:
        return bot.reply_to(message, "🚫 Không có quyền.")
    if not GROUP_INFOS:
        return bot.reply_to(message, "📭 Chưa có nhóm nào.")
    text = "<b>📦 Danh sách nhóm:</b>\n\n"
    for g in GROUP_INFOS:
        title = g.get("title", "Không rõ")
        link = f"https://t.me/{g.get('username')}" if g.get("username") else "⛔ Chưa có link"
        text += f"📌 <b>{title}</b>\n{link}\n\n"
    bot.reply_to(message, text, parse_mode="HTML", disable_web_page_preview=True)

# Webhook Flask
@app.route("/")
def index():
    return "<h3>🛰️ ZProject LeakBot is live!</h3>"

@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    update = telebot.types.Update.de_json(request.data.decode("utf-8"))
    bot.process_new_updates([update])
    return "OK", 200

# Khởi động
if __name__ == "__main__":
    try:
        bot.remove_webhook()
        bot.set_webhook(url=f"{APP_URL}/{TOKEN}")
        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
    except Exception as e:
        print("❌ Error:", e)