import os
import time
import logging
import requests
import re
import base64
import uuid
import json
from datetime import datetime
from io import BytesIO
from PIL import Image # Đảm bảo Pillow được cài đặt nếu dùng chức năng ảnh
import random
import string
import threading # Thêm import này cho auto_delete_email

from flask import Flask, request
from threading import Thread
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from types import SimpleNamespace

# --- Cấu hình logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)

# --- Cấu hình chung ---
# Lấy BOT_TOKEN từ biến môi trường, hoặc dùng giá trị mặc định nếu không có (chỉ để phát triển)
TOKEN = os.environ.get("BOT_TOKEN", "7539540916:AAENFBF2B2dyXLITmEC2ccgLYim2t9vxOQk") # THAY BẰNG TOKEN BOT CỦA BẠN
ADMIN_ID = int(os.environ.get("ADMIN_ID", 5819094246)) # THAY BẰNG ID ADMIN CỦA BẠN

# Đảm bảo APP_URL là URL thuần túy, không có Markdown
APP_URL = os.environ.get("APP_URL", "https://zproject-111.onrender.com") # THAY BẰNG URL APP CỦA BẠN

logging.info(f"APP_URL được cấu hình: {APP_URL}")

bot = telebot.TeleBot(TOKEN, threaded=False)
app = Flask(__name__)
START_TIME = time.time()

USER_IDS = set()
GROUP_INFOS = []
# Từ điển để lưu trữ thông tin phản hồi của người dùng (feedback_message_id: original_chat_id)
# Điều này cần thiết để admin có thể reply và bot biết gửi về đâu
bot.feedback_messages = {}
# Lưu trữ các đoạn code để copy
bot.code_snippets = {}
# Lưu trữ các câu trả lời để chuyển thành voice
bot.voice_map = {}

# Lưu thông tin người dùng Mail.tm (email, mật khẩu, token, thời gian hết hạn)
user_data = {}
# Lưu trữ ID tin nhắn của bot để có thể chỉnh sửa sau này
# mail_message_id: {chat_id, user_id, type: 'mail_info' hoặc 'inbox'}
bot.mail_messages_state = {}


# Biến toàn cục để đếm số lượt tương tác
interaction_count = 0

# --- Cấu hình Requests với Retry và Timeout chung ---
session = requests.Session()
retries = Retry(total=3, backoff_factor=1, status_forcelist=[502, 503, 504])
adapter = HTTPAdapter(max_retries=retries)
session.mount("https://", adapter)
session.mount("http://", adapter)

DEFAULT_TIMEOUT_GLOBAL = 30 # Timeout mặc định cho các request khác
NGL_REQUEST_TIMEOUT = 15 # Timeout riêng cho NGL (có thể đặt ngắn hơn để bỏ qua nhanh)

# Ghi đè phương thức request để áp dụng timeout mặc định, nhưng NGL sẽ dùng timeout riêng
class TimeoutSession(requests.Session):
    def request(self, method, url, **kwargs):
        # Apply NGL_REQUEST_TIMEOUT if it's an NGL URL, otherwise use DEFAULT_TIMEOUT_GLOBAL
        if "zeusvr.x10.mx/ngl" in url:
            kwargs.setdefault('timeout', NGL_REQUEST_TIMEOUT)
        else:
            kwargs.setdefault('timeout', DEFAULT_TIMEOUT_GLOBAL)
        return super(TimeoutSession, self).request(method, url, **kwargs)

session = TimeoutSession()
session.mount("https://", adapter)
session.mount("http://", adapter)

# --- Cấu hình Gemini API và Prompt từ xa ---
GEMINI_API_KEY = "AIzaSyDpmTfFibDyskBHwekOADtstWsPUCbIrzE" # THAY BẰNG KHÓA API GEMINI CỦA BẠN
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
REMOTE_PROMPT_URL = "https://zcode.x10.mx/prompt.json"
REMOTE_LOG_HOST = "https://zcode.x10.mx/save.php"

# --- URL ảnh dùng trong bot ---
NGL_SUCCESS_IMAGE_URL = "https://i.ibb.co/fV1srXJ8/9885878c-2a4b-4246-ae2e-fda17d735e2d.jpg"
START_IMAGE_URL = "https://i.ibb.co/MkQ2pTjv/ca68c4b2-60dc-4eb1-9a20-ebf2cc5c577f.jpg"
NOTI_IMAGE_URL = "https://i.ibb.co/QvrB4zMB/ca68c4b2-2a4b-4246-ae2e-fda17d735e2d.jpg"
TUONGTAC_IMAGE_URL = "https://i.ibb.co/YF4yRCBP/1751301092916.png"

# --- Các hàm Dummy (Cần thay thế bằng logic thực tế của bạn) ---
def load_user_memory(user_id):
    """Tải lịch sử trò chuyện của người dùng."""
    # Đây là hàm dummy, hãy thay thế bằng logic tải dữ liệu thực tế
    return []

def save_user_memory(user_id, memory):
    """Lưu lịch sử trò chuyện của người dùng."""
    # Đây là hàm dummy, hãy thay thế bằng logic lưu dữ liệu thực tế
    pass

def html_escape(text):
    """Định dạng văn bản thành HTML, tránh lỗi ký tự đặc biệt."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#039;")

class gTTS:
    """Class dummy cho gTTS. Thay thế bằng thư viện gTTS thực tế nếu bạn muốn chức năng này hoạt động."""
    def __init__(self, text, lang="vi", slow=False):
        self.text = text
        self.lang = lang
        self.slow = slow
    def save(self, filename):
        logging.info(f"Dummy gTTS: Saving '{self.text[:50]}...' to {filename}")
        with open(filename, "wb") as f:
            f.write(b"dummy_audio_data")

# --- Các hàm hỗ trợ cho chức năng Mail.tm ---

# Tạo chuỗi ngẫu nhiên
def random_string(length=3):
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))

# Tự động xóa email sau 10 phút
def auto_delete_email(user_id):
    time.sleep(600)  # 10 phút
    if user_id in user_data:
        # THỰC HIỆN XÓA TÀI KHOẢN TRÊN MAIL.TM NẾU CÓ THỂ
        # Ví dụ (cần lưu account_id và token vào user_data khi tạo mail):
        # try:
        #     account_info = user_data[user_id]
        #     if 'account_id' in account_info and 'token' in account_info:
        #         headers = {"Authorization": f"Bearer {account_info['token']}"}
        #         session.delete(f"https://api.mail.tm/accounts/{account_info['account_id']}", headers=headers)
        #         logging.info(f"Đã xóa tài khoản Mail.tm: {account_info['email']}")
        # except Exception as e:
        #     logging.error(f"Lỗi khi xóa tài khoản Mail.tm cho user {user_id}: {e}")

        del user_data[user_id]
        send_message_robustly(user_id, "⏰ Mail 10 phút của bạn đã hết hạn!")

# Lấy domain có sẵn từ API mail.tm
def get_domain():
    # Sử dụng session đã cấu hình của ZProject bot
    try:
        r = session.get("https://api.mail.tm/domains")
        r.raise_for_status() # Kiểm tra lỗi HTTP
        domains = r.json()["hydra:member"]
        # Lọc các domain có isActive = True
        active_domains = [d for d in domains if d.get('isActive', False)]
        if active_domains:
            return random.choice(active_domains)["domain"] # Chọn ngẫu nhiên một domain
        return None
    except requests.exceptions.RequestException as e: # Bắt lỗi requests cụ thể
        logging.error(f"Lỗi khi lấy domain từ Mail.tm: {e}")
        return None
    except Exception as e: # Bắt các lỗi khác
        logging.error(f"Lỗi không xác định khi lấy domain từ Mail.tm: {e}")
        return None

# Đăng ký và lấy token
def create_temp_mail():
    domain = get_domain()
    if not domain:
        return None, None, None

    email = f"zproject_{random_string()}@{domain}"
    password = random_string(12)

    try:
        # Tạo tài khoản
        r_acc = session.post("https://api.mail.tm/accounts", json={
            "address": email,
            "password": password
        })
        r_acc.raise_for_status()

        # Đăng nhập để lấy token
        r_token = session.post("https://api.mail.tm/token", json={
            "address": email,
            "password": password
        })
        r_token.raise_for_status()

        token = r_token.json()['token']
        return email, password, token
    except Exception as e:
        logging.error(f"Lỗi khi tạo/đăng nhập mail.tm: {e}")
        return None, None, None

# Hàm xây dựng các nút cho Mail.tm
def build_mail_buttons(user_id, state):
    markup = InlineKeyboardMarkup()
    # Thêm user_id vào callback_data để kiểm tra quyền
    if state == 'mail_info':
        markup.row(InlineKeyboardButton("📩 Xem Hộp Thư", callback_data=f"mailtm_inbox|{user_id}"))
    elif state == 'inbox':
        markup.row(
            InlineKeyboardButton("🔄 Làm Mới", callback_data=f"mailtm_refresh|{user_id}"),
            InlineKeyboardButton("↩️ Quay Lại", callback_data=f"mailtm_back|{user_id}")
        )
    return markup


# === Đồng bộ nhóm/người dùng từ API ===
def sync_chat_to_server(chat):
    """Đồng bộ thông tin chat (người dùng/nhóm) lên server từ xa."""
    if chat.type not in ["private", "group", "supergroup"]:
        return
    try:
        payload = {
            "id": chat.id,
            "type": chat.type,
            "title": getattr(chat, "title", ""),
            "username": getattr(chat, "username", "")
        }
        response = session.post("https://zcode.x10.mx/apizproject.php", json=payload, timeout=DEFAULT_TIMEOUT_GLOBAL)
        response.raise_for_status()
        logging.info(f"Synced chat {chat.id} to server")
    except Exception as e:
        logging.error(f"Error syncing chat {chat.id}: {e}")

def update_id_list_loop():
    """Vòng lặp định kỳ để cập nhật danh sách người dùng và nhóm từ API."""
    global USER_IDS, GROUP_INFOS
    while True:
        try:
            response = session.get("https://zcode.x10.mx/group-idchat.json", timeout=DEFAULT_TIMEOUT_GLOBAL)
            response.raise_for_status()
            data = response.json()
            new_users = set(data.get("users", []))
            new_groups = data.get("groups", [])
            if new_users != USER_IDS or new_groups != GROUP_INFOS:
                USER_IDS = new_users
                GROUP_INFOS = new_groups
                logging.info("Updated user and group lists")
        except Exception as e:
            logging.error(f"Error updating lists: {e}")
        time.sleep(10) # Đợi 30 giây trước khi cập nhật lại

# Khởi chạy luồng cập nhật ID
Thread(target=update_id_list_loop, daemon=True).start()

# --- Hàm hỗ trợ cho /ask và callbacks ---
def build_reply_button(user_id, question, reply_id=None):
    """Tạo các nút phản hồi cho tin nhắn /ask."""
    # Giới hạn độ dài của question để tránh lỗi callback_data quá dài
    safe_q = (re.sub(r"[^\w\s]", "", question.strip())[:50] + '...') if len(question.strip()) > 50 else question.strip()
    
    markup = InlineKeyboardMarkup()
    buttons = [
        InlineKeyboardButton("🔁 Trả lời lại", callback_data=f"retry|{user_id}|{safe_q}")
    ]
    if reply_id:
        buttons.append(InlineKeyboardButton("🔊 Chuyển sang Voice", callback_data=f"tts|{user_id}|{reply_id}"))
    markup.row(*buttons)
    return markup

# Decorator để tăng interaction_count cho mỗi lệnh
def increment_interaction_count(func):
    def wrapper(message, *args, **kwargs):
        global interaction_count
        interaction_count += 1 # Tăng số lượt tương tác
        return func(message, *args, **kwargs)
    return wrapper

# Hàm gửi tin nhắn có xử lý lỗi reply_to_message_id
def send_message_robustly(chat_id, text=None, photo=None, caption=None, reply_markup=None, parse_mode="HTML", reply_to_message_id=None, disable_web_page_preview=None):
    try:
        if photo:
            return bot.send_photo(
                chat_id=chat_id,
                photo=photo,
                caption=caption,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                reply_to_message_id=reply_to_message_id
            )
        else:
            return bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                reply_to_message_id=reply_to_message_id,
                disable_web_page_preview=disable_web_page_preview
            )
    except telebot.apihelper.ApiTelegramException as e:
        if "message to be replied not found" in str(e):
            logging.warning(f"Failed to reply to message {reply_to_message_id} in chat {chat_id}: {e}. Sending as new message.")
            if photo:
                return bot.send_photo(
                    chat_id=chat_id,
                    photo=photo,
                    caption=caption,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode
                )
            else:
                return bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode,
                    disable_web_page_preview=disable_web_page_preview
                )
        else:
            logging.error(f"Error sending message to chat {chat_id}: {e}")
            raise

# === LỆNH XỬ LÝ TIN NHẮN ===

@bot.message_handler(commands=["start"])
@increment_interaction_count
def start_cmd(message):
    """Xử lý lệnh /start, hiển thị thông tin bot và các liên kết."""
    sync_chat_to_server(message.chat)
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("👤 Admin", url="https://t.me/zproject2"),
        InlineKeyboardButton("📢 Thông Báo", url="https://t.me/zproject3"),
        InlineKeyboardButton("💬 Chat", url="https://t.me/zproject4")
    )
    send_message_robustly(
        message.chat.id,
        photo=START_IMAGE_URL,
        caption="<b>🚀 ZProject Bot</b>\n\n"
                "Chào mừng bạn đến với Dịch Vụ Zproject Bot Được Make Bởi @zproject2\n "
                "● Chúng Tôi Có Các Dịch Vụ Như Treo Bot 24/7 Giá Cực Rẻ Hơn VPS và Máy Ảo \n● Bạn Có Thể Liên Hệ Telegram @zproject2.\n"
                "--> Gõ /phanhoi Để Phản Hồi Lỗi Hoặc Cần Cải Tiến Gì Đó Cho Bot, Ví Dụ <code>/phanhoi Lỗi Ở Lệnh Ask 503.</code>\n"
                "--> Gõ /help để xem danh sách các lệnh.",
        reply_markup=markup,
        parse_mode="HTML",
        reply_to_message_id=message.message_id
    )

@bot.message_handler(commands=["help"])
@increment_interaction_count
def help_command(message):
    """Xử lý lệnh /help, hiển thị menu các lệnh."""
    sync_chat_to_server(message.chat)
    help_text = (
        "<b>📚 Menu Lệnh ZProject Bot</b>\n\n"
        "•  <code>/start</code> - Start Zproject Bot.\n"
        "•  <code>/help</code>  - Show Menu Zproject Bot.\n"
        "•  <code>/time</code>  - Uptime Zproject Bot.\n"
        "•  <code>/ask &lt;câu hỏi&gt;</code> - Hỏi AI Được Tích Hợp WormGpt V2.\n"
        "•  <code>/ngl &lt;username&gt; &lt;tin_nhắn&gt; &lt;số_lần&gt;</code> - Spam Ngl.\n"
        "•  <code>/noti &lt;nội dung&gt;</code> - <i>(Chỉ Admin)</i> Gửi thông báo.\n"
        "•  <code>/sever</code> - <i>(Chỉ Admin)</i> Sever Bot.\n"
        "•  <code>/tuongtac</code> - Xem tổng số lượt tương tác của bot.\n"
        "•  <code>/phanhoi</code> - Gửi Phản Hồi Lỗi Hoặc Chức Năng Cần Cải Tiến.\n"
        "•  <code>/mail10p</code> - Tạo mail 10 phút dùng 1 lần.\n"
        "•  <code>/hopthu</code> - Xem hộp thư của mail 10 phút đã tạo.\n"
        "•  <code>/xoamail10p</code> - Xóa mail 10 phút hiện tại của bạn." # Thêm lệnh mới
    )
    send_message_robustly(
        chat_id=message.chat.id,
        photo=NGL_SUCCESS_IMAGE_URL,
        caption=help_text,
        parse_mode="HTML",
        reply_to_message_id=message.message_id
    )

@bot.message_handler(commands=["time"])
@increment_interaction_count
def time_cmd(message):
    """Xử lý lệnh /time, hiển thị thời gian hoạt động của bot."""
    sync_chat_to_server(message.chat)
    now = time.time()
    seconds = int(now - START_TIME)
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    sec = seconds % 60
    send_message_robustly(
        message.chat.id,
        text=f"<blockquote>⏱️ Bot đã hoạt động được:\n<b>{days} ngày {hours} giờ {minutes} phút {sec} giây</b></blockquote>",
        parse_mode="HTML",
        reply_to_message_id=message.message_id
    )

@bot.message_handler(commands=["tuongtac"])
@increment_interaction_count
def tuongtac_command(message):
    """Xử lý lệnh /tuongtac, hiển thị tổng số lượt tương tác của bot."""
    sync_chat_to_server(message.chat)
    
    reply_text = (
        f"<b>📊 THỐNG KÊ ZPROJECT BOT</b>\n\n"
        f"● Tổng Thống Kê Zproject Bot.\n\n"
        f"<b>Tổng số lượt tương tác:</b> <code>{interaction_count}</code>\n"
        f"<i>Lưu ý: Số Lượt Tương Tác Càng Cao Chứng Tỏ Độ Uy Tín Của Bot 🎉.</i>"
    )
    
    send_message_robustly(
        chat_id=message.chat.id,
        photo=TUONGTAC_IMAGE_URL,
        caption=reply_text,
        parse_mode="HTML",
        reply_to_message_id=message.message_id
    )

@bot.message_handler(commands=["noti"])
@increment_interaction_count
def send_noti(message):
    """Xử lý lệnh /noti, cho phép Admin gửi thông báo kèm ảnh (tùy chọn) tới tất cả người dùng/nhóm."""
    if message.from_user.id != ADMIN_ID:
        return send_message_robustly(message.chat.id, text="🚫 Bạn không có quyền sử dụng lệnh này.", parse_mode="HTML", reply_to_message_id=message.message_id)

    text = message.text.replace("/noti", "").strip()

    photo_file_id = None
    if message.reply_to_message and message.reply_to_message.photo:
        photo_file_id = message.reply_to_message.photo[-1].file_id

    if not text and not photo_file_id:
        return send_message_robustly(message.chat.id, text="⚠️ Sử dụng: <code>/noti &lt;nội dung&gt;</code> hoặc reply vào ảnh và dùng <code>/noti &lt;nội dung&gt;</code>.", parse_mode="HTML", reply_to_message_id=message.message_id)

    notify_caption = f"<b>[!] THÔNG BÁO TỪ ADMIN DEPZAI CUTO</b>\n\n{text}" if text else "<b>[!] THÔNG BÁO</b>"

    ok, fail = 0, 0
    failed_ids = []

    all_recipients = USER_IDS.union({g["id"] for g in GROUP_INFOS})

    for uid in all_recipients:
        try:
            if photo_file_id:
                bot.send_photo(
                    chat_id=uid,
                    photo=photo_file_id,
                    caption=notify_caption,
                    parse_mode="HTML"
                )
            else:
                bot.send_message(
                    chat_id=uid,
                    text=notify_caption,
                    parse_mode="HTML",
                    disable_web_page_preview=True
                )
            ok += 1
            time.sleep(0.1)
        except Exception as e:
            fail += 1
            failed_ids.append(uid)
            logging.error(f"Failed to send notification to {uid}: {e}")

    send_message_robustly(
        message.chat.id,
        text=f"✅ Gửi thành công: {ok} tin nhắn.\n❌ Gửi thất bại: {fail} tin nhắn.\n"
             f"Danh sách ID thất bại: <code>{failed_ids}</code>",
        parse_mode="HTML",
        reply_to_message_id=message.message_id
    )

@bot.message_handler(commands=["ngl"])
@increment_interaction_count
def spam_ngl_command(message):
    """Xử lý lệnh /ngl để gửi tin nhắn ẩn danh tới NGL.
       Khi lỗi, sẽ bỏ qua lệnh này cho người dùng hiện tại và đợi lệnh mới."""
    sync_chat_to_server(message.chat)

    args = message.text.split(maxsplit=3)

    if len(args) < 4:
        return send_message_robustly(message.chat.id, text="⚠️ Sử dụng: <code>/ngl &lt;username&gt; &lt;tin_nhan&gt; &lt;số_lần&gt;</code>", parse_mode="HTML", reply_to_message_id=message.message_id)

    username = args[1]
    tinnhan = args[2]
    solan_str = args[3]

    try:
        solan = int(solan_str)
        if not (1 <= solan <= 50):
            return send_message_robustly(message.chat.id, text="❗ Số lần phải từ 1 đến 50.", parse_mode="HTML", reply_to_message_id=message.message_id)
    except ValueError:
        return send_message_robustly(message.chat.id, text="❗ Số lần phải là một số hợp lệ, không phải ký tự.", parse_mode="HTML", reply_to_message_id=message.message_id)

    ngl_api_url = f"https://zeusvr.x10.mx/ngl?api-key=dcbfree&username={username}&tinnhan={tinnhan}&solan={solan}"

    try:
        response = session.get(ngl_api_url) 
        response.raise_for_status()
        data = response.json()

        if data.get("status") == "success":
            total_sent = data["data"].get("total_sent", 0)
            failed_count = data["data"].get("failed", 0)

            reply_text = (
                f"<blockquote><b>✅ Đã Attack NGL Thành Công!</b></blockquote>\n\n"
                f"<b>👤 Username:</b> <code>{username}</code>\n"
                f"<b>💬 Tin nhắn:</b> <code>{tinnhan}</code>\n"
                f"<b>🔢 Số lần gửi:</b> <code>{total_sent}</code>\n"
                f"<b>❌ Thất bại:</b> <code>{failed_count}</code>"
            )

            send_message_robustly(
                chat_id=message.chat.id,
                photo=NGL_SUCCESS_IMAGE_URL,
                caption=reply_text,
                parse_mode="HTML",
                reply_to_message_id=message.message_id
            )
        else:
            error_message = data.get("message", "Có lỗi xảy ra khi gọi API NGL.")
            send_message_robustly(message.chat.id, text=f"❌ Lỗi NGL API: {error_message}", parse_mode="HTML", reply_to_message_id=message.message_id)

    except requests.exceptions.ReadTimeout as e:
        logging.error(f"Lỗi timeout khi gọi NGL API cho người dùng {message.from_user.id}: {e}")
        send_message_robustly(message.chat.id, text="❌ Lỗi: API NGL không phản hồi kịp thời. Vui lòng thử lại sau.", parse_mode="HTML", reply_to_message_id=message.message_id)
    except requests.exceptions.ConnectionError as e:
        logging.error(f"Lỗi kết nối khi gọi NGL API cho người dùng {message.from_user.id}: {e}")
        send_message_robustly(message.chat.id, text=f"❌ Lỗi kết nối đến NGL API: Không thể kết nối đến máy chủ. Vui lòng kiểm tra lại sau.", parse_mode="HTML", reply_to_message_id=message.message_id)
    except requests.exceptions.RequestException as e:
        logging.error(f"Lỗi HTTP (4xx/5xx) hoặc request khác khi gọi NGL API cho người dùng {message.from_user.id}: {e}")
        send_message_robustly(message.chat.id, text=f"❌ Lỗi khi gọi NGL API: Đã có lỗi xảy ra từ máy chủ NGL. Chi tiết: <code>{e}</code>", parse_mode="HTML", reply_to_message_id=message.message_id)
    except ValueError as e:
        logging.error(f"Lỗi phân tích JSON từ NGL API cho người dùng {message.from_user.id}: {e}")
        send_message_robustly(message.chat.id, text="❌ Lỗi: Phản hồi API NGL không hợp lệ.", parse_mode="HTML", reply_to_message_id=message.message_id)
    except Exception as e:
        logging.error(f"Lỗi không xác định khi xử lý /ngl cho người dùng {message.from_user.id}: {e}")
        send_message_robustly(message.chat.id, text=f"❌ Đã xảy ra lỗi không mong muốn khi xử lý lệnh spam NGL: <code>{e}</code>", parse_mode="HTML", reply_to_message_id=message.message_id)

@bot.message_handler(commands=["phanhoi"])
@increment_interaction_count
def send_feedback_to_admin(message):
    """Xử lý lệnh /phanhoi, cho phép người dùng gửi phản hồi đến admin."""
    sync_chat_to_server(message.chat)
    feedback_text = message.text.replace("/phanhoi", "").strip()

    if not feedback_text:
        return send_message_robustly(message.chat.id, text="⚠️ Vui lòng nhập nội dung phản hồi. Ví dụ: <code>/phanhoi Bot bị lỗi ở lệnh /ask</code>", parse_mode="HTML", reply_to_message_id=message.message_id)

    user_info_for_admin = f"<a href='tg://user?id={message.from_user.id}'>{message.from_user.first_name}</a>"
    if message.from_user.last_name:
        user_info_for_admin += f" {message.from_user.last_name}"
    if message.from_user.username:
        user_info_for_admin += f" (@{message.from_user.username})"
    user_info_for_admin += f" (<code>{message.from_user.id}</code>)"

    chat_info_for_admin = f"ID Chat: <code>{message.chat.id}</code>\n" \
                          f"Loại Chat: {message.chat.type}"
    if message.chat.type in ["group", "supergroup"]:
        chat_info_for_admin += f"\nTên Chat: {message.chat.title}"

    timestamp = datetime.now().strftime("%H:%M:%S ngày %d/%m/%Y")

    admin_notification = (
        f"<b>📧 PHẢN HỒI MỚI TỪ NGƯỜI DÙNG</b>\n\n"
        f"<b>Người gửi:</b>\n{user_info_for_admin}\n"
        f"<b>Thông tin Chat:</b>\n{chat_info_for_admin}\n"
        f"<b>Thời gian:</b> <code>{timestamp}</code>\n\n"
        f"<b>Nội dung phản hồi:</b>\n<blockquote>{html_escape(feedback_text)}</blockquote>\n\n"
        f"<i>Để phản hồi lại người dùng này, hãy reply tin nhắn này và dùng lệnh <code>/adminph &lt;nội dung phản hồi&gt;</code></i>"
    )

    try:
        sent_message_to_admin = bot.send_message(
            chat_id=ADMIN_ID,
            text=admin_notification,
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        bot.feedback_messages[sent_message_to_admin.message_id] = {
            'chat_id': message.chat.id,
            'user_id': message.from_user.id,
            'user_first_name': message.from_user.first_name,
            'feedback_text': feedback_text
        }
        
        send_message_robustly(message.chat.id, text="✅ Cảm ơn bạn đã gửi phản hồi! Admin sẽ xem xét sớm nhất có thể.", parse_mode="HTML", reply_to_message_id=message.message_id)
    except Exception as e:
        logging.error(f"Lỗi khi gửi phản hồi đến admin: {e}")
        send_message_robustly(message.chat.id, text="❌ Đã xảy ra lỗi khi gửi phản hồi. Vui lòng thử lại sau.", parse_mode="HTML", reply_to_message_id=message.message_id)

@bot.message_handler(commands=["adminph"])
@increment_interaction_count
def admin_reply_to_feedback(message):
    """Xử lý lệnh /adminph, cho phép admin phản hồi lại người dùng đã gửi feedback."""
    if message.from_user.id != ADMIN_ID:
        return send_message_robustly(message.chat.id, text="🚫 Bạn không có quyền sử dụng lệnh này.", parse_mode="HTML", reply_to_message_id=message.message_id)

    if not message.reply_to_message:
        return send_message_robustly(message.chat.id, text="⚠️ Bạn cần reply vào tin nhắn phản hồi của người dùng để sử dụng lệnh này.", parse_mode="HTML", reply_to_message_id=message.message_id)

    original_feedback_message_id = message.reply_to_message.message_id
    feedback_data = bot.feedback_messages.get(original_feedback_message_id)

    if not feedback_data:
        return send_message_robustly(message.chat.id, text="❌ Không tìm thấy thông tin chat của người dùng này. Có thể tin nhắn quá cũ hoặc bot đã khởi động lại.", parse_mode="HTML", reply_to_message_id=message.message_id)

    user_chat_id = feedback_data['chat_id']
    user_id_to_tag = feedback_data['user_id']
    user_name_to_tag = feedback_data['user_first_name']
    original_feedback_text = feedback_data['feedback_text']

    admin_response_text = message.text.replace("/adminph", "").strip()

    if not admin_response_text:
        return send_message_robustly(message.chat.id, text="⚠️ Vui lòng nhập nội dung phản hồi của admin. Ví dụ: <code>/adminph Cảm ơn bạn, chúng tôi đã khắc phục lỗi.</code>", parse_mode="HTML", reply_to_message_id=message.message_id)

    user_tag = f"<a href='tg://user?id={user_id_to_tag}'>{user_name_to_tag}</a>"

    admin_reply_to_user = (
        f"<b>👨‍💻 Admin đã phản hồi bạn {user_tag}!</b>\n\n"
        f"<b>Nội dung phản hồi của bạn:</b>\n"
        f"<blockquote>{html_escape(original_feedback_text)}</blockquote>\n\n"
        f"<b>Phản hồi từ Admin:</b>\n"
        f"<blockquote>{html_escape(admin_response_text)}</blockquote>\n\n"
        f"<i>Nếu bạn có thêm câu hỏi, vui lòng gửi phản hồi mới qua lệnh <code>/phanhoi</code>.</i>"
    )

    try:
        bot.send_message(
            chat_id=user_chat_id,
            text=admin_reply_to_user,
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        send_message_robustly(message.chat.id, text="✅ Đã gửi phản hồi của Admin đến người dùng thành công.", parse_mode="HTML", reply_to_message_id=message.message_id)
    except Exception as e:
        logging.error(f"Lỗi khi gửi phản hồi của admin đến người dùng {user_chat_id}: {e}")
        send_message_robustly(message.chat.id, text="❌ Đã xảy ra lỗi khi gửi phản hồi của Admin đến người dùng.", parse_mode="HTML", reply_to_message_id=message.message_id)

@bot.message_handler(commands=["sever"])
@increment_interaction_count
def show_groups(message):
    """Xử lý lệnh /sever, hiển thị danh sách các nhóm bot đang tham gia (chỉ Admin)."""
    if message.from_user.id != ADMIN_ID:
        return send_message_robustly(message.chat.id, text="🚫 Bạn không có quyền sử dụng lệnh này.", parse_mode="HTML", reply_to_message_id=message.message_id)
    if not GROUP_INFOS:
        return send_message_robustly(message.chat.id, text="📭 Hiện tại bot chưa có thông tin về nhóm nào.", parse_mode="HTML", reply_to_message_id=message.message_id)
    text = "<b>📦 Sever:</b>\n\n"
    for g in GROUP_INFOS:
        title = g.get("title", "Không rõ tên nhóm")
        link = f"https://t.me/{g.get('username')}" if g.get("username") else "⛔ Không có link mời"
        text += f"📌 <b>{title}</b>\n{link}\n\n"
    send_message_robustly(message.chat.id, text=text, parse_mode="HTML", disable_web_page_preview=True, reply_to_message_id=message.message_id)


# Lệnh tạo mail 10 phút
@bot.message_handler(commands=['mail10p'])
@increment_interaction_count
def handle_mail10p(message):
    sync_chat_to_server(message.chat)
    user_id = message.chat.id
    
    # Kiểm tra xem người dùng đã có mail chưa và còn thời gian không
    if user_id in user_data:
        elapsed_time = int(time.time() - user_data[user_id]["created_at"])
        remaining_time = 600 - elapsed_time
        if remaining_time > 0:
            minutes = remaining_time // 60
            seconds = remaining_time % 60
            
            # Gửi lại thông tin mail kèm nút "Xem Hộp Thư"
            mail_info_text = (
                f"⚠️ Bạn đã có một mail 10 phút rồi:\n"
                f"📧 `{user_data[user_id]['email']}`\n"
                f"⏰ Mail này sẽ hết hạn sau {minutes} phút {seconds} giây."
            )
            markup = build_mail_buttons(user_id, 'mail_info')
            
            sent_msg = send_message_robustly(message.chat.id, 
                                            text=mail_info_text,
                                            parse_mode='Markdown',
                                            reply_markup=markup,
                                            reply_to_message_id=message.message_id)
            if sent_msg:
                bot.mail_messages_state[sent_msg.message_id] = {'chat_id': user_id, 'user_id': user_id, 'type': 'mail_info'}
            return
        else:
            # Nếu hết hạn nhưng chưa bị xóa, xóa nó đi
            del user_data[user_id]
            send_message_robustly(message.chat.id, "⏰ Mail 10 phút của bạn đã hết hạn, đang tạo mail mới...", parse_mode='Markdown', reply_to_message_id=message.message_id)


    email, pwd, token = create_temp_mail()

    if email:
        user_data[user_id] = {
            "email": email,
            "password": pwd,
            "token": token,
            "created_at": time.time()
        }
        
        mail_info_text = (
            f"✅ Mail 10 phút của bạn là:\n"
            f"📧 `{email}`\n"
            f"⏰ Hết hạn sau 10 phút."
        )
        markup = build_mail_buttons(user_id, 'mail_info')
        
        sent_msg = send_message_robustly(message.chat.id, 
                                       text=mail_info_text, 
                                       parse_mode='Markdown',
                                       reply_markup=markup,
                                       reply_to_message_id=message.message_id)
        # Lưu trữ ID tin nhắn để có thể chỉnh sửa sau này
        if sent_msg:
            bot.mail_messages_state[sent_msg.message_id] = {'chat_id': user_id, 'user_id': user_id, 'type': 'mail_info'}
        
        threading.Thread(target=auto_delete_email, args=(user_id,)).start()
    else:
        send_message_robustly(message.chat.id, "❌ Không thể tạo email. Vui lòng thử lại sau!", parse_mode='Markdown', reply_to_message_id=message.message_id)

# Lệnh mới để xóa mail 10 phút
@bot.message_handler(commands=['xoamail10p'])
@increment_interaction_count
def handle_xoamail10p(message):
    sync_chat_to_server(message.chat)
    user_id = message.chat.id

    if user_id in user_data:
        # Xóa tài khoản Mail.tm nếu có thể (thêm logic gọi API Mail.tm nếu có account_id)
        # Ví dụ:
        # try:
        #     account_info = user_data[user_id]
        #     if 'account_id' in account_info and 'token' in account_info:
        #         headers = {"Authorization": f"Bearer {account_info['token']}"}
        #         session.delete(f"https://api.mail.tm/accounts/{account_info['account_id']}", headers=headers)
        #         logging.info(f"Đã xóa tài khoản Mail.tm: {account_info['email']}")
        # except Exception as e:
        #     logging.error(f"Lỗi khi xóa tài khoản Mail.tm cho user {user_id}: {e}")

        del user_data[user_id]
        send_message_robustly(message.chat.id, "<i>🗑️ Mail 10 phút của bạn đã được xóa thành công!</i>", parse_mode='HTML', reply_to_message_id=message.message_id)
    else:
        send_message_robustly(message.chat.id, "<i>⚠️ Bạn không có mail 10 phút nào đang hoạt động để xóa.<i>", parse_mode='HTML', reply_to_message_id=message.message_id)


# Hàm nội bộ để lấy nội dung hộp thư và tạo markup
def _get_inbox_content(user_id):
    info = user_data.get(user_id)

    if not info:
        return "<i>❌ Bạn chưa tạo email. Gõ /mail10p để tạo nhé!</i>", None, 'HTML'

    # Kiểm tra xem mail đã hết hạn chưa
    elapsed_time = int(time.time() - info["created_at"])
    if elapsed_time >= 600: # 10 phút
        # Lấy thông tin email trước khi xóa
        expired_mail_address = info.get('address', 'không xác định')
        
        del user_data[user_id]
        # Thông báo mail hết hạn với địa chỉ mail cụ thể và thông tin về thư
        # Sử dụng parser_mode HTML và tag người dùng (giả định cách tag với ID)
        reply_text = (
            f"⏰ <b>Mail <code>{expired_mail_address}</code> của bạn đã hết hạn!</b> "
            f"<blockquote>Tất cả thư của mail này sẽ bị xóa.</blockquote> "
            f"Vui lòng tạo mail mới bằng lệnh /mail10p."
        )
        # Nếu bạn muốn tag người dùng cụ thể, bạn cần có username hoặc full name của họ.
        # Ví dụ: f"<a href='tg://user?id={user_id}'>Người dùng của bạn</a>"
        return reply_text, None, 'HTML'

    headers = {
        "Authorization": f"Bearer {info['token']}"
    }

    try:
        r = session.get("https://api.mail.tm/messages", headers=headers)
        r.raise_for_status() # Kiểm tra lỗi HTTP
        messages = r.json().get("hydra:member", [])
        
        reply_text = ""
        if not messages:
            reply_text = "📭 Hộp thư của bạn hiện đang trống."
        else:
            reply_text = f"📥 Có {len(messages)} thư trong hộp thư:\n"
            for msg in messages:
                sender = msg['from']['address']
                subject = msg['subject']
                preview = msg['intro']
                
                sender_esc = html_escape(sender)
                subject_esc = html_escape(subject)
                preview_esc = html_escape(preview)

                reply_text += f"\n👤 <b>Từ:</b> <code>{sender_esc}</code>\n" \
                              f"✉️ <b>Chủ đề:</b> {subject_esc}\n" \
                              f"📝 <b>Nội dung:</b> {preview_esc}\n"
        
        markup = build_mail_buttons(user_id, 'inbox')
        return reply_text, markup, 'HTML'

    except Exception as e:
        logging.error(f"Lỗi khi kiểm tra hộp thư Mail.tm cho user {user_id}: {e}")
        return "❌ Lỗi khi kiểm tra hộp thư. Vui lòng thử lại sau.", None, 'Markdown'


# Lệnh kiểm tra hộp thư (vẫn giữ để dùng lệnh /hopthu)
@bot.message_handler(commands=['hopthu'])
@increment_interaction_count
def handle_hopthu(message):
    sync_chat_to_server(message.chat)
    user_id = message.chat.id
    
    text, markup, parse_mode = _get_inbox_content(user_id)
    sent_msg = send_message_robustly(message.chat.id, 
                                   text=text, 
                                   parse_mode=parse_mode, 
                                   reply_markup=markup,
                                   reply_to_message_id=message.message_id)
    if sent_msg:
        # Nếu gửi tin nhắn mới, lưu trạng thái là inbox
        bot.mail_messages_state[sent_msg.message_id] = {'chat_id': user_id, 'user_id': user_id, 'type': 'inbox'}


# Hàm mới để định dạng đầu ra AI
def format_ai_response_html(text):
    """
    Phân tích văn bản từ AI, tách code block và văn bản thông thường,
    sau đó định dạng chúng với HTML cho Telegram, đặc biệt là thẻ <code>.
    Trả về danh sách các phần (text hoặc code) để xử lý.
    """
    parts = []
    # Regex để tìm kiếm các block code Markdown (```language\ncode\n```)
    code_blocks = re.split(r"```(?:\w+)?\n(.*?)```", text, flags=re.DOTALL)

    for i, part in enumerate(code_blocks):
        if i % 2 == 0:  # Phần văn bản (hoặc phần trước code đầu tiên, hoặc sau code cuối cùng)
            if part:
                parts.append({"type": "text", "content": html_escape(part.strip()), "raw_content": part.strip()})
        else:  # Phần code (là nội dung của group 1 từ regex)
            if part:
                formatted_code = f"<code>{html_escape(part.strip())}</code>"
                parts.append({"type": "code", "content": formatted_code, "raw_content": part.strip()})
    return parts


@bot.callback_query_handler(func=lambda call: call.data.startswith("copycode|"))
def copy_code_button(call):
    """Xử lý nút 'Copy Code'."""
    try:
        _, code_id = call.data.split("|", 1)
        code_content = bot.code_snippets.get(code_id)

        if code_content:
            bot.answer_callback_query(call.id, text="Đã sao chép nội dung code!", show_alert=True)
            try:
                bot.send_message(
                    chat_id=call.message.chat.id,
                    text=f"```\n{code_content}\n```",
                    parse_mode="MarkdownV2",
                    reply_to_message_id=call.message.message_id
                )
            except telebot.apihelper.ApiTelegramException as e:
                logging.warning(f"Failed to send code snippet for copy to chat {call.message.chat.id}: {e}. Sending plain text.")
                bot.send_message(
                    chat_id=call.message.chat.id,
                    text=f"Bạn có thể sao chép đoạn code này:\n\n{code_content}",
                    reply_to_message_id=call.message.message_id
                )
        else:
            bot.answer_callback_query(call.id, text="Lỗi: Không tìm thấy nội dung code này.", show_alert=True)
    except Exception as e:
        logging.error(f"Lỗi khi xử lý nút copy code: {e}")
        bot.answer_callback_query(call.id, text="Đã xảy ra lỗi khi sao chép code.", show_alert=True)


@bot.message_handler(commands=["ask"])
@increment_interaction_count
def ask_command(message):
    """Xử lý lệnh /ask để gửi câu hỏi đến Gemini AI. Hỗ trợ hỏi kèm ảnh."""
    sync_chat_to_server(message.chat)
    prompt = message.text.replace("/ask", "").strip()
    if not prompt:
        return send_message_robustly(message.chat.id, text="❓ Bạn chưa nhập câu hỏi rồi đó! Vui lòng gõ <code>/ask &lt;câu hỏi của bạn&gt;</code>.", parse_mode="HTML", reply_to_message_id=message.message_id)

    try:
        msg_status = bot.send_message(message.chat.id, "🤖", reply_to_message_id=message.message_id)
    except telebot.apihelper.ApiTelegramException as e:
        logging.warning(f"Failed to send initial 'thinking' message in chat {message.chat.id}: {e}. Proceeding without reply_to.")
        msg_status = bot.send_message(message.chat.id, "🤖")

    user_id = message.from_user.id
    user_name = message.from_user.first_name
    memory = load_user_memory(user_id)

    try:
        prompt_data = session.get(REMOTE_PROMPT_URL, timeout=DEFAULT_TIMEOUT_GLOBAL).json()
        system_prompt = prompt_data.get("prompt", "Bạn là AI thông minh và hữu ích.")
    except Exception as e:
        logging.error(f"Lỗi tải prompt từ xa: {e}")
        system_prompt = "Bạn là AI thông minh và hữu ích."

    history_block = ""
    if memory:
        for item in memory[-5:]:
            history_block += f"Người dùng hỏi: {item['question']}\nAI: {item['answer']}\n"

    full_prompt = f"{system_prompt}\n\n[Ngữ cảnh trước đó với {user_name}]\n{history_block}\nNgười dùng hiện tại hỏi: {prompt}"

    headers = {"Content-Type": "application/json"}
    parts = [{"text": full_prompt}]
    image_attached = False

    if message.reply_to_message and message.reply_to_message.photo:
        try:
            photo = message.reply_to_message.photo[-1]
            file_info = bot.get_file(photo.file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            image = Image.open(BytesIO(downloaded_file))
            buffer = BytesIO()
            if image.mode in ("RGBA", "P"):
                image = image.convert("RGB")
            image.save(buffer, format="JPEG")
            base64_img = base64.b64encode(buffer.getvalue()).decode()
            parts.insert(0, {
                "inline_data": {
                    "mime_type": "image/jpeg",
                    "data": base64_img
                }
            })
            image_attached = True
        except Exception as e:
            logging.error(f"Lỗi xử lý ảnh đính kèm: {e}")

    data = {"contents": [{"parts": parts}]}
    try:
        res = session.post(GEMINI_URL, headers=headers, json=data, timeout=DEFAULT_TIMEOUT_GLOBAL)
        res.raise_for_status()
        result = res.json()["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        try:
            bot.edit_message_text(
                f"❌ Đã xảy ra lỗi khi gọi API Gemini:\n<pre>{html_escape(str(e))}</pre>",
                msg_status.chat.id,
                msg_status.message_id,
                parse_mode="HTML"
            )
        except telebot.apihelper.ApiTelegramException as edit_e:
            logging.warning(f"Failed to edit message {msg_status.message_id}: {edit_e}. Sending new error message.")
            send_message_robustly(message.chat.id, text=f"❌ Đã xảy ra lỗi khi gọi API Gemini:\n<pre>{html_escape(str(e))}</pre>", parse_mode="HTML")
        return

    entry = {
        "question": prompt,
        "answer": result,
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "with_image": image_attached,
        "name": message.from_user.first_name
    }
    memory.append(entry)
    save_user_memory(user_id, memory)

    try:
        session.post(
            f"{REMOTE_LOG_HOST}?uid={user_id}",
            data=json.dumps(memory, ensure_ascii=False),
            headers={"Content-Type": "application/json"},
            timeout=DEFAULT_TIMEOUT_GLOBAL
        )
    except Exception as e:
        logging.error(f"Lỗi gửi log từ xa: {e}")

    # --- Xử lý định dạng và gửi tin nhắn ---
    response_parts_structured = format_ai_response_html(result)
    reply_id = uuid.uuid4().hex[:6]
    bot.voice_map[reply_id] = result # Lưu toàn bộ kết quả gốc cho TTS

    # Tính toán tổng độ dài của nội dung (thô) để quyết định gửi file hay gửi tin nhắn
    total_raw_length = 0
    full_content_for_file = []
    for part in response_parts_structured:
        total_raw_length += len(part["raw_content"])
        if part["type"] == "text":
            full_content_for_file.append(part["raw_content"])
        elif part["type"] == "code":
            full_content_for_file.append(f"\n```\n{part['raw_content']}\n```\n")

    # Telegram có giới hạn 4096 ký tự cho tin nhắn và 1024 cho caption ảnh/document.
    # Sử dụng ngưỡng an toàn thấp hơn để quyết định gửi file.
    # Nếu có nhiều code block hoặc văn bản rất dài, gửi file sẽ tốt hơn.
    if total_raw_length > 1500 or any(p["type"] == "code" for p in response_parts_structured):
        filename = f"zproject_{reply_id}.txt"
        with open(filename, "w", encoding="utf-8") as f:
            f.write("".join(full_content_for_file)) # Viết toàn bộ nội dung đã gom lại

        with open(filename, "rb") as f:
            try:
                bot.send_document(
                    message.chat.id,
                    f,
                    caption=f"📄 Trả lời quá dài hoặc có code block! Mình đã đóng gói vào file <code>{filename}</code> nha {html_escape(message.from_user.first_name)}!\n\n"
                            f"<i>Vui lòng tải xuống để xem toàn bộ nội dung.</i>",
                    parse_mode="HTML",
                    reply_to_message_id=message.message_id
                )
            except telebot.apihelper.ApiTelegramException as e:
                logging.warning(f"Failed to send document replying to message {message.message_id}: {e}. Sending without reply_to.")
                f.seek(0)
                bot.send_document(
                    message.chat.id,
                    f,
                    caption=f"📄 Trả lời quá dài hoặc có code block! Mình đã đóng gói vào file <code>{filename}</code> nha {html_escape(message.from_user.first_name)}!\n\n"
                            f"<i>Vui lòng tải xuống để xem toàn bộ nội dung.</i>",
                    parse_mode="HTML"
                )
        os.remove(filename)
        # Xóa tin nhắn "đang xử lý" ban đầu
        try:
            bot.delete_message(msg_status.chat.id, msg_status.message_id)
        except telebot.apihelper.ApiTelegramException as e:
            logging.warning(f"Failed to delete status message {msg_status.message_id}: {e}")

    else: # Gửi tin nhắn thông thường nếu không quá dài hoặc không có code block riêng
        main_markup = build_reply_button(user_id, prompt, reply_id)
        current_message_text = f"🤖 <i>ZProject [WORMGPT] trả lời:</i>\n\n"
        
        # Nếu có code block, chúng ta sẽ gửi kèm nút copy riêng lẻ
        # Nếu không có code block, nút copy sẽ không được tạo
        
        # Để đơn giản hóa, nếu không gửi file, ta sẽ gom tất cả thành 1 tin nhắn HTML
        # Các nút copy code sẽ được xử lý riêng trong callback_query_handler
        
        combined_text_for_telegram = ""
        for part in response_parts_structured:
            if part["type"] == "text":
                combined_text_for_telegram += part["content"] + "\n\n" # Thêm xuống dòng giữa các đoạn văn bản
            elif part["type"] == "code":
                # Thêm nút copy code vào markup chính cho phần code đó
                copy_id = uuid.uuid4().hex[:8]
                bot.code_snippets[copy_id] = part["raw_content"]
                
                # InlineKeyboardMarkup mới cho mỗi code block
                code_markup = InlineKeyboardMarkup()
                code_markup.add(InlineKeyboardButton("📄 Sao chép Code", callback_data=f"copycode|{copy_id}"))

                # Gửi phần code block riêng với nút copy của nó
                try:
                    # Gửi text trước nếu có, rồi gửi code sau
                    if combined_text_for_telegram.strip():
                        bot.edit_message_text( # Cố gắng edit tin nhắn status nếu chưa bị thay thế
                            current_message_text + combined_text_for_telegram.strip(),
                            msg_status.chat.id,
                            msg_status.message_id,
                            parse_mode="HTML"
                        )
                        msg_status = None # Đã sử dụng tin nhắn status
                    
                    bot.send_message(
                        message.chat.id,
                        text=f"<b>Code:</b>\n{part['content']}", # Đã là HTML escaped
                        parse_mode="HTML",
                        reply_markup=code_markup,
                        reply_to_message_id=message.message_id # Reply về tin nhắn gốc
                    )
                except telebot.apihelper.ApiTelegramException as e:
                    logging.warning(f"Failed to send code part in chat {message.chat.id}: {e}. Sending without reply_to.")
                    bot.send_message(
                        message.chat.id,
                        text=f"<b>Code:</b>\n{part['content']}",
                        parse_mode="HTML",
                        reply_markup=code_markup
                    )
                combined_text_for_telegram = "" # Reset sau khi gửi code
        
        # Gửi phần văn bản cuối cùng (nếu có) và các nút chung
        final_response_text = current_message_text + combined_text_for_telegram.strip()
        
        try:
            if msg_status: # Nếu tin nhắn status ban đầu vẫn còn
                bot.edit_message_text(
                    final_response_text,
                    msg_status.chat.id,
                    msg_status.message_id,
                    parse_mode="HTML",
                    reply_markup=main_markup
                )
            else: # Nếu tin nhắn status đã được sử dụng (ví dụ để gửi phần text trước code)
                bot.send_message(
                    message.chat.id,
                    text=final_response_text,
                    parse_mode="HTML",
                    reply_markup=main_markup,
                    reply_to_message_id=message.message_id # Reply về tin nhắn gốc
                )
        except telebot.apihelper.ApiTelegramException as e:
            logging.warning(f"Failed to send/edit final message in chat {message.chat.id}: {e}. Sending as new message.")
            send_message_robustly(
                message.chat.id,
                text=final_response_text,
                parse_mode="HTML",
                reply_markup=main_markup,
                reply_to_message_id=message.message_id
            )
        except Exception as e:
            logging.error(f"Error in final message sending for /ask: {e}")
            send_message_robustly(message.chat.id, text=f"❌ Đã xảy ra lỗi khi gửi kết quả: {e}", parse_mode="HTML", reply_to_message_id=message.message_id)


# --- NÚT CALLBACK CỦA BOT ZPROJECT ---

@bot.callback_query_handler(func=lambda call: call.data.startswith("retry|"))
def retry_button(call):
    """Xử lý nút 'Trả lời lại' từ câu hỏi /ask."""
    try:
        _, uid, question = call.data.split("|", 2)
        if str(call.from_user.id) != uid:
            return bot.answer_callback_query(call.id, "🚫 Bạn không phải người yêu cầu câu hỏi này.", show_alert=True)

        # Tạo một đối tượng message giả lập để truyền vào ask_command
        msg = SimpleNamespace(
            chat=call.message.chat,
            message_id=call.message.message_id,
            text="/ask " + question,
            from_user=call.from_user,
            reply_to_message=None # Giả định không có reply_to_message khi retry
        )

        bot.answer_callback_query(call.id, "🔁 Đang thử lại câu hỏi...")
        try:
            bot.edit_message_text("🤖 Đang xử lý lại...", call.message.chat.id, call.message.message_id)
        except telebot.apihelper.ApiTelegramException as e:
            logging.warning(f"Failed to edit message {call.message.message_id} on retry: {e}. Sending new 'thinking' message.")
            bot.send_message(call.message.chat.id, "🤖 Đang xử lý lại...", reply_to_message_id=call.message.message_id)

        ask_command(msg)
    except Exception as e:
        bot.answer_callback_query(call.id, "⚠️ Lỗi khi thử lại!", show_alert=True)
        logging.error(f"[RETRY] Lỗi: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith("tts|"))
def tts_button(call):
    """Xử lý nút 'Chuyển sang Voice' từ câu trả lời /ask."""
    try:
        parts = call.data.split("|")
        uid = parts[1]
        reply_id = parts[2]

        if str(call.from_user.id) != uid:
            return bot.answer_callback_query(call.id, "🚫 Bạn không phải người yêu cầu voice này.", show_alert=True)

        answer = bot.voice_map.get(reply_id)
        if not answer:
            return bot.answer_callback_query(call.id, "❌ Không tìm thấy dữ liệu giọng nói.", show_alert=True)

        # Xóa các định dạng HTML và Markdown để gTTS chỉ nhận văn bản thuần
        clean_text = re.sub(r"<code>.*?</code>", "", answer, flags=re.DOTALL)
        clean_text = re.sub(r"<[^>]+>", "", clean_text)
        clean_text = re.sub(r"```.*?```", "", clean_text, flags=re.DOTALL)
        clean_text = clean_text.replace('"', '').replace("'", '')

        text_to_speak = clean_text.strip()

        if not text_to_speak or len(text_to_speak) < 5:
            return bot.answer_callback_query(call.id, "❗ Nội dung quá ngắn hoặc rỗng để chuyển voice.", show_alert=True)

        filename = f"zproject_tts_{reply_id}.mp3"
        tts = gTTS(text=text_to_speak, lang="vi", slow=False)
        tts.save(filename)

        with open(filename, "rb") as f:
            try:
                bot.send_voice(call.message.chat.id, f, caption="🗣️ Đây là Voice ZProject:v", reply_to_message_id=call.message.message_id)
            except telebot.apihelper.ApiTelegramException as e:
                logging.warning(f"Failed to send voice replying to message {call.message.message_id}: {e}. Sending without reply_to.")
                f.seek(0)
                bot.send_voice(call.message.chat.id, f, caption="🗣️ Đây là Voice ZProject:v")
        os.remove(filename)
        bot.answer_callback_query(call.id, "🎧 Voice đã được gửi!")
    except Exception as e:
        bot.answer_callback_query(call.id, "⚠️ Lỗi khi tạo voice.", show_alert=True)
        logging.error(f"[TTS] Lỗi: {e}")

# --- NÚT CALLBACK CỦA MAIL.TM ---

def check_mail_owner(call, expected_user_id):
    """Kiểm tra xem người nhấn nút có phải là người đã tạo mail không."""
    # Chuyển expected_user_id sang int để so sánh chính xác
    if call.from_user.id != int(expected_user_id):
        bot.answer_callback_query(call.id, "🚫 Chat Riêng Với Bot Để Dùng Chức Năng Mail10p .", show_alert=True)
        return False
    return True

@bot.callback_query_handler(func=lambda call: call.data.startswith("mailtm_inbox|"))
def show_inbox_button(call):
    user_id = call.message.chat.id
    expected_user_id = call.data.split("|")[1]

    if not check_mail_owner(call, expected_user_id):
        return

    bot.answer_callback_query(call.id, "Đang tải hộp thư...", show_alert=False)

    text, markup, parse_mode = _get_inbox_content(user_id)

    try:
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=markup
        )
        # Cập nhật trạng thái tin nhắn
        bot.mail_messages_state[call.message.message_id]['type'] = 'inbox'
    except telebot.apihelper.ApiTelegramException as e:
        if "message is not modified" in str(e):
            logging.info(f"Message {call.message.message_id} in chat {call.message.chat.id} was not modified (inbox).")
        else:
            logging.error(f"Lỗi khi chỉnh sửa tin nhắn thành hộp thư cho user {user_id}: {e}")
            send_message_robustly(call.message.chat.id, text=text, parse_mode=parse_mode, reply_markup=markup)
            # Xóa trạng thái cũ và thêm trạng thái mới
            if call.message.message_id in bot.mail_messages_state:
                del bot.mail_messages_state[call.message.message_id]
            sent_msg = send_message_robustly(call.message.chat.id, "❌ Đã có lỗi khi cập nhật hộp thư. Đây là tin nhắn mới.", parse_mode="HTML")
            if sent_msg:
                bot.mail_messages_state[sent_msg.message_id] = {'chat_id': user_id, 'user_id': user_id, 'type': 'inbox'}
            
    except Exception as e:
        logging.error(f"Lỗi không xác định khi xem hộp thư: {e}")
        bot.answer_callback_query(call.id, "⚠️ Lỗi khi xem hộp thư!", show_alert=True)


@bot.callback_query_handler(func=lambda call: call.data.startswith("mailtm_refresh|"))
def refresh_inbox_button(call):
    user_id = call.message.chat.id
    expected_user_id = call.data.split("|")[1]

    if not check_mail_owner(call, expected_user_id):
        return

    bot.answer_callback_query(call.id, "Đang làm mới hộp thư...", show_alert=False)

    text, markup, parse_mode = _get_inbox_content(user_id)

    try:
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=markup
        )
        # Cập nhật trạng thái tin nhắn
        bot.mail_messages_state[call.message.message_id]['type'] = 'inbox'
    except telebot.apihelper.ApiTelegramException as e:
        if "message is not modified" in str(e):
            logging.info(f"Message {call.message.message_id} in chat {call.message.chat.id} was not modified (refresh inbox).")
        else:
            logging.error(f"Lỗi khi làm mới hộp thư cho user {user_id}: {e}")
            send_message_robustly(call.message.chat.id, text=text, parse_mode=parse_mode, reply_markup=markup)
            # Xóa trạng thái cũ và thêm trạng thái mới
            if call.message.message_id in bot.mail_messages_state:
                del bot.mail_messages_state[call.message.message_id]
            sent_msg = send_message_robustly(call.message.chat.id, "❌ Đã có lỗi khi làm mới hộp thư. Đây là tin nhắn mới.", parse_mode="HTML")
            if sent_msg:
                bot.mail_messages_state[sent_msg.message_id] = {'chat_id': user_id, 'user_id': user_id, 'type': 'inbox'}
    except Exception as e:
        logging.error(f"Lỗi không xác định khi làm mới hộp thư: {e}")
        bot.answer_callback_query(call.id, "⚠️ Lỗi khi làm mới hộp thư!", show_alert=True)


@bot.callback_query_handler(func=lambda call: call.data.startswith("mailtm_back|"))
def back_to_mail_info_button(call):
    user_id = call.message.chat.id
    expected_user_id = call.data.split("|")[1]

    if not check_mail_owner(call, expected_user_id):
        return
    
    bot.answer_callback_query(call.id, "Quay lại thông tin mail...", show_alert=False)

    info = user_data.get(user_id)

    if not info:
        text = "<i>❌ Bạn chưa tạo email. Gõ /mail10p để tạo nhé!</i>"
        markup = None
        parse_mode = 'HTML'
    else:
        elapsed_time = int(time.time() - info["created_at"])
        remaining_time = 600 - elapsed_time
        if remaining_time > 0:
            minutes = remaining_time // 60
            seconds = remaining_time % 60
            text = (
                f"<blockquote>✅ Mail 10 phút của bạn là:\n"
                f"<code>📧 {info['email']}</code>\n"
                f"⏰ Hết hạn sau {minutes} phút {seconds} giây.</blockquote>"
            )
            markup = build_mail_buttons(user_id, 'mail_info')
            parse_mode = 'HTML'
        else:
            del user_data[user_id]
            text = "⏰ Mail 10 phút của bạn đã hết hạn! Vui lòng tạo mail mới bằng lệnh /mail10p."
            markup = None
            parse_mode = 'HTML'
    
    try:
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=markup
        )
        # Cập nhật trạng thái tin nhắn
        bot.mail_messages_state[call.message.message_id]['type'] = 'mail_info'
    except telebot.apihelper.ApiTelegramException as e:
        if "message is not modified" in str(e):
            logging.info(f"Message {call.message.message_id} in chat {call.message.chat.id} was not modified (back to mail info).")
        else:
            logging.error(f"Lỗi khi chỉnh sửa tin nhắn về thông tin mail cho user {user_id}: {e}")
            send_message_robustly(call.message.chat.id, text=text, parse_mode=parse_mode, reply_markup=markup)
            # Xóa trạng thái cũ và thêm trạng thái mới
            if call.message.message_id in bot.mail_messages_state:
                del bot.mail_messages_state[call.message.message_id]
            sent_msg = send_message_robustly(call.message.chat.id, "❌ Đã có lỗi khi quay lại thông tin mail. Đây là tin nhắn mới.", parse_mode="HTML")
            if sent_msg:
                bot.mail_messages_state[sent_msg.message_id] = {'chat_id': user_id, 'user_id': user_id, 'type': 'mail_info'}
    except Exception as e:
        logging.error(f"Lỗi không xác định khi quay lại thông tin mail: {e}")
        bot.answer_callback_query(call.id, "⚠️ Lỗi khi quay lại thông tin mail!", show_alert=True)

# === Webhook Flask ===
@app.route("/")
def index():
    """Trang chủ đơn giản cho biết bot đang hoạt động."""
    return "<h3>🛰️ ZProject Bot đang hoạt động!</h3>"

@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    """Điểm cuối webhook để nhận cập nhật từ Telegram."""
    try:
        update = telebot.types.Update.de_json(request.data.decode("utf-8"))
        bot.process_new_updates([update])
        return "OK", 200
    except Exception as e:
        logging.error(f"Lỗi webhook: {e}")
        return "Error", 500

# === Khởi chạy Bot ===
if __name__ == "__main__":
    try:
        webhook_info = bot.get_webhook_info()
        current_webhook_url = f"{APP_URL}/{TOKEN}"
        if webhook_info.url != current_webhook_url:
            logging.info(f"Webhook hiện tại ({webhook_info.url}) không khớp với URL mong muốn ({current_webhook_url}). Đang xóa và đặt lại webhook.")
            bot.remove_webhook()
            time.sleep(1)
            bot.set_webhook(url=current_webhook_url)
            logging.info(f"Webhook đã được đặt tới: {current_webhook_url}")
        else:
            logging.info(f"Webhook đã được đặt chính xác tới: {current_webhook_url}")

        port = int(os.environ.get("PORT", 10000))
        app.run(host="0.0.0.0", port=port)
    except Exception as e:
        logging.critical(f"Lỗi nghiêm trọng khi khởi động bot: {e}")
