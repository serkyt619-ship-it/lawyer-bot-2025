import asyncio
import os
from typing import Dict, Optional

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from dotenv import load_dotenv
import aiohttp

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CARD_NUMBER = os.getenv("CARD_NUMBER", "")
CARD_HOLDER = os.getenv("CARD_HOLDER", "")

YANDEX_API_KEY = os.getenv("YANDEX_API_KEY")
YANDEX_FOLDER_ID = os.getenv("YANDEX_FOLDER_ID")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан")
if not YANDEX_API_KEY:
    raise ValueError("YANDEX_API_KEY не задан (Railway Variables)")
if not YANDEX_FOLDER_ID:
    raise ValueError("YANDEX_FOLDER_ID не задан (Railway Variables)")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- Настройки YandexGPT ---
YANDEX_COMPLETION_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
YANDEX_MODEL_URI = f"gpt://{YANDEX_FOLDER_ID}/yandexgpt/latest"  # YandexGPT Pro (ветка latest)

# --- Память диалога (простая, без БД) ---
# user_id -> выбранный тип документа
pending_doc_type: Dict[int, str] = {}

# --- Кнопки меню ---
menu_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📄 Жалоба")],
        [KeyboardButton(text="⚖️ Исковое заявление")],
        [KeyboardButton(text="📝 Объяснительная")],
        [KeyboardButton(text="📑 Ходатайство")],
        [KeyboardButton(text="ℹ️ Оплата")],
        [KeyboardButton(text="🤖 ИИ: Сгенерировать документ")],
    ],
    resize_keyboard=True
)

doc_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📄 Жалоба"), KeyboardButton(text="⚖️ Исковое заявление")],
        [KeyboardButton(text="📝 Объяснительная"), KeyboardButton(text="📑 Ходатайство")],
        [KeyboardButton(text="⬅️ Назад")],
    ],
    resize_keyboard=True
)


def build_prompt(doc_type: str, user_text: str) -> str:
    """
    Формируем промпт: ИИ генерирует документ в официальном стиле с пустыми полями.
    """
    return f"""
Сгенерируй документ на русском языке: "{doc_type}".
Стиль: официальный, юридический, максимально понятный.

Требования:
1) Добавь "шапку" с полями:
   - Куда: (орган/суд/организация — оставить пустым)
   - От кого: ФИО, адрес, телефон, e-mail — оставить пустым
2) Далее: "Заявление/Жалоба/Иск/Ходатайство" (по типу документа)
3) Изложи обстоятельства по тексту пользователя (ниже), но аккуратно и структурировано.
4) Добавь правовую часть: упомяни, что заявитель просит рассмотреть обращение и принять меры согласно законодательству РФ (без точных статей, если не уверен).
5) Добавь просительную часть пунктами (3–6 пунктов, по смыслу).
6) В конце: "Приложения:" (список примерных приложений по смыслу) + "Дата/Подпись".
7) Не выдумывай факты — используй только то, что есть в описании.
8) Добавь короткую приписку в конце: "Это не является юридической консультацией. Для точности обратитесь к юристу."

Описание пользователя:
{user_text}
""".strip()


async def yandexgpt_generate(doc_type: str, user_text: str) -> str:
    """
    Вызов YandexGPT через REST completion.
    Auth: Authorization: Api-Key <API_key>
    """
    prompt = build_prompt(doc_type, user_text)

    body = {
        "modelUri": YANDEX_MODEL_URI,
        "completionOptions": {
            "stream": False,
            "temperature": 0.3,
            "maxTokens": "1800",
            "reasoningOptions": {"mode": "DISABLED"},
        },
        "messages": [
            {"role": "system", "text": "Ты аккуратный юридический помощник. Пишешь официально и структурировано."},
            {"role": "user", "text": prompt},
        ],
    }

    headers = {
        "Authorization": f"Api-Key {YANDEX_API_KEY}",
        "Content-Type": "application/json",
    }

    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(YANDEX_COMPLETION_URL, json=body, headers=headers) as resp:
            text = await resp.text()
            if resp.status != 200:
                # Возвращаем понятную ошибку
                return f"❌ Ошибка YandexGPT (HTTP {resp.status}). Ответ:\n{text}"

            data = await resp.json()

    # В ответе обычно: result -> alternatives[0] -> message -> text
    try:
        return data["result"]["alternatives"][0]["message"]["text"]
    except Exception:
        return f"❌ Не смог разобрать ответ YandexGPT.\nСырой ответ:\n{text}"


# --- Команды/меню ---
@dp.message(Command("start"))
async def start_handler(message: types.Message):
    pending_doc_type.pop(message.from_user.id, None)
    await message.answer(
        "👋 Привет!\n\n"
        "Я юрист-бот. Могу:\n"
        "• показать оплату\n"
        "• сгенерировать документ (обычный шаблон)\n"
        "• 🤖 сгенерировать документ через ИИ (YandexGPT)\n\n"
        "Выбирай 👇",
        reply_markup=menu_kb
    )


@dp.message(lambda m: m.text == "ℹ️ Оплата")
async def payment_info(message: types.Message):
    await message.answer(
        "💳 Оплата переводом на карту:\n\n"
        f"Номер карты: {CARD_NUMBER}\n"
        f"Получатель: {CARD_HOLDER}\n\n"
        "После оплаты можешь выбрать документ или генерацию через ИИ."
    )


# Быстрый шаблон (без ИИ)
async def send_simple_template(message: types.Message, doc_type: str):
    text = (
        f"{doc_type}\n\n"
        "Куда: ______________________\n"
        "От: ________________________\n"
        "Адрес: ______________________\n"
        "Телефон: ____________________\n"
        "E-mail: _____________________\n\n"
        "Текст:\n"
        "Прошу рассмотреть настоящее обращение и принять меры в соответствии с законодательством РФ.\n\n"
        "Приложения:\n"
        "1) _________________________\n"
        "2) _________________________\n\n"
        "Дата: ____________    Подпись: ____________\n"
    )
    await message.answer(f"✅ Шаблон готов:\n\n{text}")


@dp.message(lambda m: m.text in ["📄 Жалоба", "⚖️ Исковое заявление", "📝 Объяснительная", "📑 Ходатайство"])
async def doc_templates(message: types.Message):
    await send_simple_template(message, message.text.replace("📄 ", "").replace("⚖️ ", "").replace("📝 ", "").replace("📑 ", ""))


# --- ИИ режим ---
@dp.message(lambda m: m.text == "🤖 ИИ: Сгенерировать документ")
async def ai_start(message: types.Message):
    pending_doc_type[message.from_user.id] = ""  # пока пусто
    await message.answer(
        "🤖 Ок! Выбери тип документа:",
        reply_markup=doc_kb
    )


@dp.message(lambda m: m.text == "⬅️ Назад")
async def back_to_menu(message: types.Message):
    pending_doc_type.pop(message.from_user.id, None)
    await message.answer("Ок, вернулись в меню 👇", reply_markup=menu_kb)


@dp.message(lambda m: m.text in ["📄 Жалоба", "⚖️ Исковое заявление", "📝 Объяснительная", "📑 Ходатайство"])
async def ai_choose_doc(message: types.Message):
    uid = message.from_user.id
    if uid in pending_doc_type:
        # Это выбор типа для ИИ
        doc_type = message.text.replace("📄 ", "").replace("⚖️ ", "").replace("📝 ", "").replace("📑 ", "")
        pending_doc_type[uid] = doc_type
        await message.answer(
            f"✅ Тип выбран: {doc_type}\n\n"
            "Теперь опиши ситуацию одним сообщением:\n"
            "• кто/что/где/когда\n"
            "• что хочешь получить в итоге\n"
            "• если есть даты/суммы — укажи\n\n"
            "Я сгенерирую документ через ИИ.",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="⬅️ Назад")]],
                resize_keyboard=True
            )
        )
    else:
        # Это не ИИ-режим — уже обработано как шаблон выше
        pass


@dp.message()
async def ai_generate_or_fallback(message: types.Message):
    uid = message.from_user.id

    # Если пользователь в режиме ИИ и уже выбрал тип
    if uid in pending_doc_type and pending_doc_type[uid]:
        doc_type = pending_doc_type[uid]
        user_text = message.text.strip()

        if len(user_text) < 15:
            await message.answer("Слишком коротко. Напиши чуть подробнее (минимум пару предложений).")
            return

        await message.answer("⏳ Генерирую документ через ИИ...")

        result = await yandexgpt_generate(doc_type, user_text)

        # Сбрасываем режим после генерации
        pending_doc_type.pop(uid, None)

        # Отправляем результат (если очень длинный — Telegram сам порежет, но обычно нормально)
        await message.answer(result, reply_markup=menu_kb)
        return

    # Иначе — подсказка
    await message.answer(
        "Выбери действие в меню 👇\n\n"
        "Если хочешь ИИ — нажми: 🤖 ИИ: Сгенерировать документ",
        reply_markup=menu_kb
    )


async def main():
    # чтобы не было TelegramConflictError (webhook vs polling)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
