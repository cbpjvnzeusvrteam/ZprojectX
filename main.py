import os
import requests
import json
import telebot
from flask import Flask, request
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from threading import Thread
import time

TOKEN = "7539540916:AAFH3TBho-13IT6RB_nynN1T9j83GizVDNo"
APP_URL = "https://zproject-111.onrender.com"
ADMIN_ID = 5819094246  # Cập nhật đúng ID admin thật

bot = telebot.TeleBot(TOKEN, threaded=False)
app = Flask(__name__)

# ==== Biến lưu danh sách ID từ server ====
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

# ==== Tải danh sách ID từ server mỗi 1 giây ====

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

# ==== START ====

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
        "Tôi là trợ lý kiểm tra email rò rỉ – bạn chỉ cần gửi một địa chỉ email như <code>abc@gmail.com</code>\n"
        "Tôi sẽ kiểm tra nó đã từng xuất hiện trong các vụ rò rỉ dữ liệu hay chưa 🔐\n\n"
        "<b>👑 Admin:</b> ZProject\n\n"
        "👇 Tham gia các nhóm của ZProject để cập nhật thông tin!"
    )
    bot.send_message(message.chat.id, intro, parse_mode="HTML", reply_markup=markup)

# ==== /checkmail (hoặc gửi email bất kỳ) ====

@bot.message_handler(commands=["checkmail"])
def checkmail_cmd(message):
    bot.reply_to(message, "📩 Gửi địa chỉ email cần kiểm tra sau lệnh này.")

@bot.message_handler(func=lambda m: "@" in m.text and "." in m.text)
def check_email(message):
    email = message.text.strip()
    sync_chat_to_server(message.chat)
    status_msg = bot.reply_to(message, f"<b>⏳ Zproject Đang kiểm tra email:</b> <code>{email}</code>", parse_mode="HTML")

    try:
        url = f"https://haveibeenpwned.com/unifiedsearch/{email}"
        headers = {"User-Agent": "ZProject LeakBot"}
        r = requests.get(url, headers=headers, timeout=5)

        if r.status_code == 404:
            result = f"✅ <b><==> Zproject Bot Check <==></b>\n\nXin Chúc Mừng Bạn Nhá:v \nEmail <code>{email}</code> chưa từng xuất hiện trong vụ rò rỉ nào."
        elif r.status_code == 200:
            result = (
                f"⚠️ <b><==> Bot Check <==></b>\n\n"
                f"<code>{email}</code> đã bị rò rỉ dữ liệu trên internet.\n"
                f"🔗 Kiểm tra chi tiết tại:\n"
                f"<code>https://haveibeenpwned.com/unifiedsearch/{email}</code>"
            )
        else:
            result = "❌ Không thể kết nối hệ thống kiểm tra hoặc bị giới hạn lượt truy cập."

    except Exception as e:
        result = f"🚫 Lỗi kiểm tra: <code>{e}</code>"

    try:
        bot.edit_message_text(result, chat_id=status_msg.chat.id, message_id=status_msg.message_id, parse_mode="HTML")
    except:
        bot.send_message(message.chat.id, result, parse_mode="HTML")

# ==== /noti (admin gửi thông báo) ====

@bot.message_handler(commands=["noti"])
def send_noti(message):
    if message.from_user.id != ADMIN_ID:
        return bot.reply_to(message, "🚫 Chỉ admin mới dùng được.")
    text = message.text.replace("/noti", "").strip()
    if not text:
        return bot.reply_to(message, "⚠️ Cú pháp: /noti nội_dung")

    sent, failed = 0, 0
    noti_text = f"<b>[!] THÔNG BÁO TỪ ZPROJECT</b>\n\n{text}"

    for uid in USER_IDS.union({g["id"] for g in GROUP_INFOS}):
        try:
            bot.send_message(uid, noti_text, parse_mode="HTML")
            sent += 1
        except:
            failed += 1

    bot.reply_to(message, f"✅ Đã gửi: {sent}\n❌ Thất bại: {failed}")

# ==== /sever (admin xem danh sách group) ====

@bot.message_handler(commands=["sever"])
def show_server_info(message):
    if message.from_user.id != ADMIN_ID:
        return bot.reply_to(message, "🚫 Không có quyền truy cập.")
    if not GROUP_INFOS:
        return bot.reply_to(message, "⚠️ Chưa có dữ liệu nhóm.")

    text = "<b>📦 Danh sách nhóm bot đang ghi nhận:</b>\n\n"
    for g in GROUP_INFOS:
        link = f"https://t.me/{g['username']}" if g.get("username") else "🚫 Chưa có link"
        text += f"📌 <b>{g.get('title', 'Không rõ')}</b>\n{link}\n\n"
    bot.reply_to(message, text, parse_mode="HTML", disable_web_page_preview=True)

# ==== FLASK + WEBHOOK ====

@app.route("/")
def index():
    return "<h3>🔐 ZProject LeakBot đang hoạt động.</h3>"

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
        print(f"Khởi động lỗi: {e}")