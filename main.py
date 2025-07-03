import os
import time
import requests
from flask import Flask, request
from threading import Thread
from bs4 import BeautifulSoup
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import undetected_chromedriver as uc
from selenium.webdriver.chrome.options import Options

# === Cấu hình ===
TOKEN = "7539540916:AAFH3TBho-13IT6RB_nynN1T9j83GizVDNo"
APP_URL = "https://zproject-111.onrender.com"
ADMIN_ID = 5819094246  # Telegram user ID của bạn

bot = telebot.TeleBot(TOKEN, threaded=False)
app = Flask(__name__)
START_TIME = time.time()

USER_IDS = set()
GROUP_INFOS = []

# === Đồng bộ nhóm/người dùng từ API ===
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

# === Lệnh /start ===
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
        "🔗 Gõ lệnh để bypass Link4M nhanh chóng:\n"
        "<code>/get4m https://link4m.com/abcxyz</code>\n\n"
        "🕒 Kiểm tra thời gian hoạt động bằng /time.",
        reply_markup=markup,
        parse_mode="HTML"
    )

# === Lệnh /time ===
@bot.message_handler(commands=["time"])
def time_cmd(message):
    now = time.time()
    seconds = int(now - START_TIME)
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    sec = seconds % 60
    bot.reply_to(message,
        f"⏱️ Bot đã hoạt động được:\n<b>{days} ngày {hours} giờ {minutes} phút {sec} giây</b>",
        parse_mode="HTML"
    )

# === Lệnh /get4m ===
@bot.message_handler(commands=["get4m"])
def bypass_link4m(message):
    parts = message.text.split()
    if len(parts) != 2 or "link4m.com" not in parts[1]:
        return bot.reply_to(message, "⚠️ Dùng: /get4m https://link4m.com/abcdxyz")

    short_url = parts[1]
    bot.reply_to(message, "🧠 Đang xử lý... vui lòng chờ 5–10 giây")

    try:
        options = Options()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-dev-shm-usage")
        options.binary_location = "/usr/bin/google-chrome"  # đường dẫn Chrome trên Render

        driver = uc.Chrome(options=options)
        driver.get(short_url)
        time.sleep(8)  # chờ JS redirect

        real_url = driver.current_url
        driver.quit()

        # Nếu vẫn là link4m thì tìm thêm link gốc từ thẻ <a id="link">
        if "link4m.com" in real_url:
            try:
                soup = BeautifulSoup(requests.get(real_url, timeout=10).text, "html.parser")
                tag = soup.find("a", {"id": "link"})
                if tag and tag.get("href"):
                    real_url = tag["href"]
            except:
                pass

        bot.send_message(message.chat.id, f"✅ Link gốc thực sự:\n<code>{real_url}</code>", parse_mode="HTML")

    except Exception as e:
        bot.send_message(message.chat.id, f"🚫 Lỗi vượt link: <code>{e}</code>", parse_mode="HTML")

# === Lệnh /noti ===
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

# === Lệnh /sever ===
@bot.message_handler(commands=["sever"])
def show_groups(message):
    if message.from_user.id != ADMIN_ID:
        return bot.reply_to(message, "🚫 Không có quyền.")
    if not GROUP_INFOS:
        return bot.reply_to(message, "📭 Chưa có nhóm nào.")
    text = "<b>📦 Danh sách nhóm:</b>\n\n"
    for g in GROUP_INFOS:
        title = g.get("title", "Không rõ")
        link = f"https://t.me/{g.get('username')}" if g.get("username") else "⛔ Không có link"
        text += f"📌 <b>{title}</b>\n{link}\n\n"
    bot.reply_to(message, text, parse_mode="HTML", disable_web_page_preview=True)

# === Webhook Flask ===
@app.route("/")
def index():
    return "<h3>🛰️ ZProject BypassBot is live!</h3>"

@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    update = telebot.types.Update.de_json(request.data.decode("utf-8"))
    bot.process_new_updates([update])
    return "OK", 200

# === Khởi chạy ===
if __name__ == "__main__":
    try:
        bot.remove_webhook()
        bot.set_webhook(url=f"{APP_URL}/{TOKEN}")
        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
    except Exception as e:
        print("❌ Error:", e)