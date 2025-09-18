import os
import logging
import sqlite3
import replicate
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
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

# ----------------------- ЛОГИ -----------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ----------------------- НАСТРОЙКИ -----------------------
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_KEY")
WEBHOOK_URL = os.getenv("RENDER_EXTERNAL_URL")
DB_PATH = "users.db"
GEN_COST = 4

# ----------------------- БАЗА ДАННЫХ -----------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, balance INTEGER, last_image TEXT)")
    conn.commit()
    conn.close()


def get_balance(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT balance FROM users WHERE id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0


def update_balance(user_id, diff):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (id, balance, last_image) VALUES (?, ?, ?) ", (user_id, 3, None))
    c.execute("UPDATE users SET balance = balance + ? WHERE id=?", (diff, user_id))
    conn.commit()
    conn.close()


def set_last_image(user_id, url):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE users SET last_image=? WHERE id=?", (url, user_id))
    conn.commit()
    conn.close()


def get_last_image(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT last_image FROM users WHERE id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None


# ----------------------- ОБРАБОТЧИКИ -----------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Для вас работает **Google Gemini (Nano Banana)** — одна из самых мощных нейросетей для генерации и редактирования изображений.\n\n"
        "**Готовы начать?**\n\n"
        "Отправьте от 1 до 4 изображений, которые хотите изменить, или просто напишите текст, чтобы создать новое изображение.",
        parse_mode="Markdown"
    )


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bal = get_balance(update.effective_user.id)
    await update.message.reply_text(f"💰 У вас {bal} генераций.")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bal = get_balance(user_id)

    if bal < GEN_COST:
        await update.message.reply_text("❌ Недостаточно генераций. Пополните баланс.")
        return

    prompt = update.message.text
    await generate_and_send(update, context, prompt, [])


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bal = get_balance(user_id)

    if bal < GEN_COST:
        await update.message.reply_text("❌ Недостаточно генераций. Пополните баланс.")
        return

    photos = update.message.photo
    caption = update.message.caption

    file_id = photos[-1].file_id
    file = await context.bot.get_file(file_id)
    img_url = file.file_path

    context.user_data.setdefault("pending_images", []).append(img_url)

    if len(context.user_data["pending_images"]) > 4:
        await update.message.reply_text("❌ Можно загрузить максимум 4 изображения за раз.")
        context.user_data["pending_images"] = []
        return

    if caption:
        await generate_and_send(update, context, caption, context.user_data["pending_images"])
        context.user_data["pending_images"] = []
    else:
        await update.message.reply_text("📌 Добавьте описание для генерации.")


async def generate_and_send(update, context, prompt, images):
    user_id = update.effective_user.id

    # Сообщение-заглушка
    wait_msg = await update.message.reply_text("⏳ Подождите, идёт генерация...")

    try:
        update_balance(user_id, -GEN_COST)

        inputs = {"prompt": prompt}
        if images:
            inputs["image"] = images[0]  # пока только первая для edit

        output = replicate.run("google/nano-banana", input=inputs)
        result_url = output[0] if isinstance(output, list) else output

        set_last_image(user_id, result_url)

        # Удаляем "идёт генерация"
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=wait_msg.message_id)

        keyboard = [[
            InlineKeyboardButton("Сгенерировать другой вариант", callback_data="retry"),
            InlineKeyboardButton("Закончить генерацию", callback_data="end")
        ]]

        await update.message.reply_photo(
            photo=result_url,
            caption=("✨ Результат генерации.\n\n"
                     "✏️ Вы можете просто написать новый текст в чат — он применится к последнему изображению."),
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception as e:
        logger.error(f"Ошибка генерации: {e}")
        await update.message.reply_text("⚠️ Ошибка при генерации. Попробуйте снова.")
    finally:
        context.user_data["pending_images"] = []


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "retry":
        last_img = get_last_image(user_id)
        if not last_img:
            await query.edit_message_caption("❌ Нет изображения для повтора.")
            return

        bal = get_balance(user_id)
        if bal < GEN_COST:
            await query.edit_message_caption("❌ Недостаточно генераций.")
            return

        await generate_and_send(query, context, "", [last_img])

    elif query.data == "end":
        await query.edit_message_caption("✅ Сессия завершена.")


# ----------------------- MAIN -----------------------
def main():
    init_db()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(CallbackQueryHandler(button_handler))

    if WEBHOOK_URL:
        app.run_webhook(
            listen="0.0.0.0",
            port=int(os.environ.get("PORT", 10000)),
            url_path="webhook",
            webhook_url=f"{WEBHOOK_URL}/webhook"
        )
    else:
        app.run_polling()


if __name__ == "__main__":
    logger.info("🚀 Бот запускается...")
    main()



