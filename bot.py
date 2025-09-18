import os
import re
import json
import asyncio
import logging
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
RENDER_URL = os.getenv("RENDER_URL")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "webhook")
PROVIDER_TOKEN = os.getenv("PROVIDER_TOKEN")  # токен платежного провайдера Telegram

if not TELEGRAM_BOT_TOKEN:
    raise ValueError("Не найден TELEGRAM_BOT_TOKEN в переменных окружения")
if not REPLICATE_API_KEY:
    raise ValueError("Не найден REPLICATE_API_KEY в переменных окружения")
if not RENDER_URL:
    raise ValueError("Не найден RENDER_URL в переменных окружения")

# ==========================
# Клиент Replicate
# ==========================
client = replicate.Client(api_token=REPLICATE_API_KEY)

# ==========================
# Хранилище usage
# ==========================
USAGE_FILE = "usage.json"
FREE_LIMIT = 3

def load_usage():
    if os.path.exists(USAGE_FILE):
        try:
            with open(USAGE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_usage(data):
    try:
        with open(USAGE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Ошибка сохранения usage.json: {e}")

user_usage = load_usage()

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
        save_usage(user_usage)

    balance = user_usage.get(user_id, 0)

    keyboard = [[InlineKeyboardButton("Создать изображение", callback_data="generate")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "👋 Привет! Я бот-генератор изображений.\n\n"
        f"У тебя доступно *{balance} генераций* ✨\n"
        "После окончания можно будет покупать генерации за Telegram Stars ⭐️\n\n"
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
    logger.info(f"User {user_id} запросил промпт: {user_prompt}")

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
            "stability-ai/stable-diffusion:ac732df8",
            input={
                "prompt": prompt,
                "num_inference_steps": 30,
                "guidance_scale": 7.5,
                "width": 512,
                "height": 512,
            },
        )

        if isinstance(output, list):
            for url in output:
                await update.message.reply_photo(photo=url)
        elif isinstance(output, str):
            await update.message.reply_photo(photo=output)
        else:
            await update.message.reply_text(f"Неожиданный ответ: {output}")

        # уменьшаем баланс
        user_usage[user_id] = max(0, balance - 1)
        save_usage(user_usage)

        # показываем новый баланс
        new_balance = user_usage[user_id]
        await update.message.reply_text(
            f"✅ Готово! У тебя осталось *{new_balance} генераций*.",
            parse_mode="Markdown",
        )

    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

# ==========================
# Оплата
# ==========================
async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not PROVIDER_TOKEN:
        await update.message.reply_text("⚠️ Оплата не настроена. Свяжитесь с админом.")
        return

    prices = [LabeledPrice("10 генераций", 100 * 10)]
    await update.message.reply_invoice(
        title="Дополнительные генерации",
        description="Пакет из 10 генераций",
        provider_token=PROVIDER_TOKEN,
        currency="XTR",
        prices=prices,
        payload="buy_generations",
    )

async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    await query.answer(ok=True)

async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    user_usage[user_id] = user_usage.get(user_id, 0) + 10
    save_usage(user_usage)
    await update.message.reply_text("✅ Оплата прошла успешно! Добавлено 10 генераций.")

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
async def main():
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("balance", balance))
    application.add_handler(CommandHandler("generate", generate))
    application.add_handler(CommandHandler("buy", buy))
    application.add_handler(CallbackQueryHandler(button))
    application.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))

    await application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=WEBHOOK_PATH,
        webhook_url=f"{RENDER_URL}/{WEBHOOK_PATH}",
    )

if __name__ == "__main__":
    asyncio.run(main())
