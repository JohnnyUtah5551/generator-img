import os
import logging
import replicate
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackQueryHandler,
    PreCheckoutQueryHandler,
    ContextTypes,
)
from deep_translator import GoogleTranslator

# === Логирование ===
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# === Переменные окружения ===
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
REPLICATE_API_KEY = os.getenv("REPLICATE_API_KEY")
RENDER_URL = os.getenv("RENDER_URL", "https://generator-img-1.onrender.com")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "webhook")
ADMIN_ID = os.getenv("ADMIN_ID")

# === Клиент Replicate ===
replicate_client = replicate.Client(api_token=REPLICATE_API_KEY)

# === Данные пользователей ===
users = {}  # user_id -> {"balance": int, "used": int}

FREE_GENERATIONS = 3
GEN_COST = 2  # 2⭐ за 1 генерацию

PACKAGES = {
    "10": 20,   # 10 генераций = 20⭐
    "50": 100,  # 50 генераций = 100⭐
    "100": 200, # 100 генераций = 200⭐
}

# === Старт ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in users:
        users[user_id] = {"balance": FREE_GENERATIONS, "used": 0}
    await update.message.reply_text(
        "👋 Привет! Я бот для генерации изображений с помощью нейросети **Nano Banana 🍌 (Google Gemini 2.5 Flash)**.\n\n"
        f"У тебя {FREE_GENERATIONS} бесплатных генераций.\n"
        "Напиши /generate и свой запрос или загрузи 1–4 фото с описанием."
    )

# === Баланс ===
async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = users.get(user_id, {"balance": 0, "used": 0})
    await update.message.reply_text(
        f"💰 Остаток генераций: {user['balance']}\n"
        f"📊 Использовано: {user['used']}"
    )

# === Генерация текста + фото ===
async def generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = users.get(user_id, {"balance": FREE_GENERATIONS, "used": 0})
    prompt = " ".join(context.args)

    if not prompt:
        await update.message.reply_text("✍️ Напиши запрос после команды /generate")
        return

    if user["balance"] <= 0:
        await update.message.reply_text("⚠️ У тебя закончились генерации. Купи ещё через /buy")
        return

    # Переводим запрос на английский
    translated_prompt = GoogleTranslator(source="auto", target="en").translate(prompt)

    await update.message.reply_text("⏳ Генерирую изображение...")

    try:
        output = replicate_client.run(
            "google/nano-banana:latest",
            input={"prompt": translated_prompt}
        )
        img_url = output[0]

        await update.message.reply_photo(photo=img_url)
        user["balance"] -= 1
        user["used"] += 1
    except Exception as e:
        logger.error(f"Ошибка генерации: {e}")
        await update.message.reply_text("❌ Ошибка генерации. Попробуй позже.")

# === Обработка фото ===
async def handle_photos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = users.get(user_id, {"balance": FREE_GENERATIONS, "used": 0})
    photos = update.message.photo
    caption = update.message.caption

    if not caption:
        await update.message.reply_text("✍️ Добавь описание для генерации вместе с фото.")
        return

    if user["balance"] <= 0:
        await update.message.reply_text("⚠️ У тебя закончились генерации. Купи ещё через /buy")
        return

    # Переводим описание
    translated_prompt = GoogleTranslator(source="auto", target="en").translate(caption)

    # Берём до 4 фото
    photo_files = []
    for photo in photos[:4]:
        file = await context.bot.get_file(photo.file_id)
        photo_files.append(file.file_path)

    await update.message.reply_text("⏳ Генерирую изображение...")

    try:
        output = replicate_client.run(
            "google/nano-banana:latest",
            input={"prompt": translated_prompt, "image": photo_files}
        )
        for img in output:
            await update.message.reply_photo(photo=img)

        user["balance"] -= 1
        user["used"] += 1
    except Exception as e:
        logger.error(f"Ошибка генерации (фото): {e}")
        await update.message.reply_text("❌ Ошибка генерации. Попробуй позже.")

# === Покупка генераций ===
async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("10 генераций — 20⭐", callback_data="buy_10")],
        [InlineKeyboardButton("50 генераций — 100⭐", callback_data="buy_50")],
        [InlineKeyboardButton("100 генераций — 200⭐", callback_data="buy_100")],
    ]
    await update.message.reply_text("🛒 Выбери пакет:", reply_markup=InlineKeyboardMarkup(keyboard))

async def buy_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    choice = query.data.split("_")[1]
    stars = PACKAGES[choice]
    await query.message.reply_invoice(
        title=f"Пакет {choice} генераций",
        description=f"Пополнение баланса на {choice} генераций.",
        payload=f"buy_{choice}",
        provider_token="",  # Telegram Stars → оставить пустым
        currency="XTR",
        prices=[LabeledPrice(label=f"{choice} генераций", amount=stars)],
        start_parameter="test",
    )

async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    await query.answer(ok=True)

async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    payload = update.message.successful_payment.invoice_payload
    amount = int(payload.split("_")[1])
    users[user_id]["balance"] += int(amount)
    await update.message.reply_text(f"✅ Баланс пополнен на {amount} генераций!")

# === Main ===
def main():
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("balance", balance))
    application.add_handler(CommandHandler("generate", generate))
    application.add_handler(CommandHandler("buy", buy))
    application.add_handler(CallbackQueryHandler(buy_button, pattern="^buy_"))
    application.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photos))

    application.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", 5000)),
        url_path=WEBHOOK_PATH,
        webhook_url=f"{RENDER_URL}/{WEBHOOK_PATH}",
    )

if __name__ == "__main__":
    main()
