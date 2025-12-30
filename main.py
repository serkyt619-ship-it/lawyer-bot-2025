import asyncio
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CARD_NUMBER = os.getenv("CARD_NUMBER")
CARD_HOLDER = os.getenv("CARD_HOLDER")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Кнопки меню
menu_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📄 Жалоба")],
        [KeyboardButton(text="⚖️ Исковое заявление")],
        [KeyboardButton(text="📝 Объяснительная")],
        [KeyboardButton(text="📑 Ходатайство")],
        [KeyboardButton(text="ℹ️ Оплата")],
    ],
    resize_keyboard=True
)

# /start
@dp.message(Command("start"))
async def start_handler(message: types.Message):
    await message.answer(
        "👋 Здравствуйте!\n\n"
        "Я автоматический юрист-бот.\n"
        "Выберите тип документа 👇",
        reply_markup=menu_kb
    )

# Оплата
@dp.message(lambda m: m.text == "ℹ️ Оплата")
async def payment_info(message: types.Message):
    await message.answer(
        "💳 Оплата производится переводом на карту:\n\n"
        f"Номер карты: {CARD_NUMBER}\n"
        f"Получатель: {CARD_HOLDER}\n\n"
        "После оплаты выберите нужный документ."
    )

# Универсальный генератор заявления
async def generate_document(message: types.Message, doc_type: str):
    text = (
        f"{doc_type}\n\n"
        "От: ______________________\n"
        "Адрес: ___________________\n"
        "Телефон: _________________\n\n"
        "Текст заявления:\n"
        "Прошу рассмотреть настоящее обращение и принять меры "
        "в соответствии с действующим законодательством.\n\n"
        "Дата: ____________    Подпись: ____________"
    )

    await message.answer(
        f"✅ Документ готов:\n\n{text}\n\n"
        "📌 Если нужен индивидуальный вариант — напишите текстом детали."
    )

# Обработчики документов
@dp.message(lambda m: m.text == "📄 Жалоба")
async def complaint(message: types.Message):
    await generate_document(message, "ЖАЛОБА")

@dp.message(lambda m: m.text == "⚖️ Исковое заявление")
async def lawsuit(message: types.Message):
    await generate_document(message, "ИСКОВОЕ ЗАЯВЛЕНИЕ")

@dp.message(lambda m: m.text == "📝 Объяснительная")
async def explanation(message: types.Message):
    await generate_document(message, "ОБЪЯСНИТЕЛЬНАЯ ЗАПИСКА")

@dp.message(lambda m: m.text == "📑 Ходатайство")
async def motion(message: types.Message):
    await generate_document(message, "ХОДАТАЙСТВО")

# Запуск бота
async def main():
    # 🔥 ВАЖНО: удаляем webhook, чтобы не было TelegramConflictError
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
