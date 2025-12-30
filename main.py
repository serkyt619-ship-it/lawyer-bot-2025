import asyncio
import os
import re
import sqlite3
import time
from typing import Dict, Tuple

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

# =========================
# Pricing (5 categories)
# =========================
CATEGORIES: Dict[str, Dict] = {
    "police": {"title": "Заявление в полицию", "price": 149, "doc_hint": "кража/мошенничество/угрозы/побои"},
    "claim":  {"title": "Претензия продавцу/услуге", "price": 199, "doc_hint": "возврат денег/гарантия/услуги"},
    "compl":  {"title": "Жалоба в госорган", "price": 179, "doc_hint": "прокуратура/УК/Роспотребнадзор"},
    "lawsuit":{"title": "Иск в суд", "price": 399, "doc_hint": "взыскать деньги/восстановить права"},
    "motion": {"title": "Ходатайство", "price": 129, "doc_hint": "процессуальная просьба суду/органу"},
}

# =========================
# YandexGPT config
# =========================
YANDEX_COMPLETION_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
YANDEX_MODEL_URI = f"gpt://{YANDEX_FOLDER_ID}/yandexgpt/latest"
TIMEOUT = aiohttp.ClientTimeout(total=75)

# =========================
# DB (payments)
# =========================
DB_PATH = "payments.db"

def db_init():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            user_id INTEGER NOT NULL,
            category TEXT NOT NULL,
            paid_at INTEGER NOT NULL,
            pay_code TEXT NOT NULL,
            PRIMARY KEY (user_id, category)
        )
    """)
    con.commit()
    con.close()

def mark_paid(user_id: int, category: str, pay_code: str):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO payments (user_id, category, paid_at, pay_code) VALUES (?, ?, ?, ?)",
        (user_id, category, int(time.time()), pay_code)
    )
    con.commit()
    con.close()

def is_paid(user_id: int, category: str, ttl_days: int = 30) -> bool:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT paid_at FROM payments WHERE user_id=? AND category=?", (user_id, category))
    row = cur.fetchone()
    con.close()
    if not row:
        return False
    paid_at = int(row[0])
    return (time.time() - paid_at) <= ttl_days * 86400

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
        [KeyboardButton(text="🆘 Помощь")],
    ],
    resize_keyboard=True
)

# =========================
# State (simple, in-memory)
# =========================
# pending[user_id] = {"category": "...", "problem": "..."}
pending = {}

# =========================
# Helpers
# =========================
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

def price_text() -> str:
    lines = ["💰 *Прайс (5 категорий)*\n"]
    for k, v in CATEGORIES.items():
        lines.append(f"• *{v['title']}* — {v['price']} ₽ _(подходит: {v['doc_hint']})_")
    lines.append("\nОплата: переводом на карту (по кнопке «ℹ️ Оплата»).")
    return "\n".join(lines)

def category_kb() -> InlineKeyboardMarkup:
    rows = []
    for key, v in CATEGORIES.items():
        rows.append([InlineKeyboardButton(text=f"{v['title']} — {v['price']} ₽", callback_data=f"cat:{key}")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cat:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def make_pay_code(user_id: int, category: str) -> str:
    # Код, который пользователь пишет в комментарии к переводу (желательно)
    # и потом отправляет боту.
    return f"LAW-{category.upper()}-{user_id}"

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
    # Жёстко задаём тип, потому что категория выбрана пользователем.
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
1) Официальный деловой стиль, чётко и структурировано.
2) В начале "шапка" с полями (оставить пустыми):
   - Куда: (орган/суд/организация)
   - От: ФИО
   - Адрес
   - Телефон
   - E-mail
3) Далее заголовок документа.
4) Раздел "Обстоятельства" — только факты из описания (не выдумывай).
5) Раздел "Правовое обоснование" — общие формулировки права РФ (без точных статей, если не уверен).
6) Раздел "Прошу" — 3–10 пунктов по смыслу.
   - Если это заявление в полицию: добавь пункт "зарегистрировать сообщение в КУСП" (если уместно).
7) "Приложения" — примерный список по смыслу (если уместно).
8) В конце: Дата/Подпись.
9) В конце дисклеймер: "Это не является юридической консультацией..."

Описание пользователя:
{user_text}
""".strip()

async def generate_document(category_key: str, user_text: str) -> Tuple[bool, str]:
    system = "Ты аккуратный юридический помощник. Не выдумывай факты. Пиши структурировано и официально."
    prompt = build_prompt(category_key, user_text)
    return await yandexgpt_completion(system, prompt, max_tokens=2200, temperature=0.25)

# =========================
# Handlers
# =========================
@dp.message(Command("start"))
async def start_handler(message: types.Message):
    await message.answer(
        "👋 Привет!\n\n"
        "1) Нажми «🤖 Сгенерировать документ»\n"
        "2) Выбери категорию\n"
        "3) Если не оплачено — бот покажет реквизиты и код\n"
        "4) После «✅ Я оплатил» откроется генерация\n\n"
        "Оплата показывается только по кнопке «ℹ️ Оплата».",
        reply_markup=menu_kb
    )

@dp.message(lambda m: m.text == "💰 Прайс")
async def price_handler(message: types.Message):
    await message.answer(price_text(), parse_mode="Markdown", reply_markup=menu_kb)

@dp.message(lambda m: m.text == "🆘 Помощь")
async def help_handler(message: types.Message):
    await message.answer(
        "Пиши так:\n"
        "• кто/что/где/когда\n"
        "• суммы/даты/названия\n"
        "• чего хочешь добиться\n\n"
        "После оплаты бот генерирует документ одним сообщением.",
        reply_markup=menu_kb
    )

@dp.message(lambda m: m.text == "ℹ️ Оплата")
async def pay_handler(message: types.Message):
    if CARD_NUMBER or CARD_HOLDER:
        await message.answer(
            "💳 Оплата переводом на карту:\n\n"
            f"Карта: {mask_card_number(CARD_NUMBER)}\n"
            f"Получатель: {CARD_HOLDER}\n\n"
            "Чтобы было проще найти оплату (для вас):\n"
            "в комментарии к переводу пишите код, который покажет бот после выбора категории.",
            reply_markup=menu_kb
        )
    else:
        await message.answer(
            "ℹ️ Оплата ещё не настроена.\n"
            "Добавь в Railway Variables: CARD_NUMBER и CARD_HOLDER.",
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
        await call.message.answer("Ок, отменил ✅", reply_markup=menu_kb)
        return

    if key not in CATEGORIES:
        await call.message.answer("Не понял категорию, попробуй ещё раз.", reply_markup=menu_kb)
        return

    user_id = call.from_user.id
    cat = CATEGORIES[key]

    # Если уже оплачено — сразу просим ситуацию
    if is_paid(user_id, key):
        pending[user_id] = {"category": key, "problem": ""}
        await call.message.answer(
            f"✅ Доступ активен для: {cat['title']}\n\n"
            "Напиши ситуацию одним сообщением (2–8 предложений).",
            reply_markup=menu_kb
        )
        return

    # Если не оплачено — даём реквизиты и код
    pay_code = make_pay_code(user_id, key)
    pending[user_id] = {"category": key, "problem": ""}

    price = cat["price"]
    title = cat["title"]
    masked = mask_card_number(CARD_NUMBER)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"paid:{key}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="paid:cancel")],
    ])

    await call.message.answer(
        f"💳 *Оплата для категории:* {title}\n"
        f"*Стоимость:* {price} ₽\n\n"
        f"*Перевод на карту:* {masked}\n"
        f"*Получатель:* {CARD_HOLDER}\n\n"
        f"✍️ *Код для комментария к переводу:* `{pay_code}`\n\n"
        "После оплаты нажми «✅ Я оплатил» и отправь код (или последние 4 цифры + сумму).",
        parse_mode="Markdown",
        reply_markup=kb
    )

@dp.callback_query(lambda c: c.data and c.data.startswith("paid:"))
async def paid_flow(call: types.CallbackQuery):
    await call.answer()
    user_id = call.from_user.id
    key = call.data.split(":", 1)[1]
    if key == "cancel":
        await call.message.answer("Ок, отменил ✅", reply_markup=menu_kb)
        pending.pop(user_id, None)
        return

    if user_id not in pending or pending[user_id].get("category") != key:
        await call.message.answer("Сначала выбери категорию через «🤖 Сгенерировать документ».", reply_markup=menu_kb)
        return

    cat = CATEGORIES[key]
    code = make_pay_code(user_id, key)

    await call.message.answer(
        "Отправь одним сообщением подтверждение (любой формат):\n\n"
        f"Пример 1: `{code}`\n"
        f"Пример 2: `оплатил {cat['price']} последние4 8545`\n"
        "Пример 3: `скрин есть` (если хочешь)\n\n"
        "⚠️ Важно: это перевод на карту, бот не может проверить банк автоматически.\n"
        "Эта версия открывает доступ после ввода кода.",
        parse_mode="Markdown",
        reply_markup=menu_kb
    )

@dp.message()
async def text_handler(message: types.Message):
    user_id = message.from_user.id
    text = norm_text(message.text)

    # Если пользователь в процессе оплаты/генерации
    if user_id in pending:
        category = pending[user_id].get("category")
        if category not in CATEGORIES:
            pending.pop(user_id, None)
            await message.answer("Ошибка состояния. Нажми «🤖 Сгенерировать документ» заново.", reply_markup=menu_kb)
            return

        # Если ещё не оплачено — считаем любое сообщение подтверждением оплаты (по твоему требованию "без админа")
        if not is_paid(user_id, category):
            pay_code = make_pay_code(user_id, category)

            # Мини-проверка: если человек прислал именно код — отлично. Если нет — всё равно откроем.
            # Можно ужесточить позже.
            mark_paid(user_id, category, pay_code)

            await message.answer(
                f"✅ Оплата зафиксирована.\n\n"
                f"Теперь напиши ситуацию одним сообщением для категории:\n*{CATEGORIES[category]['title']}*",
                parse_mode="Markdown",
                reply_markup=menu_kb
            )
            return

        # Оплачено — генерируем документ
        if len(text) < 15:
            await message.answer("Напиши чуть подробнее (минимум 2–3 предложения).", reply_markup=menu_kb)
            return

        await message.answer("⏳ Генерирую документ…")
        ok, result = await generate_document(category, text)
        if not ok:
            await message.answer(result, reply_markup=menu_kb)
            return

        for part in chunk_text(result):
            await message.answer(part)

        await message.answer("Готово ✅ Если надо — дополни детали, я перегенерирую.", reply_markup=menu_kb)
        return

    # Если не в процессе — подсказка
    if text and text.lower() not in ("/start",):
        await message.answer("Нажми «🤖 Сгенерировать документ» и выбери категорию.", reply_markup=menu_kb)

# =========================
# Run
# =========================
async def main():
    db_init()
    await bot.delete_webhook(drop_pending_updates=True)  # фикс TelegramConflictError
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
