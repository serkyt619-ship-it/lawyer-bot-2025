import asyncio
import os
import re
import sqlite3
import time
import random
from typing import Dict, Tuple, Optional

import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
YANDEX_API_KEY = os.environ.get("YANDEX_API_KEY")
YANDEX_FOLDER_ID = os.environ.get("YANDEX_FOLDER_ID")

CARD_NUMBER = os.environ.get("CARD_NUMBER", "")
CARD_HOLDER = os.environ.get("CARD_HOLDER", "")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан (Railway Variables)")
if not YANDEX_API_KEY:
    raise ValueError("YANDEX_API_KEY не задан (Railway Variables)")
if not YANDEX_FOLDER_ID:
    raise ValueError("YANDEX_FOLDER_ID не задан (Railway Variables)")

CATEGORIES: Dict[str, Dict] = {
    "police": {"title": "Заявление в полицию", "price": 149},
    "claim":  {"title": "Претензия продавцу/услуге", "price": 199},
    "compl":  {"title": "Жалоба в госорган", "price": 179},
    "lawsuit":{"title": "Иск в суд", "price": 399},
    "motion": {"title": "Ходатайство", "price": 129},
}

ORDER_TTL_MINUTES = 30

YANDEX_COMPLETION_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
YANDEX_MODEL_URI = f"gpt://{YANDEX_FOLDER_ID}/yandexgpt/latest"
TIMEOUT = aiohttp.ClientTimeout(total=75)

DB_PATH = "payments.db"

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
            verified INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, category)
        )
    """)
    con.commit()
    con.close()

def create_or_replace_order(user_id: int, category: str, amount_cents: int, code: str) -> None:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        INSERT OR REPLACE INTO orders (user_id, category, amount_cents, code, created_at, verified)
        VALUES (?, ?, ?, ?, ?, 0)
    """, (user_id, category, amount_cents, code, int(time.time())))
    con.commit()
    con.close()

def get_order(user_id: int, category: str) -> Optional[Tuple[int, str, int, int]]:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT amount_cents, code, created_at, verified FROM orders WHERE user_id=? AND category=?",
                (user_id, category))
    row = cur.fetchone()
    con.close()
    if not row:
        return None
    return int(row[0]), str(row[1]), int(row[2]), int(row[3])

def mark_verified(user_id: int, category: str):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("UPDATE orders SET verified=1 WHERE user_id=? AND category=?", (user_id, category))
    con.commit()
    con.close()

def is_verified(user_id: int, category: str, ttl_days: int = 30) -> bool:
    row = get_order(user_id, category)
    if not row:
        return False
    amount_cents, code, created_at, verified = row
    if verified != 1:
        return False
    return (time.time() - created_at) <= ttl_days * 86400

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

menu_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🤖 Сгенерировать документ")],
        [KeyboardButton(text="💰 Прайс")],
        [KeyboardButton(text="ℹ️ Оплата")],
        [KeyboardButton(text="🆘 Помощь")],
    ],
    resize_keyboard=True
)

pending_category: Dict[int, str] = {}

def norm_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()

def chunk_text(s: str, chunk_size: int = 3500):
    for i in range(0, len(s), chunk_size):
        yield s[i:i + chunk_size]

def mask_card_number(card: str) -> str:
    digits = re.sub(r"\D", "", card or "")
    if len(digits) < 4:
        return "**** **** **** ****"
    return f"**** **** **** {digits[-4:]}"

def fmt_amount(amount_cents: int) -> str:
    rub = amount_cents // 100
    kop = amount_cents % 100
    return f"{rub}.{kop:02d} ₽"

def make_unique_amount(base_rub: int) -> int:
    kop = random.randint(10, 99)
    return base_rub * 100 + kop

def make_code(user_id: int, category: str) -> str:
    return f"LAW-{category.upper()}-{user_id}"

def category_kb() -> InlineKeyboardMarkup:
    rows = []
    for key, v in CATEGORIES.items():
        rows.append([InlineKeyboardButton(text=f"{v['title']} — от {v['price']} ₽", callback_data=f"cat:{key}")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cat:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def price_text() -> str:
    lines = ["💰 *Прайс (5 категорий)*\n"]
    for _, v in CATEGORIES.items():
        lines.append(f"• *{v['title']}* — от {v['price']} ₽")
    lines.append("\nОплата: уникальная сумма (копейки) + код.")
    return "\n".join(lines)

def parse_confirm(text: str) -> Tuple[Optional[int], Optional[str]]:
    t = (text or "").upper()
    m_code = re.search(r"(LAW-[A-Z]+-\d+)", t)
    code = m_code.group(1) if m_code else None
    m_amt = re.search(r"(\d{2,6})[.,](\d{2})", t)
    if not m_amt:
        return None, code
    rub = int(m_amt.group(1))
    kop = int(m_amt.group(2))
    return rub * 100 + kop, code

async def yandexgpt_completion(system_text: str, user_text: str, max_tokens: int = 1800, temperature: float = 0.2) -> Tuple[bool, str]:
    body = {
        "modelUri": YANDEX_MODEL_URI,
        "completionOptions": {"stream": False, "temperature": temperature, "maxTokens": str(max_tokens)},
        "messages": [{"role": "system", "text": system_text}, {"role": "user", "text": user_text}],
    }
    headers = {"Authorization": f"Api-Key {YANDEX_API_KEY}", "Content-Type": "application/json"}

    async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
        async with session.post(YANDEX_COMPLETION_URL, json=body, headers=headers) as resp:
            raw = await resp.text()
            if resp.status != 200:
                return False, f"❌ Ошибка YandexGPT (HTTP {resp.status}).\n{raw}"
            try:
                data = await resp.json()
                text = data["result"]["alternatives"][0]["message"]["text"]
                return True, text
            except Exception:
                return False, f"❌ Не смог разобрать ответ YandexGPT.\n{raw}"

def build_prompt(category_key: str, user_text: str) -> str:
    if category_key == "police":
        doc_title = "ЗАЯВЛЕНИЕ О ПРЕСТУПЛЕНИИ"
    elif category_key == "claim":
        doc_title = "ПРЕТЕНЗИЯ"
    elif category_key == "compl":
        doc_title = "ЖАЛОБА"
    elif category_key == "lawsuit":
        doc_title = "ИСКОВОЕ ЗАЯВЛЕНИЕ"
    else:
        doc_title = "ХОДАТАЙСТВО"

    return f"""
Сгенерируй документ на русском языке: "{doc_title}" по описанию ниже.

Требования:
1) Официальный стиль, структурировано.
2) "Шапка" с пустыми полями (Куда/От/Адрес/Телефон/E-mail).
3) Обстоятельства — только факты.
4) Прошу — 3–10 пунктов.
5) Для полиции — пункт про КУСП.
6) Приложения, Дата/Подпись, дисклеймер.

Описание пользователя:
{user_text}
""".strip()

async def generate_document(category_key: str, user_text: str) -> Tuple[bool, str]:
    system = "Ты аккуратный юридический помощник. Не выдумывай факты. Пиши официально."
    prompt = build_prompt(category_key, user_text)
    return await yandexgpt_completion(system, prompt, max_tokens=2200, temperature=0.25)

def order_expired(created_at: int) -> bool:
    return (time.time() - created_at) > ORDER_TTL_MINUTES * 60

@dp.message(Command("start"))
async def start_handler(message: types.Message):
    await message.answer(
        "👋 Привет!\n\n"
        "Оплата усилена:\n"
        "• уникальная сумма (копейки)\n"
        "• уникальный код\n"
        "Доступ открывается только при совпадении суммы+кода.\n\n"
        "Нажми «🤖 Сгенерировать документ».",
        reply_markup=menu_kb
    )

@dp.message(lambda m: m.text == "💰 Прайс")
async def price_handler(message: types.Message):
    await message.answer(price_text(), parse_mode="Markdown", reply_markup=menu_kb)

@dp.message(lambda m: m.text == "ℹ️ Оплата")
async def pay_handler(message: types.Message):
    if not CARD_NUMBER or not CARD_HOLDER:
        await message.answer("Добавь CARD_NUMBER и CARD_HOLDER в Railway Variables.", reply_markup=menu_kb)
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👁 Показать номер карты", callback_data="showcard")]
    ])
    await message.answer(
        "💳 Оплата переводом на карту.\n\n"
        f"Карта (скрыта): {mask_card_number(CARD_NUMBER)}\n"
        f"Получатель: {CARD_HOLDER}\n\n"
        "Полный номер можно открыть по кнопке (по запросу).",
        reply_markup=kb
    )

@dp.callback_query(lambda c: c.data == "showcard")
async def show_card(call: types.CallbackQuery):
    await call.answer()
    if not CARD_NUMBER:
        await call.message.answer("Карта не настроена.")
        return
    # Показываем полный номер только по запросу
    await call.message.answer(
        f"✅ Полный номер карты для перевода:\n`{CARD_NUMBER}`\n\n"
        "После перевода выбери категорию и подтверди оплату суммой+кодом.",
        parse_mode="Markdown",
        reply_markup=menu_kb
    )

@dp.message(lambda m: m.text == "🆘 Помощь")
async def help_handler(message: types.Message):
    await message.answer(
        "Порядок:\n"
        "1) Выбери категорию\n"
        "2) Получи уникальную сумму и код\n"
        "3) Переведи точную сумму\n"
        "4) Отправь боту: `сумма код`\n"
        "5) Напиши ситуацию — получишь документ\n",
        reply_markup=menu_kb
    )

@dp.message(lambda m: m.text == "🤖 Сгенерировать документ")
async def gen_btn(message: types.Message):
    await message.answer("Выбери категорию 👇", reply_markup=menu_kb)
    await message.answer("Категории:", reply_markup=category_kb())

@dp.callback_query(lambda c: c.data and c.data.startswith("cat:"))
async def cat_select(call: types.CallbackQuery):
    await call.answer()
    key = call.data.split(":", 1)[1]

    if key == "cancel":
        pending_category.pop(call.from_user.id, None)
        await call.message.answer("Ок, отменил ✅", reply_markup=menu_kb)
        return

    if key not in CATEGORIES:
        await call.message.answer("Не понял категорию.", reply_markup=menu_kb)
        return

    user_id = call.from_user.id
    pending_category[user_id] = key
    cat = CATEGORIES[key]

    if is_verified(user_id, key):
        await call.message.answer(
            f"✅ Доступ активен: {cat['title']}\n\nНапиши ситуацию (2–8 предложений).",
            reply_markup=menu_kb
        )
        return

    amount_cents = make_unique_amount(cat["price"])
    code = make_code(user_id, key)
    create_or_replace_order(user_id, key, amount_cents, code)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить оплату", callback_data="confirm")],
        [InlineKeyboardButton(text="🔄 Сменить сумму", callback_data="regen")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")],
    ])

    await call.message.answer(
        f"💳 Оплата: *{cat['title']}*\n\n"
        f"Переведи точную сумму: *{fmt_amount(amount_cents)}*\n"
        f"На карту: {mask_card_number(CARD_NUMBER)}\n"
        f"Получатель: {CARD_HOLDER}\n"
        f"Код: `{code}`\n\n"
        f"Срок: {ORDER_TTL_MINUTES} минут.\n\n"
        "После перевода отправь боту: `сумма код`\n"
        f"Пример: `{fmt_amount(amount_cents).replace(' ₽','')} {code}`",
        parse_mode="Markdown",
        reply_markup=kb
    )

@dp.callback_query(lambda c: c.data in {"confirm", "regen", "cancel"})
async def pay_actions(call: types.CallbackQuery):
    await call.answer()
    user_id = call.from_user.id

    if call.data == "cancel":
        pending_category.pop(user_id, None)
        await call.message.answer("Ок, отменил ✅", reply_markup=menu_kb)
        return

    if user_id not in pending_category:
        await call.message.answer("Сначала выбери категорию.", reply_markup=menu_kb)
        return

    key = pending_category[user_id]
    cat = CATEGORIES[key]

    if call.data == "regen":
        amount_cents = make_unique_amount(cat["price"])
        code = make_code(user_id, key)
        create_or_replace_order(user_id, key, amount_cents, code)
        await call.message.answer(
            f"🔄 Новая сумма: *{fmt_amount(amount_cents)}*\nКод: `{code}`\n"
            "Переведи и отправь: `сумма код`",
            parse_mode="Markdown",
            reply_markup=menu_kb
        )
        return

    row = get_order(user_id, key)
    if not row:
        await call.message.answer("Заказ не найден. Выбери категорию заново.", reply_markup=menu_kb)
        return
    amount_cents, code, created_at, verified = row
    if order_expired(created_at):
        await call.message.answer("⛔ Срок оплаты истёк. Выбери категорию заново.", reply_markup=menu_kb)
        return

    await call.message.answer(
        f"Отправь подтверждение: `{fmt_amount(amount_cents).replace(' ₽','')} {code}`",
        parse_mode="Markdown",
        reply_markup=menu_kb
    )

@dp.message()
async def text_handler(message: types.Message):
    user_id = message.from_user.id
    text = norm_text(message.text)

    key = pending_category.get(user_id)
    if not key:
        if text and text.lower() not in ("/start",):
            await message.answer("Нажми «🤖 Сгенерировать документ» и выбери категорию.", reply_markup=menu_kb)
        return

    cat = CATEGORIES[key]

    if is_verified(user_id, key):
        if len(text) < 15:
            await message.answer("Напиши чуть подробнее (2–3 предложения).", reply_markup=menu_kb)
            return
        await message.answer("⏳ Генерирую документ…")
        ok, result = await generate_document(key, text)
        if not ok:
            await message.answer(result, reply_markup=menu_kb)
            return
        for part in chunk_text(result):
            await message.answer(part)
        await message.answer("Готово ✅", reply_markup=menu_kb)
        return

    row = get_order(user_id, key)
    if not row:
        await message.answer("Заказ не найден. Выбери категорию заново.", reply_markup=menu_kb)
        return

    amount_cents, code, created_at, verified = row
    if order_expired(created_at):
        await message.answer("⛔ Срок оплаты истёк. Выбери категорию заново.", reply_markup=menu_kb)
        return

    amt_in, code_in = parse_confirm(text)
    if amt_in is None or code_in is None:
        await message.answer(
            f"❌ Нужны сумма и код.\nПример: `{fmt_amount(amount_cents).replace(' ₽','')} {code}`",
            parse_mode="Markdown",
            reply_markup=menu_kb
        )
        return

    if amt_in != amount_cents or code_in != code:
        await message.answer(
            f"❌ Не совпало.\nНужно: *{fmt_amount(amount_cents)}* и `{code}`",
            parse_mode="Markdown",
            reply_markup=menu_kb
        )
        return

    mark_verified(user_id, key)
    await message.answer(
        f"✅ Оплата подтверждена: *{cat['title']}*\n\nТеперь напиши ситуацию (2–8 предложений).",
        parse_mode="Markdown",
        reply_markup=menu_kb
    )

async def main():
    db_init()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
