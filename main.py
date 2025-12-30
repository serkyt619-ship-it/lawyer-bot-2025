import asyncio
import os
import re
import sqlite3
import time
import random
from typing import Dict, Optional, Tuple

import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

# =====================
# ENV
# =====================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
YANDEX_API_KEY = os.environ.get("YANDEX_API_KEY")
YANDEX_FOLDER_ID = os.environ.get("YANDEX_FOLDER_ID")

CARD_NUMBER = str(os.environ.get("CARD_NUMBER", "")).strip()
CARD_HOLDER = str(os.environ.get("CARD_HOLDER", "")).strip()

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not set")
if not YANDEX_API_KEY:
    raise ValueError("YANDEX_API_KEY not set")
if not YANDEX_FOLDER_ID:
    raise ValueError("YANDEX_FOLDER_ID not set")
if not CARD_NUMBER:
    raise ValueError("CARD_NUMBER not set")
if not CARD_HOLDER:
    raise ValueError("CARD_HOLDER not set")

# =====================
# DATA
# =====================
CATEGORIES = {
    "police": ("Заявление в полицию", 149),
    "claim": ("Претензия", 199),
    "complaint": ("Жалоба", 179),
    "lawsuit": ("Иск в суд", 399),
    "motion": ("Ходатайство", 129),
}

ORDER_TTL = 30 * 60
DB_PATH = "orders.db"

# =====================
# DB
# =====================
def init_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            user_id INTEGER,
            category TEXT,
            amount INTEGER,
            code TEXT,
            created INTEGER,
            paid INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, category)
        )
    """)
    con.commit()
    con.close()

def save_order(user_id, category, amount, code):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO orders VALUES (?, ?, ?, ?, ?, 0)",
        (user_id, category, amount, code, int(time.time()))
    )
    con.commit()
    con.close()

def get_order(user_id, category):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        "SELECT amount, code, created, paid FROM orders WHERE user_id=? AND category=?",
        (user_id, category)
    )
    row = cur.fetchone()
    con.close()
    return row

def confirm_payment(user_id, category):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        "UPDATE orders SET paid=1 WHERE user_id=? AND category=?",
        (user_id, category)
    )
    con.commit()
    con.close()

# =====================
# BOT
# =====================
bot = Bot(BOT_TOKEN)
dp = Dispatcher()

menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🤖 Создать документ")],
        [KeyboardButton(text="ℹ️ Оплата")],
    ],
    resize_keyboard=True
)

user_state = {}

# =====================
# HELPERS
# =====================
def unique_amount(base):
    return base * 100 + random.randint(11, 99)

def make_code(uid, cat):
    return f"LAW-{cat.upper()}-{uid}"

# =====================
# HANDLERS
# =====================
@dp.message(Command("start"))
async def start(msg: types.Message):
    await msg.answer(
        "Привет 👋\n\n"
        "Я помогу составить юридический документ.\n"
        "Оплата — переводом на карту.\n\n"
        "Нажми «🤖 Создать документ»",
        reply_markup=menu
    )

@dp.message(lambda m: m.text == "ℹ️ Оплата")
async def payment_info(msg: types.Message):
    await msg.answer(
        "💳 ОПЛАТА ПЕРЕВОДОМ НА КАРТУ\n\n"
        "НОМЕР КАРТЫ:\n"
        f"{CARD_NUMBER}\n\n"
        "ПОЛУЧАТЕЛЬ:\n"
        f"{CARD_HOLDER}\n\n"
        "⚠️ Сумму и код бот выдаст после выбора документа.\n"
        "Копейки ОБЯЗАТЕЛЬНЫ.",
        reply_markup=menu
    )

@dp.message(lambda m: m.text == "🤖 Создать документ")
async def choose_category(msg: types.Message):
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"{v[0]} — от {v[1]} ₽", callback_data=k)]
            for k, v in CATEGORIES.items()
        ]
    )
    await msg.answer("Выбери тип документа:", reply_markup=kb)

@dp.callback_query()
async def category_selected(call: types.CallbackQuery):
    cat = call.data
    title, price = CATEGORIES[cat]
    uid = call.from_user.id

    amount = unique_amount(price)
    code = make_code(uid, cat)

    save_order(uid, cat, amount, code)
    user_state[uid] = cat

    await call.message.answer(
        f"💳 Оплата: {title}\n\n"
        f"ТОЧНАЯ СУММА:\n{amount / 100:.2f} ₽\n\n"
        "НОМЕР КАРТЫ:\n"
        f"{CARD_NUMBER}\n\n"
        f"ПОЛУЧАТЕЛЬ:\n{CARD_HOLDER}\n\n"
        f"КОД:\n{code}\n\n"
        "После перевода отправь ОДНИМ сообщением:\n"
        "сумма + код\n"
        f"Пример:\n{amount / 100:.2f} {code}"
    )

@dp.message()
async def handle_text(msg: types.Message):
    uid = msg.from_user.id
    if uid not in user_state:
        return

    cat = user_state[uid]
    row = get_order(uid, cat)
    if not row:
        return

    amount, code, created, paid = row
    if time.time() - created > ORDER_TTL:
        await msg.answer("⛔ Время оплаты истекло. Начни заново.")
        return

    text = msg.text.upper()
    if f"{amount/100:.2f}" in text and code in text:
        confirm_payment(uid, cat)
        await msg.answer(
            "✅ Оплата подтверждена.\n\n"
            "Теперь опиши ситуацию ОДНИМ сообщением."
        )
        user_state.pop(uid)
    else:
        await msg.answer(
            "❌ Не совпадает сумма или код.\n"
            f"Нужно:\n{amount/100:.2f} {code}"
        )

# =====================
# RUN
# =====================
async def main():
    init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
