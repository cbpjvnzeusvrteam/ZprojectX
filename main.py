import os
import requests
import json
import telebot
from flask import Flask, request
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from threading import Thread
import time
from bs4 import BeautifulSoup

TOKEN = "7539540916:AAFH3TBho-13IT6RB_nynN1T9j83GizVDNo"
APP_URL = "https://zproject-111.onrender.com"
ADMIN_ID = 5819094246

bot = telebot.TeleBot(TOKEN, threaded=False)
app = Flask(__name__)

USER_IDS = set()
GROUP_INFOS = []

def sync_chat_to_server(chat):
    if chat.type not in ["private", "group", "supergroup"]:
        return
    payload = {
        "id": chat.id,
        "type": chat.type,
        "title": getattr(chat, "title", ""),
        "username": getattr(chat, "username", "")
    }
    try:
        requests.post("https://zcode.x10.mx/apizproject.php", json=payload, timeout=5)
    except:
        pass

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
        time.sleep(1)

Thread(target=update_id_list_loop, daemon=True).start()

# ===== /start =====
@bot.message_handler(commands=["start"])
def start_cmd(message):
    sync_chat_to_server(message.chat)
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("👤 Admin ZProject", url="https://t.me/zproject2"),
        InlineKeyboardButton("📢 Group Thông Báo", url="https://t.me/zproject3"),
        InlineKeyboardButton("💬 Group Chat", url="https://t.me/zproject4")
    )
    intro = (
        "<b>👋 Xin chào!</b>\n\n"
        "Tôi là ZProject Bot – giúp kiểm tra địa chỉ email có từng bị rò rỉ dữ liệu không.\n"
        "Dùng lệnh /checkmail concac@gmail.com hoặc chỉ cần gửi email bot sẽ tự nhận check!\n\n"
        "<b>👑 Admin:</b> ZProject\n\n"
        "👇 Tham gia các nhóm cộng đồng để nhận hỗ trợ:"
    )
    bot.send_message(message.chat.id, intro, parse_mode="HTML", reply_markup=markup)

# ===== /checkmail hoặc gửi email bất kỳ =====
@bot.message_handler(commands=["checkmail"])
def checkmail_cmd(msg):
    parts = msg.text.strip().split()
    if len(parts) == 2 and "@" in parts[1]:
        fake = msg
        fake.text = parts[1]
        check_email(fake)
    else:
        bot.reply_to(msg, "📩 Dùng /checkmail email@example.com hoặc chỉ cần gửi email.")

@bot.message_handler(func=lambda m: "@" in m.text and "." in m.text)
def check_email(message):
    email = message.text.strip()
    sync_chat_to_server(message.chat)
    msg = bot.reply_to(message, f"<b>⏳ Đang kiểm tra:</b> <code>{email}</code>", parse_mode="HTML")

    try:
        url = f"https://haveibeenpwned.com/account/{email}"
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        alert = soup.find("div", {"class": "pwnedSummary"})

        if alert:
            result = (
                f"⚠️ <b><==> ZProject Bot Check <==></b>\n\n"
                f"<code>{email}</code> đã bị rò rỉ dữ liệu trên internet!\n"
                f"🔗 Chi tiết: https://haveibeenpwned.com/account/{email}"
            )
        else:
            result = f"✅ <b><==> ZProject Bot Check <==></b>\n\n<code>{email}</code> chưa từng bị rò rỉ!"
    except Exception as e:
        result = f"🚫 Lỗi kiểm tra email: <code>{e}</code>"

    try:
        bot.edit_message_text(result, msg.chat.id, msg.message_id, parse_mode="HTML")
    except:
        bot.send_message(message.chat.id, result, parse_mode="HTML")

# ===== /noti =====
@bot.message_handler(commands=["noti"])
def send_noti(msg):
    if msg.from_user.id != ADMIN_ID:
        return bot.reply_to(msg, "🚫 Không có quyền.")
    text = msg.text.replace("/noti", "").strip()
    if not text:
        return bot.reply_to(msg, "⚠️ Dùng: /noti nội_dung")

    notify = f"<b>[!] THÔNG BÁO</b>\n\n{text}"
    ok, fail = 0, 0
    all_ids = USER_IDS.union({g["id"] for g in GROUP_INFOS})
    for uid in all_ids:
        try:
            bot.send_message(uid, notify, parse_mode="HTML")
            ok += 1
        except:
            fail += 1
    bot.reply_to(msg, f"✅ Gửi: {ok} | ❌ Lỗi: {fail}")

# ===== /sever =====
@bot.message_handler(commands=["sever"])
def show_groups(msg):
    if msg.from_user.id != ADMIN_ID:
        return bot.reply_to(msg, "🚫 Không có quyền.")
    if not GROUP_INFOS:
        return bot.reply_to(msg, "⛔ Chưa có nhóm nào.")
    text = "<b>📦 All Nhóm Bot Đã Join:</b>\n\n"
    for g in GROUP_INFOS:
        title = g.get("title", "Không rõ")
        link = f"https://t.me/{g.get('username')}" if g.get("username") else "⛔ Không có link"
        text += f"📌 <b>{title}</b>\n{link}\n\n"
    bot.reply_to(msg, text, parse_mode="HTML", disable_web_page_preview=True)

# ===== Webhook Flask =====
@app.route("/")
def index():
    return "<h3>🤖 ZProject Bot đang hoạt động ổn định!</h3>"

@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    update = telebot.types.Update.de_json(request.data.decode("utf-8"))
    bot.process_new_updates([update])
    return "OK", 200

if __name__ == "__main__":
    try:
        bot.remove_webhook()
        bot.set_webhook(url=f"{APP_URL}/{TOKEN}")
        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
    except Exception as e:
        print("[BOOT ERROR]", e)