# bot_assistant_api_fixed.py — рабочая версия для Render + YandexGPT (декабрь 2025)
import os
import logging
import html
import asyncio
from typing import Optional

import telegram
print("PTB version:", telegram.__version__)

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters
)

from openai import AsyncOpenAI  # ← теперь асинхронный клиент!

# ───── ПЕРЕМЕННЫЕ ИЗ .env (на Render добавь их в Environment Variables) ─────
BOT_TOKEN = os.getenv("BOT_TOKEN")
YC_FOLDER_ID = os.getenv("YC_FOLDER_ID")
YC_IAM_TOKEN = os.getenv("YC_IAM_TOKEN")        # ← теперь нужен именно IAM-токен!
# Или можно использовать YC_API_KEY (OAuth-токен), но IAM работает надёжнее

if not BOT_TOKEN or not YC_FOLDER_ID or (not YC_IAM_TOKEN and not os.getenv("YC_API_KEY")):
    raise ValueError("Обязательно задай: BOT_TOKEN, YC_FOLDER_ID и YC_IAM_TOKEN (или YC_API_KEY)")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ───── АСИНХРОННЫЙ КЛИЕНТ YandexGPT (новый API 2025) ─────
client = AsyncOpenAI(
    api_key=YC_IAM_TOKEN or os.getenv("YC_API_KEY"),
    base_url="https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
)

document_templates = {
    "prosecutor": {"name": "Жалоба в прокуратуру", "price": 700},
    "court": {"name": "Исковое заявление в суд", "price": 1500},
    "mvd": {"name": "Жалоба в МВД", "price": 800},
    "zkh": {"name": "Жалоба в жилищную инспекцию / Роспотребнадзор", "price": 600},
    "consumer": {"name": "Претензия по защите прав потребителей", "price": 500},
}

# ───── ГЕНЕРАЦИЯ ДОКУМЕНТА (асинхронно, без to_thread) ─────
async def generate_document(user_text: str, service: str) -> Optional[str]:
    try:
        response = await client.chat.completions.create(
            model="yandexgpt",                     # ← актуальное имя модели
            # model="yandexgpt-lite"               # ← можно и lite, она дешевле
            temperature=0.3,
            max_tokens=3500,
            messages=[
                {"role": "system", "content": (
                    "Ты — профессиональный российский юрист. "
                    "Составляй ТОЛЬКО чистый текст юридического документа на русском языке. "
                    "Никаких пояснений, комментариев, вступлений и заключений — только сам документ."
                )},
                {"role": "user", "content": (
                    f"Составь документ: {document_templates[service]['name']}\n\n"
                    f"Ситуация пользователя:\n{user_text}"
                )}
            ]
        )
        return response.choices[0].message.content.strip()

    except Exception as e:
        logger.error(f"Ошибка YandexGPT: {e}")
        return None

# ───── Хэндлеры ─────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton(f"{v['name']} — {v['price']} ₽", callback_data=k)]
        for k, v in document_templates.items()
    ]
    await update.message.reply_text(
        "АВТОЮРИСТ 2025\n\nВыберите нужный документ:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    service = query.data
    context.user_data["service"] = service

    await query.edit_message_text(
        f"<b>{document_templates[service]['name']}</b>\n"
        f"Цена: {document_templates[service]['price']} ₽\n\n"
        f"Опишите вашу ситуацию максимально подробно:",
        parse_mode="HTML"
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "service" not in context.user_data:
        await update.message.reply_text("Сначала нажмите /start")
        return

    thinking_msg = await update.message.reply_text("Генерирую документ… ⏳")

    user_text = update.message.text
    service = context.user_data["service"]

    document = await generate_document(user_text, service)

    if not document:
        await thinking_msg.edit_text("Ошибка связи с YandexGPT. Попробуйте позже или напишите /start")
        return

    safe_doc = html.escape(document)

    if len(document) > 3800:  # Telegram лимит ~4096 символов
        filename = f"{service}_document.txt"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(document)
        await thinking_msg.delete()
        await update.message.reply_document(
            document=open(filename, "rb"),
            filename=filename,
            caption=f"{document_templates[service]['name']} — {document_templates[service]['price']} ₽\n\n"
                    f"Отправь оплату и пришли чек — пришлю Word+PDF."
        )
        os.remove(filename)
    else:
        await thinking_msg.edit_text(
            f"<b>ГОТОВО!</b>\n\n"
            f"<b>{document_templates[service]['name']}</b> — {document_templates[service]['price']} ₽\n\n"
            f"{safe_doc}\n\n"
            f"<b>Оплата:</b>\n"
            f"Карта Т-Банк: <code>2200 7007 0401 2581</code>\n\n"
            f"После оплаты пришлите чек — пришлю документ в Word + PDF",
            parse_mode="HTML"
        )

    context.user_data.pop("service", None)

# ───── ЗАПУСК ─────
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Бот запущен и работает на YandexGPT")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
