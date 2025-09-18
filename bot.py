import logging
import os
import replicate
import requests
from deep_translator import GoogleTranslator
from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto, LabeledPrice
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackContext, CallbackQueryHandler,
    filters
)

# Логирование
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Токены и ключи
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
REPLICATE_API_KEY = os.getenv("REPLICATE_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
RENDER_URL = os.getenv("RENDER_URL", "https://generator-img-1.onrender.com")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "webhook")

# Клиент Replicate
os.environ["REPLICATE_API_TOKEN"] = REPLICATE_API_KEY

# Временные хранилища
user_balances = {}  # {user_id: количество генераций}
user_freebies = {}  # {user_id: сколько бесплатных использовано}
user_photos = {}    # {user_id: [список фото]}
FREE_GENERATIONS = 3

# Inline меню
main_menu = InlineKeyboardMarkup([
    [InlineKeyboardButton("✨ Сгенерировать", callback_data="generate")],
    [InlineKeyboardButton("🖼 Баланс", callback_data="balance")],
    [InlineKeyboardButton("⭐ Купить генерации", callback_data="buy")]
])

back_menu = InlineKeyboardMarkup([
    [InlineKeyboardButton("⬅️ Назад в меню", callback_data="menu")]
])


# ==========================
# 📌 Обработчики
# ==========================
async def start(update: Update, context: CallbackContext):
    await update.message.reply_text(
        "👋 Привет! Я бот для генерации и редактирования изображений "
        "с помощью **Google Gemini 2.5 Flash — Nano Banana 🍌**.\n\n"
        "⚡ Nano Banana — одна из самых мощных нейросетей на сегодняшний день.\n\n"
        "✨ У тебя есть 3 бесплатные генерации.\n"
        "Выбери действие ниже 👇",
        reply_markup=main_menu,
        parse_mode="Markdown"
    )


async def button_handler(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "generate":
        user_photos[user_id] = []
        await query.edit_message_text(
            "📸 Пришли описание или до 4 фото (в одном сообщении), чтобы я сгенерировал результат.",
            reply_markup=back_menu
        )
    elif query.data == "balance":
        balance = user_balances.get(user_id, 0)
        freebies_used = user_freebies.get(user_id, 0)
        free_left = max(0, FREE_GENERATIONS - freebies_used)
        await query.edit_message_text(
            f"💳 Твой баланс:\n"
            f"— Бесплатные генерации: {free_left}\n"
            f"— Купленные генерации: {balance}\n\n",
            reply_markup=main_menu
        )
    elif query.data == "buy":
        keyboard = [
            [InlineKeyboardButton("✨ 10 генераций — 40⭐", callback_data="buy_10")],
            [InlineKeyboardButton("💫 50 генераций — 200⭐", callback_data="buy_50")],
            [InlineKeyboardButton("🚀 100 генераций — 400⭐", callback_data="buy_100")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="menu")]
        ]
        await query.edit_message_text(
            "Выбери пакет генераций:", reply_markup=InlineKeyboardMarkup(keyboard)
        )
    elif query.data == "menu":
        await query.edit_message_text("Главное меню 👇", reply_markup=main_menu)


# ==========================
# 📌 Генерация
# ==========================
async def handle_text(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    text = update.message.text.strip()

    if not await check_and_decrement_balance(update, user_id):
        return

    try:
        prompt = GoogleTranslator(source="auto", target="en").translate(text)
    except Exception:
        prompt = text

    await update.message.reply_text("✨ Генерирую изображение, подожди...")

    try:
        output = replicate.run(
            "google/nano-banana:8b5d8483cbb4e72c772b9477d5193a004d19c7a95d24e30f7110e2c735023d4e",
            input={"prompt": prompt}
        )
        if output:
            await update.message.reply_photo(photo=output[0], reply_markup=back_menu)
        else:
            await update.message.reply_text(
                "❌ Ошибка генерации. Попробуй позже.", reply_markup=back_menu
            )
    except Exception as e:
        logger.error(f"Ошибка генерации (текст): {e}")
        await update.message.reply_text(
            "❌ Ошибка генерации. Попробуй позже.", reply_markup=back_menu
        )


async def handle_photo(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    photos = update.message.photo

    file = await context.bot.get_file(photos[-1].file_id)
    url = file.file_path

    user_photos.setdefault(user_id, []).append(url)

    if len(user_photos[user_id]) >= 4:
        await generate_with_photos(update, context, user_id)
    else:
        await update.message.reply_text(
            f"📸 Фото загружено ({len(user_photos[user_id])}/4).\n"
            f"Отправь ещё фото или текстовое описание для генерации.",
            reply_markup=back_menu
        )


async def generate_with_photos(update: Update, context: CallbackContext, user_id: int):
    if not await check_and_decrement_balance(update, user_id):
        return

    await update.message.reply_text("✨ Обрабатываю фото, подожди...")

    try:
        output = replicate.run(
            "google/nano-banana:8b5d8483cbb4e72c772b9477d5193a004d19c7a95d24e30f7110e2c735023d4e",
            input={"image": user_photos[user_id]}
        )
        if output:
            media = [InputMediaPhoto(img) for img in output]
            await update.message.reply_media_group(media)
            await update.message.reply_text("✅ Готово!", reply_markup=back_menu)
        else:
            await update.message.reply_text(
                "❌ Ошибка генерации. Попробуй позже.", reply_markup=back_menu
            )
    except Exception as e:
        logger.error(f"Ошибка генерации (фото): {e}")
        await update.message.reply_text(
            "❌ Ошибка генерации. Попробуй позже.", reply_markup=back_menu
        )
    finally:
        user_photos[user_id] = []


# ==========================
# 📌 Баланс и покупки
# ==========================
async def check_and_decrement_balance(update: Update, user_id: int) -> bool:
    freebies_used = user_freebies.get(user_id, 0)
    if freebies_used < FREE_GENERATIONS:
        user_freebies[user_id] = freebies_used + 1
        return True

    balance = user_balances.get(user_id, 0)
    if balance > 0:
        user_balances[user_id] = balance - 1
        return True

    await update.message.reply_text(
        "❌ У тебя закончились генерации.\n"
        "Купи дополнительные в меню ⭐ Купить генерации.",
        reply_markup=main_menu
    )
    return False


async def successful_payment(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    payload = update.message.successful_payment.invoice_payload

    if payload == "buy_10":
        user_balances[user_id] = user_balances.get(user_id, 0) + 10
    elif payload == "buy_50":
        user_balances[user_id] = user_balances.get(user_id, 0) + 50
    elif payload == "buy_100":
        user_balances[user_id] = user_balances.get(user_id, 0) + 100

    await update.message.reply_text("✅ Покупка успешно завершена!", reply_markup=main_menu)


# ==========================
# 📌 Запуск
# ==========================
def main():
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))

    application.run_webhook(
        listen="0.0.0.0",
        port=5000,
        url_path=TELEGRAM_BOT_TOKEN,
        webhook_url=f"{RENDER_URL}/{TELEGRAM_BOT_TOKEN}"
    )


if __name__ == "__main__":
    main()

