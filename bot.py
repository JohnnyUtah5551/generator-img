import os
import logging
import requests
from datetime import datetime
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
)
import replicate

# === ЛОГИ ===
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === Конфиг ===
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
REPLICATE_API_KEY = os.getenv("REPLICATE_API_KEY")
PORT = int(os.environ.get("PORT", 5000))
RENDER_URL = os.getenv("RENDER_URL")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "webhook")

ADMIN_ID = 641377565  # твой ID
FREE_GENERATIONS = 3

client = replicate.Client(api_token=REPLICATE_API_KEY)

# Хранилище генераций
user_generations = {}
user_purchases = {}
daily_stats = {"purchases": 0, "generations": 0}


# === Получение баланса Replicate ===
def get_replicate_balance():
    try:
        url = "https://api.replicate.com/v1/account"
        headers = {"Authorization": f"Token {REPLICATE_API_KEY}"}
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            return data.get("credits", {}).get("usd_cents", 0) / 100
        return None
    except Exception as e:
        logger.error(f"Ошибка получения баланса Replicate: {e}")
        return None


# === Уведомление админа ===
async def notify_admin(context: ContextTypes.DEFAULT_TYPE, message: str):
    balance = get_replicate_balance()
    balance_text = f"\n💰 Баланс Replicate: {balance:.2f}$" if balance is not None else ""
    await context.bot.send_message(chat_id=ADMIN_ID, text=message + balance_text)


# === Команда /start ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("Создать изображение", callback_data="generate")]]
    await update.message.reply_text(
        "Привет! У тебя 3 бесплатные генерации. Можешь купить больше через /buy",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# === Генерация изображения ===
async def generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "Без ника"

    count = user_generations.get(user_id, FREE_GENERATIONS)

    if count <= 0:
        await update.message.reply_text("❌ У тебя закончились генерации. Используй /buy")
        return

    prompt = " ".join(context.args) if context.args else "A futuristic city with flying cars"
    await update.message.reply_text("⏳ Генерирую изображение...")

    try:
        output = client.run(
            "stability-ai/stable-diffusion:d70beb400d223e6432425a5299910329c6050c6abcf97b8c70537d6a1fcb269a",
            input={"prompt": prompt},
        )

        if isinstance(output, list):
            for url in output:
                await update.message.reply_photo(photo=url)
        elif isinstance(output, str):
            await update.message.reply_photo(photo=output)

        # уменьшаем генерации
        user_generations[user_id] = count - 1
        daily_stats["generations"] += 1

        # уведомляем админа
        await notify_admin(
            context,
            f"👤 Пользователь @{username} (ID: {user_id})\n"
            f"📝 Промпт: {prompt}\n"
            f"🎯 Осталось генераций: {user_generations[user_id]}",
        )

    except Exception as e:
        await update.message.reply_text("⚠️ Ошибка генерации. Скоро исправим!")
        await notify_admin(
            context,
            f"❌ Ошибка у @{username} (ID: {user_id})\nПромпт: {prompt}\nОшибка: {e}",
        )


# === Баланс ===
async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    count = user_generations.get(user_id, FREE_GENERATIONS)
    await update.message.reply_text(f"📊 У тебя осталось {count} генераций.")


# === Покупки ===
async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("10 генераций - 20⭐", callback_data="buy_10")],
        [InlineKeyboardButton("50 генераций - 100⭐", callback_data="buy_50")],
        [InlineKeyboardButton("100 генераций - 200⭐", callback_data="buy_100")],
    ]
    await update.message.reply_text(
        "Выбери пакет генераций:", reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def buy_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "buy_10":
        prices = [LabeledPrice("10 генераций", 20)]
        amount = 10
    elif query.data == "buy_50":
        prices = [LabeledPrice("50 генераций", 100)]
        amount = 50
    else:
        prices = [LabeledPrice("100 генераций", 200)]
        amount = 100

    await context.bot.send_invoice(
        chat_id=query.message.chat_id,
        title="Покупка генераций",
        description="Оплата через Telegram Stars",
        payload=f"buy_{amount}",
        provider_token="",  # вставишь provider_token от Telegram Stars
        currency="XTR",
        prices=prices,
    )


async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    await query.answer(ok=True)


async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "Без ника"

    payload = update.message.successful_payment.invoice_payload
    if "buy_" in payload:
        amount = int(payload.split("_")[1])
        user_generations[user_id] = user_generations.get(user_id, FREE_GENERATIONS) + amount
        user_purchases[user_id] = user_purchases.get(user_id, 0) + amount
        daily_stats["purchases"] += 1

        await update.message.reply_text(f"✅ Успешная покупка! Тебе добавлено {amount} генераций.")

        await notify_admin(
            context,
            f"💸 Покупка у @{username} (ID: {user_id})\nКупил: {amount} генераций\n"
            f"Итого у него: {user_generations[user_id]}",
        )


# === Статистика ===
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    total_purchases = sum(user_purchases.values())
    total_generations = sum(user_generations.values())
    await update.message.reply_text(
        f"📊 Статистика:\nПокупок: {total_purchases}\nГенераций осталось у всех: {total_generations}"
    )


async def send_daily_stats(context: ContextTypes.DEFAULT_TYPE):
    await notify_admin(
        context,
        f"📅 Ежедневная статистика:\n"
        f"🛒 Покупок сегодня: {daily_stats['purchases']}\n"
        f"🎨 Генераций сегодня: {daily_stats['generations']}",
    )
    daily_stats["purchases"] = 0
    daily_stats["generations"] = 0


# === MAIN ===
def main():
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("balance", balance))
    application.add_handler(CommandHandler("generate", generate))
    application.add_handler(CommandHandler("buy", buy))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CallbackQueryHandler(buy_button, pattern="^buy_"))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    application.add_handler(PreCheckoutQueryHandler(precheckout_callback))

    if application.job_queue:
        application.job_queue.run_daily(
            send_daily_stats, time=datetime.strptime("23:59", "%H:%M").time()
        )
    else:
        logger.warning("⚠️ JobQueue недоступен!")

    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=WEBHOOK_PATH,
        webhook_url=f"{RENDER_URL}/{WEBHOOK_PATH}",
    )


if __name__ == "__main__":
    main()
