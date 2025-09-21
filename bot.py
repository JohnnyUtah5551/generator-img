import os
import logging
from uuid import uuid4
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
    ContextTypes,
    filters,
)
import replicate
from datetime import datetime, timedelta
import pytz

# Логирование
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Переменные окружения
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
RENDER_URL = os.getenv("RENDER_URL")
REPLICATE_API_KEY = os.getenv("REPLICATE_API_KEY")

# Replicate клиент
replicate_client = replicate.Client(api_token=REPLICATE_API_KEY)

# Балансы пользователей
user_balances = {}
FREE_GENERATIONS = 3

# Счетчики для статистики
daily_generations = 0
daily_users = set()

# Главное меню
def main_menu():
    keyboard = [
        [InlineKeyboardButton("🎨 Сгенерировать", callback_data="generate")],
        [InlineKeyboardButton("💰 Баланс", callback_data="balance")],
        [InlineKeyboardButton("⭐ Купить генерации", callback_data="buy")],
        [InlineKeyboardButton("ℹ️ Помощь", callback_data="help")],
    ]
    return InlineKeyboardMarkup(keyboard)

# Генерация изображения через Replicate
async def generate_image(prompt: str, images: list[str] = None):
    try:
        input_data = {"prompt": prompt}
        if images:
            input_data["image"] = images
        output = replicate_client.run(
            "google/nano-banana:9f3b10f33c31d7b8f1dc6f93aef7da71bdf2c1c6d53e11b6c0e4eafd7d7b0b3e",
            input=input_data,
        )
        if isinstance(output, list) and len(output) > 0:
            return output[0]
        return None
    except Exception as e:
        logger.error(f"Ошибка генерации: {e}")
        return None

# Старт
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_balances:
        user_balances[user_id] = FREE_GENERATIONS

    text = (
        "👋 Привет! Я бот для генерации и редактирования изображений с помощью "
        "нейросети Nano Banana (Google Gemini 2.5 Flash ⚡) — одной из самых мощных моделей.\n\n"
        f"✨ У тебя {FREE_GENERATIONS} бесплатных генерации.\n\n"
        "Нажмите кнопку «Сгенерировать» и отправьте от 1 до 4 изображений с подписью, "
        "что нужно изменить, или просто напишите текст, чтобы создать новое изображение."
    )

    await update.message.reply_text(text, reply_markup=main_menu())

# Обработчик меню
async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    elif query.data == "balance":
        balance = user_balances.get(query.from_user.id, FREE_GENERATIONS)
        await query.message.reply_text(f"💰 У вас {balance} генераций.", reply_markup=main_menu())

    elif query.data == "buy":
        keyboard = [
            [InlineKeyboardButton("10 генераций — 40⭐", callback_data="buy_10")],
            [InlineKeyboardButton("50 генераций — 200⭐", callback_data="buy_50")],
            [InlineKeyboardButton("100 генераций — 400⭐", callback_data="buy_100")],
        ]
        await query.message.reply_text("Выберите пакет:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data == "help":
        help_text = (
            "ℹ️ Чтобы сгенерировать изображение, сначала нажмите кнопку «Сгенерировать».\n\n"
            "После этого отправьте от 1 до 4 изображений с подписью, что нужно изменить, "
            "или просто текст для новой картинки.\n\n"
            "💰 Для покупок генераций используется Telegram Stars. "
            "Если у вас их не хватает — пополните через Telegram → Кошелек → Пополнить."
        )
        await query.message.reply_text(help_text, reply_markup=main_menu())

# Покупки
async def buy_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    package_map = {
        "buy_10": (10, 40),
        "buy_50": (50, 200),
        "buy_100": (100, 400),
    }

    if query.data in package_map:
        gens, stars = package_map[query.data]

        # Отправляем счёт через Telegram Stars
        await query.message.reply_invoice(
            title="Покупка генераций",
            description=f"{gens} генераций для нейросети",
            payload=f"buy_{gens}",
            provider_token="",  # ❗ Для Stars можно оставить пустым
            currency="XTR",
            prices=[LabeledPrice(label=f"{gens} генераций", amount=stars)],
            start_parameter="stars-payment",
        )

# Обработка успешной оплаты
async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment = update.message.successful_payment
    user_id = update.effective_user.id

    gens_map = {
        "buy_10": 10,
        "buy_50": 50,
        "buy_100": 100,
    }

    gens = gens_map.get(payment.invoice_payload, 0)
    if gens > 0:
        user_balances[user_id] = user_balances.get(user_id, 0) + gens
        await update.message.reply_text(
            f"✅ Оплата прошла успешно! На ваш баланс добавлено {gens} генераций.",
            reply_markup=main_menu()
        )

# Сообщения с текстом / фото
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global daily_generations, daily_users

    user_id = update.effective_user.id
    balance = user_balances.get(user_id, FREE_GENERATIONS)

    if balance <= 0:
        await update.message.reply_text(
            "⚠️ У вас закончились генерации. Пополните баланс через меню.",
            reply_markup=main_menu()
        )
        return

    prompt = update.message.caption or update.message.text
    if not prompt:
        await update.message.reply_text("Пожалуйста, добавьте описание для генерации.")
        return

    await update.message.reply_text("⏳ Генерация изображения...")

    images = []
    if update.message.photo:
        file = await update.message.photo[-1].get_file()
        images.append(file.file_path)

    result = await generate_image(prompt, images if images else None)

    if result:
        await update.message.reply_photo(result)
        user_balances[user_id] -= 1

        # Считаем статистику
        daily_generations += 1
        daily_users.add(user_id)

        # Отчёт админу: промпт + фото
        try:
            await context.bot.send_message(
                ADMIN_ID,
                f"👤 Пользователь: {update.effective_user.username or update.effective_user.id}\n"
                f"🆔 ID: {user_id}\n"
                f"📝 Запрос: {prompt}\n"
                f"📉 Остаток: {user_balances[user_id]}",
            )
            await context.bot.send_photo(ADMIN_ID, result)
        except Exception as e:
            logger.error(f"Ошибка отправки отчета админу: {e}")

        keyboard = [
            [
                InlineKeyboardButton("🔄 Повторить", callback_data="generate"),
                InlineKeyboardButton("✅ Завершить", callback_data="end"),
            ]
        ]
        await update.message.reply_text(
            "Напишите в чат, если нужно изменить что-то ещё.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    else:
        await update.message.reply_text("⚠️ Извините, генерация временно недоступна.")

# Завершение сессии
async def end_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("Главное меню:", reply_markup=main_menu())

# Ежедневный отчет админу
async def daily_report(context: ContextTypes.DEFAULT_TYPE):
    global daily_generations, daily_users
    try:
        await context.bot.send_message(
            ADMIN_ID,
            f"📊 Ежедневный отчет:\n"
            f"🖼 Генераций за сутки: {daily_generations}\n"
            f"👥 Уникальных пользователей: {len(daily_users)}"
        )
    except Exception as e:
        logger.error(f"Ошибка отправки ежедневного отчета: {e}")

    # Сброс статистики
    daily_generations = 0
    daily_users = set()

# Запуск приложения
def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu_handler, pattern="^(generate|balance|buy|help)$"))
    app.add_handler(CallbackQueryHandler(buy_handler, pattern="^(buy_10|buy_50|buy_100)$"))
    app.add_handler(CallbackQueryHandler(end_handler, pattern="^end$"))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_message))

    # Планировщик для отчета
    moscow_tz = pytz.timezone("Europe/Moscow")
    target_time = datetime.now(moscow_tz).replace(hour=12, minute=0, second=0, microsecond=0)
    if target_time < datetime.now(moscow_tz):
        target_time += timedelta(days=1)
    app.job_queue.run_daily(daily_report, time=target_time.timetz(), days=(0, 1, 2, 3, 4, 5, 6), tzinfo=moscow_tz)

    # Webhook для Render
    port = int(os.environ.get("PORT", 5000))
    app.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=TOKEN,
        webhook_url=f"{RENDER_URL}/{TOKEN}"
    )

if __name__ == "__main__":
    main()


