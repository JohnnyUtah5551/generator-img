import os
import logging
import sqlite3
from datetime import datetime
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    LabeledPrice,
    PreCheckoutQuery,
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

# Логирование
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Переменные окружения
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
RENDER_URL = os.getenv("RENDER_URL")
REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN")

# Replicate клиент
replicate_client = replicate.Client(api_token=REPLICATE_API_TOKEN)

# Настройка базы данных
DB_FILE = "bot.db"


def init_db():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            balance INTEGER DEFAULT 3,
            created_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            type TEXT,
            amount INTEGER,
            payment_id TEXT UNIQUE,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()


def get_user(user_id: int):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT id, balance FROM users WHERE id=?", (user_id,))
    row = cur.fetchone()
    if not row:
        cur.execute(
            "INSERT INTO users (id, balance, created_at) VALUES (?, ?, ?)",
            (user_id, 3, datetime.utcnow().isoformat()),
        )
        conn.commit()
        balance = 3
    else:
        balance = row[1]
    conn.close()
    return balance


def update_balance(user_id: int, delta: int, tx_type: str):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("UPDATE users SET balance = balance + ? WHERE id=?", (delta, user_id))
    cur.execute(
        "INSERT INTO transactions (user_id, type, amount, created_at) VALUES (?, ?, ?, ?)",
        (user_id, tx_type, delta, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()

# Проверка подписки на канал
async def check_subscription(user_id, bot):
    try:
        member = await bot.get_chat_member(chat_id="@imaigenpromts", user_id=user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception as e:
        logger.warning(f"Ошибка проверки подписки: {e}")
        return False

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
async def generate_image(prompt: str, images: list = None):
    try:
        input_data = {"prompt": prompt}
        if images:
            input_data["image_input"] = images  # список URL

        output = replicate_client.run(
            "google/nano-banana",
            input=input_data,
        )

        if output:
            if isinstance(output, list) and len(output) > 0:
                return output[0]
            return output
        return None
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Ошибка генерации: {error_msg}")
        if "insufficient credit" in error_msg.lower():
            return {"error": "Недостаточно генераций. Пополните баланс."}
        elif "flagged as sensitive" in error_msg.lower():
            return {"error": "Запрос отклонён системой модерации. Попробуйте изменить формулировку."}
        else:
            return {"error": "Извините, генерация временно недоступна."}


# Старт
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    get_user(user_id)

    text = (
        "👋 Привет! Я бот для генерации и редактирования изображений с помощью "
        "нейросети Nano Banana (Google Gemini 2.5 Flash ⚡).\n\n"
        "✨ У тебя 3 бесплатные генерации.\n\n"
        "Нажмите кнопку «Сгенерировать» и отправьте одно изображение с подписью, "
        "что нужно изменить, или просто напишите текст, чтобы создать новое изображение."
        " Канал с текстовыми промтами ИИ-фотосессий @imaigenpromts "
    )

    await update.message.reply_text(text, reply_markup=main_menu())


# Обработчик меню
async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # --- Генерация ---
    if query.data == "generate":
        user_id = query.from_user.id
        balance = get_user(user_id)

        # Админ всегда может генерировать
        if user_id != ADMIN_ID:
            if balance > 0:
                subscribed = await check_subscription(user_id, context.bot)
                if not subscribed and not context.user_data.get("subscribed_once"):
                    keyboard = [
                        [InlineKeyboardButton("Я подписался ✅", callback_data="confirm_sub")]
                    ]
                    await query.message.reply_text(
                        "🎁 Чтобы получить *3 бесплатные генерации*, подпишитесь на канал:\n"
                        "👉 @imaigenpromts\n\n"
                        "После подписки нажмите кнопку ниже.",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                    return

        # Разрешаем генерацию
        context.user_data["can_generate"] = True
        await query.message.reply_text(
            "Создавайте изображение!\nОтправьте текст или одно фото с подписью."
        )
        await query.message.delete()
        return  # важно, чтобы не шло дальше к elif

    # --- Баланс ---
    elif query.data == "balance":
        balance = get_user(query.from_user.id)
        await query.message.reply_text(
            f"💰 У вас {balance} генераций.",
            reply_markup=main_menu()
        )

    # --- Покупка ---
    elif query.data == "buy":
        keyboard = [
            [InlineKeyboardButton("10 генераций — 40⭐", callback_data="buy_10")],
            [InlineKeyboardButton("50 генераций — 200⭐", callback_data="buy_50")],
            [InlineKeyboardButton("100 генераций — 400⭐", callback_data="buy_100")],
        ]
        await query.message.reply_text(
            "Выберите пакет:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    # --- Помощь ---
    elif query.data == "help":
        help_text = (
            "ℹ️ Чтобы сгенерировать изображение, сначала нажмите кнопку «Сгенерировать».\n\n"
            "После этого отправьте одно изображение с подписью, что нужно изменить, "
            "или просто текст для новой картинки.\n\n"
            "💰 Для покупок генераций используется Telegram Stars.\n"
            "Канал с текстовыми промтами @imaigenpromts"
        )
        await query.message.reply_text(help_text, reply_markup=main_menu())

# Пользователь нажал "Я подписался"
async def confirm_sub_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    subscribed = await check_subscription(user_id, context.bot)

    if subscribed:
        context.user_data["subscribed_once"] = True
        await query.message.edit_text(
            "🎉 Отлично! Подписка подтверждена.\nТеперь вы можете использовать бесплатные генерации.",
            reply_markup=main_menu()
        )
    else:
        await query.message.reply_text(
            "❌ Вы ещё не подписались!\nПожалуйста, подпишитесь на канал:\n@imaigenpromts"
        )


# Покупки с Telegram Stars
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

        await context.bot.send_invoice(
            chat_id=query.from_user.id,
            title="Покупка генераций",
            description=f"{gens} генераций для нейросети",
            payload=query.data,
            provider_token="",  # для Telegram Stars оставляем пустым!
            currency="XTR",
            prices=[LabeledPrice(label=f"{gens} генераций", amount=stars)],
            start_parameter=f"stars-payment-{gens}",
        )


# Подтверждение покупки
async def pre_checkout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query: PreCheckoutQuery = update.pre_checkout_query
    await query.answer(ok=True)


# Обработка успешной оплаты
async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment = update.message.successful_payment
    user_id = update.effective_user.id
    payload = payment.invoice_payload
    currency = payment.currency
    amount = payment.total_amount
    payment_id = payment.telegram_payment_charge_id  # уникальный ID транзакции

    logger.info(f"✅ Successful payment: user={user_id}, payload={payload}, {amount} {currency}, id={payment_id}")

    gens_map = {
        "buy_10": 10,
        "buy_50": 50,
        "buy_100": 100,
    }
    gens = gens_map.get(payload, 0)
    if gens <= 0:
        await update.message.reply_text("⚠️ Ошибка: неизвестный пакет.")
        return

    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    # Проверяем, не был ли уже обработан этот платеж
    cur.execute("SELECT COUNT(*) FROM transactions WHERE payment_id=?", (payment_id,))
    if cur.fetchone()[0] > 0:
        logger.warning(f"Повторная оплата {payment_id} от user {user_id}, пропускаем дублирование.")
        conn.close()
        return

    # Записываем транзакцию
    cur.execute(
    "INSERT INTO transactions (user_id, type, amount, payment_id, created_at) VALUES (?, ?, ?, ?, datetime('now'))",
    (user_id, "buy", gens, payment_id)
    )
    conn.commit()
    conn.close()

    # Начисляем генерации
    update_balance(user_id, gens, "buy")

    await update.message.reply_text(
        f"✅ Оплата прошла успешно! На ваш баланс добавлено {gens} генераций.",
        reply_markup=main_menu()
    )


# Сообщения с текстом / фото
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("can_generate"):
        return

    user_id = update.effective_user.id
    balance = get_user(user_id)
    is_admin = user_id == ADMIN_ID

    if not is_admin and balance <= 0:
        await update.message.reply_text(
            "⚠️ У вас закончились генерации. Пополните баланс через меню.",
            reply_markup=main_menu()
        )
        return

    prompt = update.message.caption or update.message.text
    if not prompt:
        await update.message.reply_text(
            "Пожалуйста, добавьте описание для генерации."
        )
        return

    user = update.effective_user
    username = f"@{user.username}" if user.username else user.full_name
    logger.info(f"🎨 Генерация: {username} (ID {user.id}) → '{prompt}'")

    await update.message.reply_text("⏳ Генерация изображения...")

    images = []
    if update.message.photo:
        file = await update.message.photo[-1].get_file()
        images = [file.file_path]

    result = await generate_image(prompt, images if images else None)

    # --- Проверяем результат (внутри функции!) ---
    if not result or (isinstance(result, dict) and "error" in result):
        error_text = result["error"] if isinstance(result, dict) and "error" in result else \
            "⚠️ Генерация отклонена системой модерации или временно недоступна.\nПопробуйте изменить описание и отправить снова."
        await update.message.reply_text(error_text)
    else:
        await update.message.reply_photo(result)
        context.user_data["can_generate"] = False
        if not is_admin:
            update_balance(user_id, -1, "spend")

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

# Завершение сессии
async def end_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["can_generate"] = False
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("Главное меню:", reply_markup=main_menu())


# Отчёты для админа
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*), SUM(balance) FROM users")
    users_count, total_balance = cur.fetchone()
    cur.execute("SELECT SUM(amount) FROM transactions WHERE type='buy'")
    total_bought = cur.fetchone()[0] or 0
    cur.execute("SELECT SUM(amount) FROM transactions WHERE type='spend'")
    total_spent = abs(cur.fetchone()[0] or 0)
    conn.close()

    text = (
        f"📊 Статистика:\n\n"
        f"👥 Пользователей: {users_count}\n"
        f"💰 Суммарный баланс: {total_balance}\n"
        f"⭐ Куплено генераций: {total_bought}\n"
        f"🎨 Израсходовано генераций: {total_spent}"
    )
    await update.message.reply_text(text)


from telegram.ext import PreCheckoutQueryHandler
from telegram.error import Forbidden, TimedOut, NetworkError
from apscheduler.schedulers.background import BackgroundScheduler
import requests

# --- Глобальный обработчик ошибок ---
async def error_handler(update, context):
    try:
        raise context.error
    except Forbidden:
        user_id = update.effective_user.id if update and update.effective_user else "неизвестно"
        logger.warning(f"⚠️ Пользователь {user_id} заблокировал бота.")
    except (TimedOut, NetworkError):
        logger.warning("⚠️ Временная сетевая ошибка — бот продолжает работу.")
    except Exception as e:
        logger.error(f"❌ Необработанная ошибка: {e}", exc_info=True)

# --- Keep-alive ---
def start_keep_alive():
    scheduler = BackgroundScheduler()
    
    def ping():
        try:
            if RENDER_URL:
                r = requests.get(RENDER_URL)
                logger.info(f"Keep-alive ping: {r.status_code}")
        except Exception as e:
            logger.warning(f"Keep-alive error: {e}")

    scheduler.add_job(ping, "interval", minutes=10)
    scheduler.start()

# --- Запуск приложения ---
def main():
    init_db()
    app = Application.builder().token(TOKEN).build()

    # Команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))

    # Меню
    app.add_handler(CallbackQueryHandler(menu_handler, pattern="^(generate|balance|buy|help)$"))
    app.add_handler(CallbackQueryHandler(buy_handler, pattern="^(buy_10|buy_50|buy_100)$"))
    app.add_handler(CallbackQueryHandler(end_handler, pattern="^end$"))

    # Оплата
    app.add_handler(PreCheckoutQueryHandler(pre_checkout_handler))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))

    # Сообщения с текстом / фото
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_message))
    app.add_handler(CallbackQueryHandler(confirm_sub_handler, pattern="^confirm_sub$"))

    # Обработчик ошибок
    app.add_error_handler(error_handler)

    # Старт keep-alive
    start_keep_alive()
    
    port = int(os.environ.get("PORT", "10000"))
    app.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=TOKEN,
        webhook_url=f"{RENDER_URL}/{TOKEN}"
    )

if __name__ == "__main__":
    main()










