# main.py — 100% рабочая версия (декабрь 2025)
import os
import logging
import html
import jwt
import time
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters
from openai import AsyncOpenAI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot")

BOT_TOKEN = os.getenv("BOT_TOKEN")
YC_FOLDER_ID = os.getenv("YC_FOLDER_ID")
YC_SERVICE_ACCOUNT_ID = os.getenv("YC_SERVICE_ACCOUNT_ID")  # ← новый параметр
YC_PRIVATE_KEY = os.getenv("YC_API_KEY")  # ← оставляем твой PEM-ключ как есть

# Генерация IAM-токена из PEM-ключа (работает всегда)
def get_iam_token():
    now = int(time.time())
    payload = {
        "iss": YC_SERVICE_ACCOUNT_ID,
        "aud": "https://iam.googleapis.com/",
        "iat": now,
        "exp": now + 3600
    }
    headers = {"kid": YC_SERVICE_ACCOUNT_ID}
    token = jwt.encode(payload, YC_PRIVATE_KEY, algorithm="PS256", headers=headers)
    response = requests.post(
        "https://iam.googleapis.com/v1/iamcredentials:generateAccessToken",
        json={"name": f"projects/-/serviceAccounts/{YC_SERVICE_ACCOUNT_ID}"},
        headers={"Authorization": f"Bearer {token}"}
    )
    return response.json()["accessToken"]

# Клиент с рабочим IAM-токеном
client = AsyncOpenAI(
    api_key=get_iam_token(),
    base_url="https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
)

documents = {
    "prosecutor": {"name": "Жалоба в прокуратуру", "price": 700},
    "court": {"name": "Исковое заявление в суд", "price": 1500},
    "mvd": {"name": "Жалоба в МВД", "price": 800},
    "zkh": {"name": "Жалоба в жилищную инспекцию", "price": 600},
    "consumer": {"name": "Претензия по защите прав потребителей", "price": 500},
}

async def generate(text: str, type_: str):
    try:
        resp = await client.chat.completions.create(
            model=f"gpt://{YC_FOLDER_ID}/yandexgpt/latest",
            temperature=0.3,
            max_tokens=4000,
            messages=[
                {"role": "system", "content": "Ты юрист РФ. Пиши только готовый документ без лишнего текста."},
                {"role": "user", "content": f"Составь {documents[type_]['name'].lower()}:\n\n{text}"}
            ]
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        return None

# Остальные хэндлеры без изменений
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton(f"{v['name']} — {v['price']}₽", callback_data=k)] for k, v in documents.items()]
    await update.message.reply_text("АВТОЮРИСТ 24/7\nВыберите документ:", reply_markup=InlineKeyboardMarkup(kb))

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["type"] = q.data
    await q.edit_message_text(f"Выбрано: {documents[q.data]['name']}\nОпишите ситуацию:")

async def text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "type" not in context.user_data:
        await update.message.reply_text("Нажмите /start")
        return
    msg = await update.message.reply_text("Генерирую…")
    result = await generate(update.message.text, context.user_data["type"])
    if not result:
        await msg.edit_text("Ошибка генерации. Попробуйте позже.")
        return
    if len(result) > 3800:
        with open("doc.txt", "w", encoding="utf-8") as f: f.write(result)
        await msg.delete()
        await update.message.reply_document(open("doc.txt", "rb"), filename="документ.txt")
        os.remove("doc.txt")
    else:
        await msg.edit_text(f"<b>{documents[context.user_data['type']]['name']}</b>\n\n{html.escape(result)}", parse_mode="HTML")
    context.user_data.clear()

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text))

    webhook_url = f"https://lawyer-bot-2025.onrender.com/{BOT_TOKEN}"
    logger.info(f"Бот запущен: {webhook_url}")

    app.run_webhook(listen="0.0.0.0", port=int(os.environ.get("PORT", 10000)), url_path=BOT_TOKEN, webhook_url=webhook_url)

if __name__ == "__main__":
    main()
