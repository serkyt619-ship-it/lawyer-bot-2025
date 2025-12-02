# main.py — ФИНАЛЬНАЯ РАБОЧАЯ ВЕРСИЯ (декабрь 2025)
# Работает на Render + YandexGPT без санкций и ошибок webhook

import os
import logging
import html
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters
)

# Правильный клиент YandexGPT (OpenAI-совместимый)
from openai import AsyncOpenAI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot")

# Переменные окружения (обязательно задать в Render → Environment)
BOT_TOKEN = os.getenv("BOT_TOKEN")
YC_FOLDER_ID = os.getenv("YC_FOLDER_ID")
YC_API_KEY = os.getenv("YC_API_KEY")

if not all([BOT_TOKEN, YC_FOLDER_ID, YC_API_KEY]):
    raise ValueError("❗ Задай BOT_TOKEN, YC_FOLDER_ID и YC_API_KEY в Render!")

# Правильный endpoint и модель YandexGPT (работает в декабре 2025)
client = AsyncOpenAI(
    api_key=YC_API_KEY,
    base_url="https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
)

# Список документов
document_templates = {
    "prosecutor": {"name": "Жалоба в прокуратуру", "price": 700},
    "court": {"name": "Исковое заявление в суд", "price": 1500},
    "mvd": {"name": "Жалоба в МВД", "price": 800},
    "zkh": {"name": "Жалоба в жилищную инспекцию / Роспотребнадзор", "price": 600},
    "consumer": {"name": "Претензия по защите прав потребителей", "price": 500},
}

# Генерация документа
async def generate_document(user_text: str, service: str) -> Optional[str]:
    try:
        response = await client.chat.completions.create(
            model=f"gpt://{YC_FOLDER_ID}/yandexgpt/latest",   # ← правильная модель
            temperature=0.3,
            max_tokens=4000,
            messages=[
                {"role": "system", "content": "Ты — профессиональный российский юрист. Пиши ТОЛЬКО готовый юридический документ без лишних слов и пояснений."},
                {"role": "user", "content": f"Составь {document_templates[service]['name']} по следующей ситуации:\n\n{user_text}"}
            ],
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Ошибка YandexGPT: {e}")
        return None

# /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton(f"{v['name']} — {v['price']} ₽", callback_data=k)]
                for k, v in document_templates.items()]
    await update.message.reply_text(
        "АВТОЮРИСТ 24/7 ⚖️\n\nВыберите тип документа:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# Кнопки
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    service = query.data
    context.user_data["service"] = service
    await query.edit_message_text(
        f"<b>{document_templates[service]['name']}</b>\n"
        f"Цена: {document_templates[service]['price']} ₽\n\n"
        f"Опишите вашу ситуацию подробно:",
        parse_mode="HTML"
    )

# Обработка текста пользователя
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "service" not in context.user_data:
        await update.message.reply_text("Сначала нажмите /start и выберите документ")
        return

    thinking = await update.message.reply_text("Генерирую документ…")

    document = await generate_document(update.message.text, context.user_data["service"])

    if not document:
        await thinking.edit_text("Ошибка генерации. Пополните баланс Yandex Cloud или попробуйте позже.")
        return

    safe_doc = html.escape(document)

    if len(document) > 3800:
        with open("document.txt", "w", encoding="utf-8") as f:
            f.write(document)
        await thinking.delete()
        await update.message.reply_document(
            open("document.txt", "rb"),
            filename=f"{document_templates[context.user_data['service']]['name']}.txt",
            caption="Готово! 💼\nОплата: 2200 7007 0401 2581"
        )
        os.remove("document.txt")
    else:
        await thinking.edit_text(
            f"<b>ГОТОВО!</b>\n\n"
            f"<b>{document_templates[context.user_data['service']]['name']}</b>\n\n"
            f"{safe_doc}\n\n"
            f"<b>Оплата:</b> <code>2200 7007 0401 2581</code>",
            parse_mode="HTML"
        )

    context.user_data.clear()

# Запуск бота
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # ←←← ЭТО САМАЯ ВАЖНАЯ СТРОКА — РАБОТАЕТ НА RENDER ВСЕГДА
    webhook_url = f"https://lawyer-bot-2025.onrender.com/{BOT_TOKEN}"
    logger.info(f"Бот запущен на webhook: {webhook_url}")

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
        url_path=BOT_TOKEN,
        webhook_url=webhook_url
    )

if __name__ == "__main__":
    main()
