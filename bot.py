import logging
import os
import replicate
import requests
from deep_translator import GoogleTranslator
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    LabeledPrice,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    PreCheckoutQueryHandler,
)

# === ЛОГИ ===
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# === ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ===
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
REPLICATE_API_KEY = os.getenv("REPLICATE_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
RENDER_URL = os.getenv("RENDER_URL", "https://generator-img-1.onrender.com")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "webhook")

# === ПАМЯТЬ ПОЛЬЗОВАТЕЛЕЙ ===
user_data = {}

# === ЦЕНЫ (в звёздах) ===
PRICES = {
    "10": 40,   # 10 генераций — 40⭐
    "50": 200,  # 50 генераций — 200⭐
    "100": 400, # 100 генераций — 400⭐
}

# === КОМАНДА /start ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_data:
        user_data[user_id] = {"free": 3, "paid": 0}
    await update.message.reply_text(
        "👋 Привет! Я бот для генерации изображений с помощью нейросети **Nano Banana 🍌**.\n\n"
        "✨ У тебя есть 3 бесплатные генерации.\n"
        "💫 Хочешь больше? Купи пакеты генераций через Telegram Stars!\n\n"
        "📌 Доступные команды:\n"
        "/balance — Проверить баланс\n"
        "/generate — Сгенерировать изображение\n"
        "/buy — Купить генерации"
    )

# === КОМАНДА /balance ===
async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    data = user_data.get(user_id, {"free": 0, "paid": 0})
    await update.message.reply_text(
        f"📊 Баланс:\n"
        f"Бесплатные: {data['free']}\n"
        f"Платные: {data['paid']}"
    )

# === КОМАНДА /buy ===
async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("10 генераций — 40⭐", callback_data="buy_10")],
        [InlineKeyboardButton("50 генераций — 200⭐", callback_data="buy_50")],
        [InlineKeyboardButton("100 генераций — 400⭐", callback_data="buy_100")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("💫 Выбери пакет генераций:", reply_markup=reply_markup)

# === ОБРАБОТКА КНОПОК ПОКУПКИ ===
async def buy_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    package = query.data.split("_")[1]
    price = PRICES.get(package)
    if not price:
        await query.edit_message_text("❌ Ошибка. Попробуй снова.")
        return

    prices = [LabeledPrice(label=f"{package} генераций", amount=price * 100)]  # Stars → копейки
    await context.bot.send_invoice(
        chat_id=query.message.chat_id,
        title=f"{package} генераций",
        description=f"Покупка {package} генераций для Nano Banana 🍌",
        payload=f"buy_{package}",
        provider_token="",
        currency="XTR",  # Telegram Stars
        prices=prices,
        start_parameter="test-payment",
    )

# === CALLBACK ПОКУПКИ ===
async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    await query.answer(ok=True)

# === ОБРАБОТКА УСПЕШНОГО ПЛАТЕЖА ===
async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.chat_id
    payload = update.message.successful_payment.invoice_payload
    package = payload.split("_")[1]

    if user_id not in user_data:
        user_data[user_id] = {"free": 0, "paid": 0}

    user_data[user_id]["paid"] += int(package)
    await update.message.reply_text(f"✅ Успех! Баланс пополнен на {package} генераций.")

# === КОМАНДА /generate ===
async def generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    data = user_data.get(user_id, {"free": 0, "paid": 0})

    if data["free"] <= 0 and data["paid"] <= 0:
        await update.message.reply_text("❌ У тебя закончились генерации. Купи ещё с помощью /buy")
        return

    if not context.args:
        await update.message.reply_text("✍️ Введи описание изображения. Пример:\n/generate кот в космосе")
        return

    prompt = " ".join(context.args)

    try:
        translated_prompt = GoogleTranslator(source="auto", target="en").translate(prompt)
    except Exception:
        translated_prompt = prompt

    try:
        client = replicate.Client(api_token=REPLICATE_API_KEY)
        output = client.run(
            "google/nano-banana",
            input={"prompt": translated_prompt}
        )

        if not output:
            raise Exception("Пустой ответ от модели")

        image_url = output[0]
        await update.message.reply_photo(photo=image_url, caption="✨ Готово!")

        if data["free"] > 0:
            data["free"] -= 1
        else:
            data["paid"] -= 1

    except Exception as e:
        logger.error(f"Ошибка генерации: {e}")
        await update.message.reply_text("❌ Ошибка генерации. Попробуй позже.")

# === MAIN ===
def main():
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("balance", balance))
    application.add_handler(CommandHandler("generate", generate))
    application.add_handler(CommandHandler("buy", buy))
    application.add_handler(CallbackQueryHandler(buy_button, pattern="^buy_"))
    application.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))

    application.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", 5000)),
        url_path=WEBHOOK_PATH,
        webhook_url=f"{RENDER_URL}/{WEBHOOK_PATH}",
    )

if __name__ == "__main__":
    main()
