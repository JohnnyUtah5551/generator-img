import os
import json
import logging
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    LabeledPrice,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    PreCheckoutQueryHandler,
    ContextTypes,
    filters,
)
import replicate

# Логирование
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Переменные окружения
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
REPLICATE_API_KEY = os.getenv("REPLICATE_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
PROVIDER_TOKEN = ""  # Telegram Stars не требуют provider_token

# Файл с балансами
BALANCES_FILE = "balances.json"

def load_balances():
    if os.path.exists(BALANCES_FILE):
        try:
            with open(BALANCES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Ошибка загрузки балансов: {e}")
            return {}
    return {}

def save_balances():
    try:
        with open(BALANCES_FILE, "w", encoding="utf-8") as f:
            json.dump(user_balances, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Ошибка сохранения балансов: {e}")

# Балансы пользователей
user_balances = load_balances()

# Стартовое меню
MAIN_MENU = InlineKeyboardMarkup([
    [InlineKeyboardButton("🎨 Сгенерировать", callback_data="generate")],
    [InlineKeyboardButton("💳 Купить генерации", callback_data="buy")],
    [InlineKeyboardButton("💰 Баланс", callback_data="balance")],
    [InlineKeyboardButton("❓ Помощь", callback_data="help")],
])

# Пакеты покупок
PRICES = {
    "buy_10": {"amount": 10, "price": 40},   # 40⭐
    "buy_50": {"amount": 50, "price": 200},  # 200⭐
    "buy_100": {"amount": 100, "price": 400} # 400⭐
}

# Хендлеры
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if str(user_id) not in user_balances:
        user_balances[str(user_id)] = 3  # 3 бесплатные генерации
        save_balances()
    await update.message.reply_text(
        "👋 Привет! Я бот для генерации и редактирования изображений с помощью нейросети Nano Banana (Google Gemini 2.5 Flash).\n\n"
        "✨ У тебя 3 бесплатных генерации.\n\n"
        "Отправьте от 1 до 4 изображений с подписью, что нужно изменить, или просто напишите текст, чтобы создать новое изображение.",
        reply_markup=MAIN_MENU,
    )

async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "generate":
        await query.message.reply_text(
            "Создавайте и редактируйте изображения прямо в чате.\n\n"
            "Для вас работает Google Gemini 2.5 Flash — она же Nano Banana 🍌\n\n"
            "Готовы начать?\n"
            "Отправьте от 1 до 4 изображений, которые вы хотите изменить, или напишите в чат, что нужно создать"
        )
        await query.message.delete()

    elif query.data == "buy":
        keyboard = [
            [InlineKeyboardButton("10 генераций — 40⭐", callback_data="buy_10")],
            [InlineKeyboardButton("50 генераций — 200⭐", callback_data="buy_50")],
            [InlineKeyboardButton("100 генераций — 400⭐", callback_data="buy_100")],
        ]
        await query.message.reply_text("Выберите пакет генераций:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data == "balance":
        user_id = str(query.from_user.id)
        balance = user_balances.get(user_id, 0)
        await query.message.reply_text(f"💰 Ваш баланс: {balance} генераций")

    elif query.data == "help":
        await query.message.reply_text(
            "❓ Помощь\n\n"
            "— Отправьте описание картинки или загрузите 1–4 фото с подписью.\n"
            "— Для генерации используется Google Gemini 2.5 Flash (Nano Banana).\n"
            "— У вас есть 3 бесплатные генерации, далее можно купить дополнительные ⭐."
        )

# Покупка генераций
async def buy_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    package = query.data

    if package not in PRICES:
        return

    price_data = PRICES[package]
    title = f"{price_data['amount']} генераций"
    description = f"Пополнение баланса на {price_data['amount']} генераций"
    payload = package
    currency = "XTR"  # Stars
    prices = [LabeledPrice("Генерации", price_data["price"])]

    await context.bot.send_invoice(
        chat_id=query.from_user.id,
        title=title,
        description=description,
        payload=payload,
        provider_token=PROVIDER_TOKEN,
        currency=currency,
        prices=prices,
    )

async def precheckout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    await query.answer(ok=True)

async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    payload = update.message.successful_payment.invoice_payload

    if payload in PRICES:
        user_balances[user_id] = user_balances.get(user_id, 0) + PRICES[payload]["amount"]
        save_balances()
        await update.message.reply_text("✅ Оплата прошла успешно! Баланс пополнен.", reply_markup=MAIN_MENU)

# Обработка текста/фото
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    balance = user_balances.get(user_id, 0)

    if balance <= 0:
        await update.message.reply_text("⚠️ У вас закончились генерации. Пополните баланс в меню.")
        return

    prompt = update.message.text or update.message.caption
    photos = update.message.photo

    if photos and not prompt:
        await update.message.reply_text("📩 Пожалуйста, добавьте описание к фото, чтобы я знал, что нужно сделать.")
        return

    # Списываем генерацию
    user_balances[user_id] = balance - 1
    save_balances()

    await update.message.reply_text("⏳ Генерация изображения... (Nano Banana через Replicate)")
    try:
        client = replicate.Client(api_token=REPLICATE_API_KEY)
        output = client.run(
            "google/nano-banana:latest",
            input={"prompt": prompt}
        )
        if output and isinstance(output, list):
            await update.message.reply_photo(photo=output[0])
            keyboard = [
                [InlineKeyboardButton("🔁 Повторить", callback_data="generate"),
                 InlineKeyboardButton("✅ Завершить", callback_data="menu")],
            ]
            await update.message.reply_text("Напишите в чат, если нужно изменить что-то еще:",
                                            reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.message.reply_text("⚠️ Не удалось получить изображение.")
    except Exception as e:
        logger.error(f"Ошибка генерации: {e}")
        await update.message.reply_text("❌ Ошибка при генерации изображения.")

# Запуск бота
def main():
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(menu_callback, pattern="^(generate|buy|balance|help|menu)$"))
    application.add_handler(CallbackQueryHandler(buy_handler, pattern="^(buy_10|buy_50|buy_100)$"))
    application.add_handler(PreCheckoutQueryHandler(precheckout_handler))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_message))

    application.run_polling()

if __name__ == "__main__":
    main()
