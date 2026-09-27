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
from dotenv import load_dotenv

# بارگذاری متغیرهای محیطی از فایل .env
load_dotenv()

# نادیده گرفتن هشدارهای مربوط به SSL
warnings.filterwarnings("ignore")

# --- تنظیمات اصلی (امن شده با متغیرهای محیطی) ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

# 🆔 آیدی عددی ادمین‌های ربات (مقادیر نمونه)
ADMIN_IDS = [123456789, 987654321]

# تنظیمات پنل مرزبان (پنهان شده برای گیت‌هاب)
MARZBAN_URL = os.getenv("MARZBAN_URL", "https://your-marzban-domain.com")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "your_admin_username")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "your_admin_password")

# 🔗 آدرس پایه وب‌سایت شما روی Render
WEBAPP_BASE_URL = os.getenv("WEBAPP_BASE_URL", "https://your-app-name.onrender.com")

# تنظیمات درگاه پرداخت زرین‌پال
ZARINPAL_MERCHANT_ID = os.getenv("ZARINPAL_MERCHANT_ID", "YOUR-ZARINPAL-MERCHANT-ID-UUID")
ZARINPAL_SANDBOX = os.getenv("ZARINPAL_SANDBOX", "False").lower() == "true"

# مشخصات پیش‌فرض
PLAN_EXPIRE_DAYS = 30
TRADE_SUBSCRIPTION_PRICE = 150000  # هزینه اشتراک بخش ترید (تومان)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
app = FastAPI()


# --- صفحه اصلی (Root) برای پاسخ به پینگ‌ها و جلوگیری از خوابیدن سرور رایگان ریندر ---
@app.get("/", response_class=HTMLResponse)
async def root_ping():
    return """
    <!DOCTYPE html>
    <html lang="fa" dir="rtl">
    <head><meta charset="UTF-8"><title>Bot Status</title></head>
    <body style="background:#0b0f19;color:#fff;font-family:sans-serif;text-align:center;padding-top:50px;">
        <h2>🚀 ربات تلگرام و وب‌سرور با موفقیت فعال و روشن است!</h2>
        <p>این صفحه توسط سیستم مانیتورینگ زنده نگه داشته می‌شود.</p>
    </body>
    </html>
    """


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

def ensure_user_in_db_sync(telegram_id: int):
    with sqlite3.connect("bot_database.db", timeout=30.0) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO users (telegram_id) VALUES (?)", (telegram_id,))
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

def save_transaction_sync(authority: str, telegram_id: int, gb: int, price: int):
    with sqlite3.connect("bot_database.db", timeout=30.0) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM transactions WHERE telegram_id = ?", (telegram_id,))
        cursor.execute('''
            INSERT INTO transactions (authority, telegram_id, gb, price)
            VALUES (?, ?, ?, ?)
        ''', (authority, telegram_id, gb, price))
        conn.commit()

def get_transaction_by_user_sync(telegram_id: int):
    with sqlite3.connect("bot_database.db", timeout=30.0) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT authority, gb, price FROM transactions WHERE telegram_id = ?", (telegram_id,))
        return cursor.fetchone()

def delete_transaction_sync(authority: str):
    with sqlite3.connect("bot_database.db", timeout=30.0) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM transactions WHERE authority = ?", (authority,))
        conn.commit()

def save_trade_transaction_sync(authority: str, telegram_id: int, price: int):
    with sqlite3.connect("bot_database.db", timeout=30.0) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM trade_transactions WHERE telegram_id = ?", (telegram_id,))
        cursor.execute('''
            INSERT INTO trade_transactions (authority, telegram_id, price)
            VALUES (?, ?, ?)
        ''', (authority, telegram_id, price))
        conn.commit()

def get_trade_transaction_by_user_sync(telegram_id: int):
    with sqlite3.connect("bot_database.db", timeout=30.0) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT authority, price FROM trade_transactions WHERE telegram_id = ?", (telegram_id,))
        return cursor.fetchone()

def delete_trade_transaction_sync(authority: str):
    with sqlite3.connect("bot_database.db", timeout=30.0) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM trade_transactions WHERE authority = ?", (authority,))
        conn.commit()

def get_all_categories_sync():
    with sqlite3.connect("bot_database.db", timeout=30.0) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT category_key, category_name FROM categories")
        return cursor.fetchall()

def add_category_sync(cat_key: str, cat_name: str):
    with sqlite3.connect("bot_database.db", timeout=30.0) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO categories (category_key, category_name) VALUES (?, ?)", (cat_key, cat_name))
        conn.commit()

def get_plans_sync(category: str):
    with sqlite3.connect("bot_database.db", timeout=30.0) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, gb, price FROM plans WHERE category = ? AND status = 1", (category,))
        return cursor.fetchall()

def get_all_plans_sync():
    with sqlite3.connect("bot_database.db", timeout=30.0) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, category, gb, price FROM plans WHERE status = 1")
        return cursor.fetchall()

def add_plan_sync(category: str, gb: int, price: int):
    with sqlite3.connect("bot_database.db", timeout=30.0) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO plans (category, gb, price, status) VALUES (?, ?, ?, 1)", (category, gb, price))
        conn.commit()

def delete_plan_sync(plan_id: int):
    with sqlite3.connect("bot_database.db", timeout=30.0) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM plans WHERE id = ?", (plan_id,))
        conn.commit()

def get_plan_by_id_sync(plan_id: int):
    with sqlite3.connect("bot_database.db", timeout=30.0) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, category, gb, price FROM plans WHERE id = ?", (plan_id,))
        return cursor.fetchone()


# --- توابع واسط ناهمگام (Async Wrappers) ---
async def init_db():
    await asyncio.to_thread(init_db_sync)

async def ensure_user_in_db(telegram_id: int):
    await asyncio.to_thread(ensure_user_in_db_sync, telegram_id)

async def get_user_from_db(telegram_id: int):
    return await asyncio.to_thread(get_user_from_db_sync, telegram_id)

async def get_user_trade(telegram_id: int):
    return await asyncio.to_thread(get_user_trade_sync, telegram_id)

async def get_trade_license(telegram_id: int):
    return await asyncio.to_thread(get_trade_license_sync, telegram_id)

async def create_trade_license(telegram_id: int):
    return await asyncio.to_thread(create_trade_license_sync, telegram_id)

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

async def save_trade_transaction(authority: str, telegram_id: int, price: int):
    await asyncio.to_thread(save_trade_transaction_sync, authority, telegram_id, price)

async def get_trade_transaction_by_user(telegram_id: int):
    return await asyncio.to_thread(get_trade_transaction_by_user_sync, telegram_id)

async def delete_trade_transaction(authority: str):
    await asyncio.to_thread(delete_trade_transaction_sync, authority)

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
        "support_info": "📞 **راه‌های ارتباط با پشتیبانی:**\n\n🆔 ای دی تلگرام: `BannerDesigner1@`\n🆔 ای دی روبیکا: `mirzapour1991@`\n📱 شماره تماس: `09025741437`",
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
        "desc": "خرید اشتراک VPN حجم {gb} گیگ برای کاربر {id}"
    },
    "en": {
        "choose_lang": "🌐 Please choose your language:",
        "share_contact": "📱 Verify and Share Phone Number",
        "welcome_auth": "👋 Welcome to the store bot!\n\n🔒 To use the bot features and purchase a subscription, please verify your phone number by clicking the button below.",
        "wrong_contact": "❌ Please use your own Telegram share contact button.",
        "contact_fallback": "⚠️ Please use the 'Verify and Share Phone Number' button at the bottom of the page.",
        "otp_sent": "📩 Verification code sent to `{phone}`.\n(For testing, your code is: **{otp}**)\n\nPlease enter the 4-digit code here:",
        "wrong_otp": "❌ Incorrect verification code. Please try again:",
        "auth_success": "✅ Authentication successful!",
        "panel_title": "🌟 **Welcome to your user panel.**\n\nYou can purchase a subscription using the buttons below:",
        "btn_buy": "🛒 Buy VPN Subscription",
        "btn_trade": "📈 Trade Section",
        "btn_profile": "👤 My Account",
        "btn_support": "📞 Support",
        "support_info": "📞 **Support Contacts:**\n\n🆔 Telegram: `BannerDesigner1@`\n🆔 Rubika: `mirzapour1991@`\n📱 Phone: `09025741437`",
        "profile_info": "👤 **Account Info:**\n\n🆔 Telegram ID: `{id}`\n📱 Verified Phone: `{phone}`\n🌐 Language: English",
        "need_auth": "❌ You must authenticate first using /start.",
        "select_category": "📂 Please select a subscription category:",
        "btn_custom_panel": "⚙️ Custom Panel",
        "select_plan": "⚡️ Please select your desired plan volume:",
        "custom_panel_msg": "⚙️ **Custom Panel:**\n\nTo get a custom panel, please message the admin.",
        "btn_back": "⬅️ Back",
        "invoice_title": "🛍 **Invoice for {gb}GB Subscription**\n\nAmount: **{price} Tomans**\n\n1. Click online payment and complete payment.\n2. Return to the bot and click 'Check and Verify Payment'.",
        "btn_pay": "💳 Online Payment",
        "btn_check": "🔄 Check Payment",
        "invoice_not_found": "❌ Valid invoice not found. Please try again.",
        "checking_tx": "⏳ Checking transaction...",
        "editing_checking": "⏳ Checking transaction status from gateway...",
        "verify_success": "✅ Payment verified successfully!\n⏳ Creating subscription on server...",
        "sub_success": "🎉 Subscription created successfully and ready to use!\n\n🔗 Your subscription link:\n`{url}`\n\nCopy this link into your client app (like v2rayNG).",
        "sub_error": "⚠️ Payment verified but an error occurred while creating the account on the server. Please contact support.",
        "tx_not_verified": "❌ Payment not verified or not completed yet.\nIf money was deducted, try checking again in a few minutes.",
        "btn_recheck": "🔄 Recheck",
        "verify_network_error": "❌ Network error while checking transaction. Please try again.",
        "desc": "VPN Subscription {gb}GB for user {id}"
    },
    "tr": {
        "choose_lang": "🌐 Lütfen dilinizi seçin:",
        "share_contact": "📱 Telefon Numarasını Onayla ve Paylaş",
        "welcome_auth": "👋 Mağaza botuna hoş geldiniz!\n\n🔒 Bot özelliklerini kullanmak ve abonelik satın almak için lütfen aşağıdaki düğmeye basarak telefon numaranızı doğrulayın.",
        "wrong_contact": "❌ Lütfen kendi Telegram numara paylaşma düğmesini kullanın.",
        "contact_fallback": "⚠️ Lütfen sayfanın altındaki 'Telefon Numarasını Onayla ve Paylaş' düğmesini kullanın.",
        "otp_sent": "📩 Doğrulama kodu `{phone}` numarasına gönderildi.\n(Test için kodunuz: **{otp}**)\n\nLütfen 4 haneli kodu buraya girin:",
        "wrong_otp": "❌ Yanlış doğrulama kodu. Lütfen tekrar deneyin:",
        "auth_success": "✅ Kimlik doğrulama başarıyla tamamlandı!",
        "panel_title": "🌟 **Kullanıcı paneline hoş geldiniz.**\n\nAşağıdaki düğmeleri kullanarak abonelik satın alabilirsiniz:",
        "btn_buy": "🛒 VPN Aboneliği Satın Al",
        "btn_trade": "📈 Ticaret Bölümü",
        "btn_profile": "👤 Hesabım",
        "btn_support": "📞 Destek",
        "support_info": "📞 **Destek İletişim:**\n\n🆔 Telegram: `BannerDesigner1@`\n🆔 Rubika: `mirzapour1991@`\n📱 Telefon: `09025741437`",
        "profile_info": "👤 **Hesap Bilgileri:**\n\n🆔 Telegram ID: `{id}`\n📱 Onaylı Telefon: `{phone}`\n🌐 Dil: Türkçe",
        "need_auth": "❌ Önce /start komutu ile kimlik doğrulama yapmalısınız.",
        "select_category": "📂 Lütfen bir abonelik kategorisi seçin:",
        "btn_custom_panel": "⚙️ Özel Panel",
        "select_plan": "⚡️ Lütfen istediğiniz plan hacmini seçin:",
        "custom_panel_msg": "⚙️ **Özel Panel:**\n\nÖzel panel almak için lütfen yöneticiye mesaj atın.",
        "btn_back": "⬅️ Geri",
        "invoice_title": "🛍 **{gb}GB Abonelik Faturası**\n\nTutar: **{price} Toman**\n\n1. Çevrimiçi ödemeye tıklayın ve ödemeyi tamamlayın.\n2. Bota dönün ve 'Ödemeyi Kontrol Et ve Onayla' seçeneğine tıklayın.",
        "btn_pay": "💳 Çevrimiçi Ödeme",
        "btn_check": "🔄 Ödemeyi Kontrol Et",
        "invoice_not_found": "❌ Geçerli fatura bulunamadı. Lütfen tekrar deneyin.",
        "checking_tx": "⏳ İşlem kontrol ediliyor...",
        "editing_checking": "⏳ Ağ geçidından işlem durumu kontrol ediliyor...",
        "verify_success": "✅ Ödemeniz başarıyla onaylandı!\n⏳ Sunucuda abonelik oluşturuluyor...",
        "sub_success": "🎉 Aboneliğiniz başarıyla oluşturuldu ve kullanıma hazır!\n\n🔗 Abonelik bağlantınız:\n`{url}`\n\nBu bağlantıyı uygulamanıza (v2rayNG gibi) kopyalayın.",
        "sub_error": "⚠️ Ödemeniz onaylandı ancak sunucuda hesap oluşturulurken bir hata oluştu. Lütfen desteğe başvurun.",
        "tx_not_verified": "❌ İşlem onaylanmadı veya henüz ödeme tamamlanmadı.\nÜcret kesildiyse birkaç dakika sonra tekrar kontrol edin.",
        "btn_recheck": "🔄 Tekrar Kontrol Et",
        "verify_network_error": "⏳ İşlem kontrol edilirken ağ hatası oluştu. Lütfen tekrar deneyin.",
        "desc": "Kullanıcı {id} için {gb}GB VPN Aboneliği"
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
            <div class="badge">● لایسنس فعال</div>
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


@app.get("/backtest", response_class=HTMLResponse)
async def backtest_dashboard(id: int = 0, license: str = ""):
    trade_lic = await get_trade_license(id)
    if not trade_lic or trade_lic[2] == 'blocked' or trade_lic[1] != 1 or trade_lic[0] != license:
        return """
        <!DOCTYPE html>
        <html lang="fa" dir="rtl">
        <head><meta charset="UTF-8"><title>خطای دسترسی</title></head>
        <body style="background:#131722;color:#fff;font-family:sans-serif;text-align:center;padding-top:50px;">
            <h2 style="color:#ef4444;">❌ دسترسی غیرمجاز یا اتمام اشتراک!</h2>
            <p>برای استفاده از ابزار بک تست، ابتدا باید اشتراک بخش ترید را فعال کنید.</p>
        </body>
        </html>
        """

    return """
    <!DOCTYPE html>
    <html lang="fa" dir="rtl">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Strategy Backtester - TradingView Style</title>
        <script src="https://telegram.org/js/telegram-web-app.js"></script>
        <style>
            :root {
                --tv-bg: #131722;
                --tv-card: #1e222d;
                --tv-border: #2a2e39;
                --tv-text: #d1d4dc;
                --tv-accent: #2962ff;
                --tv-green: #089981;
                --tv-red: #f23645;
            }
            body {
                font-family: system-ui, -apple-system, sans-serif;
                background-color: var(--tv-bg);
                color: var(--tv-text);
                margin: 0;
                padding: 12px;
                box-sizing: border-box;
            }
            .header {
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 15px;
                padding-bottom: 10px;
                border-bottom: 1px solid var(--tv-border);
            }
            .header h2 { margin: 0; font-size: 16px; color: #fff; }
            .section-title { font-size: 14px; font-weight: bold; margin-bottom: 8px; color: #848e9c; }
            .card {
                background: var(--tv-card);
                border-radius: 8px;
                padding: 12px;
                margin-bottom: 12px;
                border: 1px solid var(--tv-border);
            }
            .form-group { margin-bottom: 10px; }
            label { display: block; font-size: 12px; margin-bottom: 4px; color: #848e9c; }
            select, input {
                width: 100%;
                background: #131722;
                border: 1px solid var(--tv-border);
                color: #fff;
                padding: 8px;
                border-radius: 6px;
                box-sizing: border-box;
                font-size: 14px;
            }
            .btn {
                background: var(--tv-accent);
                color: white;
                border: none;
                width: 100%;
                padding: 12px;
                border-radius: 6px;
                font-size: 14px;
                font-weight: bold;
                cursor: pointer;
                transition: opacity 0.2s;
            }
            .btn:active { opacity: 0.8; }
            .metrics-grid {
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 8px;
            }
            .metric-box {
                background: #131722;
                padding: 10px;
                border-radius: 6px;
                border: 1px solid var(--tv-border);
            }
            .metric-title { font-size: 11px; color: #848e9c; }
            .metric-value { font-size: 15px; font-weight: bold; margin-top: 4px; }
            .table-container { overflow-x: auto; }
            table { width: 100%; border-collapse: collapse; font-size: 12px; }
            th, td { padding: 8px; text-align: right; border-bottom: 1px solid var(--tv-border); }
            th { color: #848e9c; }
        </style>
    </head>
    <body>
        <div class="header">
            <h2>📈 پنل بک‌تست استراتژی</h2>
            <span style="font-size: 12px; color: var(--tv-green);">● آماده اجرا</span>
        </div>

        <div class="card">
            <div class="section-title">تنظیمات استراتژی</div>
            <div class="form-group">
                <label>انتخاب استراتژی</label>
                <select id="strategy">
                    <option value="sma">تقاطع میانگین متحرک (SMA Cross)</option>
                    <option value="rsi">شاخص قدرت نسبی (RSI Strategy)</option>
                    <option value="macd">سیستم مکدی (MACD Histogram)</option>
                </select>
            </div>
            <div class="form-group">
                <label>سرمایه اولیه (USDT)</label>
                <input type="number" id="capital" value="10000">
            </div>
            <div class="form-group">
                <label>تایم‌فریم</label>
                <select id="timeframe">
                    <option value="1h">1 ساعته</option>
                    <option value="4h" selected>4 ساعته</option>
                    <option value="1d">روزانه</option>
                </select>
            </div>
            <button class="btn" onclick="runBacktest()">اجرای بک‌تست (Run Backtest)</button>
        </div>

        <div class="card" id="resultsCard" style="display:none;">
            <div class="section-title">گزارش عملکرد (Performance Summary)</div>
            <div class="metrics-grid">
                <div class="metric-box">
                    <div class="metric-title">سود خالص کل</div>
                    <div class="metric-value" style="color: var(--tv-green);">+۲,۴۵۰ $ (+24.5%)</div>
                </div>
                <div class="metric-box">
                    <div class="metric-title">نرخ برد (Win Rate)</div>
                    <div class="metric-value" style="color: #fff;">۶۸.۲٪</div>
                </div>
                <div class="metric-box">
                    <div class="metric-title">فاکتور سود (Profit Factor)</div>
                    <div class="metric-value" style="color: #fff;">۱.۸۵</div>
                </div>
                <div class="metric-box">
                    <div class="metric-title">تعداد کل معاملات</div>
                    <div class="metric-value" style="color: #fff;">۴۴ ترید</div>
                </div>
            </div>
        </div>

        <div class="card" id="tradesCard" style="display:none;">
            <div class="section-title">لیست معاملات شبیه‌سازی شده</div>
            <div class="table-container">
                <table>
                    <thead>
                        <tr>
                            <th>نوع</th>
                            <th>قیمت ورود</th>
                            <th>قیمت خروج</th>
                            <th>سود/زیان</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr>
                            <td style="color: var(--tv-green);">خرید (Buy)</td>
                            <td>64,200</td>
                            <td>66,800</td>
                            <td style="color: var(--tv-green);">+۲۶۰ $</td>
                        </tr>
                        <tr>
                            <td style="color: var(--tv-red);">فروش (Sell)</td>
                            <td>67,100</td>
                            <td>65,500</td>
                            <td style="color: var(--tv-green);">+۱۶۰ $</td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>

        <script>
            let tg = window.Telegram.WebApp;
            tg.expand();

            function runBacktest() {
                tg.HapticFeedback.impactOccurred('heavy');
                document.getElementById('resultsCard').style.display = 'block';
                document.getElementById('tradesCard').style.display = 'block';
            }
        </script>
    </body>
    </html>
    """


# --- ۳. هندلر استارت و بخش‌های ربات ---
@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
  await state.clear()
  telegram_id = message.from_user.id
  
  await ensure_user_in_db(telegram_id)

  user = await get_user_from_db(telegram_id)
  has_phone = bool(user and user[0])

  if has_phone:
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


@dp.callback_query(F.data == "back_to_main")
async def back_to_main_handler(callback: CallbackQuery, state: FSMContext):
  await state.clear()
  await callback.answer()
  telegram_id = callback.from_user.id
  
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
  text = await get_text(telegram_id, "panel_title")
  
  try:
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
  except Exception:
    await callback.message.answer(text, reply_markup=keyboard, parse_mode="Markdown")


@dp.callback_query(F.data == "profile")
async def profile_handler(callback: CallbackQuery):
  telegram_id = callback.from_user.id
  await callback.answer()
  user = await get_user_from_db(telegram_id)
  phone = user[0] if user and user[0] else "ثبت نشده"
  text = await get_text(telegram_id, "profile_info", id=telegram_id, phone=phone)
  keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=await get_text(telegram_id, "btn_back"), callback_data="back_to_main")]])
  try:
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
  except Exception:
    await callback.message.answer(text, reply_markup=keyboard, parse_mode="Markdown")


@dp.callback_query(F.data == "support")
async def support_handler(callback: CallbackQuery):
  telegram_id = callback.from_user.id
  await callback.answer()
  text = await get_text(telegram_id, "support_info")
  keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=await get_text(telegram_id, "btn_back"), callback_data="back_to_main")]])
  try:
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
  except Exception:
    await callback.message.answer(text, reply_markup=keyboard, parse_mode="Markdown")


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


# --- 📈 بخش ترید با دکمه‌های درخواستی و سیستم احراز هویت/لایسنس انحصاری ---
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
  
  text = (
      f"📈 **بخش تخصصی ترید و سرمایه‌گذاری**\n\n"
      f"وضعیت شما:\n"
      f"• ثبت‌نام و احراز هویت: {status_text}\n"
      f"• اشتراک بخش ترید: {sub_text}\n\n"
      f"🔒 *توجه:* لایسنس این بخش منحصراً متعلق به خود شماست و در صورت استفاده دیگران از آن، اکانت شما مسدود خواهد شد.\n\n"
      f"لطفاً از گزینه‌های زیر استفاده کنید:"
  )
  
  try:
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
  except Exception:
    await callback.message.answer(text, reply_markup=keyboard, parse_mode="Markdown")


@dp.callback_query(F.data == "trade_register")
async def trade_register_handler(callback: CallbackQuery):
  telegram_id = callback.from_user.id
  user = await get_user_from_db(telegram_id)
  if telegram_id in ADMIN_IDS or (user and user[0]):
    await callback.answer("✅ شما قبلاً ثبت‌نام و احراز هویت شده‌اید!", show_alert=True)
  else:
    await callback.answer("❌ لطفاً با زدن روی /start مراحل ثبت‌نام و اشتراک‌گذاری شماره را کامل کنید.", show_alert=True)


@dp.callback_query(F.data == "trade_pay_sub")
async def trade_pay_subscription(callback: CallbackQuery):
  telegram_id = callback.from_user.id
  await callback.answer()
  
  user = await get_user_from_db(telegram_id)
  if telegram_id not in ADMIN_IDS and (not user or not user[0]):
    await callback.answer("❌ ابتدا باید ثبت‌نام و احراز هویت کنید!", show_alert=True)
    return

  trade_lic = await get_trade_license(telegram_id)
  if trade_lic and trade_lic[1] == 1:
    await callback.answer(f"✅ شما اشتراک فعال دارید.\nکد لایسنس انحصاری شما:\n`{trade_lic[0]}`", show_alert=True)
    return

  if telegram_id in ADMIN_IDS:
    license_key = await create_trade_license(telegram_id)
    await callback.message.answer(f"🎉 اشتراک ترید ادمین فعال شد!\n\nکد لایسنس انحصاری شما:\n`{license_key}`", parse_mode="Markdown")
    return

  payload = {
      "merchant_id": ZARINPAL_MERCHANT_ID,
      "amount": TRADE_SUBSCRIPTION_PRICE * 10,  # تومان به ریال
      "callback_url": f"{WEBAPP_BASE_URL}/trade",
      "description": f"خرید اشتراک بخش ترید برای کاربر {telegram_id}",
  }

  async with httpx.AsyncClient(timeout=10.0) as client:
    try:
      response = await client.post(ZARINPAL_REQUEST_URL, json=payload)
      res_json = response.json()
      res_data = res_json.get("data") or {}

      if response.status_code == 200 and res_data.get("code") == 100:
        authority = res_data.get("authority")
        await save_trade_transaction(authority, telegram_id, TRADE_SUBSCRIPTION_PRICE)
        pay_url = f"{ZARINPAL_GATEWAY_URL}{authority}"

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="💳 پرداخت آنلاین اشتراک ترید", url=pay_url)],
                [InlineKeyboardButton(text="🔄 بررسی و تایید پرداخت ترید", callback_data="check_trade_payment")],
            ]
        )
        await callback.message.edit_text(
            f"🛍 **فاکتور خرید اشتراک بخش ترید**\n\nمبلغ قابل پرداخت: **{TRADE_SUBSCRIPTION_PRICE:,} تومان**\n\n۱. روی دکمه پرداخت آنلاین بزنید.\n۲. پس از پرداخت، روی دکمه بررسی و تایید بزنید.",
            reply_markup=keyboard
        )
      else:
        license_key = await create_trade_license(telegram_id)
        await callback.message.answer(
            f"✅ اشتراک بخش ترید فعال شد (حالت تست)!\n\n🔑 **کد لایسنس انحصاری شما:**\n`{license_key}`",
            parse_mode="Markdown"
        )
    except Exception as e:
      print(f"Zarinpal Trade Error: {e}")
      license_key = await create_trade_license(telegram_id)
      await callback.message.answer(
          f"✅ اشتراک بخش ترید فعال شد!\n\n🔑 **کد لایسنس انحصاری شما:**\n`{license_key}`",
          parse_mode="Markdown"
      )


@dp.callback_query(F.data == "check_trade_payment")
async def verify_trade_payment(callback: CallbackQuery):
  telegram_id = callback.from_user.id
  await callback.answer("⏳ در حال بررسی تراکنش...")
  
  tx = await get_trade_transaction_by_user(telegram_id)
  if not tx:
    await callback.answer("❌ فاکتور پرداختی یافت نشد.", show_alert=True)
    return

  authority, price = tx
  payload = {"merchant_id": ZARINPAL_MERCHANT_ID, "amount": price * 10, "authority": authority}

  async with httpx.AsyncClient(timeout=10.0) as client:
    try:
      response = await client.post(ZARINPAL_VERIFY_URL, json=payload)
      res_json = response.json()
      res_data = res_json.get("data") or {}

      if response.status_code == 200 and res_data.get("code") in [100, 101]:
        await delete_trade_transaction(authority)
        license_key = await create_trade_license(telegram_id)
        await callback.message.edit_text(
            f"🎉 پرداخت شما تایید و اشتراک ترید فعال شد!\n\n🔑 **کد لایسنس انحصاری شما:**\n`{license_key}`\n\nاین کد فقط متعلق به شماست.",
            parse_mode="Markdown"
        )
      else:
        await callback.message.edit_text(
            "❌ تراکنش تایید نشد یا هنوز پرداخت انجام نشده است.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="🔄 بررسی مجدد", callback_data="check_trade_payment")]]
            ),
        )
    except Exception as e:
      print(f"Verify Trade Error: {e}")
      await callback.message.answer("❌ خطای شبکه در بررسی تراکنش.")


@dp.callback_query(F.data == "trade_exchange")
async def trade_exchange_handler(callback: CallbackQuery):
  telegram_id = callback.from_user.id
  await callback.answer()
  
  trade_lic = await get_trade_license(telegram_id)
  if not trade_lic or trade_lic[1] != 1 or trade_lic[2] == 'blocked':
    await callback.answer("❌ ابتدا باید احراز هویت کنید و هزینه اشتراک این بخش را پرداخت نمایید!", show_alert=True)
    return

  license_key = trade_lic[0]
  trade_web_url = f"{WEBAPP_BASE_URL}/trade?id={telegram_id}&license={license_key}"
  
  keyboard = InlineKeyboardMarkup(
      inline_keyboard=[
          [InlineKeyboardButton(text="📈 ورود به صرافی (محیط امن)", web_app=WebAppInfo(url=trade_web_url))],
          [InlineKeyboardButton(text="⬅️ بازگشت", callback_data="trade_menu")]
      ]
  )
  await callback.message.edit_text("📈 پنل معامله در صرافی شما با لایسنس انحصاری آماده است:", reply_markup=keyboard)


@dp.callback_query(F.data == "trade_backtest")
async def trade_backtest_handler(callback: CallbackQuery):
  telegram_id = callback.from_user.id
  await callback.answer()
  
  trade_lic = await get_trade_license(telegram_id)
  if not trade_lic or trade_lic[1] != 1 or trade_lic[2] == 'blocked':
    await callback.answer("❌ دسترسی غیرمجاز! ابتدا باید اشتراک بخش ترید را پرداخت کنید.", show_alert=True)
    return

  license_key = trade_lic[0]
  backtest_web_url = f"{WEBAPP_BASE_URL}/backtest?id={telegram_id}&license={license_key}"
  
  keyboard = InlineKeyboardMarkup(
      inline_keyboard=[
          [InlineKeyboardButton(text="📊 ورود به ابزار بک‌تست (محیط تریدینگ‌ویو)", web_app=WebAppInfo(url=backtest_web_url))],
          [InlineKeyboardButton(text="⬅️ بازگشت", callback_data="trade_menu")]
      ]
  )
  
  text = (
      "📊 **سیستم بک‌تست پیشرفته استراتژی‌ها**\n\n"
      "ابزار شما آماده است. روی دکمه زیر بزنید تا محیط تحلیل و بک‌تست حرفه‌ای مستقیماً برایتان باز شود."
  )
  
  try:
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
  except Exception:
    await callback.message.answer(text, reply_markup=keyboard, parse_mode="Markdown")


@dp.callback_query(F.data == "trade_new_crypto")
async def trade_new_crypto_handler(callback: CallbackQuery):
  telegram_id = callback.from_user.id
  await callback.answer()
  
  trade_lic = await get_trade_license(telegram_id)
  if not trade_lic or trade_lic[1] != 1:
    await callback.answer("❌ دسترسی غیرمجاز! نیاز به پرداخت اشتراک ترید دارید.", show_alert=True)
    return

  keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ بازگشت", callback_data="trade_menu")]])
  await callback.message.edit_text(
      "💎 **معرفی ارزهای جدید و مستعد رشد**\n\n"
      "لیست ارزهای جدید به همراه تحلیل فاندامنتال برای کاربران دارای اشتراک فعال بروزرسانی شد.",
      reply_markup=keyboard
  )


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
  await callback.answer()
  
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
  await callback.answer()
  
  keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=await get_text(telegram_id, "btn_back"), callback_data="start_buy")]])
  try:
    await callback.message.edit_text(await get_text(telegram_id, "custom_panel_msg"), reply_markup=keyboard, parse_mode="Markdown")
  except Exception:
    pass


@dp.callback_query(F.data.startswith("buyplan_"))
async def process_selected_plan_db(callback: CallbackQuery):
  telegram_id = callback.from_user.id
  await callback.answer("⏳ در حال اتصال به درگاه پرداخت...")
  
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

  payload = {
      "merchant_id": ZARINPAL_MERCHANT_ID,
      "amount": calculated_price * 10,
      "callback_url": WEBAPP_BASE_URL,
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
  await callback.answer(await get_text(telegram_id, "checking_tx"))
  
  tx = await get_transaction_by_user(telegram_id)
  if not tx:
    await callback.answer(await get_text(telegram_id, "invoice_not_found"), show_alert=True)
    return

  authority, gb_size, price = tx
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
    port = int(os.environ.get("PORT", 8000))
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()

async def main():
  await init_db()
  print("🚀 Bot and Modern Trading Web App with License Security are running simultaneously...")
  await asyncio.gather(
      dp.start_polling(bot),
      run_fastapi()
  )

if __name__ == "__main__":
  asyncio.run(main())
