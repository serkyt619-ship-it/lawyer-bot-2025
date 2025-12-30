import os
import uuid
import asyncio
from datetime import datetime

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage

from docx import Document
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

# ================== НАСТРОЙКИ ==================
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
CARD_NUMBER = os.getenv("CARD_NUMBER")
CARD_HOLDER = os.getenv("CARD_HOLDER")

OUT_DIR = "docs"
os.makedirs(OUT_DIR, exist_ok=True)

bot = Bot(BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ================== КАТЕГОРИИ ==================
CATEGORIES = {
    "police": ("Заявление в полицию", 199),
    "seller": ("Претензия продавцу", 299),
    "court": ("Исковое заявление в суд", 499),
    "rospotreb": ("Жалоба в Роспотребнадзор", 249),
    "bailiff": ("Заявление судебным приставам", 349),
}

# ================== FSM ==================
class Form(StatesGroup):
    category = State()
    fio = State()
    address = State()
    phone = State()
    details = State()
    waiting_transfer = State()

# ================== КЛАВИАТУРЫ ==================
def categories_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text=f"{name} — {price} ₽",
                callback_data=key
            )]
            for key, (name, price) in CATEGORIES.items()
        ]
    )

# ================== START ==================
@dp.message(CommandStart())
async def start(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer(
        "👨‍⚖️ *Юрист-бот*\n\n"
        "Я автоматически составлю заявление.\n"
        "Выбери категорию:",
        reply_markup=categories_kb(),
        parse_mode="Markdown"
    )

# ================== ВЫБОР КАТЕГОРИИ ==================
@dp.callback_query()
async def choose_category(cb: CallbackQuery, state: FSMContext):
    if cb.data not in CATEGORIES:
        return
    await state.update_data(category=cb.data)
    await state.set_state(Form.fio)
    await cb.message.answer("Введите *ФИО*:", parse_mode="Markdown")

# ================== СБОР ДАННЫХ ==================
@dp.message(Form.fio)
async def step_fio(msg: Message, state: FSMContext):
    await state.update_data(fio=msg.text)
    await state.set_state(Form.address)
    await msg.answer("Введите *адрес проживания*:")

@dp.message(Form.address)
async def step_address(msg: Message, state: FSMContext):
    await state.update_data(address=msg.text)
    await state.set_state(Form.phone)
    await msg.answer("Введите *телефон*:")

@dp.message(Form.phone)
async def step_phone(msg: Message, state: FSMContext):
    await state.update_data(phone=msg.text)
    await state.set_state(Form.details)
    await msg.answer("Кратко опишите ситуацию *по фактам*:")

# ================== ОПЛАТА ==================
@dp.message(Form.details)
async def step_details(msg: Message, state: FSMContext):
    data = await state.get_data()
    cat_name, price = CATEGORIES[data["category"]]

    order_id = uuid.uuid4().hex[:6]
    await state.update_data(details=msg.text, order_id=order_id)

    await msg.answer(
        f"📄 *{cat_name}*\n"
        f"💰 Сумма: *{price} ₽*\n\n"
        f"💳 Карта: `{CARD_NUMBER}`\n"
        f"👤 Получатель: *{CARD_HOLDER}*\n\n"
        f"📝 Комментарий к переводу:\n"
        f"`LAW-{order_id}`\n\n"
        "⏳ После перевода бот *автоматически* подготовит документ "
        "в течение ~1 минуты.",
        parse_mode="Markdown"
    )

    await state.set_state(Form.waiting_transfer)

    # 🔁 АВТОМАТ (ожидание)
    asyncio.create_task(auto_generate(msg.chat.id, state))

# ================== АВТОГЕНЕРАЦИЯ ==================
async def auto_generate(chat_id: int, state: FSMContext):
    await asyncio.sleep(60)  # время на перевод

    data = await state.get_data()
    if not data:
        return

    docx, pdf = generate_docs(data)

    await bot.send_message(
        chat_id,
        "✅ Оплата принята.\n"
        "Документы готовы:"
    )
    await bot.send_document(chat_id, FSInputFile(docx))
    await bot.send_document(chat_id, FSInputFile(pdf))

    await state.clear()

# ================== ГЕНЕРАЦИЯ ДОКУМЕНТОВ ==================
def generate_docs(data):
    name, _ = CATEGORIES[data["category"]]

    text = (
        f"{name.upper()}\n\n"
        f"ФИО: {data['fio']}\n"
        f"Адрес: {data['address']}\n"
        f"Телефон: {data['phone']}\n\n"
        f"Суть обращения:\n{data['details']}\n\n"
        f"Дата: {datetime.now().strftime('%d.%m.%Y')}\n"
        f"Подпись: ____________________"
    )

    base = f"doc_{uuid.uuid4().hex[:8]}"
    docx_path = f"{OUT_DIR}/{base}.docx"
    pdf_path = f"{OUT_DIR}/{base}.pdf"

    # DOCX
    doc = Document()
    for line in text.split("\n"):
        doc.add_paragraph(line)
    doc.save(docx_path)

    # PDF
    c = canvas.Canvas(pdf_path, pagesize=A4)
    y = 800
    for line in text.split("\n"):
        c.drawString(40, y, line)
        y -= 14
    c.save()

    return docx_path, pdf_path

# ================== RUN ==================
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
