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
import threading # Thêm import này cho auto_delete_email và Locks
from telebot import types
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

# THAY ĐỔI QUAN TRỌNG: BẬT CHẾ ĐỘ ĐA LUỒNG
bot = telebot.TeleBot(TOKEN, threaded=True) # <<< ĐÃ CHỈNH SỬA Ở ĐÂY
app = Flask(__name__)
START_TIME = time.time()

# Biến toàn cục và các Lock để bảo vệ truy cập đa luồng
USER_IDS = set()
GROUP_INFOS = []
user_data = {}
bot.feedback_messages = {}
bot.code_snippets = {}
bot.voice_map = {}
bot.mail_messages_state = {}
interaction_count = 0

# Khởi tạo Locks cho các biến dùng chung
user_data_lock = threading.Lock()
feedback_messages_lock = threading.Lock()
code_snippets_lock = threading.Lock()
voice_map_lock = threading.Lock()
mail_messages_state_lock = threading.Lock() # Thêm lock cho bot.mail_messages_state
interaction_count_lock = threading.Lock()
user_group_info_lock = threading.Lock()


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
    with user_data_lock: # Bảo vệ truy cập user_data
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
            
            with user_group_info_lock: # Bảo vệ USER_IDS và GROUP_INFOS
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
        with interaction_count_lock: # Sử dụng lock
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

# --- THÊM VÀO PHẦN KHAI BÁO BIẾN TOÀN CỤC CỦA BOT ZPROJECT CỦA BẠN ---
import threading
import time
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import logging
import datetime
import random
import string
import re
import queue
import requests
from requests.exceptions import ProxyError

# Cài đặt cấu hình logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Biến toàn cục cho quản lý trạng thái của lệnh /locket
# user_id: { 'step': 'waiting_for_target', 'target': None, 'message_id': None, 'chat_id': None, 'spam_thread': None, 'last_action_time': None, 'current_attack_count': 0 }
locket_states = {}
locket_states_lock = threading.Lock()

# Biến toàn cục cho Rate Limiting
LAST_LOCKET_COMMAND_TIME = {}
RATE_LIMIT_DURATION = 300 # 5 phút = 300 giây

# Biến toàn cục cho Proxy
proxy_queue = queue.Queue()
last_proxy_update_time = 0
proxy_update_interval = 300 # 5 phút

# Định nghĩa các nguồn proxy miễn phí và uy tín (cập nhật nếu cần)
FREE_PROXY_SOURCES = [
    'https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all',
    'https://api.proxyscrape.com/v2/?request=displayproxies&protocol=https&timeout=20000&country=all&ssl=all&anonymity=all',
    'https://raw.githubusercontent.com/Zaeem20/FREE_PROXIES_LIST/master/http.txt',
    'https://raw.githubusercontent.com/Zaeem20/FREE_PROXIES_LIST/master/https.txt',
    'https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt',
    'https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt',
    'https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt',
    'https://raw.githubusercontent.com/sunny9577/proxies/master/proxies.txt'
]



# --- THÊM VÀO CODE BOT CỦA BẠN (CÙNG VỚI CÁC @bot.message_handler khác) ---

@bot.message_handler(commands=["locket"])
# @increment_interaction_count # Nếu bạn có hàm này để đếm tương tác, hãy bỏ comment
def handle_locket_command(message):
    user_id = message.from_user.id
    chat_id = message.chat.id

    # Kiểm tra quyền admin
    if user_id != ADMIN_ID:
        return send_message_robustly(chat_id, text="🚫 Bạn không có quyền sử dụng lệnh này.", parse_mode="HTML", reply_to_message_id=message.message_id)

    # Kiểm tra rate limit
    with locket_states_lock:
        last_time = LAST_LOCKET_COMMAND_TIME.get(user_id)
        current_time = time.time()
        if last_time and (current_time - last_time < RATE_LIMIT_DURATION):
            remaining_time = int(RATE_LIMIT_DURATION - (current_time - last_time))
            return send_message_robustly(
                chat_id,
                text=f"⏳ Vui lòng chờ <b>{remaining_time} giây</b> nữa trước khi sử dụng lại lệnh /locket.",
                parse_mode="HTML",
                reply_to_message_id=message.message_id
            )
        LAST_LOCKET_COMMAND_TIME[user_id] = current_time

    # Xóa trạng thái cũ nếu có
    with locket_states_lock:
        if user_id in locket_states:
            del locket_states[user_id]

    # Tin nhắn ban đầu: Đang kiểm tra...
    checking_msg = send_message_robustly(
        chat_id,
        text="⏳ Đang kiểm tra link/username Locket...",
        parse_mode="HTML",
        reply_to_message_id=message.message_id
    )

    if not checking_msg:
        logging.error(f"Failed to send initial checking message for user {user_id}")
        return

    # Lưu trạng thái ban đầu
    with locket_states_lock:
        locket_states[user_id] = {
            'step': 'waiting_for_target',
            'target': None,
            'message_id': checking_msg.message_id, # Lưu ID tin nhắn để chỉnh sửa
            'chat_id': chat_id,
            'spam_thread': None, # Luồng spam sẽ được lưu ở đây
            'current_attack_count': 0 # Đếm số vòng spam hiện tại
        }

    # Trích xuất username/link từ tin nhắn
    command_args = message.text.replace("/locket", "").strip()

    if not command_args:
        # Yêu cầu người dùng cung cấp username/link
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=checking_msg.message_id,
            text="⚠️ Vui lòng cung cấp Username hoặc Link Locket. Ví dụ: <code>/locket wusthanhdieu</code> hoặc <code>/locket https://locket.cam/wusthanhdieu</code>",
            parse_mode="HTML"
        )
        return

    # Bắt đầu luồng kiểm tra Locket UID trong nền
    threading.Thread(target=check_locket_target_thread, args=(user_id, command_args)).start()


def check_locket_target_thread(user_id, target_input):
    with locket_states_lock:
        state = locket_states.get(user_id)
        if not state:
            logging.error(f"State for user {user_id} not found during Locket target check.")
            return

    chat_id = state['chat_id']
    message_id = state['message_id']

    # Sử dụng hàm _extract_uid_locket từ zlocket_bot_handler
    zlocket_bot_handler.messages = [] # Xóa thông báo lỗi cũ
    locket_uid = zlocket_bot_handler._extract_uid_locket(target_input)

    if locket_uid:
        with locket_states_lock:
            state['target'] = locket_uid
            state['step'] = 'target_checked'
            locket_states[user_id] = state # Cập nhật lại state

        bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=f"✅ Đã tìm thấy Locket!\n\n"
                 f"<b>Locket UID:</b> <code>{locket_uid}</code>\n"
                 f"<b>Username/Link:</b> <code>{html_escape(target_input)}</code>\n\n"
                 "Bạn có muốn khởi động tấn công (spam kết bạn) Locket này không?",
            parse_mode="HTML",
            reply_markup=get_locket_action_markup()
        )
        logging.info(f"Locket target {locket_uid} found for user {user_id}")
    else:
        error_msg = "\n".join(zlocket_bot_handler.messages)
        if not error_msg:
            error_msg = "Không xác định. Vui lòng kiểm tra lại username/link."

        bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=f"❌ Không tìm thấy Locket hoặc Link không hợp lệ.\n\n"
                 f"<b>Lỗi:</b> {html_escape(error_msg)}\n\n"
                 "Vui lòng thử lại với lệnh <code>/locket &lt;username/link&gt;</code>.",
            parse_mode="HTML"
        )
        # Xóa trạng thái sau khi lỗi
        with locket_states_lock:
            if user_id in locket_states:
                del locket_states[user_id]
        logging.warning(f"Locket target not found for user {user_id}: {error_msg}")


def get_locket_action_markup():
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("🚀 Bật Attack", callback_data="locket_action|start_attack"),
        InlineKeyboardButton("⛔️ Tắt Attack", callback_data="locket_action|cancel")
    )
    return markup


@bot.callback_query_handler(func=lambda call: call.data.startswith("locket_action|"))
def handle_locket_action_callback(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    action = call.data.split("|")[1]

    # Đảm bảo chỉ người dùng tạo lệnh mới có thể tương tác
    with locket_states_lock:
        state = locket_states.get(user_id)
        if not state or state['message_id'] != message_id:
            bot.answer_callback_query(call.id, "Phiên làm việc đã hết hoặc bạn không phải người tạo lệnh này.", show_alert=True)
            return

    bot.answer_callback_query(call.id) # Gửi phản hồi callback để tắt loading trên nút

    if action == "start_attack":
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text="⏳ Đang khởi động tấn công Locket...",
            parse_mode="HTML"
        )
        # Bắt đầu luồng tấn công spam
        spam_thread = threading.Thread(target=start_locket_attack_thread, args=(user_id,))
        spam_thread.daemon = True # Đảm bảo luồng sẽ dừng khi bot dừng
        spam_thread.start()

        with locket_states_lock:
            state['spam_thread'] = spam_thread
            state['step'] = 'attacking'
            locket_states[user_id] = state # Cập nhật lại state

    elif action == "cancel":
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text="Đã hủy yêu cầu tấn công Locket.",
            parse_mode="HTML"
        )
        # Xóa trạng thái
        with locket_states_lock:
            if user_id in locket_states:
                del locket_states[user_id]


def start_locket_attack_thread(user_id):
    with locket_states_lock:
        state = locket_states.get(user_id)
        if not state or not state.get('target'):
            logging.error(f"Cannot start Locket attack: invalid state for user {user_id}")
            return

    chat_id = state['chat_id']
    message_id = state['message_id']
    target_uid = state['target']

    zlocket_bot_handler.TARGET_FRIEND_UID = target_uid
    zlocket_bot_handler.FIREBASE_APP_CHECK = zlocket_bot_handler._load_token_() # Đảm bảo token luôn mới

    if not zlocket_bot_handler.FIREBASE_APP_CHECK:
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text="❌ Lỗi: Không thể lấy token Locket. Vui lòng thử lại sau.",
            parse_mode="HTML"
        )
        with locket_states_lock:
            if user_id in locket_states:
                del locket_states[user_id]
        return

    # Lặp lại 2-3 vòng
    num_rounds = random.randint(2, 3)
    successful_rounds = 0

    for round_num in range(1, num_rounds + 1):
        with locket_states_lock:
            state = locket_states.get(user_id) # Lấy trạng thái mới nhất
            if not state or state.get('step') != 'attacking':
                logging.info(f"Attack stopped prematurely for user {user_id}.")
                break # Dừng vòng lặp nếu trạng thái thay đổi

        bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=f"🚀 Đang tấn công Locket <b><code>{html_escape(target_uid)}</code></b>...\n"
                 f"Vòng: <b>{round_num}/{num_rounds}</b>\n"
                 f"Tài khoản đã tạo: <b>{zlocket_bot_handler.successful_requests}</b>\n"
                 f"Yêu cầu thất bại: <b>{zlocket_bot_handler.failed_requests}</b>",
            parse_mode="HTML"
        )

        # Lấy một số lượng proxy nhất định cho mỗi vòng tấn công
        # Để đảm bảo phân phối đều và không làm cạn kiệt nhanh chóng
        num_proxies_per_round = 10 # Số lượng proxy được lấy từ queue cho mỗi vòng
        current_round_proxies = []
        for _ in range(num_proxies_per_round):
            proxy = get_next_proxy()
            if proxy:
                current_round_proxies.append(proxy)
            else:
                logging.warning(f"Not enough proxies for round {round_num}. Using {len(current_round_proxies)} available proxies.")
                break # Không có đủ proxy, dùng số lượng hiện có

        if not current_round_proxies:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"❌ Lỗi: Không có proxy khả dụng cho vòng tấn công {round_num}. Dừng tấn công.",
                parse_mode="HTML"
            )
            break

        threads = []
        stop_event_attack = threading.Event() # Event để dừng các luồng con nếu cần
        
        # Reset lại số liệu thống kê cho mỗi vòng để dễ theo dõi hơn
        zlocket_bot_handler.successful_requests = 0
        zlocket_bot_handler.failed_requests = 0

        for i in range(len(current_round_proxies)): # Chỉ cần số lượng luồng bằng số proxy hiện có
            thread = threading.Thread(
                target=run_locket_spam_worker,
                args=(user_id, i, stop_event_attack)
            )
            threads.append(thread)
            thread.start()

        # Chờ các luồng hoàn thành trong vòng này
        for t in threads:
            t.join() # Chờ từng luồng hoàn thành

        if zlocket_bot_handler.successful_requests > 0:
            successful_rounds += 1
        
        time.sleep(5) # Nghỉ giữa các vòng

    final_message = f"✅ Đã hoàn tất tấn công Locket <b><code>{html_escape(target_uid)}</code></b>!\n\n"
    if successful_rounds > 0:
        final_message += f"<b>Tổng số vòng thành công:</b> {successful_rounds}/{num_rounds}\n"
        final_message += f"<b>Tổng tài khoản tạo thành công:</b> {zlocket_bot_handler.successful_requests}\n"
        final_message += f"<b>Tổng yêu cầu thất bại:</b> {zlocket_bot_handler.failed_requests}\n\n"
        final_message += "Đã kết thúc quá trình attack."
    else:
        final_message = f"❌ Không thể tấn công Locket <b><code>{html_escape(target_uid)}</code></b>.\n"
        final_message += "Có thể do không có proxy khả dụng hoặc lỗi kết nối. Vui lòng thử lại sau."


    bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=final_message,
        parse_mode="HTML"
    )

    # Xóa trạng thái sau khi hoàn thành
    with locket_states_lock:
        if user_id in locket_states:
            del locket_states[user_id]


def run_locket_spam_worker(user_id, thread_id, stop_event):
    """
    Hàm worker cho mỗi luồng spam Locket.
    Mỗi luồng sẽ cố gắng tạo một số lượng tài khoản/yêu cầu kết bạn nhất định.
    """
    with locket_states_lock:
        state = locket_states.get(user_id)
    
    if not state:
        logging.error(f"Worker {thread_id} failed: No state for user {user_id}")
        return

    # Mỗi luồng sẽ cố gắng tạo ACC_PER_THREAD tài khoản
    accounts_per_thread_target = random.randint(6, 10) # Có thể lấy từ config.ACCOUNTS_PER_PROXY
    
    successful_accounts_in_thread = 0
    failed_attempts_in_thread = 0
    max_failed_attempts_per_thread = 5 # Số lần thử lại tối đa cho 1 luồng nếu gặp lỗi liên tiếp

    while not stop_event.is_set() and \
          successful_accounts_in_thread < accounts_per_thread_target and \
          failed_attempts_in_thread < max_failed_attempts_per_thread:
        
        if stop_event.is_set():
            return

        prefix = f"[{thread_id:03d} | Register]"
        email = _rand_email_()
        password = _rand_pw_()
        
        payload = {
            "data": {
                "email": email,
                "password": password,
                "client_email_verif": True,
                "client_token": _rand_str_(40, chars=string.hexdigits.lower()),
                "platform": "ios"
            }
        }
        
        response_data = zlocket_bot_handler.excute(
            f"{zlocket_bot_handler.API_LOCKET_URL}/createAccountWithEmailPassword",
            headers=zlocket_bot_handler.headers_locket(),
            payload=payload,
            thread_id=thread_id,
            step="Register"
        )

        if stop_event.is_set():
            return

        if response_data == "no_proxy" or response_data == "proxy_dead" or response_data == "too_many_requests" or response_data is None:
            failed_attempts_in_thread += 1
            logging.warning(f"[{thread_id}] Proxy/Network issue or too many requests. Retrying. Attempts: {failed_attempts_in_thread}/{max_failed_attempts_per_thread}")
            time.sleep(1) # Chờ một chút trước khi thử lại
            continue
        
        if isinstance(response_data, dict) and response_data.get('result', {}).get('status') == 200:
            successful_accounts_in_thread += 1
            failed_attempts_in_thread = 0 # Reset lỗi khi thành công

            id_token, local_id = step1b_sign_in(email, password, thread_id, None)
            if id_token and local_id:
                if step2_finalize_user(id_token, thread_id, None):
                    # Gửi yêu cầu kết bạn ban đầu
                    if step3_send_friend_request(id_token, thread_id, None):
                        # Boost thêm 15 yêu cầu
                        for _ in range(15):
                            if stop_event.is_set():
                                return
                            step3_send_friend_request(id_token, thread_id, None)
                    else:
                        logging.warning(f"[{thread_id}] Initial friend request failed for new account.")
                else:
                    logging.warning(f"[{thread_id}] Profile finalization failed for new account.")
            else:
                logging.warning(f"[{thread_id}] Authentication (step 1b) failed for new account.")
        else:
            failed_attempts_in_thread += 1
            logging.warning(f"[{thread_id}] Identity creation failed. Attempts: {failed_attempts_in_thread}/{max_failed_attempts_per_thread}. Response: {response_data}")
            # Có thể thêm logic kiểm tra lỗi cụ thể từ response_data để đưa ra hành động phù hợp hơn

    logging.info(f"Worker {thread_id} finished. Created {successful_accounts_in_thread} accounts.")

@bot.message_handler(commands=["start"])
@increment_interaction_count
def start_cmd(message):
    """Xử lý lệnh /start, hiển thị thông tin bot và các liên kết."""
    logging.info(f"Received /start from user {message.from_user.id} in chat {message.chat.id}") # Thêm log
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
    logging.info(f"Received /help from user {message.from_user.id} in chat {message.chat.id}") # Thêm log
    sync_chat_to_server(message.chat)
    help_text = (
        "<i>📚 Menu Lệnh ZProject Bot</i>\n\n"
        "•  <code>/start</code> - Start Zproject Bot.\n"
        "•  <code>/help</code>  - Show Menu Zproject Bot.\n"
        "•  <code>/time</code>  - Uptime Zproject Bot.\n"
        "•  <code>/ask &lt;câu hỏi&gt;</code> - Hỏi AI Được Tích Hợp WormGpt V2.\n"
        "•  <code>/ngl &lt;username&gt; &lt;tin_nhắn&gt; &lt;số_lần&gt;</code> - Spam Ngl.\n"
        "•  <code>/noti &lt;nội dung&gt;</code> - <i>(Chỉ Admin)</i> Gửi thông báo.\n"
        "•  <code>/sever</code> - <i>(Chỉ Admin)</i> Sever Bot.\n"
        "•  <code>/tuongtac</code> - Xem tổng số lượt tương tác của bot.\n"
        "•  <code>/phanhoi</code> - Gửi Phản Hồi Lỗi Hoặc Chức Năng Cần Cải Tiến.\n"
        "•  <code>/ping</code> - Xem Ping Sever Bot.\n"
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
    logging.info(f"Received /time from user {message.from_user.id} in chat {message.chat.id}") # Thêm log
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
    logging.info(f"Received /tuongtac from user {message.from_user.id} in chat {message.chat.id}") # Thêm log
    sync_chat_to_server(message.chat)
    
    with interaction_count_lock: # Đọc biến được bảo vệ
        current_interaction_count = interaction_count

    reply_text = (
        f"<b>📊 THỐNG KÊ ZPROJECT BOT</b>\n\n"
        f"● Tổng Thống Kê Zproject Bot.\n\n"
        f"<b>Tổng số lượt tương tác:</b> <code>{current_interaction_count}</code>\n"
        f"<i>Lưu ý: Số Lượt Tương Tác Càng Cao Chứng Tỏ Độ Uy Tín Của Bot 🎉.</i>"
    )
    
    send_message_robustly(
        chat_id=message.chat.id,
        photo=TUONGTAC_IMAGE_URL,
        caption=reply_text,
        parse_mode="HTML",
        reply_to_message_id=message.message_id
    )
# Thêm vào phần Biến toàn cục và các Lock
# ... (giữ nguyên các lock cũ) ...
noti_states_lock = threading.Lock() # Thêm lock mới cho bot.noti_states
bot.noti_states = {} # Lưu trạng thái tạo thông báo của admin

# ... (các hàm khác) ...

@bot.message_handler(commands=["noti"])
@increment_interaction_count
def send_noti(message):
    """Xử lý lệnh /noti, cho phép Admin gửi thông báo kèm ảnh (tùy chọn) tới tất cả người dùng/nhóm."""
    logging.info(f"Received /noti from user {message.from_user.id} in chat {message.chat.id}") # Thêm log
    if message.from_user.id != ADMIN_ID:
        return send_message_robustly(message.chat.id, text="🚫 Bạn không có quyền sử dụng lệnh này.", parse_mode="HTML", reply_to_message_id=message.message_id)

    text = message.text.replace("/noti", "").strip()

    photo_file_id = None
    if message.reply_to_message and message.reply_to_message.photo:
        photo_file_id = message.reply_to_message.photo[-1].file_id

    if not text and not photo_file_id:
        return send_message_robustly(message.chat.id, text="⚠️ Sử dụng: <code>/noti &lt;nội dung&gt;</code> hoặc reply vào ảnh và dùng <code>/noti &lt;nội dung&gt;</code>.", parse_mode="HTML", reply_to_message_id=message.message_id)

    notify_caption = f"<b>[!] THÔNG BÁO TỪ ADMIN DEPZAI CUTO</b>\n\n{text}\n\n<i>Gửi Bởi Admin @Zproject2</i>" if text else "<b>[!] THÔNG BÁO</b>"

    with noti_states_lock: # Bảo vệ truy cập bot.noti_states
        bot.noti_states[message.chat.id] = {
            'caption': notify_caption,
            'photo_file_id': photo_file_id,
            'original_message_id': message.message_id, # Lưu ID tin nhắn gốc để reply
            'button_text': None,
            'button_url': None
        }

    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("✅ Có", callback_data="noti_add_button|yes"),
        InlineKeyboardButton("❌ Không", callback_data="noti_add_button|no")
    )

    send_message_robustly(
        message.chat.id,
        text="Bạn có muốn thêm nút (button) vào thông báo này không?",
        reply_markup=markup,
        parse_mode="HTML",
        reply_to_message_id=message.message_id
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("noti_add_button|"))
def noti_add_button(call):
    """Xử lý việc admin chọn thêm nút vào thông báo."""
    user_id = call.message.chat.id
    
    # Đảm bảo chỉ admin mới có thể dùng nút này
    if user_id != ADMIN_ID:
        bot.answer_callback_query(call.id, "🚫 Bạn không có quyền sử dụng nút này.", show_alert=True)
        return

    _, choice = call.data.split("|")

    with noti_states_lock:
        noti_info = bot.noti_states.get(user_id)

    if not noti_info:
        bot.answer_callback_query(call.id, "Đã xảy ra lỗi hoặc phiên làm việc đã hết. Vui lòng thử lại lệnh /noti.", show_alert=True)
        return

    if choice == "yes":
        bot.answer_callback_query(call.id, "Bạn đã chọn thêm nút.", show_alert=False)
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="Tuyệt vời! Hãy gửi cho tôi **tên của nút** bạn muốn hiển thị (ví dụ: `Tham gia nhóm`).",
            parse_mode="HTML"
        )
        # Đặt bước tiếp theo là chờ tên nút
        bot.register_next_step_handler(call.message, process_button_text)
    else: # choice == "no"
        bot.answer_callback_query(call.id, "Bạn đã chọn không thêm nút.", show_alert=False)
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="Đang gửi thông báo...",
            parse_mode="HTML"
        )
        # Gửi thông báo ngay lập tức
        send_final_notification(user_id)


def process_button_text(message):
    """Xử lý tên nút được admin gửi."""
    user_id = message.chat.id
    with noti_states_lock:
        noti_info = bot.noti_states.get(user_id)

    if not noti_info:
        send_message_robustly(user_id, "Đã xảy ra lỗi hoặc phiên làm việc đã hết. Vui lòng thử lại lệnh /noti.", parse_mode="HTML")
        return

    button_text = message.text.strip()
    if not button_text:
        send_message_robustly(user_id, "⚠️ Tên nút không được để trống. Vui lòng gửi lại tên nút.", parse_mode="HTML", reply_to_message_id=message.message_id)
        bot.register_next_step_handler(message, process_button_text)
        return

    with noti_states_lock:
        noti_info['button_text'] = button_text
        bot.noti_states[user_id] = noti_info # Cập nhật lại state

    send_message_robustly(
        user_id,
        f"Đã lưu tên nút: <b>{html_escape(button_text)}</b>. Bây giờ hãy gửi cho tôi **URL** mà nút sẽ dẫn đến (ví dụ: `https://t.me/zproject3`).",
        parse_mode="HTML",
        reply_to_message_id=message.message_id
    )
    # Đặt bước tiếp theo là chờ URL
    bot.register_next_step_handler(message, process_button_url)


def process_button_url(message):
    """Xử lý URL của nút được admin gửi và gửi thông báo cuối cùng."""
    user_id = message.chat.id
    with noti_states_lock:
        noti_info = bot.noti_states.get(user_id)

    if not noti_info:
        send_message_robustly(user_id, "Đã xảy ra lỗi hoặc phiên làm việc đã hết. Vui lòng thử lại lệnh /noti.", parse_mode="HTML")
        return

    button_url = message.text.strip()
    if not button_url or not (button_url.startswith("http://") or button_url.startswith("https://")):
        send_message_robustly(user_id, "⚠️ URL không hợp lệ. Vui lòng gửi lại một URL đầy đủ (ví dụ: `https://t.me/zproject3`).", parse_mode="HTML", reply_to_message_id=message.message_id)
        bot.register_next_step_handler(message, process_button_url)
        return

    with noti_states_lock:
        noti_info['button_url'] = button_url
        bot.noti_states[user_id] = noti_info # Cập nhật lại state

    send_message_robustly(
        user_id,
        "Đã lưu URL. Đang tiến hành gửi thông báo...",
        parse_mode="HTML",
        reply_to_message_id=message.message_id
    )

    send_final_notification(user_id)


def send_final_notification(admin_id):
    """Hàm thực hiện gửi thông báo cuối cùng tới tất cả người nhận."""
    with noti_states_lock:
        noti_info = bot.noti_states.pop(admin_id, None) # Lấy và xóa state

    if not noti_info:
        send_message_robustly(admin_id, "Đã xảy ra lỗi khi gửi thông báo. Thông tin không tồn tại.", parse_mode="HTML")
        return

    notify_caption = noti_info['caption']
    photo_file_id = noti_info['photo_file_id']
    button_text = noti_info['button_text']
    button_url = noti_info['button_url']
    original_message_id = noti_info['original_message_id']

    markup = None
    if button_text and button_url:
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton(button_text, url=button_url))

    ok_users_count, ok_groups_count = 0, 0
    failed_count = 0
    failed_details = [] # Lưu chi tiết lỗi (ID, username/title, lỗi)
    
    with user_group_info_lock: # Đọc biến được bảo vệ
        all_users = list(USER_IDS)
        all_groups = list(GROUP_INFOS)

    # Gửi tới tất cả người dùng
    for uid in all_users:
        try:
            if photo_file_id:
                bot.send_photo(
                    chat_id=uid,
                    photo=photo_file_id,
                    caption=notify_caption,
                    parse_mode="HTML",
                    reply_markup=markup
                )
            else:
                bot.send_message(
                    chat_id=uid,
                    text=notify_caption,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                    reply_markup=markup
                )
            ok_users_count += 1
            time.sleep(0.1)
        except Exception as e:
            failed_count += 1
            failed_details.append(f"Người dùng ID: <code>{uid}</code> (Lỗi: {html_escape(str(e))})")
            logging.error(f"Failed to send notification to user {uid}: {e}")

    # Gửi tới tất cả nhóm
    for group in all_groups:
        group_id = group["id"]
        group_title = group.get("title", "Không rõ tên nhóm")
        group_username = group.get("username", "") # Có thể không có username
        
        try:
            if photo_file_id:
                bot.send_photo(
                    chat_id=group_id,
                    photo=photo_file_id,
                    caption=notify_caption,
                    parse_mode="HTML",
                    reply_markup=markup
                )
            else:
                bot.send_message(
                    chat_id=group_id,
                    text=notify_caption,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                    reply_markup=markup
                )
            ok_groups_count += 1
            time.sleep(0.1)
        except Exception as e:
            failed_count += 1
            group_display = f"{group_title} (ID: <code>{group_id}</code>)"
            if group_username:
                group_display += f" (@{group_username})"
            failed_details.append(f"Nhóm: {group_display} (Lỗi: {html_escape(str(e))})")
            logging.error(f"Failed to send notification to group {group_id}: {e}")

    total_sent = ok_users_count + ok_groups_count
    
    result_text = (
        f"✅ Gửi thành công: {total_sent} tin nhắn (Đến <b>{ok_users_count}</b> người dùng và <b>{ok_groups_count}</b> nhóm).\n"
        f"❌ Gửi thất bại: {failed_count} tin nhắn.\n\n"
    )

    if failed_count > 0:
        result_text += "<b>⚠️ Chi tiết thất bại:</b>\n"
        for detail in failed_details:
            result_text += f"- {detail}\n"
    else:
        result_text += "🎉 Tất cả thông báo đã được gửi thành công!"

    send_message_robustly(
        admin_id,
        text=result_text,
        parse_mode="HTML",
        reply_to_message_id=original_message_id # Reply về tin nhắn /noti gốc
    )


@bot.message_handler(commands=["phanhoi"])
@increment_interaction_count
def send_feedback_to_admin(message):
    """Xử lý lệnh /phanhoi, cho phép người dùng gửi phản hồi đến admin."""
    logging.info(f"Received /phanhoi from user {message.from_user.id} in chat {message.chat.id}") # Thêm log
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
        with feedback_messages_lock: # Bảo vệ truy cập bot.feedback_messages
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
    logging.info(f"Received /adminph from user {message.from_user.id} in chat {message.chat.id}") # Thêm log
    if message.from_user.id != ADMIN_ID:
        return send_message_robustly(message.chat.id, text="🚫 Bạn không có quyền sử dụng lệnh này.", parse_mode="HTML", reply_to_message_id=message.message_id)

    if not message.reply_to_message:
        return send_message_robustly(message.chat.id, text="⚠️ Bạn cần reply vào tin nhắn phản hồi của người dùng để sử dụng lệnh này.", parse_mode="HTML", reply_to_message_id=message.message_id)

    original_feedback_message_id = message.reply_to_message.message_id
    with feedback_messages_lock: # Bảo vệ truy cập bot.feedback_messages
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
    logging.info(f"Received /sever from user {message.from_user.id} in chat {message.chat.id}") # Thêm log
    if message.from_user.id != ADMIN_ID:
        return send_message_robustly(message.chat.id, text="🚫 Bạn không có quyền sử dụng lệnh này.", parse_mode="HTML", reply_to_message_id=message.message_id)
    
    with user_group_info_lock: # Đọc biến được bảo vệ
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
    logging.info(f"Received /mail10p from user {message.from_user.id} in chat {message.chat.id}") # Thêm log
    sync_chat_to_server(message.chat)
    user_id = message.chat.id
    
    # Kiểm tra xem người dùng đã có mail chưa và còn thời gian không
    with user_data_lock: # Bảo vệ truy cập user_data
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
                with mail_messages_state_lock: # Bảo vệ truy cập bot.mail_messages_state
                    if sent_msg:
                        bot.mail_messages_state[sent_msg.message_id] = {'chat_id': user_id, 'user_id': user_id, 'type': 'mail_info'}
                return
            else:
                # Nếu hết hạn nhưng chưa bị xóa, xóa nó đi
                del user_data[user_id]
                send_message_robustly(message.chat.id, "⏰ Mail 10 phút của bạn đã hết hạn, đang tạo mail mới...", parse_mode='Markdown', reply_to_message_id=message.message_id)


    email, pwd, token = create_temp_mail()

    if email:
        with user_data_lock: # Bảo vệ truy cập user_data
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
        with mail_messages_state_lock: # Bảo vệ truy cập bot.mail_messages_state
            if sent_msg:
                bot.mail_messages_state[sent_msg.message_id] = {'chat_id': user_id, 'user_id': user_id, 'type': 'mail_info'}
        
        threading.Thread(target=auto_delete_email, args=(user_id,)).start()
    else:
        send_message_robustly(message.chat.id, "❌ Không thể tạo email. Vui lòng thử lại sau!", parse_mode='Markdown', reply_to_message_id=message.message_id)


# Hàm xử lý lệnh /ping
@bot.message_handler(commands=['ping'])
def ping_command(message):
    start_time = time.time()
    
    # Gửi tin nhắn tạm thời để tính ping
    sent_message = bot.send_message(message.chat.id, "Đang Đo Ping Sever Bot...", parse_mode='HTML')
    
    end_time = time.time()
    
    # Tính toán ping (thời gian gửi và nhận tin nhắn)
    ping_ms = round((end_time - start_time) * 1000)

    # Tạo nội dung tin nhắn HTML
    html_message = f"""
<blockquote>
    <b>⚡ Ping Sever Bot hiện tại:</b> <i>{ping_ms}ms</i>
</blockquote>
"""
    # Tạo nút inline
    keyboard = types.InlineKeyboardMarkup()
    refresh_button = types.InlineKeyboardButton("♻️ Làm mới Ping", callback_data='refresh_ping')
    keyboard.add(refresh_button)

    # Chỉnh sửa tin nhắn ban đầu với thông tin ping và nút
    bot.edit_message_text(chat_id=message.chat.id, 
                          message_id=sent_message.message_id,
                          text=html_message, 
                          reply_markup=keyboard, 
                          parse_mode='HTML')

# Hàm xử lý khi nút "Làm mới Ping" được nhấn
@bot.callback_query_handler(func=lambda call: call.data == 'refresh_ping')
def refresh_ping_callback(call):
    # Báo hiệu đã nhận callback
    bot.answer_callback_query(call.id) 

    start_time = time.time()
    
    # Chỉnh sửa tin nhắn để hiển thị trạng thái "Đang làm mới"
    # Đây là một thao tác I/O, thời gian thực hiện có thể được dùng để ước lượng ping.
    bot.edit_message_text(chat_id=call.message.chat.id, 
                          message_id=call.message.message_id,
                          text="Đang làm mới ping...", 
                          parse_mode='HTML')

    end_time = time.time()
    
    ping_ms = round((end_time - start_time) * 1000)

    html_message = f"""
<blockquote>
    <b>⚡ Ping Sever Bot Hiện Tại hiện tại:</b> <i>{ping_ms}ms</i>
</blockquote>
"""
    keyboard = types.InlineKeyboardMarkup()
    refresh_button = types.InlineKeyboardButton("♻️ Làm mới Ping", callback_data='refresh_ping')
    keyboard.add(refresh_button)

    # Chỉnh sửa lại tin nhắn với thông tin ping mới và nút
    bot.edit_message_text(chat_id=call.message.chat.id, 
                          message_id=call.message.message_id,
                          text=html_message, 
                          reply_markup=keyboard, 
                          parse_mode='HTML')


# Lệnh mới để xóa mail 10 phút
@bot.message_handler(commands=['xoamail10p'])
@increment_interaction_count
def handle_xoamail10p(message):
    logging.info(f"Received /xoamail10p from user {message.from_user.id} in chat {message.chat.id}") # Thêm log
    sync_chat_to_server(message.chat)
    user_id = message.chat.id

    with user_data_lock: # Bảo vệ truy cập user_data
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
    with user_data_lock: # Bảo vệ truy cập user_data
        info = user_data.get(user_id)

    if not info:
        return "<i>❌ Bạn chưa tạo email. Gõ /mail10p để tạo nhé!</i>", None, 'HTML'

    # Kiểm tra xem mail đã hết hạn chưa
    elapsed_time = int(time.time() - info["created_at"])
    if elapsed_time >= 600: # 10 phút
        # Lấy thông tin email trước khi xóa
        expired_mail_address = info.get('email', 'không xác định') # Dùng 'email' thay vì 'address'

        with user_data_lock: # Bảo vệ truy cập user_data khi xóa
            del user_data[user_id]
        
        reply_text = (
            f"⏰ <b>Mail <code>{expired_mail_address}</code> của bạn đã hết hạn!</b> "
            f"<blockquote>Tất cả thư của mail này sẽ bị xóa.</blockquote> "
            f"Vui lòng tạo mail mới bằng lệnh /mail10p."
        )
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
    logging.info(f"Received /hopthu from user {message.from_user.id} in chat {message.chat.id}") # Thêm log
    sync_chat_to_server(message.chat)
    user_id = message.chat.id
    
    text, markup, parse_mode = _get_inbox_content(user_id)
    sent_msg = send_message_robustly(message.chat.id, 
                                   text=text, 
                                   parse_mode=parse_mode, 
                                   reply_markup=markup,
                                   reply_to_message_id=message.message_id)
    with mail_messages_state_lock: # Bảo vệ truy cập bot.mail_messages_state
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
        with code_snippets_lock: # Bảo vệ truy cập bot.code_snippets
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
    logging.info(f"Received /ask from user {message.from_user.id} in chat {message.chat.id}") # Thêm log
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
    
    with voice_map_lock: # Bảo vệ truy cập bot.voice_map
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
        
        combined_text_for_telegram = ""
        for part in response_parts_structured:
            if part["type"] == "text":
                combined_text_for_telegram += part["content"] + "\n\n" # Thêm xuống dòng giữa các đoạn văn bản
            elif part["type"] == "code":
                # Thêm nút copy code vào markup chính cho phần code đó
                copy_id = uuid.uuid4().hex[:8]
                with code_snippets_lock: # Bảo vệ truy cập bot.code_snippets
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

        # Gọi hàm xử lý lệnh /ask (được bọc bởi decorator @increment_interaction_count)
        # Chạy trong một luồng riêng để không chặn callback
        Thread(target=ask_command, args=(msg,)).start()

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

        with voice_map_lock: # Bảo vệ truy cập bot.voice_map
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
        with mail_messages_state_lock: # Bảo vệ truy cập bot.mail_messages_state
            # Cập nhật trạng thái tin nhắn
            if call.message.message_id in bot.mail_messages_state:
                bot.mail_messages_state[call.message.message_id]['type'] = 'inbox'
    except telebot.apihelper.ApiTelegramException as e:
        if "message is not modified" in str(e):
            logging.info(f"Message {call.message.message_id} in chat {call.message.chat.id} was not modified (inbox).")
        else:
            logging.error(f"Lỗi khi chỉnh sửa tin nhắn thành hộp thư cho user {user_id}: {e}")
            # Nếu edit không thành công, thử gửi tin nhắn mới
            send_message_robustly(call.message.chat.id, text=text, parse_mode=parse_mode, reply_markup=markup)
            # Xóa trạng thái cũ và thêm trạng thái mới
            with mail_messages_state_lock: # Bảo vệ truy cập bot.mail_messages_state
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
        with mail_messages_state_lock: # Bảo vệ truy cập bot.mail_messages_state
            # Cập nhật trạng thái tin nhắn
            if call.message.message_id in bot.mail_messages_state:
                bot.mail_messages_state[call.message.message_id]['type'] = 'inbox'
    except telebot.apihelper.ApiTelegramException as e:
        if "message is not modified" in str(e):
            logging.info(f"Message {call.message.message_id} in chat {call.message.chat.id} was not modified (refresh inbox).")
        else:
            logging.error(f"Lỗi khi làm mới hộp thư cho user {user_id}: {e}")
            send_message_robustly(call.message.chat.id, text=text, parse_mode=parse_mode, reply_markup=markup)
            with mail_messages_state_lock: # Bảo vệ truy cập bot.mail_messages_state
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

    with user_data_lock: # Bảo vệ truy cập user_data
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
            with user_data_lock: # Bảo vệ truy cập user_data khi xóa
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
        with mail_messages_state_lock: # Bảo vệ truy cập bot.mail_messages_state
            # Cập nhật trạng thái tin nhắn
            if call.message.message_id in bot.mail_messages_state:
                bot.mail_messages_state[call.message.message_id]['type'] = 'mail_info'
    except telebot.apihelper.ApiTelegramException as e:
        if "message is not modified" in str(e):
            logging.info(f"Message {call.message.message_id} in chat {call.message.chat.id} was not modified (back to mail info).")
        else:
            logging.error(f"Lỗi khi chỉnh sửa tin nhắn về thông tin mail cho user {user_id}: {e}")
            send_message_robustly(call.message.chat.id, text=text, parse_mode=parse_mode, reply_markup=markup)
            with mail_messages_state_lock: # Bảo vệ truy cập bot.mail_messages_state
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
        # Xử lý update trong một luồng riêng nếu bot được khởi tạo với threaded=True
        update = telebot.types.Update.de_json(request.data.decode("utf-8"))
        bot.process_new_updates([update]) # Khi threaded=True, mỗi update sẽ sinh ra một luồng riêng
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

