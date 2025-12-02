# main.py — РАБОЧАЯ ВЕРСИЯ ДЛЯ RENDER + YANDEXGPT (декабрь 2025)

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

# НОВЫЙ клиент для YandexGPT
from openai import AsyncOpenAI

# ───────────────────────────────────────────────
#              ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ
# ───────────────────────────────────────────────

BOT_TOKEN = os.getenv("BOT_TOKEN")
YC_FOLDER_ID = os.getenv("YC_FOLDER_ID")
YC_API_KEY = os.getenv("YC_API_KEY")

if not all([BOT_TOKEN, YC_FOLDER_ID, YC_API_KEY]):
    raise ValueError("❗ В Render нужно задать BOT_TOKEN, YC_FOLDER_ID и YC_API_KEY")


# ───────────────────────────────────────────────
#                     ЛОГИ
# ───────────────────────────────────────────────

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot")


# ───────────────────────────────────────────────
#            Настройка YandexGPT API
# ───────────────────────────────────────────────

client = AsyncOpenAI(
    api_key=YC_API_KEY,
    base_url="https://llm.api.cloud.yandex.ru/llm/v1alpha"
)

MODEL_URI = f"gpt://{YC_FOLDER_ID}/yandexgpt/latest"


# ───────────────────────────────────────────────
#          ТИПЫ ДОКУМЕНТОВ ДЛЯ ВЫБОРА
# ───────────────────────────────────────────────

document_templates = {
    "prosecutor": {"name": "Жалоба в прокуратуру", "price": 700},
    "court": {"name": "Исковое заявление в суд", "price": 1500},
    "mvd": {"name": "Жалоба в МВД", "price": 800},
    "zkh": {"name": "Жалоба в жилищную инспекцию / Роспотребнадзор", "price": 600},
    "consumer": {"name": "Претензия по защите прав потребителей", "price": 500},
}


# ───────────────────────────────────────────────
#          ФУНКЦИЯ ГЕНЕРАЦИИ ДОКУМЕНТА
# ───────────────────────────────────────────────

async def generate_document(user_text: str, service: str) -> Optional[str]:
    try:
        response = await client.chat.completions.create(
            model=MODEL_URI,
            temperature=0.3,
            max_tokens=4000,
            messages=[
                {"role": "system",
                 "content": "Ты — профессиональный российский юрист. Пиши ТОЛЬКО готовый юридический документ, без пояснений."},
                {"role": "user",
                 "content": f"Составь документ: {document_templates[service]['name']}\n\nСитуация:\n{user_text}"}
            ],
        )
        return response.choices[0].message.content.strip()

    except Exception as e:
        logger.error(f"Ошибка YandexGPT: {e}")
        return None


# ───────────────────────────────────────────────
#                   ХЭНДЛЕРЫ
# ───────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton(f"{v['name']} — {v['price']} ₽", callback_data=k)]
        for k, v in document_templates.items()
    ]

    await update.message.reply_text(
        "АВТОЮРИСТ 24/7\n\nВыберите тип документа:",
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
        f"Опишите вашу ситуацию:",
        parse_mode="HTML"
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "service" not in context.user_data:
        await update.message.reply_text("Нажмите /start и выберите документ")
        return

    thinking = await update.message.reply_text("Генерирую документ…")

    document = await generate_document(update.message.text, context.user_data["service"])

    if not document:
        await thinking.edit_text("Ошибка сервиса. Попробуйте позже.")
        return

    safe_doc = html.escape(document)

    # Если документ огромный — отправляем файлом
    if len(document) > 3800:
        with open("document.txt", "w", encoding="utf-8") as f:
            f.write(document)

        await thinking.delete()

        await update.message.reply_document(
            open("document.txt", "rb"),
            filename="документ.txt",
            caption=f"{document_templates[context.user_data['service']]['name']}\n\n"
                    f"Оплата: 2200 7007 0401 2581"
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


# ───────────────────────────────────────────────
#                      ЗАПУСК
# ───────────────────────────────────────────────

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    port = int(os.environ.get("PORT", 10000))

    hostname = os.environ.get("RENDER_EXTERNAL_HOSTNAME") or os.environ["RENDER_SERVICE_NAME"]
    webhook_url = f"https://{hostname}.onrender.com/{BOT_TOKEN}"

    logger.info(f"Бот запущен на webhook: {webhook_url}")

    app.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=BOT_TOKEN,
        webhook_url=webhook_url
    )


if __name__ == "__main__":
    main()
