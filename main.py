# bot_assistant_api_fixed_async.py — улучшенная версия (ноябрь 2025)
import os
import logging
import html
import asyncio
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters
)

from openai import OpenAI  # предполагается, что это Yandex Assistant-compatible client

# ───── ПЕРЕМЕННЫЕ ИЗ .env ─────
BOT_TOKEN = os.getenv("BOT_TOKEN")
YC_FOLDER_ID = os.getenv("YC_FOLDER_ID")
YC_API_KEY = os.getenv("YC_API_KEY")

if not all([BOT_TOKEN, YC_FOLDER_ID, YC_API_KEY]):
    raise ValueError("Заполни в настройках Render: BOT_TOKEN, YC_FOLDER_ID, YC_API_KEY")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ───── КЛИЕНТ ASSISTANT API ─────
client = OpenAI(
    api_key=YC_API_KEY,
    base_url="https://llm.api.cloud.yandex.net/foundationModels/v1"
)

# Векторные хранилища (если есть)
VECTOR_STORE_IDS = []

document_templates = {
    "prosecutor": {"name": "Жалоба в прокуратуру", "price": 700},
    "court": {"name": "Исковое заявление в суд", "price": 1500},
    "mvd": {"name": "Жалоба в МВД", "price": 800},
    "zkh": {"name": "Жалоба в жилищную инспекцию / Роспотребнадзор", "price": 600},
    "consumer": {"name": "Претензия по защите прав потребителей", "price": 500},
}

# ───── ГЕНЕРАЦИЯ ДОКУМЕНТА (обёртка для неблокирующего вызова) ─────
async def generate_document(user_text: str, service: str) -> Optional[str]:
    """
    Асинхронная обёртка: если client.responses.create блокирующий,
    мы выполняем его в отдельном потоке через asyncio.to_thread.
    """
    def sync_call():
        model = f"gpt://{YC_FOLDER_ID}/yandexgpt/rc"

        tools = []
        if VECTOR_STORE_IDS:
            tools.append({
                "type": "file_search",
                "file_search": {"vector_store_ids": VECTOR_STORE_IDS}
            })

        # Инструкции и вход — адаптируй при необходимости
        try:
            resp = client.responses.create(
                model=model,
                temperature=0.3,
                max_output_tokens=3000,  # разумный предел; увеличь, если нужно
                instructions=(
                    "Ты — профессиональный российский юрист. "
                    "Составляй ТОЛЬКО чистый текст юридического документа на русском языке. "
                    "Никаких пояснений, комментариев, вступлений и заключений — только сам документ."
                ),
                tools=tools or None,
                input=(
                    f"Составь документ: {document_templates[service]['name']}\n\n"
                    f"Ситуация пользователя:\n{user_text}"
                )
            )
            # В зависимости от клиента поле может называться иначе; проверим несколько вариантов:
            if hasattr(resp, "output_text"):
                return resp.output_text.strip()
            # иногда ответ может быть в resp.output[0].content[0].text или в resp.output
            # делаем ленивую попытку достать строку:
            try:
                # если dict-like
                return str(resp.get("output_text") or resp.get("output") or "").strip()
            except Exception:
                return str(resp).strip()
        except Exception as e:
            logger.exception("Assistant API sync call failed")
            raise

    try:
        result = await asyncio.to_thread(sync_call)
        return result
    except Exception as e:
        logger.error("Ошибка при вызове Assistant API: %s", e)
        return None


# ───── Хэндлеры бота ─────
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

    # Сообщение-«заглушка»
    msg = await update.message.reply_text("Генерирую документ… немного времени займёт ⏳")

    user_text = update.message.text
    service = context.user_data.get("service")

    document = await generate_document(user_text, service)

    if not document:
        await msg.edit_text("Ошибка связи с YandexGPT. Попробуйте позже или напишите /start")
        # не очищаем user_data — чтобы пользователь мог повторить попытку
        return

    # Надёжное HTML-экранирование
    safe_doc = html.escape(document)

    # Если документ очень длинный — лучше отправить как файл. Здесь простая логика:
    if len(document) > 4000:
        # сохраним во временный файл и отправим как .txt
        filename = f"{service}_document.txt"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(document)
        await msg.delete()
        await update.message.reply_document(open(filename, "rb"), filename=filename,
                                            caption=f"{document_templates[service]['name']} — {document_templates[service]['price']} ₽\n\nОтправь оплату и пришли чек — пришлю Word+PDF.")
        try:
            os.remove(filename)
        except Exception:
            pass
    else:
        await msg.edit_message_text(
            f"<b>ГОТОВО!</b>\n\n"
            f"<b>{document_templates[service]['name']}</b> — {document_templates[service]['price']} ₽\n\n"
            f"{safe_doc}\n\n"
            f"<b>Оплата:</b>\n"
            f"Карта Т-Банк: <code>2200 7007 0401 2581</code>\n\n"
            f"После оплаты пришлите чек — пришлю документ в Word + PDF",
            parse_mode="HTML"
        )

    # аккуратно очищаем только служебные поля
    context.user_data.pop("service", None)


# ───── ЗАПУСК ─────
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Бот с Assistant API запущен")
    try:
        app.run_polling(drop_pending_updates=True)
    except Exception:
        logger.exception("App run failed")


if __name__ == "__main__":
    main()
