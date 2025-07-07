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
TOKEN = os.environ.get("BOT_TOKEN", "7539540916:AAFH3TBho-13IT6RB_nynN1T9j83GizVDNo")
ADMIN_ID = int(os.environ.get("ADMIN_ID", 5819094246))
APP_URL = "https://zproject-111.onrender.com"

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
retries = Retry(total=5, backoff_factor=1, status_forcelist=[502, 503, 504], allowed_methods=frozenset(['GET', 'POST']))
adapter = HTTPAdapter(max_retries=retries)
session.mount("https://", adapter)
session.mount("http://", adapter) # Thêm cả http nếu có request http
DEFAULT_TIMEOUT = 30 # Đặt timeout mặc định là 30 giây cho tất cả các request

# Ghi đè phương thức request để áp dụng timeout mặc định
class TimeoutSession(requests.Session):
    def request(self, method, url, **kwargs):
        kwargs.setdefault('timeout', DEFAULT_TIMEOUT)
        return super(TimeoutSession, self).request(method, url, **kwargs)

session = TimeoutSession()
session.mount("https://", adapter)
session.mount("http://", adapter)

# --- Cấu hình Gemini API và Prompt từ xa ---
GEMINI_API_KEY = "AIzaSyDpmTfFibDyskBHwekOADtstWsPUCbIrzE"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
REMOTE_PROMPT_URL = "https://zcode.x10.mx/prompt.json"
REMOTE_LOG_HOST = "https://zcode.x10.mx/save.php"

# --- URL ảnh dùng trong bot ---
NGL_SUCCESS_IMAGE_URL = "https://i.ibb.co/fV1srXJ8/9885878c-2a4b-4246-ae2e-fda17d735e2d.jpg"
# URL ảnh cho lệnh /start
START_IMAGE_URL = "https://i.ibb.co/MkQ2pTjv/ca68c4b2-60dc-4eb1-9a20-ebf2cc5c557f.jpg"
NOTI_IMAGE_URL = "https://i.ibb.co/QvrB4zMB/ca68c4b2-2a4b-4246-ae2e-fda17d735e2d.jpg" # URL ảnh cho thông báo mặc định
TUONGTAC_IMAGE_URL = "https://i.ibb.co/YF4yRCBP/1751301092916.png" # URL ảnh cho lệnh /tuongtac

# --- Các hàm Dummy (Cần thay thế bằng logic thực tế của bạn) ---
def load_user_memory(user_id):
    """Tải lịch sử trò chuyện của người dùng."""
    # Đây là hàm dummy, hãy thay thế bằng logic tải dữ liệu thực tế
    return []

def save_user_memory(user_id, memory):
    """Lưu lịch sử trò chuyện của người dùng."""
    # Đây là hàm dummy, hãy thay thế bằng logic lưu dữ liệu thực tế
    pass

def format_html(text):
    """Định dạng văn bản thành HTML, tránh lỗi ký tự đặc biệt."""
    # Bạn có thể cải thiện hàm này để xử lý HTML tốt hơn nếu cần
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
        response = session.post("https://zcode.x10.mx/apizproject.php", json=payload) # Sử dụng session với timeout mặc định
        response.raise_for_status()
        logging.info(f"Synced chat {chat.id} to server")
    except Exception as e:
        logging.error(f"Error syncing chat {chat.id}: {e}")

def update_id_list_loop():
    """Vòng lặp định kỳ để cập nhật danh sách người dùng và nhóm từ API."""
    global USER_IDS, GROUP_INFOS
    while True:
        try:
            response = session.get("https://zcode.x10.mx/group-idchat.json") # Sử dụng session với timeout mặc định
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
        interaction_count += 1
        return func(message, *args, **kwargs)
    return wrapper

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
    # Cập nhật lệnh /start để gửi kèm ảnh và caption như noti
    bot.send_photo(
        message.chat.id,
        photo=START_IMAGE_URL, # Sử dụng URL ảnh mới cho /start
        caption="<b>🚀 ZProject Bot</b>\n\n"
                "Chào mừng bạn đến với Dịch Vụ Zproject Bot Được Make Bởi @zproject2\n "
                "● Chúng Tôi Có Các Dịch Vụ Như Treo Bot 24/7 Giá Cực Rẻ Hơn VPS và Máy Ảo \n● Bạn Có Thể Liên Hệ Telegram @zproject2.\n"
                "Gõ /help để xem danh sách các lệnh.",
        reply_markup=markup,
        parse_mode="HTML",
        reply_to_message_id=message.message_id # Đảm bảo reply lại tin nhắn người dùng
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
        "•  <code>/spamngl &lt;username&gt; &lt;tin_nhắn&gt; &lt;số_lần&gt;</code> - Spam Ngl.\n"
        "•  <code>/noti &lt;nội dung&gt;</code> - <i>(Chỉ Admin)</i> Gửi thông báo.\n"
        "•  <code>/sever</code> - <i>(Chỉ Admin)</i> Sever Bot.\n"
        "•  <code>/tuongtac</code> - Xem tổng số lượt tương tác của bot.\n"
        "•  <code>/phanhoi</code> - Gửi Phản Hồi Lỗi Hoặc Chức Năng Cần Cải Tiến."
    )
    bot.send_photo(
        chat_id=message.chat.id,
        photo=NGL_SUCCESS_IMAGE_URL, # Sử dụng ảnh đã có
        caption=help_text,
        parse_mode="HTML",
        reply_to_message_id=message.message_id # Đảm bảo reply lại tin nhắn người dùng
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
    bot.reply_to(
        message,
        f"<blockquote>⏱️ Bot đã hoạt động được:\n<b>{days} ngày {hours} giờ {minutes} phút {sec} giây</b></blockquote>",
        parse_mode="HTML"
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
    
    bot.send_photo(
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
        return bot.reply_to(message, "🚫 Bạn không có quyền sử dụng lệnh này.", parse_mode="HTML")

    text = message.text.replace("/noti", "").strip()

    photo_file_id = None
    if message.reply_to_message and message.reply_to_message.photo:
        photo_file_id = message.reply_to_message.photo[-1].file_id

    if not text and not photo_file_id:
        return bot.reply_to(message, "⚠️ Sử dụng: <code>/noti &lt;nội dung&gt;</code> hoặc reply vào ảnh và dùng <code>/noti &lt;nội dung&gt;</code>.", parse_mode="HTML")

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
                    parse_mode="HTML"
                )
            ok += 1
            time.sleep(0.1)
        except Exception as e:
            fail += 1
            failed_ids.append(uid)
            logging.error(f"Failed to send notification to {uid}: {e}")

    bot.reply_to(
        message,
        f"✅ Gửi thành công: {ok} tin nhắn.\n❌ Gửi thất bại: {fail} tin nhắn.\n"
        f"Danh sách ID thất bại: <code>{failed_ids}</code>",
        parse_mode="HTML"
    )

@bot.message_handler(commands=["spamngl"])
@increment_interaction_count
def spam_ngl_command(message):
    """Xử lý lệnh /spamngl để gửi tin nhắn ẩn danh tới NGL."""
    sync_chat_to_server(message.chat)

    args = message.text.split(maxsplit=3)

    if len(args) < 4:
        return bot.reply_to(message, "⚠️ Sử dụng: <code>/spamngl &lt;username&gt; &lt;tin_nhan&gt; &lt;số_lần&gt;</code>", parse_mode="HTML")

    username = args[1]
    tinnhan = args[2]
    solan_str = args[3]

    try:
        solan = int(solan_str)
        # Giới hạn số lần spam NGL tối đa là 50
        if not (1 <= solan <= 50):
            return bot.reply_to(message, "❗ Số lần phải từ 1 đến 50.", parse_mode="HTML")
    except ValueError:
        return bot.reply_to(message, "❗ Số lần phải là một số hợp lệ, không phải ký tự.", parse_mode="HTML")

    ngl_api_url = f"https://zeusvr.x10.mx/ngl?api-key=dcbfree&username={username}&tinnhan={tinnhan}&solan={solan}"

    try:
        response = session.get(ngl_api_url) # Sử dụng session với timeout mặc định
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

            bot.send_photo(
                chat_id=message.chat.id,
                photo=NGL_SUCCESS_IMAGE_URL,
                caption=reply_text,
                parse_mode="HTML",
                reply_to_message_id=message.message_id # Đảm bảo reply lại tin nhắn người dùng
            )
        else:
            error_message = data.get("message", "Có lỗi xảy ra khi gọi API NGL.")
            bot.reply_to(message, f"❌ Lỗi NGL API: {error_message}", parse_mode="HTML")

    except requests.exceptions.ReadTimeout as e:
        logging.error(f"Lỗi timeout khi gọi NGL API: {e}")
        bot.reply_to(message, f"❌ Lỗi timeout: API NGL không phản hồi kịp thời. Vui lòng thử lại sau.", parse_mode="HTML")
    except requests.exceptions.ConnectionError as e:
        logging.error(f"Lỗi kết nối khi gọi NGL API: {e}")
        bot.reply_to(message, f"❌ Lỗi kết nối đến NGL API: Không thể kết nối đến máy chủ. Vui lòng kiểm tra lại sau.", parse_mode="HTML")
    except requests.exceptions.RequestException as e:
        logging.error(f"Lỗi chung khi gọi NGL API: {e}")
        bot.reply_to(message, f"❌ Lỗi khi gọi NGL API: <code>{e}</code>", parse_mode="HTML")
    except ValueError as e:
        logging.error(f"Lỗi phân tích JSON từ NGL API: {e}")
        bot.reply_to(message, "❌ Lỗi: Phản hồi API không hợp lệ.", parse_mode="HTML")
    except Exception as e:
        logging.error(f"Lỗi không xác định khi xử lý /spamngl: {e}")
        bot.reply_to(message, f"❌ Đã xảy ra lỗi không mong muốn: <code>{e}</code>", parse_mode="HTML")

@bot.message_handler(commands=["phanhoi"])
@increment_interaction_count
def send_feedback_to_admin(message):
    """Xử lý lệnh /phanhoi, cho phép người dùng gửi phản hồi đến admin."""
    sync_chat_to_server(message.chat)
    feedback_text = message.text.replace("/phanhoi", "").strip()

    if not feedback_text:
        return bot.reply_to(message, "⚠️ Vui lòng nhập nội dung phản hồi. Ví dụ: <code>/phanhoi Bot bị lỗi ở lệnh /ask</code>", parse_mode="HTML")

    # Lấy thông tin chi tiết của người gửi
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
        f"<b>Nội dung phản hồi:</b>\n<blockquote>{format_html(feedback_text)}</blockquote>\n\n"
        f"<i>Để phản hồi lại người dùng này, hãy reply tin nhắn này và dùng lệnh <code>/adminph &lt;nội dung phản hồi&gt;</code></i>"
    )

    try:
        sent_message_to_admin = bot.send_message(
            chat_id=ADMIN_ID,
            text=admin_notification,
            parse_mode="HTML",
            disable_web_page_preview=True # Tắt preview để tránh lỗi với tg://user
        )
        # Lưu trữ mapping tin nhắn của admin với chat ID của người dùng và các thông tin khác
        bot.feedback_messages[sent_message_to_admin.message_id] = {
            'chat_id': message.chat.id,
            'user_id': message.from_user.id, # Lưu user ID để tag
            'user_first_name': message.from_user.first_name, # Lưu tên để tag
            'feedback_text': feedback_text # Lưu nội dung phản hồi gốc
        }
        
        bot.reply_to(
            message,
            "✅ Cảm ơn bạn đã gửi phản hồi! Admin sẽ xem xét sớm nhất có thể.",
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Lỗi khi gửi phản hồi đến admin: {e}")
        bot.reply_to(message, "❌ Đã xảy ra lỗi khi gửi phản hồi. Vui lòng thử lại sau.", parse_mode="HTML")

@bot.message_handler(commands=["adminph"])
@increment_interaction_count
def admin_reply_to_feedback(message):
    """Xử lý lệnh /adminph, cho phép admin phản hồi lại người dùng đã gửi feedback."""
    if message.from_user.id != ADMIN_ID:
        return bot.reply_to(message, "🚫 Bạn không có quyền sử dụng lệnh này.", parse_mode="HTML")

    if not message.reply_to_message:
        return bot.reply_to(message, "⚠️ Bạn cần reply vào tin nhắn phản hồi của người dùng để sử dụng lệnh này.", parse_mode="HTML")

    original_feedback_message_id = message.reply_to_message.message_id
    feedback_data = bot.feedback_messages.get(original_feedback_message_id)

    if not feedback_data:
        return bot.reply_to(message, "❌ Không tìm thấy thông tin chat của người dùng này. Có thể tin nhắn quá cũ hoặc bot đã khởi động lại.", parse_mode="HTML")

    user_chat_id = feedback_data['chat_id']
    user_id_to_tag = feedback_data['user_id']
    user_name_to_tag = feedback_data['user_first_name']
    original_feedback_text = feedback_data['feedback_text']

    admin_response_text = message.text.replace("/adminph", "").strip()

    if not admin_response_text:
        return bot.reply_to(message, "⚠️ Vui lòng nhập nội dung phản hồi của admin. Ví dụ: <code>/adminph Cảm ơn bạn, chúng tôi đã khắc phục lỗi.</code>", parse_mode="HTML")

    # Tạo tag người dùng và hiển thị thông tin phản hồi gốc
    user_tag = f"<a href='tg://user?id={user_id_to_tag}'>{user_name_to_tag}</a>"

    admin_reply_to_user = (
        f"<b>👨‍💻 Admin đã phản hồi bạn {user_tag}!</b>\n\n"
        f"<b>Nội dung phản hồi của bạn:</b>\n"
        f"<blockquote>{format_html(original_feedback_text)}</blockquote>\n\n"
        f"<b>Phản hồi từ Admin:</b>\n"
        f"<blockquote>{format_html(admin_response_text)}</blockquote>\n\n"
        f"<i>Nếu bạn có thêm câu hỏi, vui lòng gửi phản hồi mới qua lệnh <code>/phanhoi</code>.</i>"
    )

    try:
        bot.send_message(
            chat_id=user_chat_id,
            text=admin_reply_to_user,
            parse_mode="HTML",
            disable_web_page_preview=True # Tắt preview để tránh lỗi với tg://user
        )
        bot.reply_to(message, "✅ Đã gửi phản hồi của Admin đến người dùng thành công.", parse_mode="HTML")
        # Xóa mapping sau khi đã phản hồi để tránh dùng lại (tùy chọn)
        # del bot.feedback_messages[original_feedback_message_id]
    except Exception as e:
        logging.error(f"Lỗi khi gửi phản hồi của admin đến người dùng {user_chat_id}: {e}")
        bot.reply_to(message, "❌ Đã xảy ra lỗi khi gửi phản hồi của Admin đến người dùng.", parse_mode="HTML")


@bot.message_handler(commands=["sever"])
@increment_interaction_count
def show_groups(message):
    """Xử lý lệnh /sever, hiển thị danh sách các nhóm bot đang tham gia (chỉ Admin)."""
    if message.from_user.id != ADMIN_ID:
        return bot.reply_to(message, "🚫 Bạn không có quyền sử dụng lệnh này.", parse_mode="HTML")
    if not GROUP_INFOS:
        return bot.reply_to(message, "📭 Hiện tại bot chưa có thông tin về nhóm nào.", parse_mode="HTML")
    text = "<b>📦 Sever:</b>\n\n"
    for g in GROUP_INFOS:
        title = g.get("title", "Không rõ tên nhóm")
        link = f"https://t.me/{g.get('username')}" if g.get("username") else "⛔ Không có link mời"
        text += f"📌 <b>{title}</b>\n{link}\n\n"
    bot.reply_to(message, text, parse_mode="HTML", disable_web_page_preview=True)

@bot.message_handler(commands=["ask"])
@increment_interaction_count
def ask_command(message):
    """Xử lý lệnh /ask để gửi câu hỏi đến Gemini AI. Hỗ trợ hỏi kèm ảnh."""
    sync_chat_to_server(message.chat)
    prompt = message.text.replace("/ask", "").strip()
    if not prompt:
        return bot.reply_to(message, "❓ Bạn chưa nhập câu hỏi rồi đó! Vui lòng gõ <code>/ask &lt;câu hỏi của bạn&gt;</code>.", parse_mode="HTML")

    msg_status = bot.reply_to(message, "🤖") # Gửi tin nhắn "đang xử lý" và lưu để cập nhật

    user_id = message.from_user.id
    user_name = message.from_user.first_name
    memory = load_user_memory(user_id)

    try:
        # Sử dụng session với timeout mặc định
        prompt_data = session.get(REMOTE_PROMPT_URL).json()
        system_prompt = prompt_data.get("prompt", "Bạn là AI thông minh và hữu ích.")
    except Exception as e:
        logging.error(f"Lỗi tải prompt từ xa: {e}")
        system_prompt = "Bạn là AI thông minh và hữu ích."

    history_block = ""
    if memory:
        for item in memory[-5:]: # Chỉ lấy 5 cuộc hội thoại gần nhất
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
            # Đảm bảo lưu ảnh dưới định dạng JPEG để tương thích với Gemini
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
        # Sử dụng session với timeout mặc định
        res = session.post(GEMINI_URL, headers=headers, json=data)
        res.raise_for_status()
        result = res.json()["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        return bot.edit_message_text(
            f"❌ Đã xảy ra lỗi khi gọi API Gemini:\n<pre>{e}</pre>",
            msg_status.chat.id,
            msg_status.message_id,
            parse_mode="HTML"
        )

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
        # Sử dụng session với timeout mặc định
        session.post(
            f"{REMOTE_LOG_HOST}?uid={user_id}",
            data=json.dumps(memory, ensure_ascii=False),
            headers={"Content-Type": "application/json"}
        )
    except Exception as e:
        logging.error(f"Lỗi gửi log từ xa: {e}")

    formatted_result = format_html(result)

    reply_id = uuid.uuid4().hex[:6]
    markup = build_reply_button(user_id, prompt, reply_id)

    bot.voice_map = getattr(bot, "voice_map", {})
    bot.voice_map[reply_id] = result

    if len(formatted_result) > 4000:
        filename = f"zproject_{reply_id}.html"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(f"<html><head><meta charset='utf-8'></head><body>{formatted_result}</body></html>")
        with open(filename, "rb") as f:
            bot.send_document(
                message.chat.id,
                f,
                caption="📄 Trả lời dài quá, đây là file HTML nha!",
                parse_mode="HTML",
                reply_to_message_id=message.message_id # Đảm bảo reply lại tin nhắn người dùng
            )
        os.remove(filename)
        bot.delete_message(msg_status.chat.id, msg_status.message_id) # Xóa tin nhắn "đang xử lý"
    else:
        bot.edit_message_text(
            f"🤖 <i>ZProject [WORMGPT] trả lời:</i>\n\n<b>{formatted_result}</b>",
            msg_status.chat.id,
            msg_status.message_id,
            parse_mode="HTML",
            reply_markup=markup
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
            message_id=call.message.message_id, # Giữ nguyên message_id của tin nhắn ban đầu
            text="/ask " + question,
            from_user=call.from_user,
            reply_to_message=None # Không có reply_to_message khi retry thông thường
        )

        bot.answer_callback_query(call.id, "🔁 Đang thử lại câu hỏi...")
        # Cập nhật tin nhắn ban đầu thành "🤖" để cho thấy đang xử lý
        bot.edit_message_text("🤖", call.message.chat.id, call.message.message_id)
        ask_command(msg) # Call ask_command, nó sẽ tự động tăng interaction_count
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

        clean_text = re.sub(r"<code>.*?</code>", "", answer, flags=re.DOTALL)
        clean_text = re.sub(r"<[^>]+>", "", clean_text)
        text_to_speak = clean_text.strip()

        if not text_to_speak or len(text_to_speak) < 5:
            return bot.answer_callback_query(call.id, "❗ Nội dung quá ngắn hoặc rỗng để chuyển voice.", show_alert=True)

        filename = f"zproject_tts_{reply_id}.mp3"
        tts = gTTS(text=text_to_speak, lang="vi", slow=False)
        tts.save(filename)

        with open(filename, "rb") as f:
            bot.send_voice(call.message.chat.id, f, caption="🗣️ Đây là Voice ZProject:v", reply_to_message_id=call.message.message_id) # Reply voice vào tin nhắn gốc
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
