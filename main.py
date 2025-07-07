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
from PIL import Image

from flask import Flask, request
from threading import Thread
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from types import SimpleNamespace

# === Cấu hình logging ===
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)

# === Cấu hình chung ===
TOKEN = os.environ.get("BOT_TOKEN", "7539540916:AAENFBF2B2dyXLITmEC2ccgLYim2t9vxOQk")
ADMIN_ID = int(os.environ.get("ADMIN_ID", 5819094246))
APP_URL = "[https://zproject-111.onrender.com](https://zproject-111.onrender.com)"

bot = telebot.TeleBot(TOKEN, threaded=False)
app = Flask(__name__)
START_TIME = time.time()

USER_IDS = set()
GROUP_INFOS = []
# Từ điển để lưu trữ thông tin phản hồi của người dùng (feedback_message_id: original_chat_id)
# Điều này cần thiết để admin có thể reply và bot biết gửi về đâu
bot.feedback_messages = {}

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
GEMINI_API_KEY = "AIzaSyDpmTfFibDyskBHwekOADtstWsPUCbIrzE"
GEMINI_URL = f"[https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key=](https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key=){GEMINI_API_KEY}"
REMOTE_PROMPT_URL = "[https://zcode.x10.mx/prompt.json](https://zcode.x10.mx/prompt.json)"
REMOTE_LOG_HOST = "[https://zcode.x10.mx/save.php](https://zcode.x10.mx/save.php)"

# --- URL ảnh dùng trong bot ---
NGL_SUCCESS_IMAGE_URL = "[https://i.ibb.co/fV1srXJ8/9885878c-2a4b-4246-ae2e-fda17d735e2d.jpg](https://i.ibb.co/fV1srXJ8/9885878c-2a4b-4246-ae2e-fda17d735e2d.jpg)"
# URL ảnh cho lệnh /start
START_IMAGE_URL = "[https://i.ibb.co/MkQ2pTjv/ca68c4b2-60dc-4eb1-9a20-ebf2cc5c557f.jpg](https://i.ibb.co/MkQ2pTjv/ca68c4b2-60dc-4eb1-9a20-ebf2cc5c557f.jpg)"
NOTI_IMAGE_URL = "[https://i.ibb.co/QvrB4zMB/ca68c4b2-2a4b-4246-ae2e-fda17d735e2d.jpg](https://i.ibb.co/QvrB4zMB/ca68c4b2-2a4b-4246-ae2e-fda17d735e2d.jpg)" # URL ảnh cho thông báo mặc định
TUONGTAC_IMAGE_URL = "[https://i.ibb.co/YF4yRCBP/1751301092916.png](https://i.ibb.co/YF4yRCBP/1751301092916.png)" # URL ảnh cho lệnh /tuongtac

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
    """Class dummy cho gTTS."""
    def __init__(self, text, lang="vi", slow=False):
        self.text = text
        self.lang = lang
        self.slow = slow
    def save(self, filename):
        # Logic lưu file âm thanh dummy
        with open(filename, "wb") as f:
            f.write(b"dummy_audio_data")


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
        response = session.post("[https://zcode.x10.mx/apizproject.php](https://zcode.x10.mx/apizproject.php)", json=payload, timeout=DEFAULT_TIMEOUT_GLOBAL)
        response.raise_for_status()
        logging.info(f"Synced chat {chat.id} to server")
    except Exception as e:
        logging.error(f"Error syncing chat {chat.id}: {e}")

def update_id_list_loop():
    """Vòng lặp định kỳ để cập nhật danh sách người dùng và nhóm từ API."""
    global USER_IDS, GROUP_INFOS
    while True:
        try:
            response = session.get("[https://zcode.x10.mx/group-idchat.json](https://zcode.x10.mx/group-idchat.json)", timeout=DEFAULT_TIMEOUT_GLOBAL)
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
        time.sleep(30) # Đợi 30 giây trước khi cập nhật lại

# Khởi chạy luồng cập nhật ID
Thread(target=update_id_list_loop, daemon=True).start()

# --- Hàm hỗ trợ cho /ask và callbacks ---
def build_reply_button(user_id, question, reply_id=None):
    """Tạo các nút phản hồi cho tin nhắn /ask."""
    safe_q = re.sub(r"[^\w\s]", "", question.strip())[:50]
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("🔁 Trả lời lại", callback_data=f"retry|{user_id}|{safe_q}"),
        InlineKeyboardButton("🔊 Chuyển sang Voice", callback_data=f"tts|{user_id}|{reply_id}") if reply_id else None
    )
    return markup

# Decorator để tăng interaction_count cho mỗi lệnh
def increment_interaction_count(func):
    def wrapper(message, *args, **kwargs):
        global interaction_count
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
                disable_web_page_preview=disable_web_page_preview # Pass the argument here
            )
    except telebot.apihelper.ApiTelegramException as e:
        if "message to be replied not found" in str(e):
            logging.warning(f"Failed to reply to message {reply_to_message_id} in chat {chat_id}: {e}. Sending as new message.")
            # Thử gửi lại mà không reply_to_message_id
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
                    disable_web_page_preview=disable_web_page_preview # Pass the argument here
                )
        else:
            logging.error(f"Error sending message to chat {chat_id}: {e}")
            raise # Re-raise other API exceptions

# === LỆNH XỬ LÝ TIN NHẮN ===

@bot.message_handler(commands=["start"])
@increment_interaction_count
def start_cmd(message):
    """Xử lý lệnh /start, hiển thị thông tin bot và các liên kết."""
    sync_chat_to_server(message.chat)
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("👤 Admin", url="[https://t.me/zproject2](https://t.me/zproject2)"),
        InlineKeyboardButton("📢 Thông Báo", url="[https://t.me/zproject3](https://t.me/zproject3)"),
        InlineKeyboardButton("💬 Chat", url="[https://t.me/zproject4](https://t.me/zproject4)")
    )
    send_message_robustly(
        message.chat.id,
        photo=START_IMAGE_URL,
        caption="<b>🚀 ZProject Bot</b>\n\n"
                "Chào mừng bạn đến với Dịch Vụ Zproject Bot Được Make Bởi @zproject2\n "
                "● Chúng Tôi Có Các Dịch Vụ Như Treo Bot 24/7 Giá Cực Rẻ Hơn VPS và Máy Ảo \n● Bạn Có Thể Liên Hệ Telegram @zproject2.\n"
                "Gõ /help để xem danh sách các lệnh.",
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
        "•  <code>/phanhoi</code> - Gửi Phản Hồi Lỗi Hoặc Chức Năng Cần Cải Tiến."
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
    # Sử dụng send_message_robustly
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
        # Sử dụng send_message_robustly
        return send_message_robustly(message.chat.id, text="🚫 Bạn không có quyền sử dụng lệnh này.", parse_mode="HTML", reply_to_message_id=message.message_id)

    text = message.text.replace("/noti", "").strip()

    photo_file_id = None
    if message.reply_to_message and message.reply_to_message.photo:
        photo_file_id = message.reply_to_message.photo[-1].file_id

    if not text and not photo_file_id:
        # Sử dụng send_message_robustly
        return send_message_robustly(message.chat.id, text="⚠️ Sử dụng: <code>/noti &lt;nội dung&gt;</code> hoặc reply vào ảnh và dùng <code>/noti &lt;nội dung&gt;</code>.", parse_mode="HTML", reply_to_message_id=message.message_id)

    notify_caption = f"<b>[!] THÔNG BÁO TỪ ADMIN DEPZAI CUTO</b>\n\n{text}" if text else "<b>[!] THÔNG BÁO</b>"

    ok, fail = 0, 0
    failed_ids = []

    all_recipients = USER_IDS.union({g["id"] for g in GROUP_INFOS})

    for uid in all_recipients:
        try:
            if photo_file_id:
                bot.send_photo( # Không dùng send_message_robustly ở đây vì đây là gửi thông báo mới, không phải reply
                    chat_id=uid,
                    photo=photo_file_id,
                    caption=notify_caption,
                    parse_mode="HTML"
                )
            else:
                bot.send_message( # Không dùng send_message_robustly ở đây vì đây là gửi thông báo mới, không phải reply
                    chat_id=uid,
                    text=notify_caption,
                    parse_mode="HTML",
                    disable_web_page_preview=True # Added this for notifications
                )
            ok += 1
            time.sleep(0.1)
        except Exception as e:
            fail += 1
            failed_ids.append(uid)
            logging.error(f"Failed to send notification to {uid}: {e}")

    # Sử dụng send_message_robustly
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
        # Sử dụng send_message_robustly
        return send_message_robustly(message.chat.id, text="⚠️ Sử dụng: <code>/ngl &lt;username&gt; &lt;tin_nhan&gt; &lt;số_lần&gt;</code>", parse_mode="HTML", reply_to_message_id=message.message_id)

    username = args[1]
    tinnhan = args[2]
    solan_str = args[3]

    try:
        solan = int(solan_str)
        if not (1 <= solan <= 50):
            # Sử dụng send_message_robustly
            return send_message_robustly(message.chat.id, text="❗ Số lần phải từ 1 đến 50.", parse_mode="HTML", reply_to_message_id=message.message_id)
    except ValueError:
        # Sử dụng send_message_robustly
        return send_message_robustly(message.chat.id, text="❗ Số lần phải là một số hợp lệ, không phải ký tự.", parse_mode="HTML", reply_to_message_id=message.message_id)

    ngl_api_url = f"[https://zeusvr.x10.mx/ngl?api-key=dcbfree&username=](https://zeusvr.x10.mx/ngl?api-key=dcbfree&username=){username}&tinnhan={tinnhan}&solan={solan}"

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
        sent_message_to_admin = bot.send_message( # Admin ID luôn nhận tin nhắn mới
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
        # Gửi phản hồi admin cho người dùng
        # Ở đây, không dùng reply_to_message_id trực tiếp vì tin nhắn gốc có thể không phải là tin nhắn phản hồi của người dùng.
        # Gửi như một tin nhắn mới trong chat của người dùng.
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
        link = f"[https://t.me/](https://t.me/){g.get('username')}" if g.get("username") else "⛔ Không có link mời"
        text += f"📌 <b>{title}</b>\n{link}\n\n"
    send_message_robustly(message.chat.id, text=text, parse_mode="HTML", disable_web_page_preview=True, reply_to_message_id=message.message_id)

# Hàm mới để định dạng đầu ra AI
def format_ai_response_html(text):
    """
    Phân tích văn bản từ AI, tách code block và văn bản thông thường,
    sau đó định dạng chúng với HTML cho Telegram, đặc biệt là thẻ <code>.
    Tạo nút "Copy Code" cho mỗi block code.
    """
    parts = []
    # Regex để tìm kiếm các block code Markdown (```language\ncode\n```)
    # Tên ngôn ngữ (nếu có) được bắt bởi group 1, code bởi group 2
    # re.split sẽ trả về các phần văn bản và các phần khớp với group.
    # Nên kết quả sẽ xen kẽ: text, code, text, code, ...
    code_blocks = re.split(r"```(?:\w+)?\n(.*?)```", text, flags=re.DOTALL)

    for i, part in enumerate(code_blocks):
        if i % 2 == 0:  # Phần văn bản (hoặc phần trước code đầu tiên, hoặc sau code cuối cùng)
            if part: # Chỉ thêm nếu có nội dung
                parts.append({"type": "text", "content": html_escape(part.strip())})
        else:  # Phần code (là nội dung của group 1 từ regex)
            if part: # Chỉ thêm nếu có nội dung
                # Tạo một ID duy nhất cho nút copy
                copy_id = uuid.uuid4().hex[:8]
                # Đảm bảo bot.code_snippets tồn tại
                bot.code_snippets = getattr(bot, "code_snippets", {})
                bot.code_snippets[copy_id] = part.strip() # Lưu nội dung code vào map
                
                # Markup cho nút copy
                copy_markup = InlineKeyboardMarkup()
                copy_markup.add(InlineKeyboardButton("📄 Sao chép Code", callback_data=f"copycode|{copy_id}"))

                # Định dạng code với thẻ <code> cho HTML
                # Một số ngôn ngữ như Python có thể có dấu < > trong code, cần escape lại lần nữa cho code
                formatted_code = f"<code>{html_escape(part.strip())}</code>"
                parts.append({"type": "code", "content": formatted_code, "raw_content": part.strip(), "markup": copy_markup})
    return parts


@bot.callback_query_handler(func=lambda call: call.data.startswith("copycode|"))
def copy_code_button(call):
    """Xử lý nút 'Copy Code'."""
    try:
        _, code_id = call.data.split("|", 1)
        code_content = bot.code_snippets.get(code_id)

        if code_content:
            bot.answer_callback_query(call.id, text="Đã sao chép nội dung code!", show_alert=True)
            # Gửi nội dung code ra một tin nhắn riêng chỉ chứa code để người dùng dễ dàng copy.
            # Lưu ý: Telegram không cho phép bot tự động copy vào clipboard của người dùng.
            # Việc gửi riêng này là cách tốt nhất để hỗ trợ.
            try:
                bot.send_message(
                    chat_id=call.message.chat.id,
                    text=f"```\n{code_content}\n```", # Sử dụng Markdown để Telegram hiển thị code block
                    parse_mode="MarkdownV2", # Sử dụng MarkdownV2 để đảm bảo định dạng code
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

    # Gửi tin nhắn "đang xử lý" ban đầu
    try:
        msg_status = bot.send_message(message.chat.id, "🤖", reply_to_message_id=message.message_id)
    except telebot.apihelper.ApiTelegramException as e:
        logging.warning(f"Failed to send initial 'thinking' message in chat {message.chat.id}: {e}. Proceeding without reply_to.")
        msg_status = bot.send_message(message.chat.id, "🤖") # Gửi mà không reply_to nếu lỗi

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
        # Lấy 5 cặp hỏi-đáp gần nhất để làm ngữ cảnh
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
            image = Image.open(Bytesio(downloaded_file))
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
        # Cập nhật tin nhắn trạng thái nếu có thể, hoặc gửi tin nhắn mới
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
        "name": user_name
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
    response_parts = format_ai_response_html(result)
    reply_id = uuid.uuid4().hex[:6]
    main_markup = build_reply_button(user_id, prompt, reply_id)
    bot.voice_map = getattr(bot, "voice_map", {})
    bot.voice_map[reply_id] = result # Lưu toàn bộ kết quả gốc cho TTS

    # Tính toán tổng độ dài của văn bản để quyết định gửi file hay không
    # Cần tính độ dài của nội dung đã được HTML escaped
    total_html_length = sum(len(part["content"]) for part in response_parts)
    
    # Telegram có giới hạn 4096 ký tự cho một tin nhắn HTML. Trừ hao một chút để an toàn.
    if total_html_length > 4000: 
        filename = f"zproject_{reply_id}.txt" # Đổi thành .txt hoặc .md
        with open(filename, "w", encoding="utf-8") as f:
            for part in response_parts:
                if part["type"] == "text":
                    # Khi ghi vào file, chúng ta muốn nội dung "thô" không phải HTML escaped.
                    # Nên dùng raw_content nếu có (cho code) hoặc undo html_escape cho text.
                    f.write(part["content"].replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&quot;", "\"").replace("&#039;", "'"))
                elif part["type"] == "code":
                    # Ghi code block với Markdown vào file
                    f.write("\n```\n")
                    f.write(part["raw_content"]) # Sử dụng raw_content để ghi code gốc vào file
                    f.write("\n```\n")
            
        with open(filename, "rb") as f:
            try:
                bot.send_document(
                    message.chat.id,
                    f,
                    caption=f"📄 Trả lời quá dài! Mình đã đóng gói vào file <code>{filename}</code> nha {html_escape(message.from_user.first_name)}!\n\n"
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
                    caption=f"📄 Trả lời quá dài! Mình đã đóng gói vào file <code>{filename}</code> nha {html_escape(message.from_user.first_name)}!\n\n"
                            f"<i>Vui lòng tải xuống để xem toàn bộ nội dung.</i>",
                    parse_mode="HTML"
                )
        os.remove(filename)
        # Xóa tin nhắn "đang xử lý" ban đầu
        try:
            bot.delete_message(msg_status.chat.id, msg_status.message_id)
        except telebot.apihelper.ApiTelegramException as e:
            logging.warning(f"Failed to delete status message {msg_status.message_id}: {e}")

    else:
        # Gửi từng phần riêng biệt nếu có nhiều code block, hoặc gửi một tin nhắn duy nhất
        current_message_text = f"🤖 <i>ZProject [WORMGPT] trả lời:</i>\n\n"
        # Các nút chung (Trả lời lại, Voice) sẽ được thêm vào markup của tin nhắn cuối cùng hoặc tin nhắn duy nhất
        
        sent_messages = [] # Để lưu các message_id nếu phải gửi nhiều tin nhắn

        # Tạo một bản sao của main_markup để có thể thêm các nút copy code vào từng phần nếu cần
        combined_markup = InlineKeyboardMarkup()
        if main_markup.keyboard:
            for row in main_markup.keyboard:
                combined_markup.row(*row)

        for i, part in enumerate(response_parts):
            if part["type"] == "text":
                current_message_text += part["content"]
            elif part["type"] == "code":
                # Khi gặp code block, gửi đoạn văn bản hiện tại (nếu có) trước
                if len(current_message_text.strip()) > len(f"🤖 <i>ZProject [WORMGPT] trả lời:</i>\n\n"):
                    try:
                        sent_msg = bot.send_message(
                            message.chat.id,
                            text=current_message_text,
                            parse_mode="HTML",
                            reply_to_message_id=message.message_id if not sent_messages else None # Reply to original message only for the first part
                        )
                        sent_messages.append(sent_msg.message_id)
                    except telebot.apihelper.ApiTelegramException as e:
                        logging.warning(f"Failed to send text part {i} in chat {message.chat.id}: {e}. Sending without reply_to.")
                        sent_msg = bot.send_message(
                            message.chat.id,
                            text=current_message_text,
                            parse_mode="HTML"
                        )
                        sent_messages.append(sent_msg.message_id)

                # Gửi code block riêng
                try:
                    sent_code_msg = bot.send_message(
                        message.chat.id,
                        text=f"<b>Code:</b>\n{part['content']}", # content đã được định dạng <code>
                        parse_mode="HTML",
                        reply_markup=part["markup"], # Markup riêng cho nút copy code
                        reply_to_message_id=message.message_id if not sent_messages and len(current_message_text.strip()) <= len(f"🤖 <i>ZProject [WORMGPT] trả lời:</i>\n\n") else None # Reply to original message if this is the very first content
                    )
                    sent_messages.append(sent_code_msg.message_id)
                except telebot.apihelper.ApiTelegramException as e:
                    logging.warning(f"Failed to send code part {i} in chat {message.chat.id}: {e}. Sending without reply_to.")
                    sent_code_msg = bot.send_message(
                        message.chat.id,
                        text=f"<b>Code:</b>\n{part['content']}",
                        parse_mode="HTML",
                        reply_markup=part["markup"]
                    )
                    sent_messages.append(sent_code_msg.message_id)

                # Reset current_message_text cho phần tiếp theo
                current_message_text = ""

        # Gửi phần văn bản cuối cùng nếu còn (hoặc nếu toàn bộ là văn bản)
        if len(current_message_text.strip()) > 0: # Kiểm tra xem có văn bản thực sự để gửi không
            try:
                # Nếu không có tin nhắn nào được gửi trước đó (nghĩa là toàn bộ phản hồi là văn bản hoặc chỉ một khối văn bản lớn)
                if not sent_messages:
                    bot.edit_message_text(
                        current_message_text,
                        msg_status.chat.id,
                        msg_status.message_id,
                        parse_mode="HTML",
                        reply_markup=combined_markup # Thêm markup chung vào tin nhắn này
                    )
                else: # Đã có các tin nhắn khác được gửi, đây là tin nhắn bổ sung
                    bot.send_message(
                        message.chat.id,
                        text=current_message_text,
                        parse_mode="HTML",
                        reply_markup=combined_markup # Thêm markup chung vào tin nhắn cuối cùng này
                    )
            except telebot.apihelper.ApiTelegramException as edit_e:
                logging.warning(f"Failed to edit message {msg_status.message_id} with final text: {edit_e}. Sending new message instead.")
                send_message_robustly(
                    message.chat.id,
                    text=current_message_text,
                    parse_mode="HTML",
                    reply_markup=combined_markup
                )
        else: # Nếu không còn văn bản sau khi gửi code blocks, và đã có tin nhắn được gửi (sent_messages không rỗng), thì chỉ cần xóa tin trạng thái ban đầu.
            if sent_messages: # Đã gửi ít nhất một tin nhắn (có thể là code block)
                try:
                    bot.delete_message(msg_status.chat.id, msg_status.message_id)
                except telebot.apihelper.ApiTelegramException as e:
                    logging.warning(f"Failed to delete status message {msg_status.message_id}: {e}")
            else: # Trường hợp đặc biệt: AI trả về rỗng hoặc chỉ có khoảng trắng, không có gì để gửi
                try:
                    bot.edit_message_text(
                        f"🤖 <i>ZProject [WORMGPT] trả lời:</i>\n\n<b>Không có nội dung phản hồi từ AI.</b>",
                        msg_status.chat.id,
                        msg_status.message_id,
                        parse_mode="HTML",
                        reply_markup=main_markup # Vẫn giữ các nút chung
                    )
                except telebot.apihelper.ApiTelegramException as edit_e:
                    logging.warning(f"Failed to edit message {msg_status.message_id} with 'no content' msg: {edit_e}. Sending new message.")
                    send_message_robustly(
                        message.chat.id,
                        text=f"🤖 <i>ZProject [WORMGPT] trả lời:</i>\n\n<b>Không có nội dung phản hồi từ AI.</b>",
                        parse_mode="HTML",
                        reply_markup=main_markup
                    )

# --- NÚT CALLBACK ---

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
        # Cập nhật tin nhắn ban đầu thành "🤖" để cho thấy đang xử lý
        try:
            bot.edit_message_text("🤖 Đang xử lý lại...", call.message.chat.id, call.message.message_id)
        except telebot.apihelper.ApiTelegramException as e:
            logging.warning(f"Failed to edit message {call.message.message_id} on retry: {e}. Sending new 'thinking' message.")
            bot.send_message(call.message.chat.id, "🤖 Đang xử lý lại...") # Send new message if edit fails

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
        clean_text = re.sub(r"```.*?```", "", clean_text, flags=re.DOTALL) # Xóa cả markdown code blocks
        clean_text = clean_text.replace('"', '').replace("'", '') # Xóa dấu nháy kép và đơn

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
                f.seek(0) # Reset con trỏ file
                bot.send_voice(call.message.chat.id, f, caption="🗣️ Đây là Voice ZProject:v")
        os.remove(filename)
        bot.answer_callback_query(call.id, "🎧 Voice đã được gửi!")
    except Exception as e:
        bot.answer_callback_query(call.id, "⚠️ Lỗi khi tạo voice.", show_alert=True)
        logging.error(f"[TTS] Lỗi: {e}")

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
            bot.remove_webhook()
            bot.set_webhook(url=current_webhook_url)
            logging.info(f"Webhook đã được đặt tới: {current_webhook_url}")
        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
    except Exception as e:
        logging.critical(f"Lỗi nghiêm trọng khi khởi động bot: {e}")
