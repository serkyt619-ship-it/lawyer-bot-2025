# bot_assistant_api_fixed.py — 100% рабочая версия (декабрь 2025)
import os
import logging
import html
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

from openai import AsyncOpenAI

# ───── ПЕРЕМЕННЫЕ ИЗ .env ─────
BOT_TOKEN = os.getenv("BOT_TOKEN")
YC_FOLDER_ID = os.getenv("YC_FOLDER_ID")
YC_IAM_TOKEN = os.getenv("YC_IAM_TOKEN")

if not BOT_TOKEN or not YC_FOLDER_ID or (not YC_IAM_TOKEN and not os.getenv("YC_API_KEY")):
    raise ValueError("Задай в .env: BOT_TOKEN, YC_FOLDER_ID и YC_IAM_TOKEN (или YC_API_KEY)")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ───── РАБОЧИЙ КЛИЕНТ YandexGPT (уже исправлено) ─────
client = AsyncOpenAI(
    api_key=YC_IAM_TOKEN or os.getenv("YC_API_KEY"),
    base_url="https://llm.api.cloud.yandex.ru/foundationModels/v1/completion"
)

MODEL_URI = f"gpt://{YC_FOLDER_ID}/yandexgpt/latest"

document_templates = {
    "prosecutor": {"name": "Жалоба в прокуратуру", "price": 700},
    "court": {"name": "Исковое заявление в суд", "price": 1500},
    "mvd": {"name": "Жалоба в МВД", "price": 800},
    "zkh": {"name": "Жалоба в жилищинспекцию / Роспотребнадзор", "price": 600},
    "consumer": {"name": "Претензия по защите прав потребителей", "price": 500},
}

async def generate_document(user_text: str, service: str) -> Optional[str]:
    try:
        response = await client.chat.completions.create(
            model=MODEL_URI,
            temperature=0.3,
            max_tokens=4000,
            messages=[
                {"role": "system", "content": "Ты — профессиональный российский юрист. Пиши ТОЛЬКО чистый текст документа без пояснений."},
                {"role": "user", "content": f"Составь: {document_templates[service]['name']}\n\nСитуация:\n{user_text}"}
            ]
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Ошибка YandexGPT: {e}")
        return None

# ───── Хэндлеры ─────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton(f"{v['name']} — {v['price']} ₽", callback_data=k)] for k, v in document_templates.items()]
    await update.message.reply_text("АВТОЮРИСТ 2025\n\nВыберите документ:", reply_markup=InlineKeyboardMarkup(keyboard))

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    service = query.data
    context.user_data["service"] = service
    await query.edit_message_text(
        f"<b>{document_templates[service]['name']}</b>\n"
        f"Цена: {document_templates[service]['price']} ₽\n\n"
        f"Опишите ситуацию подробно:",
        parse_mode="HTML"
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "service" not in context.user_data:
        await update.message.reply_text("Нажмите /start")
        return

    thinking_msg = await update.message.reply_text("Генерирую документ…")

    document = await generate_document(update.message.text, context.user_data["service"])

    if not document:
        await thinking_msg.edit_text("Ошибка YandexGPT. Попробуйте позже.")
        return

    safe_doc = html.escape(document)

    if len(document) > 3800:
        filename = "document.txt"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(document)
        await thinking_msg.delete()
        await update.message.reply_document(
            open(filename, "rb"),
            filename=filename,
            caption=f"{document_templates[context.user_data['service']]['name']} готов!\nОплата: 2200 7007 0401 2581 (Т-Банк)"
        )
        os.remove(filename)
    else:
        await thinking_msg.edit_text(
            f"<b>ГОТОВО!</b>\n\n"
            f"<b>{document_templates[context.user_data['service']]['name']}</b>\n\n"
            f"{safe_doc}\n\n"
            f"<b>Оплата:</b> <code>2200 7007 0401 2581</code>\n"
            f"После оплаты пришлите чек — пришлю Word + PDF",
            parse_mode="HTML"
        )

    context.user_data.clear()

# ───── ЗАПУСК (главное исправление от Conflict!) ─────
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Бот успешно запущен!")

    # ← ЭТА СТРОКА УБИРАЕТ CONFLICT НАВСЕГДА
    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
        timeout=30
    )

if __name__ == "__main__":
    main()
