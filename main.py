import asyncio
import os
import re
from typing import Optional, Tuple

import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from dotenv import load_dotenv

load_dotenv()

# --- Env ---
BOT_TOKEN = os.getenv("BOT_TOKEN")

YANDEX_API_KEY = os.getenv("YANDEX_API_KEY")
YANDEX_FOLDER_ID = os.getenv("YANDEX_FOLDER_ID")

CARD_NUMBER = os.getenv("CARD_NUMBER", "")
CARD_HOLDER = os.getenv("CARD_HOLDER", "")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан")
if not YANDEX_API_KEY:
    raise ValueError("YANDEX_API_KEY не задан (Railway Variables)")
if not YANDEX_FOLDER_ID:
    raise ValueError("YANDEX_FOLDER_ID не задан (Railway Variables)")

# --- Bot ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- UI ---
menu_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🤖 Сгенерировать заявление")],
        [KeyboardButton(text="ℹ️ Оплата")],
        [KeyboardButton(text="🆘 Помощь")],
    ],
    resize_keyboard=True
)

# --- YandexGPT settings ---
YANDEX_COMPLETION_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
YANDEX_MODEL_URI = f"gpt://{YANDEX_FOLDER_ID}/yandexgpt/latest"

TIMEOUT = aiohttp.ClientTimeout(total=75)

# --- Helpers ---
def strip_tg(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()

def safe_len(text: str) -> int:
    return len(text.encode("utf-8"))

def chunk_text(s: str, chunk_size: int = 3500):
    # Telegram message limit ~4096 chars; держим запас
    for i in range(0, len(s), chunk_size):
        yield s[i:i + chunk_size]

async def yandexgpt_completion(system_text: str, user_text: str, max_tokens: int = 1800, temperature: float = 0.2) -> Tuple[bool, str]:
    body = {
        "modelUri": YANDEX_MODEL_URI,
        "completionOptions": {
            "stream": False,
            "temperature": temperature,
            "maxTokens": str(max_tokens),
            "reasoningOptions": {"mode": "DISABLED"},
        },
        "messages": [
            {"role": "system", "text": system_text},
            {"role": "user", "text": user_text},
        ],
    }

    headers = {
        "Authorization": f"Api-Key {YANDEX_API_KEY}",
        "Content-Type": "application/json",
    }

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

async def detect_doc_type(problem_text: str) -> str:
    """
    Вариант B: бот сам определяет тип документа.
    Возвращает одно из:
    ЖАЛОБА | ИСКОВОЕ ЗАЯВЛЕНИЕ | ХОДАТАЙСТВО | ОБЪЯСНИТЕЛЬНАЯ | ПРЕТЕНЗИЯ
    """
    system = (
        "Ты юридический классификатор. "
        "Верни ТОЛЬКО ОДНО значение строго из списка:\n"
        "ЖАЛОБА\nИСКОВОЕ ЗАЯВЛЕНИЕ\nХОДАТАЙСТВО\nОБЪЯСНИТЕЛЬНАЯ\nПРЕТЕНЗИЯ\n\n"
        "Правила:\n"
        "- Если спор с продавцом/услугой/деньгами и сначала досудебно — ПРЕТЕНЗИЯ.\n"
        "- Если в суд — ИСКОВОЕ ЗАЯВЛЕНИЕ.\n"
        "- Если обращение в госорган/инстанцию — ЖАЛОБА.\n"
        "- Если просьба суду/следствию/органу о процессуальном действии — ХОДАТАЙСТВО.\n"
        "- Если объяснить инцидент работодателю/полиции — ОБЪЯСНИТЕЛЬНАЯ.\n"
        "Никаких пояснений, только одно слово/строка из списка."
    )
    ok, out = await yandexgpt_completion(system, problem_text, max_tokens=40, temperature=0.0)
    if not ok:
        # если классификация упала — дефолт
        return "ЖАЛОБА"
    out = strip_tg(out).upper()
    # нормализуем
    allowed = {"ЖАЛОБА", "ИСКОВОЕ ЗАЯВЛЕНИЕ", "ХОДАТАЙСТВО", "ОБЪЯСНИТЕЛЬНАЯ", "ПРЕТЕНЗИЯ"}
    # иногда модель может вернуть с точкой/кавычками
    out = re.sub(r'[^А-ЯЁ\s]', '', out).strip()
    if out in allowed:
        return out
    # попробуем частичные совпадения
    for a in allowed:
        if a in out:
            return a
    return "ЖАЛОБА"

def build_generation_prompt(doc_type: str, user_text: str) -> str:
    return f"""
Сгенерируй документ на русском языке: "{doc_type}" по описанию ниже.

Требования к документу:
1) Официальный юридический стиль, понятно и структурировано.
2) В начале "шапка" с полями (оставить пустыми):
   - Куда: (орган/суд/организация)
   - От: ФИО
   - Адрес
   - Телефон
   - E-mail
3) Далее заголовок документа.
4) Блок "Обстоятельства" — изложи факты из описания пользователя (не выдумывай).
5) Блок "Правовое обоснование" — общие формулировки про законодательство РФ (без точных статей, если не уверен).
6) Блок "Прошу" — 3–7 пунктов по смыслу.
7) "Приложения" — примерный список по смыслу (если уместно).
8) В конце: Дата/Подпись.
9) В конце добавь: "Это не является юридической консультацией. Для точности обратитесь к юристу."

Описание пользователя:
{user_text}
""".strip()

async def generate_document(doc_type: str, user_text: str) -> Tuple[bool, str]:
    system = "Ты аккуратный юридический помощник. Пишешь официально, структурировано, без выдуманных фактов."
    prompt = build_generation_prompt(doc_type, user_text)
    return await yandexgpt_completion(system, prompt, max_tokens=2000, temperature=0.25)

# --- Handlers ---
@dp.message(Command("start"))
async def start_handler(message: types.Message):
    await message.answer(
        "👋 Привет!\n\n"
        "Напиши свою ситуацию (что случилось и чего хочешь), а я:\n"
        "• сам выберу тип документа\n"
        "• сгенерирую заявление через ИИ (YandexGPT)\n\n"
        "Нажми кнопку или просто напиши текст 👇",
        reply_markup=menu_kb
    )

@dp.message(lambda m: m.text == "🆘 Помощь")
async def help_handler(message: types.Message):
    await message.answer(
        "Как пользоваться:\n"
        "1) Нажми «🤖 Сгенерировать заявление» или просто напиши проблему.\n"
        "2) Укажи: кто/что/где/когда, суммы/даты, чего хочешь добиться.\n\n"
        "Команды:\n"
        "/start — меню\n",
        reply_markup=menu_kb
    )

@dp.message(lambda m: m.text == "ℹ️ Оплата")
async def payment_handler(message: types.Message):
    await message.answer(
        "💳 Оплата переводом на карту:\n\n"
        f"Номер карты: {CARD_NUMBER}\n"
        f"Получатель: {CARD_HOLDER}\n\n"
        "После оплаты просто опиши ситуацию — я сгенерирую документ.",
        reply_markup=menu_kb
    )

@dp.message(lambda m: m.text == "🤖 Сгенерировать заявление")
async def gen_button_handler(message: types.Message):
    await message.answer(
        "Ок ✅\n\n"
        "Напиши свою ситуацию одним сообщением.\n"
        "Пример: «Купил товар, он сломался, продавец не возвращает деньги, хочу вернуть деньги и неустойку…»",
        reply_markup=menu_kb
    )

@dp.message()
async def free_text_handler(message: types.Message):
    user_text = strip_tg(message.text)
    if not user_text:
        return

    # фильтр на слишком короткие сообщения
    if len(user_text) < 15:
        await message.answer("Напиши чуть подробнее (минимум 2–3 предложения).", reply_markup=menu_kb)
        return

    await message.answer("⏳ Анализирую и выбираю тип документа...")
    doc_type = await detect_doc_type(user_text)

    await message.answer(f"✅ Определил тип: **{doc_type}**\n\n⏳ Генерирую документ...", parse_mode="Markdown")

    ok, result = await generate_document(doc_type, user_text)
    if not ok:
        await message.answer(result, reply_markup=menu_kb)
        return

    # отправляем длинные ответы кусками
    for part in chunk_text(result):
        await message.answer(part)

    await message.answer("Готово ✅ Если хочешь — напиши уточнения/факты, я перегенерирую.", reply_markup=menu_kb)

# --- Run ---
async def main():
    # чтобы не было TelegramConflictError
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
