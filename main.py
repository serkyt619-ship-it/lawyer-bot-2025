# bot_assistant_api_fixed.py — РАБОЧАЯ ВЕРСИЯ НОЯБРЬ 2025
import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters
)

from openai import OpenAI

# ───── ПЕРЕМЕННЫЕ ИЗ .env (обязательно в Render → Environment Variables) ─────
BOT_TOKEN = os.getenv("BOT_TOKEN")
YC_FOLDER_ID = os.getenv("YC_FOLDER_ID")      # например: b1gpd7t26bi4ht4g4o8p
YC_API_KEY = os.getenv("YC_API_KEY")          # ← обычный API-ключ (не OAuth!)

if not all([BOT_TOKEN, YC_FOLDER_ID, YC_API_KEY]):
    raise ValueError("Заполни в настройках: BOT_TOKEN, YC_FOLDER_ID, YC_API_KEY")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ───── РАБОЧИЙ КЛИЕНТ ASSISTANT API (ноябрь 2025) ─────
client = OpenAI(
    api_key=YC_API_KEY,
    base_url="https://llm.api.cloud.yandex.net/foundationModels/v1"   # ← ЭТО ПРАВИЛЬНЫЙ ЭНДПОИНТ
)

# ───── RAG (по желанию — вставь свои vector store ID) ─────
VECTOR_STORE_IDS = [
    # "vs_XXXXXXXXXXXXXXXX",
    # "vs_YYYYYYYYYYYYYYYY",
]

# ───── ШАБЛОНЫ ДОКУМЕНТОВ ─────
document_templates = {
    "prosecutor": {"name": "Жалоба в прокуратуру", "price": 700},
    "court": {"name": "Исковое заявление в суд", "price": 1500},
    "mvd": {"name": "Жалоба в МВД", "price": 800},
    "zkh": {"name": "Жалоба в жилищную инспекцию / Роспотребнадзор", "price": 600},
    "consumer": {"name": "Претензия по защите прав потребителей", "price": 500},
}

# ───── ГЕНЕРАЦИЯ ДОКУМЕНТА (Assistant API + RAG) ─────
def generate_document(user_text: str, service: str) -> str | None:
    # Правильная модель для Assistant API
    model = f"gpt://{YC_FOLDER_ID}/yandexgpt/rc"

    # Подключаем RAG, если есть векторные хранилища
    tools = []
    if VECTOR_STORE_IDS:
        tools.append({
            "type": "file_search",
            "file_search": {
                "vector_store_ids": VECTOR_STORE_IDS
            }
        })

    try:
        response = client.responses.create(
            model=model,
            temperature=0.3,
            max_output_tokens=7500,
            instructions=(
                "Ты — профессиональный российский юрист. "
                "Составляй ТОЛЬКО чистый текст юридического документа на русском языке. "
                "Никаких пояснений, комментариев, вступлений и заключений — только сам документ."
            ),
            tools=tools or None,
            input=f"Составь документ: {document_templates[service]['name']}\n\n"
                  f"Ситуация пользователя:\n{user_text}"
        )
        return response.output_text.strip()

    except Exception as e:
        logger.error(f"Assistant API ошибка: {e}")
        return None


# ───── КОМАНДЫ БОТА ─────
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

    msg = await update.message.reply_text("Генерирую документ… 15–50 секунд ⏳")

    user_text = update.message.text
    service = context.user_data["service"]

    document = generate_document(user_text, service)

    if not document:
        await msg.edit_text("Ошибка связи с YandexGPT. Попробуйте позже или напишите /start")
        return

    safe_doc = document.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    await msg.edit_message_text(
        f"<b>ГОТОВО!</b>\n\n"
        f"<b>{document_templates[service]['name']}</b> — {document_templates[service]['price']} ₽\n\n"
        f"{safe_doc}\n\n"
        f"<b>Оплата:</b>\n"
        f"Карта Т-Банк: <code>2200 7007 0401 2581</code>\n\n"
        f"После оплаты пришлите чек — пришлю документ в Word + PDF",
        parse_mode="HTML"
    )

    context.user_data.clear()


# ───── ЗАПУСК ─────
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Бот с Assistant API успешно запущен!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
