import asyncio
import os
import re
from typing import Tuple

import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton


# =========================
# ENV (Railway Variables)
# =========================
BOT_TOKEN = os.environ.get("BOT_TOKEN")  # В Railway переменная называется BOT_TOKEN
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
# BOT INIT
# =========================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

menu_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🤖 Сгенерировать документ")],
        [KeyboardButton(text="ℹ️ Оплата")],
        [KeyboardButton(text="🆘 Помощь")],
    ],
    resize_keyboard=True
)

# =========================
# YandexGPT config
# =========================
YANDEX_COMPLETION_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
YANDEX_MODEL_URI = f"gpt://{YANDEX_FOLDER_ID}/yandexgpt/latest"
TIMEOUT = aiohttp.ClientTimeout(total=75)


# =========================
# Helpers
# =========================
def norm_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def chunk_text(s: str, chunk_size: int = 3500):
    for i in range(0, len(s), chunk_size):
        yield s[i:i + chunk_size]


async def yandexgpt_completion(system_text: str, user_text: str, max_tokens: int = 1800, temperature: float = 0.2) -> Tuple[bool, str]:
    body = {
        "modelUri": YANDEX_MODEL_URI,
        "completionOptions": {
            "stream": False,
            "temperature": temperature,
            "maxTokens": str(max_tokens),
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
    Режим B: определяем тип документа автоматически.
    Включили ЗАЯВЛЕНИЕ В ПОЛИЦИЮ отдельно, чтобы кражи/мошенничества не уходили в 'ЖАЛОБА'.
    """
    system = (
        "Ты юридический классификатор. Верни ТОЛЬКО ОДНО значение строго из списка:\n"
        "ЗАЯВЛЕНИЕ В ПОЛИЦИЮ\n"
        "ПРЕТЕНЗИЯ\n"
        "ИСКОВОЕ ЗАЯВЛЕНИЕ\n"
        "ЖАЛОБА\n"
        "ХОДАТАЙСТВО\n"
        "ОБЪЯСНИТЕЛЬНАЯ\n\n"
        "Правила:\n"
        "- Кража/мошенничество/угрозы/побои/вымогательство/преступление -> ЗАЯВЛЕНИЕ В ПОЛИЦИЮ.\n"
        "- Спор с продавцом/услугой/возврат денег ДО суда -> ПРЕТЕНЗИЯ.\n"
        "- Нужно подать в суд -> ИСКОВОЕ ЗАЯВЛЕНИЕ.\n"
        "- Обращение в госорган/инстанцию (прокуратура, администрация, УК, Роспотребнадзор и т.п.) -> ЖАЛОБА.\n"
        "- Процессуальная просьба суду/следствию/органу -> ХОДАТАЙСТВО.\n"
        "- Объяснение работодателю/полиции по инциденту -> ОБЪЯСНИТЕЛЬНАЯ.\n"
        "Никаких пояснений. Только одно значение из списка."
    )
    ok, out = await yandexgpt_completion(system, problem_text, max_tokens=50, temperature=0.0)
    if not ok:
        return "ЖАЛОБА"

    out = norm_text(out).upper()
    out = re.sub(r"[^А-ЯЁ\s]", "", out).strip()

    allowed = {
        "ЗАЯВЛЕНИЕ В ПОЛИЦИЮ",
        "ПРЕТЕНЗИЯ",
        "ИСКОВОЕ ЗАЯВЛЕНИЕ",
        "ЖАЛОБА",
        "ХОДАТАЙСТВО",
        "ОБЪЯСНИТЕЛЬНАЯ",
    }
    if out in allowed:
        return out

    for a in allowed:
        if a in out:
            return a

    return "ЖАЛОБА"


def build_prompt(doc_type: str, user_text: str) -> str:
    # Для полиции лучше формулировка "Заявление о преступлении"
    title = doc_type
    if doc_type == "ЗАЯВЛЕНИЕ В ПОЛИЦИЮ":
        title = "ЗАЯВЛЕНИЕ О ПРЕСТУПЛЕНИИ"

    return f"""
Сгенерируй документ на русском языке: "{title}" по описанию ниже.

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
6) Раздел "Прошу" — 3–8 пунктов по смыслу.
   - Если это заявление в полицию: добавь пункт "зарегистрировать сообщение в КУСП" (если уместно).
7) "Приложения" — примерный список по смыслу (если уместно).
8) В конце: Дата/Подпись.
9) В конце добавь дисклеймер: "Это не является юридической консультацией..."

Описание пользователя:
{user_text}
""".strip()


async def generate_document(doc_type: str, user_text: str) -> Tuple[bool, str]:
    system = "Ты аккуратный юридический помощник. Не выдумывай факты. Пиши структурировано и официально."
    prompt = build_prompt(doc_type, user_text)
    return await yandexgpt_completion(system, prompt, max_tokens=2200, temperature=0.25)


# =========================
# Handlers
# =========================
@dp.message(Command("start"))
async def start_handler(message: types.Message):
    await message.answer(
        "👋 Привет!\n\n"
        "Напиши свою ситуацию (что случилось и чего хочешь), а я:\n"
        "• сам выберу тип документа\n"
        "• сгенерирую готовый текст\n\n"
        "Можно писать сразу текстом 👇",
        reply_markup=menu_kb
    )


@dp.message(lambda m: m.text == "🆘 Помощь")
async def help_handler(message: types.Message):
    await message.answer(
        "Как писать, чтобы получилось хорошо:\n"
        "• кто/что/где/когда\n"
        "• суммы/даты/названия\n"
        "• чего ты хочешь добиться\n\n"
        "Пример: «Купил телефон, сломался, магазин отказался возвращать деньги…»",
        reply_markup=menu_kb
    )


@dp.message(lambda m: m.text == "ℹ️ Оплата")
async def pay_handler(message: types.Message):
    if CARD_NUMBER or CARD_HOLDER:
        await message.answer(
            "💳 Оплата переводом на карту:\n\n"
            f"Номер карты: {CARD_NUMBER}\n"
            f"Получатель: {CARD_HOLDER}\n\n"
            "После оплаты просто опиши ситуацию — я сгенерирую документ.",
            reply_markup=menu_kb
        )
    else:
        await message.answer(
            "ℹ️ Оплата ещё не настроена.\n"
            "Добавь в Railway Variables: CARD_NUMBER и CARD_HOLDER.",
            reply_markup=menu_kb
        )


@dp.message(lambda m: m.text == "🤖 Сгенерировать документ")
async def gen_button(message: types.Message):
    await message.answer(
        "Ок ✅\n\n"
        "Напиши ситуацию одним сообщением (2–5 предложений).",
        reply_markup=menu_kb
    )


@dp.message()
async def any_text(message: types.Message):
    user_text = norm_text(message.text)
    if not user_text:
        return
    if len(user_text) < 15:
        await message.answer("Напиши чуть подробнее (минимум 2–3 предложения).", reply_markup=menu_kb)
        return

    await message.answer("⏳ Анализирую ситуацию…")
    doc_type = await detect_doc_type(user_text)

    await message.answer(f"✅ Тип документа: **{doc_type}**\n\n⏳ Генерирую текст…", parse_mode="Markdown")

    ok, result = await generate_document(doc_type, user_text)
    if not ok:
        await message.answer(result, reply_markup=menu_kb)
        return

    for part in chunk_text(result):
        await message.answer(part)

    await message.answer("Готово ✅ Если хочешь — допиши детали, я перегенерирую.", reply_markup=menu_kb)


# =========================
# Run
# =========================
async def main():
    # фикс конфликта getUpdates/webhook
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
