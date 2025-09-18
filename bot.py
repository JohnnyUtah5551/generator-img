import os
import logging
import requests
from datetime import datetime
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    InputMediaPhoto,
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
from deep_translator import GoogleTranslator

# === ЛОГИ ===
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === Конфиг ===
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
REPLICATE_API_KEY = os.getenv("REPLICATE_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
PORT = int(os.environ.get("PORT", 5000))
RENDER_URL = os.getenv("RENDER_URL")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "webhook")

FREE_GENERATIONS = 3

client = replicate.Client(api_token=REPLICATE_API_KEY)

# Хранилище
user_generations = {}
user_purchases = {}
daily_stats = {"purchases": 0, "generations": 0}
user_photos = {}  # временное хранение до 4 фото

# === Фильтр мата ===
BAD_WORDS = ["дурак", "идиот", "сука", "блять", "хуй", "fuck", "shit"]

def clean_prompt(prompt: str) -> str:
    text = prompt.lower()
    for word in BAD_WORDS:
        text = text.replace(word, "***")
    return text

# === Переводчик ===
def translate_prompt(prompt: str) -> str:
    try:
        return GoogleTranslator(source="auto", target="en").translate(prompt)
    except Exception as e:
        logger.error(f"Ошибка перевода: {e}")
        return prompt

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
        "Привет! У тебя 3 бесплатные генерации. Можешь купить больше через /buy\n"
        "Ты также можешь загрузить до 4 фото и ввести текст для редактирования.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

# === Обработка фото ===
async def handle_photos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    file_id = update.message.photo[-1].file_id
    file = await context.bot.get_file(file_id)
    url = file.file_path

    if user_id not in user_photos:
        user_photos[user_id] = []
    if len(user_photos[user_id]) < 4:
        user_photos[user_id].append(url)
        await update.message.reply_text(f"📸 Фото загружено ({len(user_photos[user_id])}/4).")
    else:
        await update.message.reply_text("⚠️ Можно загрузить не более 4 фото.")

# === Генерация ===
async def generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "Без ника"

    count = user_generations.get(user_id, FREE_GENERATIONS)
    if count <= 0:
        await update.message.reply_text("❌ У тебя закончились генерации. Используй /buy")
        return

    prompt = " ".join(context.args) if context.args else "A futuristic city with flying cars"
    prompt = clean_prompt(prompt)
    translated_prompt = translate_prompt(prompt)

    await update.message.reply_text("⏳ Генерирую изображение...")

    try:
        inputs = {"prompt": translated_prompt}
        if user_id in user_photos and user_photos[user_id]:
            inputs["image"] = user_photos[user_id]

        output = client.run(
            "google-deepmind/nano-banana:latest",
            input=inputs,
        )

        if isinstance(output, list):
            media = [InputMediaPhoto(url) for url in output]
            await update.message.reply_media_group(media)
        elif isinstance(output, str):
            await update.message.reply_photo(photo=output)

        user_generations[user_id] = count - 1
        daily_stats["generations"] += 1
        user_photos[user_id] = []  # очищаем фото после генерации

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
    application.add_handler(MessageHandler(filters.PHOTO, handle_photos))
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

