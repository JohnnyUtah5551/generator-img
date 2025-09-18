import os
import logging
import sqlite3
import replicate
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputFile,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

# -------------------
# ЛОГИРОВАНИЕ
# -------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# -------------------
# НАСТРОЙКИ
# -------------------
REPLICATE_API_KEY = os.getenv("REPLICATE_API_KEY")
MODEL_ID = "google/nano-banana"
GENERATION_COST = 4  # Стоимость одной генерации в звёздах
DB_PATH = "users.db"

# -------------------
# ИНИЦИАЛИЗАЦИЯ БАЗЫ
# -------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            balance INTEGER DEFAULT 3,
            last_image_url TEXT
        )"""
    )
    conn.commit()
    conn.close()


def get_user(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT balance, last_image_url FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    if not row:
        cur.execute("INSERT INTO users (user_id, balance) VALUES (?, ?)", (user_id, 3))
        conn.commit()
        row = (3, None)
    conn.close()
    return row


def update_balance(user_id: int, new_balance: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE users SET balance = ? WHERE user_id = ?", (new_balance, user_id))
    conn.commit()
    conn.close()


def update_last_image(user_id: int, url: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE users SET last_image_url = ? WHERE user_id = ?", (url, user_id))
    conn.commit()
    conn.close()


# -------------------
# КНОПКИ
# -------------------
def main_menu():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✨ Сгенерировать", callback_data="generate")],
            [InlineKeyboardButton("💰 Баланс", callback_data="balance")],
            [InlineKeyboardButton("🛒 Купить", callback_data="buy")],
        ]
    )


def after_generation_menu():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔄 Сгенерировать другой вариант", callback_data="regenerate")],
            [InlineKeyboardButton("✅ Закончить", callback_data="finish")],
        ]
    )


# -------------------
# ОБРАБОТЧИКИ
# -------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()
    user = update.effective_user
    balance, _ = get_user(user.id)
    await update.message.reply_text(
        f"👋 Привет, {user.first_name}!\n\n"
        "Я бот для генерации и редактирования изображений с помощью самой мощной нейросети "
        "**Google Nano Banana 🍌**.\n\n"
        f"✨ У тебя {balance} бесплатных генераций.\n"
        "💫 При необходимости можно докупить ещё через Telegram Stars.",
        reply_markup=main_menu(),
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "balance":
        balance, _ = get_user(query.from_user.id)
        await query.message.reply_text(f"💰 У тебя {balance} генераций", reply_markup=main_menu())

    elif query.data == "buy":
        await query.message.reply_text(
            "🛒 Покупка генераций через Telegram Stars пока в разработке.",
            reply_markup=main_menu(),
        )

    elif query.data == "generate":
        await query.message.reply_text("✍️ Напиши описание для генерации или пришли фото с подписью.")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt = update.message.text
    user_id = update.effective_user.id
    balance, last_image = get_user(user_id)

    if balance < GENERATION_COST:
        await update.message.reply_text("❌ Недостаточно генераций. Пополни баланс.", reply_markup=main_menu())
        return

    await update.message.reply_text("🎨 Генерация изображения...")

    try:
        output = replicate.run(
            f"{MODEL_ID}:latest",
            input={"prompt": prompt, "num_outputs": 1},
        )
        image_url = output[0]
        update_last_image(user_id, image_url)
        update_balance(user_id, balance - GENERATION_COST)

        await update.message.reply_photo(
            image_url,
            caption="✅ Готово!\nНапишите в чат, если нужно изменить что-то ещё.",
            reply_markup=after_generation_menu(),
        )
    except Exception as e:
        logger.error(f"Ошибка генерации: {e}")
        await update.message.reply_text("❌ Ошибка генерации. Попробуй позже.", reply_markup=main_menu())


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    balance, _ = get_user(user_id)

    if balance < GENERATION_COST:
        await update.message.reply_text("❌ Недостаточно генераций. Пополни баланс.", reply_markup=main_menu())
        return

    photo = update.message.photo[-1]
    file = await photo.get_file()
    file_path = f"/tmp/{user_id}.jpg"
    await file.download_to_drive(file_path)

    prompt = update.message.caption
    if not prompt:
        await update.message.reply_text("📸 Фото получено! Напиши описание для генерации.")
        context.user_data["pending_photo"] = file_path
        return

    await update.message.reply_text("🎨 Генерация изображения...")

    try:
        with open(file_path, "rb") as f:
            output = replicate.run(
                f"{MODEL_ID}:latest",
                input={"prompt": prompt, "image": f, "num_outputs": 1},
            )
        image_url = output[0]
        update_last_image(user_id, image_url)
        update_balance(user_id, balance - GENERATION_COST)

        await update.message.reply_photo(
            image_url,
            caption="✅ Готово!\nНапишите в чат, если нужно изменить что-то ещё.",
            reply_markup=after_generation_menu(),
        )
    except Exception as e:
        logger.error(f"Ошибка генерации (фото): {e}")
        await update.message.reply_text("❌ Ошибка генерации. Попробуй позже.", reply_markup=main_menu())


async def regenerate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    balance, last_image = get_user(user_id)

    if not last_image:
        await update.callback_query.message.reply_text("❌ Нет последнего изображения для повторной генерации.", reply_markup=main_menu())
        return

    if balance < GENERATION_COST:
        await update.callback_query.message.reply_text("❌ Недостаточно генераций. Пополни баланс.", reply_markup=main_menu())
        return

    await update.callback_query.message.reply_text("🔄 Генерация нового варианта...")

    try:
        output = replicate.run(
            f"{MODEL_ID}:latest",
            input={"image": last_image, "num_outputs": 1},
        )
        image_url = output[0]
        update_last_image(user_id, image_url)
        update_balance(user_id, balance - GENERATION_COST)

        await update.callback_query.message.reply_photo(
            image_url,
            caption="✅ Новый вариант готов!\nНапишите в чат, если нужно изменить что-то ещё.",
            reply_markup=after_generation_menu(),
        )
    except Exception as e:
        logger.error(f"Ошибка регенерации: {e}")
        await update.callback_query.message.reply_text("❌ Ошибка при генерации нового варианта.", reply_markup=main_menu())


async def finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.message.reply_text("✨ Генерация завершена.", reply_markup=main_menu())


# -------------------
# MAIN
# -------------------
def main():
    init_db()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set")

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler, pattern="^(balance|buy|generate)$"))
    app.add_handler(CallbackQueryHandler(regenerate, pattern="^regenerate$"))
    app.add_handler(CallbackQueryHandler(finish, pattern="^finish$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    app.run_polling()


if __name__ == "__main__":
    main()


