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

# =========================
# ENV (Railway Variables)
# =========================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
YANDEX_API_KEY = os.environ.get("YANDEX_API_KEY", "").strip()
YANDEX_FOLDER_ID = os.environ.get("YANDEX_FOLDER_ID", "").strip()

CARD_NUMBER = os.environ.get("CARD_NUMBER", "").strip()       # <-- ПОЛНЫЙ номер карты
CARD_HOLDER = os.environ.get("CARD_HOLDER", "").strip()       # <-- Получатель

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан (Railway Variables)")
if not YANDEX_API_KEY:
    raise ValueError("YANDEX_API_KEY не задан (Railway Variables)")
if not YANDEX_FOLDER_ID:
    raise ValueError("YANDEX_FOLDER_ID не задан (Railway Variables)")
if not CARD_NUMBER:
    raise ValueError("CARD_NUMBER не задан (Railway Variables)")
if not CARD_HOLDER:
    raise ValueError("CARD_HOLDER не задан (Railway Variables)")

# =========================
# Pricing (5 categories)
# =========================
CATEGORIES: Dict[str, Dict] = {
    "police":  {"title": "Заявление в полицию",           "price": 149},
    "claim":   {"title": "Претензия (магазин/услуга)",    "price": 199},
    "compl":   {"title": "Жалоба в госорган",             "price": 179},
    "lawsuit": {"title": "Иск в суд",                     "price": 399},
    "motion":  {"title": "Ходатайство",                   "price": 129},
}

ORDER_TTL_MINUTES = 30
DB_PATH = "payments.db"

# =========================
# YandexGPT
# =========================
YANDEX_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
YANDEX_MODEL_URI = f"gpt://{YANDEX_FOLDER_ID}/yandexgpt/latest"
TIMEOUT = aiohttp.ClientTimeout(total=75)

# =========================
# DB
# =========================
def db_init():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            user_id INTEGER NOT NULL,
            category TEXT NOT NULL,
            amount_cents INTEGER NOT NULL,
            code TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            paid INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, category)
        )
    """)
    con.commit()
    con.close()

def save_order(user_id: int, category: str, amount_cents: int, code: str):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        INSERT OR REPLACE INTO orders (user_id, category, amount_cents, code, created_at, paid)
        VALUES (?, ?, ?, ?, ?, 0)
    """, (user_id, category, amount_cents, code, int(time.time())))
    con.commit()
    con.close()

def get_order(user_id: int, category: str) -> Optional[Tuple[int, str, int, int]]:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT amount_cents, code, created_at, paid FROM orders WHERE user_id=? AND category=?",
                (user_id, category))
    row = cur.fetchone()
    con.close()
    if not row:
        return None
    return int(row[0]), str(row[1]), int(row[2]), int(row[3])

def set_paid(user_id: int, category: str):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("UPDATE orders SET paid=1 WHERE user_id=? AND category=?", (user_id, category))
    con.commit()
    con.close()

def is_paid(user_id: int, category: str) -> bool:
    row = get_order(user_id, category)
    if not row:
        return False
    _, _, _, paid = row
    return paid == 1

def expired(created_at: int) -> bool:
    return (time.time() - created_at) > ORDER_TTL_MINUTES * 60

# =========================
# Helpers
# =========================
def unique_amount(base_rub: int) -> int:
    # уникальные копейки, чтобы "жёстче" отличать оплаты
    return base_rub * 100 + random.randint(11, 99)

def fmt_amount(amount_cents: int) -> str:
    rub = amount_cents // 100
    kop = amount_cents % 100
    return f"{rub}.{kop:02d}"

def make_code(user_id: int, category: str) -> str:
    return f"LAW-{category.upper()}-{user_id}"

def parse_confirm(text: str) -> Tuple[Optional[int], Optional[str]]:
    t = (text or "").upper().strip()
    m_code = re.search(r"(LAW-[A-Z]+-\d+)", t)
    code = m_code.group(1) if m_code else None

    # сумма вида 399.45 или 399,45
    m_amt = re.search(r"(\d{2,6})[.,](\d{2})", t)
    if not m_amt:
        return None, code
    rub = int(m_amt.group(1))
    kop = int(m_amt.group(2))
    return rub * 100 + kop, code

def chunk_text(s: str, chunk_size: int = 3500):
    for i in range(0, len(s), chunk_size):
        yield s[i:i + chunk_size]

def build_prompt(category_key: str, user_text: str) -> str:
    titles = {
        "police": "ЗАЯВЛЕНИЕ О ПРЕСТУПЛЕНИИ",
        "claim": "ПРЕТЕНЗИЯ",
        "compl": "ЖАЛОБА",
        "lawsuit": "ИСКОВОЕ ЗАЯВЛЕНИЕ",
        "motion": "ХОДАТАЙСТВО",
    }
    doc_title = titles.get(category_key, "ЗАЯВЛЕНИЕ")

    return f"""
Составь документ на русском языке: "{doc_title}" по описанию ниже.

Требования:
1) Официальный стиль, структурировано.
2) "Шапка" с пустыми полями (Куда/От/Адрес/Телефон/E-mail).
3) Обстоятельства — только факты, без выдумки.
4) "Прошу" — 3–10 пунктов.
5) Для полиции — добавь пункт про регистрацию сообщения и выдачу талона-уведомления (КУСП).
6) Приложения, Дата/Подпись, дисклеймер "не является юр.консультацией".

Описание пользователя:
{user_text}
""".strip()

async def yandexgpt(system_text: str, user_text: str) -> Tuple[bool, str]:
    body = {
        "modelUri": YANDEX_MODEL_URI,
        "completionOptions": {"stream": False, "temperature": 0.25, "maxTokens": "2200"},
        "messages": [{"role": "system", "text": system_text}, {"role": "user", "text": user_text}],
    }
    headers = {"Authorization": f"Api-Key {YANDEX_API_KEY}", "Content-Type": "application/json"}

    async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
        async with session.post(YANDEX_URL, json=body, headers=headers) as resp:
            raw = await resp.text()
            if resp.status != 200:
                return False, f"Ошибка YandexGPT (HTTP {resp.status}).\n{raw}"
            try:
                data = await resp.json()
                text = data["result"]["alternatives"][0]["message"]["text"]
                return True, text
            except Exception:
                return False, f"Не смог разобрать ответ YandexGPT.\n{raw}"

# =========================
# Bot init
# =========================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

menu_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🤖 Сгенерировать документ")],
        [KeyboardButton(text="💰 Прайс")],
        [KeyboardButton(text="ℹ️ Оплата")],
    ],
    resize_keyboard=True
)

pending_category: Dict[int, str] = {}

def categories_kb() -> InlineKeyboardMarkup:
    rows = []
    for key, v in CATEGORIES.items():
        rows.append([InlineKeyboardButton(text=f"{v['title']} — от {v['price']} ₽", callback_data=f"cat:{key}")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cat:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def price_text() -> str:
    lines = ["Прайс:\n"]
    for v in CATEGORIES.values():
        lines.append(f"• {v['title']} — от {v['price']} ₽")
    lines.append("\nОплата: перевод на карту + подтверждение суммой и кодом.")
    return "\n".join(lines)

# =========================
# Handlers
# =========================
@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer(
        "Привет! Я помогу составить заявление/жалобу/иск.\n\n"
        "Нажми: «🤖 Сгенерировать документ»",
        reply_markup=menu_kb
    )

@dp.message(lambda m: m.text == "💰 Прайс")
async def price(message: types.Message):
    await message.answer(price_text(), reply_markup=menu_kb)

@dp.message(lambda m: m.text == "ℹ️ Оплата")
async def pay_info(message: types.Message):
    # ВАЖНО: без Markdown и без маски — выводим полностью
    await message.answer(
        "Оплата переводом на карту:\n\n"
        "Номер карты:\n"
        f"{CARD_NUMBER}\n\n"
        "Получатель:\n"
        f"{CARD_HOLDER}\n\n"
        "Сумму и код бот выдаст после выбора категории.",
        reply_markup=menu_kb
    )

@dp.message(lambda m: m.text == "🤖 Сгенерировать документ")
async def gen(message: types.Message):
    await message.answer("Выбери категорию:", reply_markup=categories_kb())

@dp.callback_query(lambda c: c.data and c.data.startswith("cat:"))
async def cat_select(call: types.CallbackQuery):
    await call.answer()
    key = call.data.split(":", 1)[1]
    uid = call.from_user.id

    if key == "cancel":
        pending_category.pop(uid, None)
        await call.message.answer("Отменил.", reply_markup=menu_kb)
        return

    if key not in CATEGORIES:
        await call.message.answer("Не понял категорию.", reply_markup=menu_kb)
        return

    pending_category[uid] = key
    cat = CATEGORIES[key]

    if is_paid(uid, key):
        await call.message.answer(
            f"Доступ активен: {cat['title']}\n\nНапиши ситуацию одним сообщением.",
            reply_markup=menu_kb
        )
        return

    amount_cents = unique_amount(cat["price"])
    code = make_code(uid, key)
    save_order(uid, key, amount_cents, code)

    # ВАЖНО: тут тоже выводим ПОЛНУЮ карту
    await call.message.answer(
        f"Оплата: {cat['title']}\n\n"
        f"Точная сумма: {fmt_amount(amount_cents)} ₽\n\n"
        "Номер карты:\n"
        f"{CARD_NUMBER}\n\n"
        "Получатель:\n"
        f"{CARD_HOLDER}\n\n"
        f"Код:\n{code}\n\n"
        "После перевода отправь одним сообщением:\n"
        "сумма + код\n"
        f"Пример: {fmt_amount(amount_cents)} {code}",
        reply_markup=menu_kb
    )

@dp.message()
async def all_text(message: types.Message):
    uid = message.from_user.id
    text = (message.text or "").strip()

    key = pending_category.get(uid)
    if not key:
        return

    row = get_order(uid, key)
    if not row:
        await message.answer("Заказ не найден. Выбери категорию заново.", reply_markup=menu_kb)
        return

    amount_cents, code, created_at, paid = row
    if expired(created_at):
        await message.answer("Время оплаты истекло. Выбери категорию заново.", reply_markup=menu_kb)
        return

    # Если не оплачено — ждём подтверждение
    if paid == 0:
        amt_in, code_in = parse_confirm(text)
        if amt_in is None or code_in is None:
            await message.answer(f"Нужно отправить: {fmt_amount(amount_cents)} {code}", reply_markup=menu_kb)
            return
        if amt_in != amount_cents or code_in != code:
            await message.answer(f"Не совпало. Нужно: {fmt_amount(amount_cents)} {code}", reply_markup=menu_kb)
            return

        set_paid(uid, key)
        await message.answer("Оплата подтверждена ✅\n\nТеперь напиши ситуацию одним сообщением.", reply_markup=menu_kb)
        return

    # Оплачено — генерируем документ
    if len(text) < 15:
        await message.answer("Напиши чуть подробнее (2–3 предложения).", reply_markup=menu_kb)
        return

    await message.answer("Генерирую документ…")
    ok, result = await yandexgpt(
        system_text="Ты аккуратный юридический помощник. Не выдумывай факты. Пиши официально и структурно.",
        user_text=build_prompt(key, text),
    )
    if not ok:
        await message.answer(result, reply_markup=menu_kb)
        return

    for part in chunk_text(result):
        await message.answer(part)
    await message.answer("Готово ✅", reply_markup=menu_kb)

# =========================
# RUN
# =========================
async def main():
    db_init()
    # Важно для Railway: убираем вебхук, чтобы polling работал без конфликтов
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
