# main.py — рабочая версия декабрь 2025 с PS256 и правильным kid

import os
import logging
import html
import time
import jwt
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters
from openai import AsyncOpenAI
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot")

# Переменные из Render Environment
BOT_TOKEN = os.getenv("BOT_TOKEN")
YC_FOLDER_ID = os.getenv("YC_FOLDER_ID")
YC_SERVICE_ACCOUNT_ID = os.getenv("YC_SERVICE_ACCOUNT_ID")  # ID сервисного аккаунта
YC_PRIVATE_KEY = os.getenv("YC_API_KEY")  # PEM ключ IAM
YC_IAM_KEY_ID = os.getenv("YC_IAM_KEY_ID")  # ID IAM-ключа (обязательно!)

if not all([BOT_TOKEN, YC_FOLDER_ID, YC_SERVICE_ACCOUNT_ID, YC_PRIVATE_KEY, YC_IAM_KEY_ID]):
    raise ValueError("Задай BOT_TOKEN, YC_FOLDER_ID, YC_SERVICE_ACCOUNT_ID, YC_API_KEY и YC_IAM_KEY_ID в Render!")

# Генерация IAM-токена из PEM-ключа (PS256)
def get_iam_token():
    now = int(time.time())
    payload = {
        "iss": YC_SERVICE_ACCOUNT_ID,  # сервисный аккаунт
        "aud": "https://iam.api.cloud.yandex.net/iam/v1/tokens",
        "iat": now,
        "exp": now + 3600
    }

    private_key_obj = serialization.load_pem_private_key(
        YC_PRIVATE_KEY.encode(),
        password=None,
        backend=default_backend()
    )

    # Важно: kid — это ID IAM-ключа, а не сервисного аккаунта
    encoded_token = jwt.encode(
        payload,
        private_key_obj,
        algorithm="PS256",
        headers={"typ": "JWT", "alg": "PS256", "kid": YC_IAM_KEY_ID}
    )

    response = requests.post(
        "https://iam.api.cloud.yandex.net/iam/v1/tokens",
        json={"jwt": encoded_token}
    )
    if response.status_code != 200:
        raise ValueError(f"Ошибка генерации IAM-токена: {response.text}")
    return response.json()["iamToken"]

# Клиент YandexGPT с IAM-токеном
client = AsyncOpenAI(
    api_key=get_iam_token(),
    base_url="https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
)

document_templates = {
    "prosecutor": {"name": "Жалоба в прокуратуру", "price": 700},
    "court": {"name": "Исковое заявление в суд", "price": 1500},
    "mvd": {"name": "Жалоба в МВД", "price": 800},
    "zkh": {"name": "Жалоба в жилищную инспекцию / Роспотребнадзор", "price": 600},
    "consumer": {"name": "Претензия по защите прав потребителей", "price": 500},
}

async def generate_document(user_text: str, service: str) -> str | None:
    try:
        response = await client.chat.completions.create(
            model=f"gpt://{YC_FOLDER_ID}/yandexgpt/latest",
            temperature=0.3,
            max_tokens=4000,
            messages=[
                {"role": "system", "content": "Ты — профессиональный российский юрист. Пиши ТОЛЬКО готовый юридический документ, без пояснений."},
                {"role": "user", "content": f"Составь документ: {document_templates[service]['name']}\n\nСитуация:\n{user_text}"}
            ],
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Ошибка YandexGPT: {e}")
        return None

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

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

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
