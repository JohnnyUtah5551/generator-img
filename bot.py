import os
import re
import json
import logging
from datetime import datetime, date
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
    PreCheckoutQueryHandler,
    MessageHandler,
    filters,
    JobQueue,
)
import replicate
from deep_translator import GoogleTranslator

# ==========================
# Логирование
# ==========================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==========================
# Переменные окружения
# ==========================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
REPLICATE_API_KEY = os.getenv("REPLICATE_API_KEY")
PORT = int(os.environ.get("PORT", 5000))

RENDER_URL = os.getenv("RENDER_URL") or os.getenv("RENDER_EXTERNAL_URL")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "webhook")
PROVIDER_TOKEN = os.getenv("PROVIDER_TOKEN")  # токен платежного провайдера Telegram

ADMIN_ID = 641377565  # твой Telegram ID

if not TELEGRAM_BOT_TOKEN:
    raise ValueError("Не найден TELEGRAM_BOT_TOKEN в переменных окружения")
if not REPLICATE_API_KEY:
    raise ValueError("Не найден REPLICATE_API_KEY в переменных окружения")
if not RENDER_URL:
    raise ValueError("Не найден RENDER_URL или RENDER_EXTERNAL_URL в переменных окружения")

# ==========================
# Клиент Replicate
# ==========================
client = replicate.Client(api_token=REPLICATE_API_KEY)

# ==========================
# Хранилище usage
# ==========================
USAGE_FILE = "usage.json"
STATS_FILE = "stats.json"
FREE_LIMIT = 3

def load_json(filename):
    if os.path.exists(filename):
        try:
            with open(filename, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_json(filename, data):
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Ошибка сохранения {filename}: {e}")

user_usage = load_json(USAGE_FILE)
stats = load_json(STATS_FILE)

def increment_stat(key, amount=1):
    today = str(date.today())
    if today not in stats:
        stats[today] = {"generations": 0, "purchases": 0}
    stats[today][key] += amount
    save_json(STATS_FILE, stats)

# ==========================
# Фильтр мата и NSFW
# ==========================
BANNED_WORDS = [
    "хуй", "пизд", "еба", "бляд", "сука", "fuck", "shit", "nigger", "cunt",
    "porn", "sex", "xxx", "nsfw"
]

def contains_profanity(text: str) -> bool:
    text_lower = text.lower()
    for word in BANNED_WORDS:
        if re.search(rf"\b{word}\b", text_lower):
            return True
    return False

def translate_to_english(text: str) -> str:
    try:
        return GoogleTranslator(source="auto", target="en").translate(text)
    except Exception:
        return text

# ==========================
# Команды
# ==========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)

    if user_id not in user_usage:
        user_usage[user_id] = FREE_LIMIT
        save_json(USAGE_FILE, user_usage)

    balance = user_usage.get(user_id, 0)

    keyboard = [[InlineKeyboardButton("Создать изображение", callback_data="generate")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "👋 Привет! Я бот-генератор изображений.\n\n"
        f"У тебя доступно *{balance} генераций* ✨\n"
        "После окончания можно покупать генерации за Telegram Stars ⭐️\n\n"
        "👉 Используй команду `/generate текст`\n"
        "👉 Узнай баланс через `/balance`\n"
        "👉 Можно писать по-русски, я переведу на английский 🌍",
        reply_markup=reply_markup,
        parse_mode="Markdown",
    )

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    balance = user_usage.get(user_id, 0)
    await update.message.reply_text(f"💰 У тебя осталось *{balance} генераций*.", parse_mode="Markdown")

async def generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    username = update.effective_user.username or "без ника"
    balance = user_usage.get(user_id, 0)

    if balance <= 0:
        await update.message.reply_text(
            "🚫 У тебя закончились генерации!\n\n"
            "⭐️ Купи дополнительные генерации с помощью команды `/buy`"
        )
        return

    if not context.args:
        await update.message.reply_text("Использование: `/generate текст_описания`", parse_mode="Markdown")
        return

    user_prompt = " ".join(context.args)
    logger.info(f"User {user_id} (@{username}) запросил промпт: {user_prompt}")

    if contains_profanity(user_prompt):
        await update.message.reply_text("🚫 Запрос содержит запрещённые слова.")
        return

    if len(user_prompt) > 200:
        user_prompt = user_prompt[:200]
        await update.message.reply_text(f"⚠️ Запрос длинный, использую первые 200 символов:\n`{user_prompt}`", parse_mode="Markdown")

    prompt = translate_to_english(user_prompt)

    await update.message.reply_text(f"🌍 Переведённый запрос: `{prompt}`\n\nГенерация...", parse_mode="Markdown")

    try:
        output = client.run(
            "stability-ai/stable-diffusion:d70beb400d223e6432425a5299910329c6050c6abcf97b8c70537d6a1fcb269a",
            input={
                "prompt": prompt,
                "num_inference_steps": 30,
                "guidance_scale": 7.5,
                "width": 512,
                "height": 512,
            },
        )

        new_balance = max(0, balance - 1)
        user_usage[user_id] = new_balance
        save_json(USAGE_FILE, user_usage)
        increment_stat("generations")

        if isinstance(output, list):
            for url in output:
                await update.message.reply_photo(photo=url)
                await context.bot.send_photo(
                    chat_id=ADMIN_ID,
                    photo=url,
                    caption=(
                        f"📸 Новая генерация!\n\n"
                        f"👤 Пользователь: {user_id} (@{username})\n"
                        f"📝 Промпт: {user_prompt}\n"
                        f"🌍 Перевод: {prompt}\n"
                        f"💰 Остаток генераций: {new_balance}"
                    ),
                )
        elif isinstance(output, str):
            await update.message.reply_photo(photo=output)
            await context.bot.send_photo(
                chat_id=ADMIN_ID,
                photo=output,
                caption=(
                    f"📸 Новая генерация!\n\n"
                    f"👤 Пользователь: {user_id} (@{username})\n"
                    f"📝 Промпт: {user_prompt}\n"
                    f"🌍 Перевод: {prompt}\n"
                    f"💰 Остаток генераций: {new_balance}"
                ),
            )
        else:
            await update.message.reply_text(f"Неожиданный ответ: {output}")

        await update.message.reply_text(
            f"✅ Готово! У тебя осталось *{new_balance} генераций*.",
            parse_mode="Markdown",
        )

    except Exception as e:
        error_text = str(e)

        if "insufficient credit" in error_text.lower():
            await update.message.reply_text(
                "⚠️ В данный момент генерация недоступна. "
                "Мы скоро пополним баланс 🚀"
            )
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    f"❌ Закончились кредиты в Replicate!\n\n"
                    f"👤 Пользователь: {user_id} (@{username})\n"
                    f"📝 Промпт: {user_prompt}\n"
                    f"🌍 Перевод: {prompt}\n"
                    f"💳 Ошибка: {error_text}"
                ),
            )
        else:
            await update.message.reply_text("⚠️ Ошибка генерации. Мы скоро исправим.")
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"❌ Ошибка у {user_id} (@{username}): {error_text}"
            )

# ==========================
# Оплата
# ==========================
async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not PROVIDER_TOKEN:
        await update.message.reply_text("⚠️ Оплата не настроена. Свяжитесь с админом.")
        return

    keyboard = [
        [InlineKeyboardButton("⭐️ Купить 10 генераций", callback_data="buy_10")],
        [InlineKeyboardButton("🌟 Купить 100 генераций", callback_data="buy_100")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выбери пакет генераций:", reply_markup=reply_markup)

async def buy_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "buy_10":
        prices = [LabeledPrice("10 генераций", 100 * 10)]
        await query.message.reply_invoice(
            title="Дополнительные генерации",
            description="Пакет из 10 генераций",
            provider_token=PROVIDER_TOKEN,
            currency="XTR",
            prices=prices,
            payload="buy_generations_10",
        )

    elif query.data == "buy_100":
        prices = [LabeledPrice("100 генераций", 100 * 100)]
        await query.message.reply_invoice(
            title="Дополнительные генерации",
            description="Пакет из 100 генераций",
            provider_token=PROVIDER_TOKEN,
            currency="XTR",
            prices=prices,
            payload="buy_generations_100",
        )

async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    await query.answer(ok=True)

async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    payload = update.message.successful_payment.invoice_payload

    if payload == "buy_generations_10":
        added = 10
    elif payload == "buy_generations_100":
        added = 100
    else:
        added = 0

    user_usage[user_id] = user_usage.get(user_id, 0) + added
    save_json(USAGE_FILE, user_usage)
    increment_stat("purchases")
    await update.message.reply_text(f"✅ Оплата прошла успешно! Добавлено {added} генераций.")

# ==========================
# Статистика
# ==========================
async def send_daily_stats(context: ContextTypes.DEFAULT_TYPE):
    today = str(date.today())
    today_stats = stats.get(today, {"generations": 0, "purchases": 0})

    text = (
        f"📊 Статистика за {today}:\n\n"
        f"🖼 Генераций: {today_stats['generations']}\n"
        f"⭐️ Покупок: {today_stats['purchases']}\n"
    )
    await context.bot.send_message(chat_id=ADMIN_ID, text=text)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    today = str(date.today())
    today_stats = stats.get(today, {"generations": 0, "purchases": 0})
    text = (
        f"📊 Статистика за {today}:\n\n"
        f"🖼 Генераций: {today_stats['generations']}\n"
        f"⭐️ Покупок: {today_stats['purchases']}\n"
    )
    await update.message.reply_text(text)

# ==========================
# Inline кнопка
# ==========================
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "generate":
        await query.message.reply_text("Напиши `/generate текст`, чтобы я сгенерировал картинку!")

# ==========================
# Main
# ==========================
def main():
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("balance", balance))
    application.add_handler(CommandHandler("generate", generate))
    application.add_handler(CommandHandler("buy", buy))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CallbackQueryHandler(buy_button, pattern="^buy_"))
    application.add_handler(CallbackQueryHandler(button))
    application.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))

    job_queue: JobQueue = application.job_queue
    job_queue.run_daily(send_daily_stats, time=datetime.strptime("23:59", "%H:%M").time())

    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=WEBHOOK_PATH,
        webhook_url=f"{RENDER_URL}/{WEBHOOK_PATH}",
    )

if __name__ == "__main__":
    logging.info("🚀 Бот запускается...")
    main()
