# main.py — 100% рабочая версия для Render Web Service с webhook (декабрь 2025)
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

if not all([BOT_TOKEN, YC_FOLDER_ID, YC_IAM_TOKEN]):
    raise ValueError("Задай BOT_TOKEN, YC_FOLDER_ID и YC_IAM_TOKEN в переменных окружения!")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot")

# ───── YandexGPT клиент (уже правильный) ─────
client = AsyncOpenAI(
    api_key=YC_IAM_TOKEN,
    base_url="https://llm.api.cloud.yandex.ru/foundationModels/v1/completion"
)

MODEL_URI = f"gpt://{YC_FOLDER_ID}/yandexgpt/latest"

document_templates = {
    "prosecutor": {"name": "Жалоба в прокуратуру", "price": 700},
    "court": {"name": "Исковое заявление в суд", "price": 1500},
    "mvd": {"name": "Жалоба в МВД", "price": 800},
    "zkh": {"name": "Жалоба в жилищную инспекцию / Роспотребнадзор", "price": 600},
    "consumer": {"name": "Претензия по защите прав потребителей", "price": 500},
}

async def generate_document(user_text: str, service: str) -> Optional[str]:
    try:
        response = await client.chat.completions.create(
            model=MODEL_URI,
            temperature=0.3,
            max_tokens=4000,
            messages=[
                {"role": "system", "content": "Ты — профессиональный российский юрист. Пиши ТОЛЬКО чистый текст юридического документа без лишних слов."},
                {"role": "user", "content": f"Составь: {document_templates[service]['name']}\n\nСитуация:\n{user_text}"}
            ]
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"YandexGPT error: {e}")
        return None

# ───── Хэндлеры ─────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton(f"{v['name']} — {v['price']} ₽", callback_data=k)] 
                for k, v in document_templates.items()]
    await update.message.reply_text(
        "АВТОЮРИСТ 24/7\n\nВыберите документ:",
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
            caption=f"{document_templates[context.user_data['service']]['name']} готов!\nОплата: 2200 7007 0401 2581"
        )
        os.remove(filename)
    else:
        await thinking_msg.edit_text(
            f"<b>ГОТОВО!</b>\n\n"
            f"<b>{document_templates[context.user_data['service']]['name']}</b>\n\n"
            f"{safe_doc}\n\n"
            f"<b>Оплата:</b> <code>2200 7007 0401 2581</code>\n"
            f"После чека — пришлю Word + PDF",
            parse_mode="HTML"
        )

    context.user_data.clear()

# ───── ЗАПУСК ПОД WEBHOOK (всё, что нужно для Render Web Service) ─────
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # ← ЭТО РАБОТАЕТ НА RENDER WEB SERVICE
    port = int(os.environ.get("PORT", 10000))
    webhook_url = f"https://{os.environ['RENDER_EXTERNAL_HOSTNAME']}.onrender.com/{BOT_TOKEN}"

    logger.info(f"Запускаюсь на webhook: {webhook_url}")

    app.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=BOT_TOKEN,
        webhook_url=webhook_url
    )

if __name__ == "__main__":
    main()
