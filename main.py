import asyncio
import os
import re
import sqlite3
import time
import random
from typing import Dict, Optional, Tuple
from google import genai
import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.exceptions import TelegramConflictError


# =========================
# ENV (Railway Variables)
# =========================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
GEMINI_API_KEY = "AIzaSyBHsx7f2l-wUn_UFZ7oH_s5ZFtZWLSoXH0"
GEMINI_MODEL = "gemini-2.5-flash"
# Gemini
GEMINI_API_KEY = genai.Client(api_key="GEMINI_API_KEY")
 # = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip()

# СБП реквизиты
SBP_PHONE = os.environ.get("SBP_PHONE", "").strip()
SBP_BANK = os.environ.get("SBP_BANK", "").strip()
SBP_HOLDER = os.environ.get("SBP_HOLDER", "").strip()
SBP_NOTE = os.environ.get("SBP_NOTE", "Оплата услуги").strip()

# Разное
DB_PATH = os.environ.get("DB_PATH", "payments.db").strip()
ORDER_TTL_MINUTES = int(os.environ.get("ORDER_TTL_MINUTES", "30").strip() or "30")

HTTP_TIMEOUT_SEC = int(os.environ.get("HTTP_TIMEOUT_SEC", "75").strip() or "75")
GEMINI_RETRIES = int(os.environ.get("GEMINI_RETRIES", "2").strip() or "2")
POLL_RESTART_DELAY_SEC = float(os.environ.get("POLL_RESTART_DELAY_SEC", "3").strip() or "3")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан (Railway Variables)")

if not GEMINI_API_KEY:
    print("WARNING: GEMINI_API_KEY не задан (генерация будет недоступна)")

if not SBP_PHONE or not SBP_BANK or not SBP_HOLDER:
    print("WARNING: SBP_PHONE / SBP_BANK / SBP_HOLDER не заданы (оплата СБП будет недоступна)")


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


# =========================
# Gemini API (REST)
# =========================
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
TIMEOUT = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SEC)
RUNTIME_MODEL: Optional[str] = None




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

    m_amt = re.search(r"(\d{2,6})[.,](\d{2})", t)
    if not m_amt:
        return None, code
    rub = int(m_amt.group(1))
    kop = int(m_amt.group(2))
    return rub * 100 + kop, code

def chunk_text(s: str, chunk_size: int = 3500):
    for i in range(0, len(s), chunk_size):
        yield s[i:i + chunk_size]

def normalize_phone(phone: str) -> str:
    p = (phone or "").strip().replace(" ", "").replace("-", "")
    # оставим + если есть, иначе добавим
    if p and p[0] != "+":
        # если начинается с 8/7, оставим как есть; иначе +7 как типовой
        if p.startswith("8"):
            p = "+7" + p[1:]
        elif p.startswith("7"):
            p = "+" + p
        else:
            p = "+7" + p
    return p

def sbp_text() -> str:
    if not SBP_PHONE or not SBP_BANK or not SBP_HOLDER:
        return "СБП не настроена."
    phone = normalize_phone(SBP_PHONE)
    return (
        f"📲 СБП (по номеру телефона)\n"
        f"Телефон: {phone}\n"
        f"Банк: {SBP_BANK}\n"
        f"Получатель: {SBP_HOLDER}\n"
        f"Назначение: {SBP_NOTE}"
    )

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


# =========================
# Gemini: model discovery + generateContent
# =========================
async def gemini_list_models(session: aiohttp.ClientSession) -> Tuple[bool, str, Optional[list]]:
    if not GEMINI_API_KEY:
        return False, "GEMINI_API_KEY не задан.", None

    url = f"{GEMINI_BASE}/models?key={GEMINI_API_KEY}"
    async with session.get(url) as resp:
        raw = await resp.text()
        if resp.status != 200:
            return False, f"ListModels ошибка (HTTP {resp.status}).\n{raw}", None
        try:
            data = await resp.json()
            return True, "ok", data.get("models", [])
        except Exception:
            return False, f"ListModels: не смог разобрать JSON.\n{raw}", None

def pick_best_model(models: list) -> Optional[str]:
    if not models:
        return None

    candidates = []
    for m in models:
        name = m.get("name")
        methods = m.get("streamGenerateContent", []) or []
        if not name:
            continue
        if "generateContent" not in methods:
            continue
        candidates.append(name)

    if not candidates:
        return None

    def score(n: str) -> int:
        nlow = n.lower()
        if "gemini-2.5-flash" in nlow:
            return 300
        if "gemini-1.5-pro" in nlow:
            return 200
        if "gemini-1.0-pro" in nlow:
            return 150
        if "gemini" in nlow:
            return 100
        return 0

    candidates.sort(key=score, reverse=True)
    return candidates[0]

async def ensure_runtime_model(session: aiohttp.ClientSession) -> Optional[str]:
    global RUNTIME_MODEL
    if RUNTIME_MODEL:
        return RUNTIME_MODEL

    # Нормализуем: если без "models/" — добавим
    model = GEMINI_MODEL
    if not model.startswith("models/"):
        model = "models/" + model
    RUNTIME_MODEL = model
    return RUNTIME_MODEL

async def gemini_generate(system_text: str, user_text: str) -> Tuple[bool, str]:
    if not GEMINI_API_KEY:
        return False, "Генерация недоступна: GEMINI_API_KEY не задан (Railway Variables)."

    async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
        await ensure_runtime_model(session)

        last_error = None
        for attempt in range(GEMINI_RETRIES + 1):
            model = RUNTIME_MODEL or "models/gemini-1.5-flash"
            url = f"{GEMINI_BASE}/{model}:generateContent?key={GEMINI_API_KEY}"

            # SystemInstruction тоже можно, но безопаснее и совместимее объединить в один user prompt
            body = {
                "contents": [
                    {"role": "user", "parts": [{"text": f"{system_text}\n\n{user_text}"}]}
                ],
                "generationConfig": {
                    "temperature": 0.25,
                    "maxOutputTokens": 2200
                }
            }

            try:
                async with session.post(url, json=body) as resp:
                    raw = await resp.text()

                    if resp.status == 404:
                        ok, _, models = await gemini_list_models(session)
                        if ok and models:
                            picked = pick_best_model(models)
                            if picked:
                                RUNTIME_MODEL = picked
                                last_error = f"Модель {model} не подошла, переключился на {picked}."
                                continue
                        return False, f"Ошибка Gemini (HTTP 404). Модель не поддерживается.\n{raw}"

                    if resp.status != 200:
                        last_error = f"Ошибка Gemini (HTTP {resp.status}).\n{raw}"
                        await asyncio.sleep(0.7 * (attempt + 1))
                        continue

                    try:
                        data = await resp.json()
                        candidates = data.get("candidates", [])
                        if not candidates:
                            return False, f"Gemini: пустой ответ.\n{raw}"

                        content = candidates[0].get("content", {})
                        parts = content.get("parts", []) or []
                        text = ""
                        for p in parts:
                            if "text" in p:
                                text += p["text"]

                        if not text.strip():
                            return False, f"Gemini: ответ без текста.\n{raw}"

                        return True, text.strip()

                    except Exception:
                        return False, f"Не смог разобрать ответ Gemini.\n{raw}"

            except Exception as e:
                last_error = f"Ошибка сети Gemini: {e}"
                await asyncio.sleep(0.7 * (attempt + 1))

        return False, last_error or "Неизвестная ошибка Gemini."


# =========================
# Bot init
# =========================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

menu_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🤖 Сгенерировать документ")],
        [KeyboardButton(text="💰 Прайс")],
        [KeyboardButton(text="ℹ️ Оплата (СБП)")],
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
    lines.append("\nОплата: перевод по СБП + подтверждение суммой и кодом.")
    return "\n".join(lines)


# =========================
# Handlers
# =========================
@dp.message(Command("start"))
async def start(message: types.Message):
    warn = []
    if not GEMINI_API_KEY:
        warn.append("⚠️ GEMINI_API_KEY не задан — генерация временно недоступна.")
    if not SBP_PHONE or not SBP_BANK or not SBP_HOLDER:
        warn.append("⚠️ СБП не настроена (SBP_PHONE/SBP_BANK/SBP_HOLDER).")

    warn_text = ("\n\n" + "\n".join(warn)) if warn else ""

    await message.answer(
        "Привет! Я помогу составить заявление/жалобу/иск.\n\n"
        "Нажми: «🤖 Сгенерировать документ»" + warn_text,
        reply_markup=menu_kb
    )

@dp.message(lambda m: m.text == "💰 Прайс")
async def price(message: types.Message):
    await message.answer(price_text(), reply_markup=menu_kb)

@dp.message(lambda m: m.text == "ℹ️ Оплата (СБП)")
async def pay_info(message: types.Message):
    if not SBP_PHONE or not SBP_BANK or not SBP_HOLDER:
        await message.answer(
            "Оплата по СБП пока не настроена.\n"
            "Нужны переменные: SBP_PHONE, SBP_BANK, SBP_HOLDER.",
            reply_markup=menu_kb
        )
        return

    await message.answer(
        "Оплата переводом по СБП:\n\n"
        f"{sbp_text()}\n\n"
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

    if not SBP_PHONE or not SBP_BANK or not SBP_HOLDER:
        await call.message.answer(
            "Оплата по СБП не настроена (SBP_PHONE/SBP_BANK/SBP_HOLDER).",
            reply_markup=menu_kb
        )
        return

    amount_cents = unique_amount(cat["price"])
    code = make_code(uid, key)
    save_order(uid, key, amount_cents, code)

    await call.message.answer(
        f"Оплата: {cat['title']}\n\n"
        f"Точная сумма: {fmt_amount(amount_cents)} ₽\n\n"
        f"{sbp_text()}\n\n"
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

    if len(text) < 15:
        await message.answer("Напиши чуть подробнее (2–3 предложения).", reply_markup=menu_kb)
        return

    await message.answer("Генерирую документ…")
    ok, result = await gemini_generate(
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

    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception:
        pass

    while True:
        try:
            await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
        except TelegramConflictError as e:
            print(f"TelegramConflictError: {e}")
            await asyncio.sleep(max(5.0, POLL_RESTART_DELAY_SEC))
        except Exception as e:
            print(f"Polling error: {e}")
            await asyncio.sleep(POLL_RESTART_DELAY_SEC)

if __name__ == "__main__":
    asyncio.run(main())
