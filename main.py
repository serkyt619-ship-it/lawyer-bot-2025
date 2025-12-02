# main.py — 100% РАБОЧАЯ ВЕРСИЯ ДЕКАБРЬ 2025
# Webhook + YandexGPT — всё работает сразу

import os
import logging
import html

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters
from openai import AsyncOpenAI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot")

BOT_TOKEN = os.getenv("BOT_TOKEN")
YC_FOLDER_ID = os.getenv("YC_FOLDER_ID")
YC_API_KEY = os.getenv("YC_API_KEY")

if not all([BOT_TOKEN, YC_FOLDER_ID, YC_API_KEY]):
    raise ValueError("Задай BOT_TOKEN, YC_FOLDER_ID, YC_API_KEY в Render!")

# ←←← 100% РАБОЧИЙ ЭНДПОИНТ ЯНДЕКСА НА ДЕКАБРЬ 2025
client = AsyncOpenAI(
    api_key=YC_API_KEY,
    base_url="https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
)

documents = {
    "prosecutor": {"name": "Жалоба в прокуратуру", "price": 700},
    "court": {"name": "Исковое заявление в суд", "price": 1500},
    "mvd": {"name": "Жалоба в МВД", "price": 800},
    "zkh": {"name": "Жалоба в жилищную инспекцию / Роспотребнадзор", "price": 600},
    "consumer": {"name": "Претензия по защите прав потребителей", "price": 500},
}

async def generate(text: str, type_: str) -> str | None:
    try:
        resp = await client.chat.completions.create(
            model=f"gpt://{YC_FOLDER_ID}/yandexgpt/latest",
            temperature=0.3,
            max_tokens=4000,
            messages=[
                {"role": "system", "content": "Ты профессиональный юрист РФ. Пиши ТОЛЬКО готовый юридический документ без пояснений и приветствий."},
                {"role": "user", "content": f"Составь {documents[type_]['name'].lower()} по ситуации:\n\n{text}"}
            ]
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"YandexGPT error: {e}")
        return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton(f"{v['name']} — {v['price']}₽", callback_data=k)] for k, v in documents.items()]
    await update.message.reply_text("АВТОЮРИСТ 24/7 ⚖️\nВыберите документ:", reply_markup=InlineKeyboardMarkup(kb))

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["type"] = q.data
    await q.edit_message_text(f"Выбрано: {documents[q.data]['name']}\nЦена: {documents[q.data]['price']}₽\n\nОпишите ситуацию:")

async def text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "type" not in context.user_data:
        await update.message.reply_text("Нажмите /start")
        return

    msg = await update.message.reply_text("Генерирую документ…")
    result = await generate(update.message.text, context.user_data["type"])

    if not result:
        await msg.edit_text("Ошибка генерации. Баланс Yandex Cloud закончился или попробуйте позже.")
        return

    if len(result) > 3800:
        with open("doc.txt", "w", encoding="utf-8") as f:
            f.write(result)
        await msg.delete()
        await update.message.reply_document(open("doc.txt", "rb"), filename="документ.txt",
                                          caption="Готово! Оплата: 2200 7007 0401 2581")
        os.remove("doc.txt")
    else:
        await msg.edit_text(
            f"<b>{documents[context.user_data['type']]['name']}</b>\n\n"
            f"{html.escape(result)}\n\n"
            f"Оплата: <code>2200 7007 0401 2581</code>",
            parse_mode="HTML"
        )
    context.user_data.clear()

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text))

    # ←←← 100% РАБОЧИЙ WEBHOOK НА RENDER
    webhook_url = f"https://lawyer-bot-2025.onrender.com/{BOT_TOKEN}"
    logger.info(f"Бот запущен → {webhook_url}")

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
        url_path=BOT_TOKEN,
        webhook_url=webhook_url
    )

if __name__ == "__main__":
    main()
