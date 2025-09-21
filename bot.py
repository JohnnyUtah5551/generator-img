import os
import logging
import replicate
from deep_translator import GoogleTranslator
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ----------------- ЛОГИ -----------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ----------------- ПЕРЕМЕННЫЕ -----------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
REPLICATE_API_KEY = os.getenv("REPLICATE_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
RENDER_URL = os.getenv("RENDER_URL")

replicate.Client(api_token=REPLICATE_API_KEY)

FREE_GENERATIONS = 3
user_data = {}

# ----------------- КНОПКИ -----------------
def main_menu():
    keyboard = [
        [InlineKeyboardButton("🎨 Сгенерировать", callback_data="generate")],
        [InlineKeyboardButton("💰 Баланс", callback_data="balance")],
        [InlineKeyboardButton("⭐ Купить генерации", callback_data="buy")],
        [InlineKeyboardButton("❓ Помощь", callback_data="help")],
    ]
    return InlineKeyboardMarkup(keyboard)


def buy_menu():
    keyboard = [
        [InlineKeyboardButton("10 генераций — 40⭐", callback_data="buy_10")],
        [InlineKeyboardButton("50 генераций — 200⭐", callback_data="buy_50")],
        [InlineKeyboardButton("100 генераций — 400⭐", callback_data="buy_100")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back")],
    ]
    return InlineKeyboardMarkup(keyboard)


def after_generation_menu():
    keyboard = [
        [
            InlineKeyboardButton("🔁 Повторить", callback_data="repeat"),
            InlineKeyboardButton("✅ Завершить", callback_data="finish"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


# ----------------- ХЕНДЛЕРЫ -----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_data:
        user_data[user_id] = {"balance": FREE_GENERATIONS, "last_images": [], "last_prompt": None}

    text = (
        "👋 Привет! Я бот для генерации и редактирования изображений с помощью "
        "нейросети Nano Banana (Google Gemini 2.5 Flash⚡️) — одной из самых мощных моделей "
        "для генерации изображений.\n\n"
        "✨ У тебя 3 бесплатных генерации.\n\n"
        "Нажмите кнопку «Сгенерировать» и отправьте от 1 до 4 изображений с подписью, "
        "что нужно изменить, или просто напишите текст, чтобы создать новое изображение."
    )

    await update.message.reply_text(text, reply_markup=main_menu())


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    data = query.data

    if data == "generate":
        await query.message.reply_text(
            "Создавайте и редактируйте изображения прямо в чате.\n\n"
            "Для вас работает Google Gemini 2.5 Flash — она же Nano Banana 🍌\n\n"
            "Готовы начать?\n"
            "Отправьте от 1 до 4 изображений, которые вы хотите изменить, "
            "или напишите в чат, что нужно создать"
        )
        await query.message.delete()

    elif data == "balance":
        balance = user_data.get(user_id, {}).get("balance", 0)
        await query.message.reply_text(f"💰 Ваш баланс: {balance} генераций")

    elif data == "buy":
        await query.message.reply_text("Выберите пакет генераций:", reply_markup=buy_menu())

    elif data == "back":
        await query.message.reply_text("Главное меню:", reply_markup=main_menu())

    elif data.startswith("buy_"):
        await query.message.reply_text("⚡ Оплата через Telegram Stars пока в разработке.")

    elif data == "help":
        await query.message.reply_text(
            "❓ Помощь\n\n"
            "— Напишите текст для генерации изображения.\n"
            "— Или отправьте до 4 фото с описанием, чтобы их изменить.\n"
            "— У вас есть 3 бесплатные генерации, далее можно купить ⭐."
        )

    elif data == "repeat":
        last_images = user_data[user_id].get("last_images")
        last_prompt = user_data[user_id].get("last_prompt")
        if last_prompt:
            await generate_image(query, context, last_prompt, last_images)

    elif data == "finish":
        await query.message.reply_text("✅ Сессия завершена.", reply_markup=main_menu())


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    prompt = update.message.text

    await generate_image(update, context, prompt)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    photos = update.message.photo

    if not photos:
        return

    # сохраняем фото
    file_ids = [p.file_id for p in photos[-4:]]
    user_data[user_id]["last_images"] = file_idsimport logging
import os
import replicate
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    LabeledPrice,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode

# Логирование
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Токены
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN")
PAYMENT_PROVIDER_TOKEN = os.getenv("PAYMENT_PROVIDER_TOKEN")  # Telegram Stars

# Балансы пользователей (id -> генерации)
user_balances = {}

# Храним состояние: фото + описание для генерации
user_sessions = {}

# --- Главное меню ---
def main_menu():
    keyboard = [
        [InlineKeyboardButton("🎨 Сгенерировать", callback_data="generate")],
        [InlineKeyboardButton("💰 Баланс", callback_data="balance")],
        [InlineKeyboardButton("⭐ Купить генерации", callback_data="buy")],
        [InlineKeyboardButton("❓ Помощь", callback_data="help")],
    ]
    return InlineKeyboardMarkup(keyboard)


# --- Старт ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_balances:
        user_balances[user_id] = 3

    text = (
        "👋 Привет! Я бот для генерации и редактирования изображений с помощью нейросети "
        "*Nano Banana (Google Gemini 2.5 Flash ⚡)* — одной из самых мощных моделей.\n\n"
        "✨ У тебя 3 бесплатных генерации.\n\n"
        "Нажми кнопку «Сгенерировать» и отправь от 1 до 4 изображений с подписью, "
        "что нужно изменить, или просто напиши текст, чтобы создать новое изображение."
    )

    await update.message.reply_text(text, reply_markup=main_menu(), parse_mode=ParseMode.MARKDOWN)


# --- Помощь ---
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "ℹ️ Сначала нажми кнопку «Сгенерировать».\n\n"
        "Затем:\n"
        "— Отправь от 1 до 4 изображений и добавь описание, что изменить.\n"
        "— Или просто напиши текст, чтобы создать новое изображение.\n\n"
        "После генерации у тебя будет выбор: повторить или завершить.\n\n"
        "💡 У тебя 3 бесплатных генерации, дальше можно пополнить баланс через Telegram Stars."
    )
    await update.callback_query.message.edit_text(text, reply_markup=main_menu())


# --- Баланс ---
async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    balance = user_balances.get(user_id, 0)
    await update.callback_query.message.edit_text(
        f"💰 У тебя осталось {balance} генераций.", reply_markup=main_menu()
    )


# --- Покупка ---
async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("⭐ 10 генераций = 100 Stars", callback_data="buy_10")],
        [InlineKeyboardButton("⭐ 50 генераций = 400 Stars", callback_data="buy_50")],
        [InlineKeyboardButton("⭐ 100 генераций = 700 Stars", callback_data="buy_100")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="main_menu")],
    ]
    await update.callback_query.message.edit_text(
        "Выбери пакет генераций:", reply_markup=InlineKeyboardMarkup(keyboard)
    )


# --- Обработка покупки ---
async def buy_generations(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id

    packages = {
        "buy_10": (10, 100),
        "buy_50": (50, 400),
        "buy_100": (100, 700),
    }

    if query.data in packages:
        gens, price = packages[query.data]

        prices = [LabeledPrice(label=f"{gens} генераций", amount=price)]
        await context.bot.send_invoice(
            chat_id=user_id,
            title=f"Пакет {gens} генераций",
            description=f"Пополнение баланса на {gens} генераций",
            payload=f"buy_{gens}",
            provider_token=PAYMENT_PROVIDER_TOKEN,
            currency="XTR",  # Telegram Stars
            prices=prices,
            start_parameter=f"buy-{gens}",
        )


# --- Предчекаут ---
async def precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    await query.answer(ok=True)


# --- Успешная оплата ---
async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    payload = update.message.successful_payment.invoice_payload

    packages = {
        "buy_10": 10,
        "buy_50": 50,
        "buy_100": 100,
    }

    if payload in packages:
        gens = packages[payload]
        user_balances[user_id] = user_balances.get(user_id, 0) + gens
        await update.message.reply_text(
            f"✅ Успешная оплата! Баланс пополнен на {gens} генераций.\n\n"
            f"💰 Теперь у тебя {user_balances[user_id]} генераций.",
            reply_markup=main_menu(),
        )


# --- Обработка callback ---
async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if query.data == "generate":
        await query.message.edit_text(
            "📩 Пришли от 1 до 4 фото и подпиши, что нужно изменить.\n"
            "Или просто напиши описание — я сгенерирую картинку.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Назад", callback_data="main_menu")]]
            ),
        )
    elif query.data == "balance":
        await balance(update, context)
    elif query.data == "buy":
        await buy(update, context)
    elif query.data in ["buy_10", "buy_50", "buy_100"]:
        await buy_generations(update, context)
    elif query.data == "help":
        await help_command(update, context)
    elif query.data == "main_menu":
        await query.message.edit_text("Главное меню:", reply_markup=main_menu())


# --- Обработка текста и фото ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    message = update.message

    # Проверяем баланс
    if user_balances.get(user_id, 0) <= 0:
        await message.reply_text(
            "❌ У тебя закончились генерации.\nПополнить баланс можно через меню.",
            reply_markup=main_menu(),
        )
        return

    description = message.caption if message.photo else message.text
    photos = message.photo[-4:] if message.photo else []

    if not description:
        await message.reply_text("✍️ Добавь описание к фото.")
        return

    await message.reply_text("⏳ Генерация изображения...")

    try:
        output = replicate.run(
            "google/nano-banana",
            input={"prompt": description},
        )

        if isinstance(output, list):
            image_url = output[0]
        else:
            image_url = output

        user_balances[user_id] -= 1

        keyboard = [
            [
                InlineKeyboardButton("🔄 Повторить", callback_data="generate"),
                InlineKeyboardButton("✅ Завершить", callback_data="main_menu"),
            ]
        ]

        await message.reply_photo(
            photo=image_url,
            caption=(
                f"✨ Вот результат!\n\n"
                f"💰 Осталось генераций: {user_balances[user_id]}"
            ),
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    except Exception as e:
        logger.error(e)
        await message.reply_text("⚠️ Ошибка генерации, попробуй снова.")


# --- Главная функция ---
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_callbacks))
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_message))

    # Оплата
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    app.add_handler(
        MessageHandler(filters.StatusUpdate.PRE_CHECKOUT_QUERY, precheckout)
    )

    app.run_polling()


if __name__ == "__main__":
    main()

