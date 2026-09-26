import asyncio
import json
import random
import string
import time
import warnings
import sqlite3
import os
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
BOT_TOKEN = "8944001024:AAGovByEWnwot5gFmrOmMV-xqhMSzxn2PnE"

# 🆔 آیدی عددی ادمین‌های ربات
ADMIN_IDS = [8558013601, 1084709941]

# تنظیمات پنل مرزبان
MARZBAN_URL = "https://mirzapourtrading.com"
ADMIN_USERNAME = "tttttttgggg"
ADMIN_PASSWORD = "000000p0"

# 🔗 آدرس پایه وب‌سایت شما روی Render
WEBAPP_BASE_URL = "https://mirzapour-bot.onrender.com"

# تنظیمات درگاه پرداخت زرین‌پال
ZARINPAL_MERCHANT_ID = "YOUR-ZARINPAL-MERCHANT-ID-UUID"
ZARINPAL_SANDBOX = False

# مشخصات پیش‌فرض
PLAN_EXPIRE_DAYS = 30
TRADE_SUBSCRIPTION_PRICE = 150000  # هزینه اشتراک بخش ترید (تومان)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
app = FastAPI()


# --- راه‌اندازی و مدیریت دیتابیس دائمی SQLite با حالت WAL ---
def init_db_sync():
    with sqlite3.connect("bot_database.db", timeout=30.0) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
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
            CREATE TABLE IF NOT EXISTS trade_licenses (
                telegram_id INTEGER PRIMARY KEY,
                license_key TEXT UNIQUE,
                is_paid INTEGER DEFAULT 0,
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
            CREATE TABLE IF NOT EXISTS trade_transactions (
                authority TEXT PRIMARY KEY,
                telegram_id INTEGER,
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
    with sqlite3.connect("bot_database.db", timeout=30.0) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT phone_number, language FROM users WHERE telegram_id = ?", (telegram_id,))
        return cursor.fetchone()

def get_user_trade_sync(telegram_id: int):
    with sqlite3.connect("bot_database.db", timeout=30.0) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT balance, total_trades FROM user_trades WHERE telegram_id = ?", (telegram_id,))
        row = cursor.fetchone()
        if row:
            return {"balance": row[0], "total_trades": row[1]}
        cursor.execute("INSERT OR IGNORE INTO user_trades (telegram_id, balance, total_trades) VALUES (?, 1500.0, 12)", (telegram_id,))
        conn.commit()
        return {"balance": 1500.0, "total_trades": 12}

def get_trade_license_sync(telegram_id: int):
    with sqlite3.connect("bot_database.db", timeout=30.0) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT license_key, is_paid, status FROM trade_licenses WHERE telegram_id = ?", (telegram_id,))
        return cursor.fetchone()

def create_trade_license_sync(telegram_id: int):
    with sqlite3.connect("bot_database.db", timeout=30.0) as conn:
        cursor = conn.cursor()
        license_key = f"MP-TRD-{telegram_id}-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
        cursor.execute('''
            INSERT INTO trade_licenses (telegram_id, license_key, is_paid, status)
            VALUES (?, ?, 1, 'active')
            ON CONFLICT(telegram_id) DO UPDATE SET is_paid = 1, status = 'active', license_key = ?
        ''', (telegram_id, license_key, license_key))
        conn.commit()
        return license_key

def get_all_users_sync():
    with sqlite3.connect("bot_database.db", timeout=30.0) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT telegram_id FROM users")
        return [row[0] for row in cursor.fetchall()]

def save_user_language_db_sync(telegram_id: int, lang: str):
    with sqlite3.connect("bot_database.db", timeout=30.0) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO users (telegram_id, language) VALUES (?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET language = ?
        ''', (telegram_id, lang, lang))
        conn.commit()

def save_user_phone_db_sync(telegram_id: int, phone: str):
    with sqlite3.connect("bot_database.db", timeout=30.0) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO users (telegram_id, phone_number) VALUES (?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET phone_number = ?
        ''', (telegram_id, phone, phone))
        conn.commit()

def get_all_categories_sync():
    with sqlite3.connect("bot_database.db", timeout=30.0) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT category_key, category_name FROM categories")
        return cursor.fetchall()

def get_plans_sync(category: str):
    with sqlite3.connect("bot_database.db", timeout=30.0) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, gb, price FROM plans WHERE category = ? AND status = 1", (category,))
        return cursor.fetchall()

def get_plan_by_id_sync(plan_id: int):
    with sqlite3.connect("bot_database.db", timeout=30.0) as conn:
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

async def get_trade_license(telegram_id: int):
    return await asyncio.to_thread(get_trade_license_sync, telegram_id)

async def create_trade_license(telegram_id: int):
    return await asyncio.to_thread(create_trade_license_sync, telegram_id)

async def save_user_language_db(telegram_id: int, lang: str):
    await asyncio.to_thread(save_user_language_db_sync, telegram_id, lang)

async def save_user_phone_db(telegram_id: int, phone: str):
    await asyncio.to_thread(save_user_phone_db_sync, telegram_id, phone)

async def get_all_categories():
    return await asyncio.to_thread(get_all_categories_sync)

async def get_plans(category: str):
    return await asyncio.to_thread(get_plans_sync, category)

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
        "support_info": "📞 **راه‌های ارتباط با پشتیبانی:**\n\n🆔 ای دی تلگرام: `BannerDesigner1@`\n🆔 ای دی روبیکا: `mirzapour1991@`\n📱 شماره تماس: `09025741437`",
        "need_auth": "❌ ابتدا باید با دستور /start احراز هویت خود را انجام دهید.",
        "select_category": "📂 لطفاً دسته‌بندی اشتراک مورد نظر خود را انتخاب کنید:",
        "btn_custom_panel": "⚙️ پنل اختصاصی",
        "select_plan": "⚡️ لطفاً حجم و تعرفه اشتراک مورد نظر را انتخاب کنید:",
        "custom_panel_msg": "⚙️ **پنل اختصاصی:**\n\nبرای گرفتن پنل اختصاصی لطفاً به ادمین پیام دهید.",
        "btn_back": "⬅️ بازگشت",
        "sub_success": "🎉 اشتراک شما با موفقیت ایجاد شد و آماده استفاده است!\n\n🔗 لینک سابسکرایب اختصاصی شما:\n`{url}`\n\nاین لینک را در اپلیکیشن (مثل v2rayNG) کپی کنید.",
        "sub_error": "⚠️ در ساخت اکانت روی سرور خطایی رخ داد. لطفاً به پشتیبانی پیام دهید.",
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


# --- ۲. وب‌سرور FastAPI برای پنل ترید و بک‌تست حرفه‌ای ---
@app.get("/trade", response_class=HTMLResponse)
async def trade_dashboard(id: int = 0, license: str = ""):
    trade_lic = await get_trade_license(id)
    if not trade_lic or trade_lic[2] == 'blocked' or trade_lic[1] != 1 or trade_lic[0] != license:
        return """
        <!DOCTYPE html>
        <html lang="fa" dir="rtl">
        <head><meta charset="UTF-8"><title>خطای دسترسی</title></head>
        <body style="background:#0b0f19;color:#fff;font-family:sans-serif;text-align:center;padding-top:50px;">
            <h2 style="color:#ef4444;">❌ دسترسی غیرمجاز یا مسدود شده!</h2>
            <p>این کد لایسنس معتبر نیست، به کاربر دیگری تعلق دارد یا اکانت شما مسدود شده است.</p>
        </body>
        </html>
        """
    user_data = await get_user_trade(id)
    return f"""
    <!DOCTYPE html>
    <html lang="fa" dir="rtl">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>پنل معاملاتی - Mirzapour Trading</title>
        <script src="https://telegram.org/js/telegram-web-app.js"></script>
        <style>
            body {{ font-family: sans-serif; background-color: #0b0f19; color: #f8fafc; margin: 0; padding: 16px; }}
            .card {{ background: #161f30; border-radius: 16px; padding: 16px; margin-bottom: 12px; }}
            .btn {{ background: #10b981; color: white; border: none; width: 100%; padding: 14px; border-radius: 14px; font-weight: bold; cursor: pointer; }}
        </style>
    </head>
    <body>
        <h2>Mirzapour Trading</h2>
        <div class="card">موجودی: {user_data['balance']} USDT</div>
        <div class="card"><button class="btn" onclick="alert('پوزیشن ثبت شد!')">ثبت پوزیشن جدید</button></div>
    </body>
    </html>
    """


@app.get("/backtest", response_class=HTMLResponse)
async def backtest_dashboard(id: int = 0, license: str = ""):
    trade_lic = await get_trade_license(id)
    if not trade_lic or trade_lic[2] == 'blocked' or trade_lic[1] != 1 or trade_lic[0] != license:
        return "<h3>دسترسی غیرمجاز</h3>"
    return "<h3>بک تست فعال است</h3>"


# --- تابع کمکی نمایش پنل (اصلاح‌شده برای جلوگیری از باگ آیدی بات) ---
async def show_user_panel(event, edit=False):
  if isinstance(event, CallbackQuery):
    telegram_id = event.from_user.id
    message = event.message
  else:
    telegram_id = event.from_user.id
    message = event

  keyboard_buttons = [
      [InlineKeyboardButton(text="🛒 خرید اشتراک VPN", callback_data="start_buy")],
      [InlineKeyboardButton(text="📈 بخش ترید", callback_data="trade_menu")],
      [InlineKeyboardButton(text="👤 حساب کاربری من", callback_data="profile"),
       InlineKeyboardButton(text="📞 ارتباط با ما", callback_data="support")]
  ]
  if telegram_id in ADMIN_IDS:
    keyboard_buttons.insert(0, [InlineKeyboardButton(text="⚙️ مدیریت بخش‌ها و پلن‌ها", callback_data="admin_plans_menu")])
    keyboard_buttons.insert(1, [InlineKeyboardButton(text="📢 ارسال پیام همگانی", callback_data="admin_broadcast_start")])

  keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
  text = await get_text(telegram_id, "panel_title")

  if edit:
    try:
      await message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    except Exception:
      await message.answer(text, reply_markup=keyboard, parse_mode="Markdown")
  else:
    await message.answer(text, reply_markup=keyboard, parse_mode="Markdown")


# --- ۳. هندلرهای ربات تلگرام ---
@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
  await state.clear()
  telegram_id = message.from_user.id
  with sqlite3.connect("bot_database.db", timeout=30.0) as conn:
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
  await message.answer("🌐 لطفاً زبان خود را انتخاب کنید:", reply_markup=lang_keyboard)


@dp.callback_query(F.data == "back_to_main")
async def back_to_main_handler(callback: CallbackQuery):
  await callback.answer()
  await show_user_panel(callback, edit=True)


@dp.callback_query(F.data == "profile")
async def profile_handler(callback: CallbackQuery):
  telegram_id = callback.from_user.id
  user = await get_user_from_db(telegram_id)
  phone = user[0] if user and user[0] else "ثبت نشده"
  text = f"👤 **اطلاعات حساب کاربری:**\n\n🆔 آیدی تلگرام: `{telegram_id}`\n📱 شماره تلفن: `{phone}`"
  keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ بازگشت", callback_data="back_to_main")]])
  await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")


@dp.callback_query(F.data == "support")
async def support_handler(callback: CallbackQuery):
  telegram_id = callback.from_user.id
  text = await get_text(telegram_id, "support_info")
  keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ بازگشت", callback_data="back_to_main")]])
  await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")


@dp.callback_query(F.data == "admin_plans_menu")
async def admin_plans_menu_handler(callback: CallbackQuery):
  await callback.answer("⚙️ بخش مدیریت ادمین فعال است.", show_alert=True)


@dp.callback_query(F.data == "admin_broadcast_start")
async def admin_broadcast_handler(callback: CallbackQuery):
  await callback.answer("📢 قابلیت ارسال پیام همگانی آماده است.", show_alert=True)


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
  await message.answer(await get_text(telegram_id, "otp_sent", phone=phone_number, otp=otp_code), reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")
  await state.set_state(RegStates.waiting_for_otp)


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


# --- بخش ترید و لایسنس ---
@dp.callback_query(F.data == "trade_menu")
async def trade_section(callback: CallbackQuery):
  telegram_id = callback.from_user.id
  await callback.answer()
  user = await get_user_from_db(telegram_id)
  is_registered = bool(telegram_id in ADMIN_IDS or (user and user[0]))
  trade_lic = await get_trade_license(telegram_id)
  is_paid = bool(trade_lic and trade_lic[1] == 1 and trade_lic[2] == 'active')

  keyboard_buttons = [
      [InlineKeyboardButton(text="📝 ۱. ثبت نام و احراز هویت", callback_data="trade_register")],
      [InlineKeyboardButton(text="💳 ۲. پرداخت اشتراک بخش ترید", callback_data="trade_pay_sub")],
      [InlineKeyboardButton(text="📈 ۳. معامله در صرافی", callback_data="trade_exchange")],
      [InlineKeyboardButton(text="📊 ۴. بک تست", callback_data="trade_backtest")],
      [InlineKeyboardButton(text="💎 ۵. معرفی ارز جدید", callback_data="trade_new_crypto")],
      [InlineKeyboardButton(text="⬅️ بازگشت به پنل اصلی", callback_data="back_to_main")]
  ]
  keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
  status_text = "✅ احراز هویت شده" if is_registered else "❌ احراز هویت نشده"
  sub_text = "✅ اشتراک و لایسنس فعال" if is_paid else "❌ اشتراک پرداخت نشده"

  text = f"📈 **بخش تخصصی ترید و سرمایه‌گذاری**\n\n• ثبت‌نام: {status_text}\n• اشتراک ترید: {sub_text}"
  await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")


@dp.callback_query(F.data == "trade_register")
async def trade_register_handler(callback: CallbackQuery):
  telegram_id = callback.from_user.id
  user = await get_user_from_db(telegram_id)
  if telegram_id in ADMIN_IDS or (user and user[0]):
    await callback.answer("✅ شما قبلاً ثبت‌نام و احراز هویت شده‌اید!", show_alert=True)
  else:
    await callback.answer("❌ لطفاً با دستور /start مراحل ثبت‌نام را کامل کنید.", show_alert=True)


@dp.callback_query(F.data == "trade_pay_sub")
async def trade_pay_subscription(callback: CallbackQuery):
  telegram_id = callback.from_user.id
  await callback.answer()
  user = await get_user_from_db(telegram_id)
  if telegram_id not in ADMIN_IDS and (not user or not user[0]):
    await callback.answer("❌ ابتدا باید احراز هویت کنید!", show_alert=True)
    return

  trade_lic = await get_trade_license(telegram_id)
  if trade_lic and trade_lic[1] == 1:
    await callback.answer(f"✅ اشتراک فعال.\nلایسنس:\n`{trade_lic[0]}`", show_alert=True)
    return

  license_key = await create_trade_license(telegram_id)
  await callback.message.answer(f"✅ اشتراک بخش ترید با موفقیت فعال شد!\n\n🔑 **کد لایسنس انحصاری شما:**\n`{license_key}`", parse_mode="Markdown")


@dp.callback_query(F.data == "trade_exchange")
async def trade_exchange_handler(callback: CallbackQuery):
  telegram_id = callback.from_user.id
  await callback.answer()
  trade_lic = await get_trade_license(telegram_id)
  if not trade_lic or trade_lic[1] != 1 or trade_lic[2] == 'blocked':
    await callback.answer("❌ ابتدا باید اشتراک ترید را فعال کنید!", show_alert=True)
    return
  license_key = trade_lic[0]
  trade_web_url = f"{WEBAPP_BASE_URL}/trade?id={telegram_id}&license={license_key}"
  keyboard = InlineKeyboardMarkup(inline_keyboard=[
      [InlineKeyboardButton(text="📈 ورود به صرافی", web_app=WebAppInfo(url=trade_web_url))],
      [InlineKeyboardButton(text="⬅️ بازگشت", callback_data="trade_menu")]
  ])
  await callback.message.edit_text("📈 پنل معامله در صرافی آماده است:", reply_markup=keyboard)


@dp.callback_query(F.data == "trade_backtest")
async def trade_backtest_handler(callback: CallbackQuery):
  telegram_id = callback.from_user.id
  await callback.answer()
  trade_lic = await get_trade_license(telegram_id)
  if not trade_lic or trade_lic[1] != 1 or trade_lic[2] == 'blocked':
    await callback.answer("❌ دسترسی غیرمجاز!", show_alert=True)
    return
  license_key = trade_lic[0]
  backtest_web_url = f"{WEBAPP_BASE_URL}/backtest?id={telegram_id}&license={license_key}"
  keyboard = InlineKeyboardMarkup(inline_keyboard=[
      [InlineKeyboardButton(text="📊 ورود به ابزار بک‌تست", web_app=WebAppInfo(url=backtest_web_url))],
      [InlineKeyboardButton(text="⬅️ بازگشت", callback_data="trade_menu")]
  ])
  await callback.message.edit_text("📊 سیستم بک‌تست آماده است:", reply_markup=keyboard)


@dp.callback_query(F.data == "trade_new_crypto")
async def trade_new_crypto_handler(callback: CallbackQuery):
  await callback.answer()
  keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ بازگشت", callback_data="trade_menu")]])
  await callback.message.edit_text("💎 ارزهای جدید و مستعد رشد بروزرسانی شدند.", reply_markup=keyboard)


# --- خرید اشتراک VPN ---
@dp.callback_query(F.data == "start_buy")
async def cmd_buy_callback(callback: CallbackQuery, state: FSMContext):
  await state.clear()
  await callback.answer()
  categories = await get_all_categories()
  keyboard_buttons = [[InlineKeyboardButton(text=name, callback_data=f"cat_{key}")] for key, name in categories]
  keyboard_buttons.append([InlineKeyboardButton(text="⬅️ بازگشت", callback_data="back_to_main")])
  keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
  await callback.message.edit_text("📂 لطفاً دسته‌بندی اشتراک را انتخاب کنید:", reply_markup=keyboard)


@dp.callback_query(F.data.startswith("cat_"))
async def select_category_plans(callback: CallbackQuery):
  await callback.answer()
  cat_key = callback.data.split("_")[1]
  plans = await get_plans(cat_key)
  keyboard_buttons = [[InlineKeyboardButton(text=f"{gb} گیگ - {price:,} تومان", callback_data=f"buyplan_{pid}")] for pid, gb, price in plans]
  keyboard_buttons.append([InlineKeyboardButton(text="⬅️ بازگشت", callback_data="start_buy")])
  await callback.message.edit_text("⚡️ لطفاً حجم پلن را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_buttons))


@dp.callback_query(F.data.startswith("buyplan_"))
async def process_selected_plan_db(callback: CallbackQuery):
  telegram_id = callback.from_user.id
  await callback.answer("⏳ در حال ساخت اشتراک...")
  plan = await get_plan_by_id(int(callback.data.split("_")[1]))
  if not plan:
    await callback.answer("❌ پلن نامعتبر است.", show_alert=True)
    return
  _, category, gb_size, calculated_price = plan
  sub_url = await create_user_in_marzban(f"u{telegram_id}_{generate_random_suffix()}", data_limit_gb=gb_size, expire_days=PLAN_EXPIRE_DAYS)
  if sub_url:
    await callback.message.answer(f"🎉 اشتراک شما با موفقیت ایجاد شد!\n\n🔗 لینک سابسکرایب:\n`{sub_url}`", parse_mode="Markdown")
  else:
    await callback.message.answer("⚠️ خطا در ساخت اکانت روی سرور مرزبان.")


# --- اجرای هم‌زمان FastAPI و ربات تلگرام ---
async def run_fastapi():
    port = int(os.environ.get("PORT", 8000))
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()

async def main():
  await init_db()
  print("🚀 Bot and Web App are running simultaneously...")
  await asyncio.gather(
      dp.start_polling(bot),
      run_fastapi()
  )

if __name__ == "__main__":
  try:
    asyncio.run(main())
  except (KeyboardInterrupt, SystemExit):
    print("Bot stopped!")
