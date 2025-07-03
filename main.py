import os
import requests
import telebot
from flask import Flask, request
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from threading import Thread
from bs4 import BeautifulSoup
import time

# Cấu hình
TOKEN = "7539540916:AAFH3TBho-13IT6RB_nynN1T9j83GizVDNo"
APP_URL = "https://zproject-111.onrender.com"
ADMIN_ID = 5819094246

bot = telebot.TeleBot(TOKEN, threaded=False)
app = Flask(__name__)
START_TIME = time.time()

USER_IDS = set()
GROUP_INFOS = []

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
        "<b>🚀 ZProject Bypass Bot</b>\n\n"
        "🔗 Bạn khó chịu vì link rút gọn mất thời gian? Bot này hỗ trợ vượt nhanh <b>Link4M.com</b> chỉ với cú pháp:\n"
        "<code>/get4m https://link4m.com/abcxyz</code>\n\n"
        "🕒 Dùng /time để xem thời gian bot đã hoạt động.\n"
        "📢 Admin có thể gửi thông báo cho tất cả người dùng bằng /noti.",
        reply_markup=markup,
        parse_mode="HTML"
    )

@bot.message_handler(commands=["time"])
def time_cmd(message):
    now = time.time()
    seconds = int(now - START_TIME)
    days = seconds // (24 * 3600)
    hours = (seconds % (24 * 3600)) // 3600
    minutes = (seconds % 3600) // 60
    sec = seconds % 60
    bot.reply_to(
        message,
        f"⏱️ Bot đã hoạt động:\n<b>{days} ngày {hours} giờ {minutes} phút {sec} giây</b>",
        parse_mode="HTML"
    )

@bot.message_handler(commands=["get4m"])
def bypass_link4m(message):
    parts = message.text.split()
    if len(parts) != 2 or "link4m.com" not in parts[1]:
        return bot.reply_to(message, "⚠️ Dùng: /get4m https://link4m.com/abcxyz")

    short_url = parts[1]

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        }
        s = requests.Session()
        r = s.get(short_url, headers=headers, allow_redirects=True, timeout=10)
        final_url = r.url

        if "link4m.com" in final_url:
            soup = BeautifulSoup(r.text, "html.parser")
            a_tag = soup.find("a", {"id": "link"})
            if a_tag and a_tag.get("href"):
                final_url = a_tag["href"]

        bot.reply_to(message, f"✅ Link gốc:\n<code>{final_url}</code>", parse_mode="HTML")

    except Exception as e:
        bot.reply_to(message, f"🚫 Lỗi vượt link: <code>{e}</code>", parse_mode="HTML")

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

@bot.message_handler(commands=["sever"])
def show_groups(message):
    if message.from_user.id != ADMIN_ID:
        return bot.reply_to(message, "🚫 Không có quyền.")
    if not GROUP_INFOS:
        return bot.reply_to(message, "📭 Chưa có nhóm nào.")
    text = "<b>📦 Tất cả nhóm đã tham gia:</b>\n\n"
    for g in GROUP_INFOS:
        title = g.get("title", "Không rõ")
        link = f"https://t.me/{g.get('username')}" if g.get("username") else "⛔ Không có link"
        text += f"📌 <b>{title}</b>\n{link}\n\n"
    bot.reply_to(message, text, parse_mode="HTML", disable_web_page_preview=True)

@app.route("/")
def index():
    return "<h3>🛰️ ZProject BypassBot is live!</h3>"

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
        print("❌ Error:", e)