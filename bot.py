import logging
import sqlite3
import json
import os
import replicate
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InputMediaPhoto,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# Логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Репликейт токен
os.environ["REPLICATE_API_TOKEN"] = "YOUR_REPLICATE_API_TOKEN"
MODEL = "google/nano-banana"
VERSION = "latest"

# SQLite
conn = sqlite3.connect("bot.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    stars INTEGER DEFAULT 0
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS sessions (
    user_id INTEGER PRIMARY KEY,
    prompt TEXT,
    initial_images TEXT,
    last_image TEXT,
    active INTEGER DEFAULT 0
)
""")
conn.commit()

# --- DB helpers ---
def get_user(user_id: int):
    cursor.execute("SELECT stars FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    if not row:
        cursor.execute("INSERT INTO users (user_id, stars) VALUES (?, ?)", (user_id, 3))
        conn.commit()
        return 3
    return row[0]

def update_stars(user_id: int, delta: int):
    stars = get_user(user_id)
    stars = max(0, stars + delta)
    cursor.execute("UPDATE users SET stars=? WHERE user_id=?", (stars, user_id))
    conn.commit()
    return stars

def set_session(user_id: int, prompt: str, images: list):
    cursor.execute("""
        INSERT OR REPLACE INTO sessions (user_id, prompt, initial_images, last_image, active)
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, prompt, json.dumps(images), None, 1))
    conn.commit()

def update_session_last_image(user_id: int, url: str):
    cursor.execute("UPDATE sessions SET last_image=?, active=1 WHERE user_id=?", (url, user_id))
    conn.commit()

def get_session(user_id: int):
    cursor.execute("SELECT prompt, initial_images, last_image, active FROM sessions WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    if not row:
        return None
    return {
        "prompt": row[0],
        "initial_images": json.loads(row[1]) if row[1] else [],
        "last_image": row[2],
        "active": row[3],
    }

def clear_session(user_id: int):
    cursor.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
    conn.commit()

# --- UI helpers ---
def main_menu():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("✨ Сгенерировать")],
            [KeyboardButton("💰 Баланс"), KeyboardButton("🛒 Купить генерации")],
        ],
        resize_keyboard=True
    )

def result_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⚡ Сгенерировать другой вариант", callback_data="variant"),
            InlineKeyboardButton("✅ Закончить генерацию", callback_data="end"),
        ]
    ])

# --- Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    get_user(update.effective_user.id)
    await update.message.reply_text(
        "👋 Привет! Я бот для генерации и редактирования изображений с помощью "
        "**Nano Banana 🍌 — самой мощной нейросети для изображений на сегодня.**\n\n"
        "✨ У тебя есть 3 бесплатные генерации.\n\n"
        "📌 Возможности:\n"
        "— Генерация изображений по тексту\n"
        "— Редактирование и вариации загруженных фото\n"
        "— Улучшение и корректировка результатов\n\n"
        "🚀 Начни с кнопки ниже!",
        reply_markup=main_menu(),
        parse_mode="Markdown"
    )

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stars = get_user(update.effective_user.id)
    await update.message.reply_text(f"💰 У тебя {stars} генераций", reply_markup=main_menu())

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if text == "✨ Сгенерировать":
        await update.message.reply_text("✍️ Отправь описание или фото для генерации", reply_markup=main_menu())
        return
    if text == "💰 Баланс":
        await balance(update, context)
        return
    if text == "🛒 Купить генерации":
        await update.message.reply_text("🛒 Покупка генераций через Telegram Stars скоро будет доступна!", reply_markup=main_menu())
        return

    session = get_session(user_id)
    if session and session["active"] and session["last_image"]:
        # Корректировка результата
        await generate(update, context, prompt=text, input_image=session["last_image"])
    else:
        # Новая генерация (только текст)
        set_session(user_id, text, [])
        await generate(update, context, prompt=text)

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    image_url = file.file_path
    caption = update.message.caption

    session = get_session(user_id)

    if caption:
        # Фото с подписью → сразу генерация
        set_session(user_id, caption, [image_url])
        await generate(update, context, prompt=caption, input_images=[image_url])
    else:
        # Фото без подписи → ждём промт
        set_session(user_id, "", [image_url])
        await update.message.reply_text("✍️ Напиши описание для генерации", reply_markup=main_menu())

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    session = get_session(user_id)

    if not session:
        await query.edit_message_caption(caption="⚠️ Нет активной генерации", reply_markup=None)
        return

    if query.data == "variant":
        await generate(query, context, prompt=session["prompt"], input_images=session["initial_images"])
    elif query.data == "end":
        clear_session(user_id)
        await query.edit_message_caption(caption="✅ Генерация завершена", reply_markup=None)

# --- Generation ---
async def generate(update, context, prompt: str, input_images: list = None, input_image: str = None):
    user_id = update.effective_user.id
    stars = get_user(user_id)
    if stars < 4:
        await context.bot.send_message(chat_id=user_id, text="❌ Недостаточно генераций. Пополни баланс.", reply_markup=main_menu())
        return

    try:
        inputs = {"prompt": prompt}
        if input_images:
            inputs["input_images"] = input_images
        if input_image:
            inputs["input_image"] = input_image

        output = replicate.run(f"{MODEL}:{VERSION}", input=inputs)
        if isinstance(output, list):
            result_url = output[0]
        else:
            result_url = output

        update_session_last_image(user_id, result_url)
        update_stars(user_id, -4)

        if isinstance(update, Update) and update.message:
            await update.message.reply_photo(
                photo=result_url,
                caption="✨ Результат\n\nНапишите в чат, если нужно изменить что-то ещё",
                reply_markup=result_keyboard()
            )
        else:
            await context.bot.send_photo(
                chat_id=user_id,
                photo=result_url,
                caption="✨ Результат\n\nНапишите в чат, если нужно изменить что-то ещё",
                reply_markup=result_keyboard()
            )
    except Exception as e:
        logger.error(f"Ошибка генерации: {e}")
        await context.bot.send_message(chat_id=user_id, text="❌ Ошибка генерации. Попробуй позже.")

# --- Main ---
def main():
    app = Application.builder().token("YOUR_TELEGRAM_BOT_TOKEN").build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(CallbackQueryHandler(button_handler))

    app.run_polling()

if __name__ == "__main__":
    main()

