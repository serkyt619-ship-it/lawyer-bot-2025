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
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)

# =========================
# ENV (Railway Variables)
# =========================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

# Google Gemini (ВАЖНО: models/...)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.environ.get(
    "GEMINI_MODEL",
    "models/gemini-1.5-flash"   # ← ПРАВИЛЬНО
).strip()

CARD_NUMBER = os.environ.get("CARD_NUMBER", "").strip()
CARD_HOLDER = os.environ.get("CARD_HOLDER", "").strip()

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан (Railway Variables)")

if not GEMINI_API_KEY:
    print("WARNING: GEMINI_API_KEY не задан (генерация будет недоступна)")

if not CARD_NUMBER or not CARD_HOLDER:
    print("WARNING: CARD_NUMBER или CARD_HOLDER не заданы (оплата будет недоступна)")

# =========================
# Pricing
# =========================
CATEGORIES: Dict[str, Dict] = {
    "police":  {"title": "Заявление в полицию",        "price": 149},
    "claim":   {"title": "Претензия",                  "price": 199},
    "compl":   {"title": "Жалоба",                     "price": 179},
    "lawsuit": {"title": "Исковое заявление",          "price": 399},
    "motion":  {"title": "Ходатайство",                "price": 129},
}

ORDER_TTL_MINUTES = 30
DB_PATH = "payments.db"

# =========================
# Gemini API
# =========================
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/{GEMINI_MODEL}:generateContent"
TIMEOUT = aiohttp.ClientTimeout(total=75)

# =========================
# DB
# =========================
def db_init():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            user_id INTEGER,
            category TEXT,
            amount_cents INTEGER,
            code TEXT,
            created_at INTEGER,
            paid INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, category)
        )
    """)
    con.commit()
    con.close()

def save_order(user_id, category, amount, code):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        INSERT OR REPLACE INTO orders
        VALUES (?, ?, ?, ?, ?, 0)
    """, (user_id, category, amount, code, int(time.time())))
    con.commit()
    con.close()

def get_order(user_id, category):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        SELECT amount_cents, code, created_at, paid
        FROM orders WHERE user_id=? AND category=?
    """, (user_id, category))
    row = cur.fetchone()
    con.close()
    return row

def set_paid(user_id, category):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        UPDATE orders SET paid=1
        WHERE user_id=? AND category=?
    """, (user_id, category))
    con.commit()
    con.close()

# =========================
# Helpers
# =========================
def unique_amount(price):
    return price * 100 + random.randint(11, 99)

def fmt_amount(cents):
    return f"{cents//100}.{cents%100:02d}"

def make_code(uid, cat):
    return f"LAW-{cat.upper()}-{uid}"

def parse_confirm(text):
    t = text.upper()
    m_code = re.search(r"(LAW-[A-Z]+-\d+)", t)
    m_amt = re.search(r"(\d+)[.,](\d{2})", t)
    if not m_code or not m_amt:
        return None, None
    return int(m_amt.group(1))*100+int(m_amt.group(2)), m_code.group(1)

def build_prompt(cat, text):
    return f"""
Составь официальный юридический документ на русском языке.

Тип документа: {CATEGORIES[cat]['title']}

Требования:
— официальный стиль
— шапка (Куда / От / Адрес / Телефон)
— обстоятельства по фактам
— раздел «Прошу»
— приложения
— дата и подпись
— дисклеймер: не является юридической консультацией

Описание ситуации:
{text}
""".strip()

async def gemini(system_text, user_text):
    if not GEMINI_API_KEY:
        return False, "❌ Gemini не настроен. Добавь GEMINI_API_KEY в Railway Variables."

    payload = {
        "systemInstruction": {"parts": [{"text": system_text}]},
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "generationConfig": {"temperature": 0.25, "maxOutputTokens": 2200}
    }

    async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
        async with session.post(
            GEMINI_URL,
            params={"key": GEMINI_API_KEY},
            json=payload
        ) as r:
            data = await r.json()
            if r.status != 200:
                return False, str(data)
            text = "".join(
                p["text"] for p in data["candidates"][0]["content"]["parts"]
            )
            return True, text

# =========================
# Bot
# =========================
bot = Bot(BOT_TOKEN)
dp = Dispatcher()

menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🤖 Сгенерировать документ")],
        [KeyboardButton(text="💰 Прайс"), KeyboardButton(text="ℹ️ Оплата")],
    ],
    resize_keyboard=True
)

pending = {}

def cats_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text=f"{v['title']} — {v['price']} ₽",
                callback_data=f"cat:{k}"
            )] for k,v in CATEGORIES.items()
        ]
    )

@dp.message(Command("start"))
async def start(m: types.Message):
    await m.answer("Привет! Я помогу составить документ.", reply_markup=menu)

@dp.message(lambda m: m.text == "🤖 Сгенерировать документ")
async def gen(m):
    await m.answer("Выбери категорию:", reply_markup=cats_kb())

@dp.callback_query(lambda c: c.data.startswith("cat:"))
async def cat(c):
    uid = c.from_user.id
    key = c.data.split(":")[1]
    pending[uid] = key
    amt = unique_amount(CATEGORIES[key]["price"])
    code = make_code(uid, key)
    save_order(uid, key, amt, code)

    await c.message.answer(
        f"Оплата {CATEGORIES[key]['title']}\n"
        f"Сумма: {fmt_amount(amt)} ₽\n"
        f"Карта: {CARD_NUMBER}\n"
        f"Получатель: {CARD_HOLDER}\n"
        f"Код: {code}\n\n"
        f"После оплаты отправь: сумма + код",
        reply_markup=menu
    )

@dp.message()
async def text(m):
    uid = m.from_user.id
    if uid not in pending:
        return

    cat = pending[uid]
    row = get_order(uid, cat)
    if not row:
        return

    amount, code, created, paid = row

    if not paid:
        a, c = parse_confirm(m.text)
        if a != amount or c != code:
            await m.answer("❌ Сумма или код неверны")
            return
        set_paid(uid, cat)
        await m.answer("✅ Оплата подтверждена. Опиши ситуацию.")
        return

    ok, res = await gemini(
        "Ты юридический помощник.",
        build_prompt(cat, m.text)
    )
    await m.answer(res if ok else f"Ошибка: {res}")

# =========================
# RUN
# =========================
async def main():
    db_init()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
