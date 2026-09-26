import asyncio
import json
import random
import string
import time
import warnings
import sqlite3
import uvicorn
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Message,
    WebAppInfo,
)
import httpx

# نادیده گرفتن هشدارهای مربوط به SSL
warnings.filterwarnings("ignore")

# --- تنظیمات اصلی ---
BOT_TOKEN = "8944001024:AAE0hHNAmR_NO86tmLFDKZ4H4maNn-X0X1U"

# 🆔 آیدی عددی ادمین‌های ربات
ADMIN_IDS = [8558013601, 1084709941]

# تنظیمات پنل مرزبان
MARZBAN_URL = ""
ADMIN_USERNAME = "sohrabmirzapour"
ADMIN_PASSWORD = "Sohrab1370@#$"

# تنظیمات درگاه پرداخت زرین‌پال
ZARINPAL_MERCHANT_ID = "YOUR-ZARINPAL-MERCHANT-ID-UUID"
ZARINPAL_SANDBOX = False

# مشخصات پیش‌فرض
PLAN_EXPIRE_DAYS = 30

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
app = FastAPI()


# --- راه‌اندازی و مدیریت دیتابیس دائمی SQLite ---
def init_db_sync():
    with sqlite3.connect("bot_database.db") as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                phone_number TEXT,
                language TEXT DEFAULT 'fa'
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_trades (
                telegram_id INTEGER PRIMARY KEY,
                balance REAL DEFAULT 1500.0,
                total_trades INTEGER DEFAULT 12,
                status TEXT DEFAULT 'active'
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS transactions (
                authority TEXT PRIMARY KEY,
                telegram_id INTEGER,
                gb INTEGER,
                price INTEGER
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS categories (
                category_key TEXT PRIMARY KEY,
                category_name TEXT
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT,
                gb INTEGER,
                price INTEGER,
                status INTEGER DEFAULT 1
            )
        ''')
        
        cursor.execute("SELECT COUNT(*) FROM categories")
        if cursor.fetchone()[0] == 0:
            default_categories = [
                ("single", "👤 تک کاربره"),
                ("multi", "👥 دو کاربره")
            ]
            cursor.executemany("INSERT INTO categories (category_key, category_name) VALUES (?, ?)", default_categories)
            conn.commit()

        cursor.execute("SELECT COUNT(*) FROM plans")
        if cursor.fetchone()[0] == 0:
            default_plans = [
                ("single", 20, 160000),
                ("single", 30, 240000),
                ("single", 50, 400000),
                ("multi", 60, 480000),
                ("multi", 80, 640000),
                ("multi", 100, 800000),
            ]
            cursor.executemany("INSERT INTO plans (category, gb, price, status) VALUES (?, ?, ?, 1)", default_plans)
            conn.commit()

def get_user_from_db_sync(telegram_id: int):
    with sqlite3.connect("bot_database.db") as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT phone_number, language FROM users WHERE telegram_id = ?", (telegram_id,))
        return cursor.fetchone()

def get_user_trade_sync(telegram_id: int):
    with sqlite3.connect("bot_database.db") as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT balance, total_trades FROM user_trades WHERE telegram_id = ?", (telegram_id,))
        row = cursor.fetchone()
        if row:
            return {"balance": row[0], "total_trades": row[1]}
        cursor.execute("INSERT OR IGNORE INTO user_trades (telegram_id, balance, total_trades) VALUES (?, 1500.0, 12)", (telegram_id,))
        conn.commit()
        return {"balance": 1500.0, "total_trades": 12}

def get_all_users_sync():
    with sqlite3.connect("bot_database.db") as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT telegram_id FROM users")
        return [row[0] for row in cursor.fetchall()]

def save_user_language_db_sync(telegram_id: int, lang: str):
    with sqlite3.connect("bot_database.db") as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO users (telegram_id, language) VALUES (?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET language = ?
        ''', (telegram_id, lang, lang))
        conn.commit()

def save_user_phone_db_sync(telegram_id: int, phone: str):
    with sqlite3.connect("bot_database.db") as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO users (telegram_id, phone_number) VALUES (?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET phone_number = ?
        ''', (telegram_id, phone, phone))
        conn.commit()

def save_transaction_sync(authority: str, telegram_id: int, gb: int, price: int):
    with sqlite3.connect("bot_database.db") as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM transactions WHERE telegram_id = ?", (telegram_id,))
        cursor.execute('''
            INSERT INTO transactions (authority, telegram_id, gb, price)
            VALUES (?, ?, ?, ?)
        ''', (authority, telegram_id, gb, price))
        conn.commit()

def get_transaction_by_user_sync(telegram_id: int):
    with sqlite3.connect("bot_database.db") as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT authority, gb, price FROM transactions WHERE telegram_id = ?", (telegram_id,))
        return cursor.fetchone()

def delete_transaction_sync(authority: str):
    with sqlite3.connect("bot_database.db") as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM transactions WHERE authority = ?", (authority,))
        conn.commit()

def get_all_categories_sync():
    with sqlite3.connect("bot_database.db") as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT category_key, category_name FROM categories")
        return cursor.fetchall()

def add_category_sync(cat_key: str, cat_name: str):
    with sqlite3.connect("bot_database.db") as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO categories (category_key, category_name) VALUES (?, ?)", (cat_key, cat_name))
        conn.commit()

def get_plans_sync(category: str):
    with sqlite3.connect("bot_database.db") as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, gb, price FROM plans WHERE category = ? AND status = 1", (category,))
        return cursor.fetchall()

def get_all_plans_sync():
    with sqlite3.connect("bot_database.db") as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, category, gb, price FROM plans WHERE status = 1")
        return cursor.fetchall()

def add_plan_sync(category: str, gb: int, price: int):
    with sqlite3.connect("bot_database.db") as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO plans (category, gb, price, status) VALUES (?, ?, ?, 1)", (category, gb, price))
        conn.commit()

def delete_plan_sync(plan_id: int):
    with sqlite3.connect("bot_database.db") as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM plans WHERE id = ?", (plan_id,))
        conn.commit()

def get_plan_by_id_sync(plan_id: int):
    with sqlite3.connect("bot_database.db") as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, category, gb, price FROM plans WHERE id = ?", (plan_id,))
        return cursor.fetchone()


# --- توابع واسط ناهمگام (Async Wrappers) ---
async def init_db():
    await asyncio.to_thread(init_db_sync)

async def get_user_from_db(telegram_id: int):
    return await asyncio.to_thread(get_user_from_db_sync, telegram_id)

async def get_user_trade(telegram_id: int):
    return await asyncio.to_thread(get_user_trade_sync, telegram_id)

async def get_all_users():
    return await asyncio.to_thread(get_all_users_sync)

async def save_user_language_db(telegram_id: int, lang: str):
    await asyncio.to_thread(save_user_language_db_sync, telegram_id, lang)

async def save_user_phone_db(telegram_id: int, phone: str):
    await asyncio.to_thread(save_user_phone_db_sync, telegram_id, phone)

async def save_transaction(authority: str, telegram_id: int, gb: int, price: int):
    await asyncio.to_thread(save_transaction_sync, authority, telegram_id, gb, price)

async def get_transaction_by_user(telegram_id: int):
    return await asyncio.to_thread(get_transaction_by_user_sync, telegram_id)

async def delete_transaction(authority: str):
    await asyncio.to_thread(delete_transaction_sync, authority)

async def get_all_categories():
    return await asyncio.to_thread(get_all_categories_sync)

async def add_category(cat_key: str, cat_name: str):
    await asyncio.to_thread(add_category_sync, cat_key, cat_name)

async def get_plans(category: str):
    return await asyncio.to_thread(get_plans_sync, category)

async def get_all_plans():
    return await asyncio.to_thread(get_all_plans_sync)

async def add_plan(category: str, gb: int, price: int):
    await asyncio.to_thread(add_plan_sync, category, gb, price)

async def delete_plan(plan_id: int):
    await asyncio.to_thread(delete_plan_sync, plan_id)

async def get_plan_by_id(plan_id: int):
    return await asyncio.to_thread(get_plan_by_id_sync, plan_id)


# --- سیستم ترجمه ---
TRANSLATIONS = {
    "fa": {
        "choose_lang": "🌐 لطفاً زبان خود را انتخاب کنید:\nLütfen dilinizi seçin:\nPlease choose your language:",
        "share_contact": "📱 تایید و اشتراک‌گذاری شماره تلفن",
        "welcome_auth": "👋 به ربات فروشگاهی خوش آمدید!\n\n🔒 برای استفاده از امکانات ربات و خرید اشتراک، لطفاً با زدن روی دکمه زیر، شماره تلفن خود را تایید کنید.",
        "wrong_contact": "❌ لطفاً از دکمه اشتراک‌گذاری شماره خودِ تلگرامتان استفاده کنید.",
        "contact_fallback": "⚠️ لطفاً حتماً از دکمه «تایید و اشتراک‌گذاری شماره تلفن» در پایین صفحه استفاده کنید.",
        "otp_sent": "📩 کد تایید احراز هویت به شماره `{phone}` ارسال شد.\n(برای تست، کد شما این است: **{otp}**)\n\nلطفاً کد ۴ رقمی دریافتی را در اینجا وارد کنید:",
        "wrong_otp": "❌ کد تایید اشتباه است. لطفاً دوباره کد صحیح را وارد کنید:",
        "auth_success": "✅ احراز هویت شما با موفقیت تایید شد!",
        "panel_title": "🌟 **به پنل کاربری خود خوش آمدید.**\n\nاز طریق دکمه زیر می‌توانید اقدام به خرید اشتراک کنید:",
        "btn_buy": "🛒 خرید اشتراک VPN",
        "btn_trade": "📈 بخش ترید",
        "btn_profile": "👤 حساب کاربری من",
        "btn_support": "📞 ارتباط با ما",
        "support_info": "📞 **راه‌های ارتباط با پشتیبانی:**\n\n🆔 ای دی تلگرام: `BannerDesigner1@`\n🆔 ای دی روبیکا: `mirzapour1991@`\n📱 شماره تماس: `09025741437`\n\n💬 همچنین می‌توانید با زدن روی دکمه زیر، مستقیماً به ادمین پیام ارسال کنید:",
        "btn_send_direct_msg": "✉️ ارسال پیام مستقیم به ادمین",
        "profile_info": "👤 **اطلاعات حساب کاربری:**\n\n🆔 آیدی تلگرام: `{id}`\n📱 شماره تلفن تایید شده: `{phone}`\n🌐 زبان: فارسی",
        "need_auth": "❌ ابتدا باید با دستور /start احراز هویت خود را انجام دهید.",
        "select_category": "📂 لطفاً دسته‌بندی اشتراک مورد نظر خود را انتخاب کنید:",
        "btn_custom_panel": "⚙️ پنل اختصاصی",
        "select_plan": "⚡️ لطفاً حجم و تعرفه اشتراک مورد نظر را انتخاب کنید:",
        "custom_panel_msg": "⚙️ **پنل اختصاصی:**\n\nبرای گرفتن پنل اختصاصی لطفاً به ادمین پیام دهید.",
        "btn_back": "⬅️ بازگشت",
        "invoice_title": "🛍 **فاکتور خرید اشتراک {gb} گیگابایتی**\n\nمبلغ قابل پرداخت: **{price} تومان**\n\n۱. روی دکمه پرداخت آنلاین بزنید و مبلغ را واریز کنید.\n۲. پس از پرداخت موفق، به ربات برگردید و روی «بررسی و تایید پرداخت» بزنید.",
        "btn_pay": "💳 پرداخت آنلاین",
        "btn_check": "🔄 بررسی و تایید پرداخت",
        "invoice_not_found": "❌ فاکتور معتبری یافت نشد. لطفاً مجدداً برای خرید اقدام کنید.",
        "checking_tx": "⏳ در حال بررسی تراکنش...",
        "editing_checking": "⏳ در حال بررسی وضعیت تراکنش از درگاه...",
        "verify_success": "✅ پرداخت شما با موفقیت تایید شد!\n⏳ در حال ساخت اشتراک در سرور...",
        "sub_success": "🎉 اشتراک شما با موفقیت ایجاد شد و آماده استفاده است!\n\n🔗 لینک سابسکرایب اختصاصی شما:\n`{url}`\n\nاین لینک را در اپلیکیشن (مثل v2rayNG) کپی کنید.",
        "sub_error": "⚠️ پرداخت شما تایید شد اما در ساخت اکانت روی سرور خطایی رخ داد. لطفاً به پشتیبانی پیام دهید.",
        "tx_not_verified": "❌ تراکنش پرداخت تایید نشد یا هنوز پرداخت انجام نشده است.\nاگر هزینه کسر شده است، چند دقیقه دیگر مجدداً روی دکمه بررسی بزنید.",
        "btn_recheck": "🔄 بررسی مجدد",
        "verify_network_error": "❌ خطای شبکه در بررسی تراکنش. لطفاً دوباره تلاش کنید.",
        "verify_unexpected_error": "❌ خطایی رخ داد. لطفاً دوباره تلاش کنید.",
        "desc": "خرید اشتراک VPN حجم {gb} گیگ برای کاربر {id}"
    }
}

async def get_text(telegram_id: int, key: str, **kwargs) -> str:
  user = await get_user_from_db(telegram_id)
  lang = user[1] if user and user[1] else "fa"
  text_template = TRANSLATIONS.get(lang, TRANSLATIONS["fa"]).get(key, TRANSLATIONS["fa"].get(key, ""))
  return text_template.format(**kwargs)


# --- ماشین‌های حالت (FSM) ---
class RegStates(StatesGroup):
  waiting_for_contact = State()
  waiting_for_otp = State()

class AdminAddCategoryStates(StatesGroup):
  waiting_for_key = State()
  waiting_for_name = State()

class AdminAddPlanStates(StatesGroup):
  waiting_for_category = State()
  waiting_for_gb = State()
  waiting_for_price = State()

class AdminBroadcastState(StatesGroup):
  waiting_for_broadcast_message = State()

class SupportStates(StatesGroup):
  waiting_for_user_message = State()

class AdminReplyState(StatesGroup):
  waiting_for_admin_reply = State()


if ZARINPAL_SANDBOX:
  ZARINPAL_REQUEST_URL = "https://sandbox.zarinpal.com/pg/v4/payment/request.json"
  ZARINPAL_VERIFY_URL = "https://sandbox.zarinpal.com/pg/v4/payment/verify.json"
  ZARINPAL_GATEWAY_URL = "https://sandbox.zarinpal.com/pg/StartPay/"
else:
  ZARINPAL_REQUEST_URL = "https://api.zarinpal.com/pg/v4/payment/request.json"
  ZARINPAL_VERIFY_URL = "https://api.zarinpal.com/pg/v4/payment/verify.json"
  ZARINPAL_GATEWAY_URL = "https://www.zarinpal.com/pg/StartPay/"


def generate_random_suffix(length=4):
  letters = string.ascii_lowercase + string.digits
  return "".join(random.choice(letters) for _ in range(length))


# --- ۱. توابع پنل مرزبان ---
async def get_admin_token() -> str | None:
  url = f"{MARZBAN_URL}/api/admin/token"
  data = {"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}
  async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
    try:
      response = await client.post(url, data=data)
      if response.status_code == 200:
        return response.json().get("access_token")
    except Exception as e:
      print(f"Marzban Auth Exception: {e}")
  return None


async def create_user_in_marzban(username: str, data_limit_gb: int, expire_days: int):
  token = await get_admin_token()
  if not token:
    return None

  url = f"{MARZBAN_URL}/api/user"
  headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
  data_limit_bytes = data_limit_gb * 1024 * 1024 * 1024
  expire_timestamp = int(time.time()) + (expire_days * 24 * 60 * 60)

  payload = {
      "username": username,
      "data_limit": data_limit_bytes,
      "expire": expire_timestamp,
      "data_limit_reset_strategy": "no_reset",
      "proxies": {"shadowsocks": {}},
      "inbounds": {"shadowsocks": ["Shadowsocks TCP"]},
  }

  async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
    try:
      response = await client.post(url, json=payload, headers=headers)
      if response.status_code == 200:
        return response.json().get("subscription_url")
    except Exception as e:
      print(f"Marzban Create User Exception: {e}")
  return None


# --- ۲. وب‌سرور FastAPI برای پنل ترید (HTML & CSS مدرن) ---
@app.get("/trade", response_class=HTMLResponse)
async def trade_dashboard(id: int = 0):
    user_data = await get_user_trade(id)
    
    html_content = f"""
    <!DOCTYPE html>
    <html lang="fa" dir="rtl">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>پنل معاملاتی - Mirzapour Trading</title>
        <script src="https://telegram.org/js/telegram-web-app.js"></script>
        <style>
            :root {{
                --bg-color: var(--tg-theme-bg-color, #0b0f19);
                --card-bg: var(--tg-theme-secondary-bg-color, #161f30);
                --text-color: var(--tg-theme-text-color, #f8fafc);
                --accent-color: #10b981;
                --accent-gradient: linear-gradient(135deg, #10b981 0%, #059669 100%);
                --danger-color: #ef4444;
            }}
            body {{
                font-family: system-ui, -apple-system, sans-serif;
                background-color: var(--bg-color);
                color: var(--text-color);
                margin: 0;
                padding: 16px;
                box-sizing: border-box;
            }}
            .header {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 20px;
                padding-bottom: 12px;
                border-bottom: 1px solid rgba(255,255,255,0.1);
            }}
            .header h2 {{
                margin: 0;
                font-size: 18px;
                color: #38bdf8;
            }}
            .badge {{
                background: rgba(16, 185, 129, 0.2);
                color: #34d399;
                padding: 4px 10px;
                border-radius: 20px;
                font-size: 12px;
                font-weight: bold;
            }}
            .grid {{
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 12px;
                margin-bottom: 16px;
            }}
            .card {{
                background: var(--card-bg);
                border-radius: 16px;
                padding: 16px;
                box-shadow: 0 4px 20px rgba(0,0,0,0.4);
                border: 1px solid rgba(255,255,255,0.05);
            }}
            .card.full {{
                grid-column: span 2;
            }}
            .card-title {{
                font-size: 13px;
                color: #94a3b8;
                margin-bottom: 8px;
            }}
            .card-value {{
                font-size: 18px;
                font-weight: bold;
            }}
            .btn {{
                background: var(--accent-gradient);
                color: white;
                border: none;
                width: 100%;
                padding: 14px;
                border-radius: 14px;
                font-size: 16px;
                font-weight: bold;
                cursor: pointer;
                box-shadow: 0 4px 14px rgba(16, 185, 129, 0.4);
                transition: transform 0.2s, opacity 0.2s;
                margin-top: 10px;
            }}
            .btn:active {{
                transform: scale(0.98);
                opacity: 0.9;
            }}
            .crypto-row {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                padding: 10px 0;
                border-bottom: 1px solid rgba(255,255,255,0.05);
                font-size: 14px;
            }}
            .crypto-row:last-child {{
                border-bottom: none;
            }}
            .up {{ color: #34d399; font-weight: bold; }}
            .down {{ color: #ef4444; font-weight: bold; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h2>Mirzapour Trading</h2>
            <div class="badge">● آنلاین (تست محلی)</div>
        </div>

        <div class="grid">
            <div class="card">
                <div class="card-title">موجودی حساب</div>
                <div class="card-value">{user_data['balance']} USDT</div>
            </div>
            <div class="card">
                <div class="card-title">کل معاملات</div>
                <div class="card-value">{user_data['total_trades']} ترید</div>
            </div>
        </div>

        <div class="card full" style="margin-bottom: 16px;">
            <div class="card-title" style="margin-bottom: 10px;">بازار زنده ارزهای دیجیتال</div>
            <div class="crypto-row">
                <span>BTC / USDT</span>
                <span class="up">64,250.00 $ (+2.4%)</span>
            </div>
            <div class="crypto-row">
                <span>ETH / USDT</span>
                <span class="up">3,480.10 $ (+1.8%)</span>
            </div>
            <div class="crypto-row">
                <span>SOL / USDT</span>
                <span class="down">142.50 $ (-0.5%)</span>
            </div>
        </div>

        <div class="card full">
            <div class="card-title">مدیریت سریع</div>
            <button class="btn" onclick="executeTrade()">ثبت پوزیشن معاملاتی جدید</button>
        </div>

        <script>
            let tg = window.Telegram.WebApp;
            tg.expand();

            function executeTrade() {{
                tg.HapticFeedback.impactOccurred('medium');
                alert("معامله شما با موفقیت در سیستم ثبت شد!");
            }}
        </script>
    </body>
    </html>
    """
    return html_content


# --- ۳. هندلر استارت و بخش‌های ربات ---
@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
  await state.clear()
  telegram_id = message.from_user.id
  
  with sqlite3.connect("bot_database.db") as conn:
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (telegram_id) VALUES (?)", (telegram_id,))
    conn.commit()

  user = await get_user_from_db(telegram_id)

  if telegram_id in ADMIN_IDS or (user and user[0]):
    await show_user_panel(message)
    return

  lang_keyboard = InlineKeyboardMarkup(
      inline_keyboard=[[
          InlineKeyboardButton(text="🇮🇷 فارسی", callback_data="lang_fa"),
          InlineKeyboardButton(text="🇬🇧 English", callback_data="lang_en"),
          InlineKeyboardButton(text="🇹🇷 Türkçe", callback_data="lang_tr")
      ]]
  )
  await message.answer("🌐 لطفاً زبان خود را انتخاب کنید:\nLütfen dilinizi seçin:\nPlease choose your language:", reply_markup=lang_keyboard)


@dp.callback_query(F.data.startswith("lang_"))
async def set_language(callback: CallbackQuery, state: FSMContext):
  telegram_id = callback.from_user.id
  await save_user_language_db(telegram_id, callback.data.split("_")[1])
  await callback.answer()

  contact_btn_text = await get_text(telegram_id, "share_contact")
  contact_keyboard = ReplyKeyboardMarkup(
      keyboard=[[KeyboardButton(text=contact_btn_text, request_contact=True)]],
      resize_keyboard=True,
      one_time_keyboard=True
  )
  try:
    await callback.message.edit_text("✅ Language selected")
  except Exception:
    pass

  await callback.message.answer(await get_text(telegram_id, "welcome_auth"), reply_markup=contact_keyboard)
  await state.set_state(RegStates.waiting_for_contact)


@dp.message(RegStates.waiting_for_contact, F.contact)
async def process_contact(message: Message, state: FSMContext):
  contact = message.contact
  telegram_id = message.from_user.id

  if contact.user_id != telegram_id:
    await message.answer(await get_text(telegram_id, "wrong_contact"))
    return

  phone_number = contact.phone_number
  otp_code = str(random.randint(1000, 9999))
  await state.update_data(phone_number=phone_number, otp_code=otp_code)

  await message.answer(
      await get_text(telegram_id, "otp_sent", phone=phone_number, otp=otp_code), 
      reply_markup=ReplyKeyboardRemove(), 
      parse_mode="Markdown"
  )
  await state.set_state(RegStates.waiting_for_otp)


@dp.message(RegStates.waiting_for_contact)
async def process_contact_fallback(message: Message):
  telegram_id = message.from_user.id
  contact_btn_text = await get_text(telegram_id, "share_contact")
  contact_keyboard = ReplyKeyboardMarkup(
      keyboard=[[KeyboardButton(text=contact_btn_text, request_contact=True)]],
      resize_keyboard=True,
      one_time_keyboard=True
  )
  await message.answer(await get_text(telegram_id, "contact_fallback"), reply_markup=contact_keyboard)


@dp.message(RegStates.waiting_for_otp)
async def process_otp(message: Message, state: FSMContext):
  user_input = message.text.strip() if message.text else ""
  data = await state.get_data()
  if user_input == data.get("otp_code"):
    await save_user_phone_db(message.from_user.id, data.get("phone_number"))
    await state.clear()
    await message.answer(await get_text(message.from_user.id, "auth_success"), reply_markup=ReplyKeyboardRemove())
    await show_user_panel(message)
  else:
    await message.answer(await get_text(message.from_user.id, "wrong_otp"))


async def show_user_panel(message: Message):
  telegram_id = message.from_user.id
  keyboard_buttons = [
      [InlineKeyboardButton(text=await get_text(telegram_id, "btn_buy"), callback_data="start_buy")],
      [InlineKeyboardButton(text=await get_text(telegram_id, "btn_trade"), callback_data="trade_menu")],
      [InlineKeyboardButton(text=await get_text(telegram_id, "btn_profile"), callback_data="profile"),
       InlineKeyboardButton(text=await get_text(telegram_id, "btn_support"), callback_data="support")]
  ]

  if telegram_id in ADMIN_IDS:
    keyboard_buttons.insert(0, [InlineKeyboardButton(text="⚙️ مدیریت بخش‌ها و پلن‌ها (ادمین)", callback_data="admin_plans_menu")])
    keyboard_buttons.insert(1, [InlineKeyboardButton(text="📢 ارسال پیام همگانی (بروزرسانی)", callback_data="admin_broadcast_start")])

  keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
  await message.answer(await get_text(telegram_id, "panel_title"), reply_markup=keyboard, parse_mode="Markdown")


async def render_admin_menu(message: Message, edit: bool = False):
  plans = await get_all_plans()
  categories = await get_all_categories()
  
  text = "⚙️ **پنل مدیریت پیشرفته ربات:**\n\n📁 **دسته‌بندی‌های فعال:**\n"
  for c_key, c_name in categories:
    text += f"▪️ `{c_key}` ➔ {c_name}\n"

  text += "\n📦 **لیست پلن‌ها:**\n"
  keyboard_buttons = []
  
  for p_id, cat, gb, price in plans:
    text += f"ID: {p_id} | دسته: {cat} | {gb} گیگ | {price:,} تومان\n"
    keyboard_buttons.append([InlineKeyboardButton(text=f"❌ حذف پلن {gb} گیگ ({cat})", callback_data=f"del_plan_{p_id}")])

  keyboard_buttons.append([InlineKeyboardButton(text="➕ افزودن دسته‌بندی (بخش) جدید", callback_data="add_cat_start")])
  keyboard_buttons.append([InlineKeyboardButton(text="➕ افزودن پلن جدید", callback_data="add_plan_start")])
  keyboard_buttons.append([InlineKeyboardButton(text="⬅️ بازگشت به پنل", callback_data="back_to_main")])
  
  keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
  if edit:
    try:
      await message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    except Exception:
      await message.answer(text, reply_markup=keyboard, parse_mode="Markdown")
  else:
    await message.answer(text, reply_markup=keyboard, parse_mode="Markdown")


@dp.callback_query(F.data == "admin_plans_menu")
async def admin_plans_menu(callback: CallbackQuery, state: FSMContext):
  if callback.from_user.id not in ADMIN_IDS:
    await callback.answer("❌ دسترسی غیرمجاز!", show_alert=True)
    return
  await state.clear()
  await callback.answer()
  await render_admin_menu(callback.message, edit=True)


@dp.callback_query(F.data == "back_to_main")
async def back_to_main_panel(callback: CallbackQuery):
  await callback.answer()
  await show_user_panel(callback.message)


@dp.callback_query(F.data == "admin_broadcast_start")
async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext):
  if callback.from_user.id not in ADMIN_IDS:
    return
  await callback.answer()
  keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ انصراف", callback_data="back_to_main")]])
  await callback.message.edit_text("📢 **ارسال پیام همگانی**\n\nپیام خود را بفرستید (متن، عکس و...):", reply_markup=keyboard)
  await state.set_state(AdminBroadcastState.waiting_for_broadcast_message)


@dp.message(AdminBroadcastState.waiting_for_broadcast_message)
async def admin_broadcast_send(message: Message, state: FSMContext):
  if message.from_user.id not in ADMIN_IDS:
    return
  await state.clear()
  users = await get_all_users()
  status_msg = await message.answer(f"⏳ در حال ارسال به {len(users)} کاربر...")
  
  success, fail = 0, 0
  for uid in users:
    try:
      await message.send_copy(chat_id=uid)
      success += 1
      await asyncio.sleep(0.05)
    except Exception:
      fail += 1

  await status_msg.edit_text(f"✅ ارسال تمام شد!\nموفق: {success}\nناموفق: {fail}")
  await show_user_panel(message)


@dp.callback_query(F.data == "support")
async def user_support(callback: CallbackQuery):
  telegram_id = callback.from_user.id
  await callback.answer()
  
  keyboard = InlineKeyboardMarkup(inline_keyboard=[
      [InlineKeyboardButton(text=await get_text(telegram_id, "btn_send_direct_msg"), callback_data="start_direct_support")],
      [InlineKeyboardButton(text="⬅️ بازگشت", callback_data="back_to_main")]
  ])
  
  support_text = await get_text(telegram_id, "support_info")
  try:
    await callback.message.edit_text(support_text, reply_markup=keyboard, parse_mode="Markdown")
  except Exception:
    await callback.message.answer(support_text, reply_markup=keyboard, parse_mode="Markdown")


@dp.callback_query(F.data == "start_direct_support")
async def start_direct_support(callback: CallbackQuery, state: FSMContext):
  await callback.answer()
  keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ انصراف", callback_data="support")]])
  await callback.message.edit_text(
      "✉️ **ارسال پیام مستقیم به ادمین:**\n\nلطفاً پیام خود را ارسال کنید:",
      reply_markup=keyboard
  )
  await state.set_state(SupportStates.waiting_for_user_message)


@dp.message(SupportStates.waiting_for_user_message)
async def receive_user_support_message(message: Message, state: FSMContext):
  telegram_id = message.from_user.id
  user_full_name = message.from_user.full_name or "کاربر"
  username = f"@{message.from_user.username}" if message.from_user.username else "ندارد"
  await state.clear()
  
  for admin_id in ADMIN_IDS:
    try:
      forward_caption = f"📩 **پیام جدید از طرف کاربر:**\n\n👤 نام: {user_full_name}\n🆔 آیدی: `{telegram_id}`\n🔗 یوزرنیم: {username}"
      await message.send_copy(chat_id=admin_id)
      reply_keyboard = InlineKeyboardMarkup(inline_keyboard=[
          [InlineKeyboardButton(text="💬 پاسخ به این کاربر", callback_data=f"admin_reply_{telegram_id}")]
      ])
      await bot.send_message(chat_id=admin_id, text=forward_caption, reply_markup=reply_keyboard, parse_mode="Markdown")
    except Exception as e:
      print(f"Error forwarding: {e}")

  await message.answer("✅ پیام شما به پشتیبانی ارسال شد.")
  await show_user_panel(message)


@dp.callback_query(F.data.startswith("admin_reply_"))
async def admin_click_reply(callback: CallbackQuery, state: FSMContext):
  if callback.from_user.id not in ADMIN_IDS:
    return
  target_user_id = int(callback.data.split("_")[2])
  await state.update_data(target_user_id=target_user_id)
  await callback.answer()
  keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ انصراف", callback_data="back_to_main")]])
  await callback.message.answer(f"✏️ پاسخ خود را برای کاربر (`{target_user_id}`) ارسال کنید:", reply_markup=keyboard, parse_mode="Markdown")
  await state.set_state(AdminReplyState.waiting_for_admin_reply)


@dp.message(AdminReplyState.waiting_for_admin_reply)
async def send_admin_reply_to_user(message: Message, state: FSMContext):
  if message.from_user.id not in ADMIN_IDS:
    return
  data = await state.get_data()
  target_user_id = data.get("target_user_id")
  await state.clear()
  if target_user_id:
    try:
      await bot.send_message(chat_id=target_user_id, text="💬 **پاسخ پشتیبانی:**", parse_mode="Markdown")
      await message.send_copy(chat_id=target_user_id)
      await message.answer("✅ پاسخ ارسال شد.")
    except Exception as e:
      await message.answer(f"❌ خطا: {e}")
  await show_user_panel(message)


@dp.callback_query(F.data == "add_cat_start")
async def admin_add_cat_start(callback: CallbackQuery, state: FSMContext):
  if callback.from_user.id not in ADMIN_IDS:
    return
  await callback.answer()
  await callback.message.edit_text("📂 کلید لاتین دسته‌بندی را وارد کنید:")
  await state.set_state(AdminAddCategoryStates.waiting_for_key)


@dp.message(AdminAddCategoryStates.waiting_for_key)
async def admin_add_cat_key(message: Message, state: FSMContext):
  await state.update_data(cat_key=message.text.strip().lower())
  await message.answer("🏷 نام نمایشی دسته‌بندی را وارد کنید:")
  await state.set_state(AdminAddCategoryStates.waiting_for_name)


@dp.message(AdminAddCategoryStates.waiting_for_name)
async def admin_add_cat_name(message: Message, state: FSMContext):
  data = await state.get_data()
  await add_category(data.get("cat_key"), message.text.strip())
  await state.clear()
  await message.answer("✅ دسته‌بندی افزوده شد!")
  await render_admin_menu(message, edit=False)


@dp.callback_query(F.data.startswith("del_plan_"))
async def admin_delete_plan(callback: CallbackQuery):
  if callback.from_user.id not in ADMIN_IDS:
    return
  await delete_plan(int(callback.data.split("_")[2]))
  await callback.answer("✅ پلن حذف شد!", show_alert=True)
  await render_admin_menu(callback.message, edit=True)


@dp.callback_query(F.data == "add_plan_start")
async def admin_add_plan_start(callback: CallbackQuery, state: FSMContext):
  if callback.from_user.id not in ADMIN_IDS:
    return
  await callback.answer()
  categories = await get_all_categories()
  keyboard_buttons = [[InlineKeyboardButton(text=name, callback_data=f"set_cat_{key}")] for key, name in categories]
  keyboard_buttons.append([InlineKeyboardButton(text="⬅️ بازگشت", callback_data="admin_plans_menu")])
  await callback.message.edit_text("📂 دسته‌بندی را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_buttons))
  await state.set_state(AdminAddPlanStates.waiting_for_category)


@dp.callback_query(AdminAddPlanStates.waiting_for_category, F.data.startswith("set_cat_"))
async def admin_add_plan_cat(callback: CallbackQuery, state: FSMContext):
  await state.update_data(category=callback.data.split("_")[2])
  await callback.answer()
  await callback.message.edit_text("⚡️ حجم پلن (گیگ):")
  await state.set_state(AdminAddPlanStates.waiting_for_gb)


@dp.message(AdminAddPlanStates.waiting_for_gb)
async def admin_add_plan_gb(message: Message, state: FSMContext):
  try:
    await state.update_data(gb=int(message.text.strip()))
    await message.answer("💵 قیمت (تومان):")
    await state.set_state(AdminAddPlanStates.waiting_for_price)
  except ValueError:
    await message.answer("❌ فقط عدد وارد کنید:")


@dp.message(AdminAddPlanStates.waiting_for_price)
async def admin_add_plan_price(message: Message, state: FSMContext):
  try:
    price = int(message.text.strip())
    data = await state.get_data()
    await add_plan(data.get("category"), data.get("gb"), price)
    await state.clear()
    await message.answer("✅ پلن ثبت شد!")
    await render_admin_menu(message, edit=False)
  except ValueError:
    await message.answer("❌ فقط عدد وارد کنید:")


@dp.callback_query(F.data == "profile")
async def user_profile(callback: CallbackQuery):
  telegram_id = callback.from_user.id
  user = await get_user_from_db(telegram_id)
  phone = user[0] if user and user[0] else ("مدیر سیستم (ادمین)" if telegram_id in ADMIN_IDS else "ثبت نشده")
  await callback.answer()
  await callback.message.answer(await get_text(telegram_id, "profile_info", id=telegram_id, phone=phone), parse_mode="Markdown")


# 👈 بروزرسانی شده برای تست محلی (در صورت نیاز به استفاده از ngrok یا آی‌پی معتبر عوض شود)
@dp.callback_query(F.data == "trade_menu")
async def trade_section(callback: CallbackQuery):
  telegram_id = callback.from_user.id
  await callback.answer()
  
  # نکته: برای تست محلی، اینجا باید لینک ngrok خودتان را بگذارید (مثال: https://xxxx.ngrok-free.app/trade?id=...)
  # یا اگر روی هاست تست می‌کنید دامنه خود را قرار دهید.
  trade_web_url = f"https://mirzapourtrading.com/trade?id={telegram_id}"
  
  keyboard = InlineKeyboardMarkup(
      inline_keyboard=[
          [InlineKeyboardButton(text="📈 ورود به پنل معاملاتی مدرن", web_app=WebAppInfo(url=trade_web_url))],
          [InlineKeyboardButton(text="⬅️ بازگشت به پنل", callback_data="back_to_main")]
      ]
  )
  
  text = (
      "📈 **بخش تخصصی ترید و سرمایه‌گذاری**\n\n"
      "پلتفرم معاملاتی شما آماده است. روی دکمه زیر بزنید تا پنل ترید "
      "با ظاهر شیک و مدرن مستقیماً برایتان باز شود."
  )
  
  try:
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
  except Exception:
    await callback.message.answer(text, reply_markup=keyboard, parse_mode="Markdown")


@dp.message(Command("buy"))
async def cmd_buy_message(message: Message, state: FSMContext):
  await state.clear()
  await show_buy_categories(message, message.from_user.id)

@dp.callback_query(F.data == "start_buy")
async def cmd_buy_callback(callback: CallbackQuery, state: FSMContext):
  await state.clear()
  await callback.answer()
  await show_buy_categories(callback.message, callback.from_user.id, edit=True)


async def show_buy_categories(message: Message, telegram_id: int, edit: bool = False):
  user = await get_user_from_db(telegram_id)
  if telegram_id not in ADMIN_IDS and (not user or not user[0]):
    await message.answer(await get_text(telegram_id, "need_auth"))
    return

  categories = await get_all_categories()
  keyboard_buttons = [[InlineKeyboardButton(text=name, callback_data=f"cat_{key}")] for key, name in categories]
  keyboard_buttons.append([InlineKeyboardButton(text=await get_text(telegram_id, "btn_custom_panel"), callback_data="cat_custom")])
  keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
  text = await get_text(telegram_id, "select_category")
  if edit:
    try:
      await message.edit_text(text, reply_markup=keyboard)
    except Exception:
      await message.answer(text, reply_markup=keyboard)
  else:
    await message.answer(text, reply_markup=keyboard)


@dp.callback_query(F.data.startswith("cat_") & (F.data != "cat_custom"))
async def select_category_plans(callback: CallbackQuery):
  telegram_id = callback.from_user.id
  plans = await get_plans(callback.data.split("_")[1])
  keyboard_buttons = [[InlineKeyboardButton(text=f"{gb} گیگابایت - {price:,} تومان", callback_data=f"buyplan_{pid}")] for pid, gb, price in plans]
  keyboard_buttons.append([InlineKeyboardButton(text=await get_text(telegram_id, "btn_back"), callback_data="start_buy")])
  try:
    await callback.message.edit_text(await get_text(telegram_id, "select_plan"), reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_buttons))
  except Exception:
    pass


@dp.callback_query(F.data == "cat_custom")
async def custom_panel_info(callback: CallbackQuery):
  telegram_id = callback.from_user.id
  keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=await get_text(telegram_id, "btn_back"), callback_data="start_buy")]])
  try:
    await callback.message.edit_text(await get_text(telegram_id, "custom_panel_msg"), reply_markup=keyboard, parse_mode="Markdown")
  except Exception:
    pass


@dp.callback_query(F.data.startswith("buyplan_"))
async def process_selected_plan_db(callback: CallbackQuery):
  telegram_id = callback.from_user.id
  user = await get_user_from_db(telegram_id)
  if telegram_id not in ADMIN_IDS and (not user or not user[0]):
    await callback.answer(await get_text(telegram_id, "need_auth"), show_alert=True)
    return

  plan = await get_plan_by_id(int(callback.data.split("_")[1]))
  if not plan:
    await callback.answer("❌ پلن نامعتبر است.", show_alert=True)
    return

  _, category, gb_size, calculated_price = plan

  if telegram_id in ADMIN_IDS:
    await callback.answer("⚙️ ساخت اشتراک رایگان ادمین...", show_alert=False)
    try:
      await callback.message.edit_text("⏳ در حال ساخت اشتراک...")
    except Exception:
      pass

    sub_url = await create_user_in_marzban(f"admin_{telegram_id}_{generate_random_suffix()}", data_limit_gb=gb_size, expire_days=PLAN_EXPIRE_DAYS)
    if sub_url:
      await callback.message.answer(await get_text(telegram_id, "sub_success", url=sub_url), parse_mode="Markdown")
    else:
      await callback.message.answer(await get_text(telegram_id, "sub_error"))
    return

  await callback.answer("⏳ در حال اتصال به درگاه پرداخت...")
  payload = {
      "merchant_id": ZARINPAL_MERCHANT_ID,
      "amount": calculated_price * 10,
      "callback_url": "https://mirzapourtrading.com",
      "description": await get_text(telegram_id, "desc", gb=gb_size, id=telegram_id),
  }

  async with httpx.AsyncClient(timeout=10.0) as client:
    try:
      response = await client.post(ZARINPAL_REQUEST_URL, json=payload)
      res_json = response.json()
      res_data = res_json.get("data") or {}

      if response.status_code == 200 and res_data.get("code") == 100:
        authority = res_data.get("authority")
        await save_transaction(authority, telegram_id, gb_size, calculated_price)
        pay_url = f"{ZARINPAL_GATEWAY_URL}{authority}"

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=await get_text(telegram_id, "btn_pay"), url=pay_url)],
                [InlineKeyboardButton(text=await get_text(telegram_id, "btn_check"), callback_data="check_payment")],
            ]
        )
        await callback.message.edit_text(await get_text(telegram_id, "invoice_title", gb=gb_size, price=f"{calculated_price:,}"), reply_markup=keyboard)
      else:
        await callback.message.answer("❌ خطا در اتصال به درگاه پرداخت.")
    except Exception as e:
      print(f"Zarinpal Error: {e}")
      await callback.message.answer("❌ خطای شبکه در اتصال به درگاه.")


@dp.callback_query(F.data == "check_payment")
async def verify_payment(callback: CallbackQuery):
  telegram_id = callback.from_user.id
  tx = await get_transaction_by_user(telegram_id)

  if not tx:
    await callback.answer(await get_text(telegram_id, "invoice_not_found"), show_alert=True)
    return

  authority, gb_size, price = tx
  await callback.answer(await get_text(telegram_id, "checking_tx"))
  try:
    await callback.message.edit_text(await get_text(telegram_id, "editing_checking"))
  except Exception:
    pass

  payload = {"merchant_id": ZARINPAL_MERCHANT_ID, "amount": price * 10, "authority": authority}

  async with httpx.AsyncClient(timeout=10.0) as client:
    try:
      response = await client.post(ZARINPAL_VERIFY_URL, json=payload)
      res_json = response.json()
      res_data = res_json.get("data") or {}

      if response.status_code == 200 and res_data.get("code") in [100, 101]:
        await delete_transaction(authority)
        try:
          await callback.message.edit_text(await get_text(telegram_id, "verify_success"))
        except Exception:
          pass

        sub_url = await create_user_in_marzban(f"u{telegram_id}_{generate_random_suffix()}", data_limit_gb=gb_size, expire_days=PLAN_EXPIRE_DAYS)
        if sub_url:
          await callback.message.answer(await get_text(telegram_id, "sub_success", url=sub_url), parse_mode="Markdown")
        else:
          await callback.message.answer(await get_text(telegram_id, "sub_error"))
      else:
        await callback.message.edit_text(
            await get_text(telegram_id, "tx_not_verified"),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=await get_text(telegram_id, "btn_recheck"), callback_data="check_payment")]]
            ),
        )
    except Exception as e:
      print(f"Verify Error: {e}")
      await callback.message.answer(await get_text(telegram_id, "verify_network_error"))


# --- اجرای هم‌زمان ربات تلگرام و سرور وب (FastAPI + Aiogram) ---
async def run_fastapi():
    config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()

async def main():
  await init_db()
  print("🚀 Bot and Modern Trading Web App are running simultaneously...")
  await asyncio.gather(
      dp.start_polling(bot),
      run_fastapi()
  )

if __name__ == "__main__":
  asyncio.run(main())
