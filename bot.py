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
    PreCheckoutQueryHandler,
)
from telegram.error import Forbidden, TimedOut, NetworkError
import replicate
import sys
import requests
from apscheduler.schedulers.background import BackgroundScheduler

# Логирование
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Отладочная информация
logger.info(f"Python version: {sys.version}")

# Переменные окружения
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
RENDER_URL = os.getenv("RENDER_URL")
REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN")

# Проверка переменных
if not TOKEN:
    logger.error("❌ TELEGRAM_BOT_TOKEN не установлен!")
    sys.exit(1)
if not RENDER_URL:
    logger.error("❌ RENDER_URL не установлен!")
    sys.exit(1)

# Replicate клиент
replicate_client = replicate.Client(api_token=REPLICATE_API_TOKEN)

# Настройка базы данных
DB_FILE = "bot.db"

def init_db():
    """Создание базы данных"""
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    
    # Таблица пользователей
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            balance INTEGER DEFAULT 3,
            created_at TEXT
        )
    """)
    
    # Таблица транзакций
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
    logger.info("✅ База данных инициализирована")

def get_user(user_id: int):
    """Получение или создание пользователя"""
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT id, balance FROM users WHERE id=?", (user_id,))
    row = cur.fetchone()
    
    if not row:
        cur.execute(
            "INSERT INTO users (id, balance, created_at) VALUES (?, ?, ?)",
            (user_id, 3, datetime.now().isoformat()),
        )
        conn.commit()
        balance = 3
        logger.info(f"👤 Новый пользователь: {user_id}")
    else:
        balance = row[1]
    
    conn.close()
    return balance

def update_balance(user_id: int, delta: int, tx_type: str, payment_id: str = None):
    """Обновление баланса пользователя"""
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    
    # Обновляем баланс
    cur.execute("UPDATE users SET balance = balance + ? WHERE id=?", (delta, user_id))
    
    # Записываем транзакцию
    if payment_id:
        cur.execute(
            "INSERT INTO transactions (user_id, type, amount, payment_id, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, tx_type, delta, payment_id, datetime.now().isoformat())
        )
    else:
        cur.execute(
            "INSERT INTO transactions (user_id, type, amount, created_at) VALUES (?, ?, ?, ?)",
            (user_id, tx_type, delta, datetime.now().isoformat())
        )
    
    conn.commit()
    conn.close()
    logger.info(f"💰 Баланс обновлён: user={user_id}, delta={delta}, type={tx_type}")

# Проверка подписки на канал
async def check_subscription(user_id, bot):
    """Проверка подписки на канал"""
    try:
        member = await bot.get_chat_member(chat_id="@imaigenpromts", user_id=user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception as e:
        logger.warning(f"⚠️ Ошибка проверки подписки: {e}")
        return False

# Главное меню
def main_menu():
    """Главное меню бота"""
    keyboard = [
        [InlineKeyboardButton("🎨 Сгенерировать/Generate", callback_data="generate")],
        [InlineKeyboardButton("💰 Баланс/Balance", callback_data="balance")],
        [InlineKeyboardButton("⭐ Купить генерации/Buy generations", callback_data="buy")],
        [InlineKeyboardButton("ℹ️ Помощь/Help", callback_data="help")],
    ]
    return InlineKeyboardMarkup(keyboard)

# Генерация изображения через Replicate
async def generate_image(prompt: str, images: list = None):
    """Генерация изображения через Replicate"""
    try:
        input_data = {"prompt": prompt}
        if images:
            input_data["image_input"] = images

        logger.info(f"🎨 Отправка запроса в Replicate: {prompt[:50]}...")
        
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
        logger.error(f"❌ Ошибка генерации: {error_msg}")
        
        if "insufficient credit" in error_msg.lower():
            return {"error": "Недостаточно генераций. Пополните баланс."}
        elif "flagged as sensitive" in error_msg.lower():
            return {"error": "Запрос отклонён системой модерации. Попробуйте изменить формулировку."}
        else:
            return {"error": "Извините, генерация временно недоступна."}

# Старт
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    try:
        user_id = update.effective_user.id
        get_user(user_id)

        text = (
            "👋 Привет! Я бот для генерации и редактирования изображений с помощью "
            "нейросети Nano Banana (Google Gemini 2.5 Flash ⚡).\n\n"
            "✨ У тебя 3 бесплатные генерации.\n\n"
            "Нажмите кнопку «Сгенерировать» и отправьте одно изображение с подписью, "
            "что нужно изменить, или просто напишите текст, чтобы создать новое изображение.\n"
            "Канал с текстовыми промтами @imaigenpromts"
        )

        await update.message.reply_text(text, reply_markup=main_menu())
        logger.info(f"✅ /start от пользователя {user_id}")
    except Exception as e:
        logger.error(f"❌ Ошибка в start: {e}")

# Обработчик меню
async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на кнопки главного меню"""
    try:
        query = update.callback_query
        await query.answer()
        user_id = query.from_user.id
        
        logger.info(f"🔘 Нажатие кнопки {query.data} от {user_id}")

        # Генерация
        if query.data == "generate":
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
                            "🎁 Чтобы получить 3 бесплатные генерации, подпишитесь на канал\n"
                            "👉 @imaigenpromts\n\n"
                            "После подписки нажмите кнопку ниже.",
                            reply_markup=InlineKeyboardMarkup(keyboard)
                        )
                        return

            context.user_data["can_generate"] = True
            await query.message.reply_text(
                "Создавайте изображение!\nОтправьте текст или одно фото с подписью."
            )
            await query.message.delete()

        # Баланс
        elif query.data == "balance":
            balance = get_user(user_id)
            await query.message.reply_text(
                f"💰 У вас {balance} генераций.",
                reply_markup=main_menu()
            )

        # Покупка
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

        # Помощь
        elif query.data == "help":
            help_text = (
                "ℹ️ **Помощь:**\n\n"
                "1. Нажмите «Сгенерировать»\n"
                "2. Отправьте текст или фото с описанием\n"
                "3. Получите изображение\n\n"
                "💰 Покупка генераций через Telegram Stars\n"
                "📢 Канал @imaigenpromts"
            )
            await query.message.reply_text(help_text, parse_mode='Markdown', reply_markup=main_menu())
            
    except Exception as e:
        logger.error(f"❌ Ошибка в menu_handler: {e}")

# Подтверждение подписки
async def confirm_sub_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение подписки на канал"""
    try:
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
    except Exception as e:
        logger.error(f"❌ Ошибка в confirm_sub_handler: {e}")

# Покупка через Stars
async def buy_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик покупки генераций через Telegram Stars"""
    try:
        query = update.callback_query
        await query.answer()

        packages = {
            "buy_10": {"gens": 10, "stars": 40},
            "buy_50": {"gens": 50, "stars": 200},
            "buy_100": {"gens": 100, "stars": 400},
        }

        if query.data in packages:
            pkg = packages[query.data]
            
            await query.message.reply_invoice(
                title=f"Покупка {pkg['gens']} генераций",
                description=f"Пополнение баланса для генерации изображений",
                payload=query.data,
                provider_token="",
                currency="XTR",
                prices=[LabeledPrice(
                    label=f"{pkg['gens']} генераций", 
                    amount=pkg['stars']
                )],
                start_parameter=f"stars-payment-{pkg['gens']}"
            )
            logger.info(f"💰 Инвойс отправлен пользователю {query.from_user.id}: {pkg['gens']} ген за {pkg['stars']}⭐")
            
    except Exception as e:
        logger.error(f"❌ Ошибка отправки инвойса: {e}")
        await query.message.reply_text(
            "❌ Ошибка при создании платежа. Пожалуйста, попробуйте позже.",
            reply_markup=main_menu()
        )

# Предварительная проверка платежа
async def pre_checkout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение предварительной проверки платежа"""
    query: PreCheckoutQuery = update.pre_checkout_query
    logger.info(f"💳 Pre-checkout: {query.invoice_payload}")
    await query.answer(ok=True)

# Успешная оплата
async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка успешного платежа"""
    try:
        payment = update.message.successful_payment
        user_id = update.effective_user.id
        payload = payment.invoice_payload
        payment_id = payment.telegram_payment_charge_id

        logger.info(f"✅ Успешный платёж: user={user_id}, payload={payload}, id={payment_id}")

        gens_map = {
            "buy_10": 10,
            "buy_50": 50,
            "buy_100": 100,
        }
        gens = gens_map.get(payload, 0)
        
        if gens <= 0:
            await update.message.reply_text("⚠️ Ошибка: неизвестный пакет.")
            return

        # Проверяем, не было ли уже такой оплаты
        conn = sqlite3.connect(DB_FILE)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM transactions WHERE payment_id=?", (payment_id,))
        if cur.fetchone()[0] > 0:
            conn.close()
            logger.warning(f"Повторная оплата {payment_id}")
            await update.message.reply_text("✅ Платёж уже был обработан.")
            return
        conn.close()

        # Начисляем генерации
        update_balance(user_id, gens, "buy", payment_id)

        await update.message.reply_text(
            f"✅ Оплата прошла успешно! На ваш баланс добавлено {gens} генераций.",
            reply_markup=main_menu()
        )
        
    except Exception as e:
        logger.error(f"❌ Ошибка в successful_payment_handler: {e}")

# Обработка сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстовых сообщений и фото"""
    try:
        if not context.user_data.get("can_generate"):
            await update.message.reply_text("Главное меню:", reply_markup=main_menu())
            return

        user_id = update.effective_user.id
        balance = get_user(user_id)
        is_admin = user_id == ADMIN_ID

        # Проверка баланса (админ всегда может)
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
        logger.info(f"🎨 Генерация: {username} (ID {user.id}) → '{prompt[:50]}...'")

        await update.message.reply_text("⏳ Генерация изображения...")

        # Получаем фото, если есть
        images = []
        if update.message.photo:
            file = await update.message.photo[-1].get_file()
            images = [file.file_path]

        # Генерируем
        result = await generate_image(prompt, images if images else None)

        if not result or (isinstance(result, dict) and "error" in result):
            error_text = result["error"] if isinstance(result, dict) and "error" in result else \
                "⚠️ Генерация временно недоступна. Попробуйте позже."
            await update.message.reply_text(error_text)
        else:
            await update.message.reply_photo(result)
            
            # Списание (только для не-админов)
            if not is_admin:
                update_balance(user_id, -1, "spend")
                logger.info(f"📉 Списана 1 генерация у {user_id}")

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
            
    except Exception as e:
        logger.error(f"❌ Ошибка в handle_message: {e}")

# Завершение сессии
async def end_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Завершение сессии генерации"""
    context.user_data["can_generate"] = False
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("Главное меню:", reply_markup=main_menu())

# Статистика для админа
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика для админа"""
    if update.effective_user.id != ADMIN_ID:
        return

    try:
        conn = sqlite3.connect(DB_FILE)
        cur = conn.cursor()
        
        cur.execute("SELECT COUNT(*), SUM(balance) FROM users")
        users_count, total_balance = cur.fetchone()
        total_balance = total_balance or 0
        
        cur.execute("SELECT SUM(amount) FROM transactions WHERE type='buy'")
        total_bought = cur.fetchone()[0] or 0
        
        cur.execute("SELECT SUM(amount) FROM transactions WHERE type='spend'")
        total_spent = abs(cur.fetchone()[0] or 0)
        
        cur.execute("SELECT COUNT(*) FROM transactions WHERE type='buy'")
        purchases_count = cur.fetchone()[0] or 0
        
        conn.close()

        text = (
            f"📊 **Статистика:**\n\n"
            f"👥 Пользователей: {users_count}\n"
            f"💰 Суммарный баланс: {total_balance}\n"
            f"⭐ Куплено генераций: {total_bought}\n"
            f"🛒 Покупок: {purchases_count}\n"
            f"🎨 Израсходовано: {total_spent}"
        )
        await update.message.reply_text(text, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"❌ Ошибка в stats: {e}")

# Тестовая команда
async def test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Тестовая команда для проверки"""
    if update.effective_user.id != ADMIN_ID:
        return
    
    await update.message.reply_text("✅ Бот работает!")

# Обработчик ошибок
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Глобальный обработчик ошибок"""
    try:
        raise context.error
    except Forbidden:
        user_id = update.effective_user.id if update and update.effective_user else "неизвестно"
        logger.warning(f"⚠️ Пользователь {user_id} заблокировал бота.")
    except (TimedOut, NetworkError):
        logger.warning("⚠️ Временная сетевая ошибка")
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}", exc_info=True)

# Keep-alive
def start_keep_alive():
    """Запуск keep-alive"""
    scheduler = BackgroundScheduler()
    
    def ping():
        try:
            if RENDER_URL:
                r = requests.get(RENDER_URL, timeout=30)
                logger.info(f"📡 Keep-alive ping: {r.status_code}")
        except Exception as e:
            logger.warning(f"⚠️ Keep-alive error: {e}")

    scheduler.add_job(ping, "interval", minutes=10)
    scheduler.start()
    logger.info("✅ Keep-alive запущен")

# Запуск приложения
def main():
    """Главная функция"""
    # Инициализация БД
    init_db()
    
    # Создание приложения
    app = Application.builder().token(TOKEN).build()

    # Команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("test", test))

    # Меню
    app.add_handler(CallbackQueryHandler(menu_handler, pattern="^(generate|balance|buy|help)$"))
    app.add_handler(CallbackQueryHandler(buy_handler, pattern="^(buy_10|buy_50|buy_100)$"))
    app.add_handler(CallbackQueryHandler(end_handler, pattern="^end$"))
    app.add_handler(CallbackQueryHandler(confirm_sub_handler, pattern="^confirm_sub$"))

    # Оплата
    app.add_handler(PreCheckoutQueryHandler(pre_checkout_handler))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))

    # Сообщения
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_message))

    # Обработчик ошибок
    app.add_error_handler(error_handler)

    # Keep-alive
    start_keep_alive()
    
    # Запуск вебхука
    port = int(os.environ.get("PORT", 10000))
    logger.info(f"🚀 Запуск вебхука на порту {port}")
    
    app.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=TOKEN,
        webhook_url=f"{RENDER_URL}/{TOKEN}"
    )

if __name__ == "__main__":
    main()
