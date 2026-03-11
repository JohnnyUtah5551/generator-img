import os
import logging
import sqlite3
import time
import signal
import sys
import asyncio
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
import requests
from apscheduler.schedulers.background import BackgroundScheduler
import platform

# ==================== НАСТРОЙКА ЛОГИРОВАНИЯ ====================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== ОБРАБОТЧИКИ СИГНАЛОВ ====================
running = True
start_time = time.time()

def signal_handler(sig, frame):
    """Обработчик сигналов остановки"""
    global running
    logger.info("📴 Получен сигнал остановки, завершаем работу...")
    running = False
    time.sleep(2)
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# ==================== ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ====================
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
RENDER_URL = os.getenv("RENDER_URL")
REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN")

if not TOKEN:
    logger.error("❌ TELEGRAM_BOT_TOKEN не установлен!")
    sys.exit(1)
if not RENDER_URL:
    logger.error("❌ RENDER_URL не установлен!")
    sys.exit(1)
if not REPLICATE_API_TOKEN:
    logger.error("❌ REPLICATE_API_TOKEN не установлен!")
    sys.exit(1)

logger.info(f"🐍 Python version: {platform.python_version()}")
logger.info(f"🚀 Render URL: {RENDER_URL}")

# ==================== КЛИЕНТ REPLICATE ====================
replicate_client = replicate.Client(api_token=REPLICATE_API_TOKEN)

# ==================== БАЗА ДАННЫХ ====================
DB_FILE = "bot.db"

def init_db():
    """Инициализация базы данных"""
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
    return True

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
async def check_subscription(user_id, bot):
    """Проверка подписки на канал"""
    try:
        member = await bot.get_chat_member(chat_id="@imaigenpromts", user_id=user_id)
        return member.status in ("member", "administrator", "creator")
    except:
        return False

def main_menu():
    """Главное меню (кнопки ВНУТРИ сообщений)"""
    keyboard = [
        [InlineKeyboardButton("🎨 Сгенерировать", callback_data="generate")],
        [InlineKeyboardButton("💰 Баланс", callback_data="balance")],
        [InlineKeyboardButton("⭐ Купить генерации", callback_data="buy")],
        [InlineKeyboardButton("ℹ️ Помощь", callback_data="help")],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_restart_count():
    """Получение количества перезапусков"""
    try:
        with open("/tmp/restart_count.txt", "r") as f:
            return int(f.read().strip())
    except:
        return 0

# ==================== ГЕНЕРАЦИЯ ИЗОБРАЖЕНИЙ ====================
async def generate_image(prompt: str, images: list = None):
    """Генерация изображения через Replicate"""
    try:
        input_data = {"prompt": prompt}
        if images:
            input_data["image_input"] = images

        logger.info(f"🎨 Отправка запроса в Replicate: {prompt[:50]}...")
        logger.info(f"📦 Входные данные: {input_data}")
        
        # Добавим замер времени
        start_time = time.time()
        
        output = replicate_client.run(
            "google/nano-banana",
            input=input_data,
        )
        
        elapsed = time.time() - start_time
        logger.info(f"✅ Генерация завершена за {elapsed:.2f}с")
        logger.info(f"📤 Результат: {output}")

        if output:
            if isinstance(output, list) and len(output) > 0:
                return output[0]
            return output
        return None
        
    except Exception as e:
        error_msg = str(e).lower()
        logger.error(f"❌ Ошибка генерации: {error_msg}", exc_info=True)
        
        if "insufficient credit" in error_msg:
            return {"error": "⚠️ Недостаточно средств на аккаунте Replicate."}
        elif "flagged as sensitive" in error_msg:
            return {"error": "🚫 Запрос отклонён цензурой. Измените формулировку."}
        else:
            return {"error": "❌ Ошибка при генерации. Попробуйте позже."}

async def generate_image_with_retry(prompt: str, images: list = None, max_retries: int = 3):
    """Генерация изображения с повторными попытками"""
    for attempt in range(max_retries):
        try:
            logger.info(f"🔄 Попытка {attempt + 1}/{max_retries}")
            result = await generate_image(prompt, images)
            
            if result and not (isinstance(result, dict) and "error" in result):
                return result
            
            # Если это не ошибка 502, возвращаем сразу
            if isinstance(result, dict) and "error" in result:
                if "502" not in result["error"].lower() and "bad gateway" not in result["error"].lower():
                    return result
                    
        except Exception as e:
            logger.error(f"❌ Попытка {attempt + 1}/{max_retries} не удалась: {e}")
        
        if attempt < max_retries - 1:
            wait_time = 2 ** attempt  # 1, 2, 4 секунды
            logger.info(f"⏳ Повторная попытка через {wait_time}с...")
            await asyncio.sleep(wait_time)
    
    return {"error": "❌ Не удалось сгенерировать после нескольких попыток. Сервис временно недоступен."}

# ==================== КОМАНДЫ ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    try:
        user_id = update.effective_user.id
        get_user(user_id)

        text = (
            "👋 Привет! Я бот для генерации изображений с помощью нейросети Nano Banana.\n\n"
            "✨ У тебя 3 бесплатные генерации.\n\n"
            "Нажмите кнопку «Сгенерировать» и отправьте текст или фото с описанием."
        )

        await update.message.reply_text(text, reply_markup=main_menu())
        logger.info(f"✅ /start от пользователя {user_id}")
    except Exception as e:
        logger.error(f"❌ Ошибка в start: {e}")

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

        uptime = time.time() - start_time

        text = (
            f"📊 **Статистика:**\n\n"
            f"👥 Пользователей: {users_count}\n"
            f"💰 Суммарный баланс: {total_balance}\n"
            f"⭐ Куплено генераций: {total_bought}\n"
            f"🛒 Покупок: {purchases_count}\n"
            f"🎨 Израсходовано: {total_spent}\n\n"
            f"⚙️ **Система:**\n"
            f"⏱ Uptime: {uptime/3600:.1f} ч\n"
            f"🔄 Перезапусков: {get_restart_count()}"
        )
        await update.message.reply_text(text, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"❌ Ошибка в stats: {e}")

async def diagnose(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Диагностика для админа"""
    if update.effective_user.id != ADMIN_ID:
        return
    
    try:
        uptime = time.time() - start_time
        restart_count = get_restart_count()
        
        text = (
            f"🔍 **Диагностика:**\n\n"
            f"⏱ Uptime: {uptime:.0f} сек ({uptime/3600:.1f} ч)\n"
            f"🔄 Перезапусков: {restart_count}\n"
            f"🐍 Python: {platform.python_version()}\n"
            f"📦 Render: {RENDER_URL}\n"
            f"🆔 Admin: {ADMIN_ID}\n"
            f"✅ Running: {running}"
        )
        
        await update.message.reply_text(text, parse_mode='Markdown')
        
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Тестовая команда для админа"""
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text("✅ Бот работает!")

async def check_replicate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверка статуса Replicate"""
    if update.effective_user.id != ADMIN_ID:
        return
    
    try:
        # Проверяем доступность API
        start = time.time()
        replicate_client.models.get("google/nano-banana")
        latency = time.time() - start
        
        # Проверяем баланс аккаунта
        account_info = "Информация о балансе недоступна через API"
        
        await update.message.reply_text(
            f"✅ **Replicate API статус:**\n\n"
            f"📊 Модель google/nano-banana доступна\n"
            f"⏱ Задержка: {latency:.2f}с\n"
            f"🔑 Токен: {'✅ установлен' if REPLICATE_API_TOKEN else '❌ не установлен'}\n"
            f"🔗 API URL: https://api.replicate.com\n\n"
            f"{account_info}",
            parse_mode='Markdown'
        )
    except Exception as e:
        await update.message.reply_text(
            f"❌ **Ошибка Replicate:**\n```\n{str(e)[:200]}\n```",
            parse_mode='Markdown'
        )

# ==================== ОБРАБОТЧИКИ КНОПОК ====================
async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на кнопки главного меню"""
    try:
        query = update.callback_query
        
        # Пытаемся ответить на callback, игнорируем ошибки
        try:
            await query.answer()
        except:
            pass
        
        user_id = query.from_user.id
        logger.info(f"🔘 Нажатие кнопки {query.data} от {user_id}")

        if query.data == "generate":
            balance = get_user(user_id)

            # Админ всегда может генерировать
            if user_id != ADMIN_ID and balance > 0:
                subscribed = await check_subscription(user_id, context.bot)
                if not subscribed and not context.user_data.get("subscribed_once"):
                    keyboard = [[InlineKeyboardButton("Я подписался ✅", callback_data="confirm_sub")]]
                    await query.message.reply_text(
                        "🎁 Чтобы получить 3 бесплатные генерации, подпишитесь на канал @imaigenpromts",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                    return

            context.user_data["can_generate"] = True
            await query.message.reply_text("Отправьте текст или фото с описанием.")
            
            try:
                await query.message.delete()
            except:
                pass

        elif query.data == "balance":
            balance = get_user(user_id)
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
                "ℹ️ **Помощь:**\n\n"
                "1. Нажмите «Сгенерировать»\n"
                "2. Отправьте текст или фото с описанием\n"
                "3. Получите изображение\n\n"
                "💰 Покупка генераций через Telegram Stars"
            )
            await query.message.reply_text(help_text, parse_mode='Markdown', reply_markup=main_menu())
            
    except Exception as e:
        logger.error(f"❌ Ошибка в menu_handler: {e}")

async def confirm_sub_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение подписки на канал"""
    try:
        query = update.callback_query
        try:
            await query.answer()
        except:
            pass

        user_id = query.from_user.id
        subscribed = await check_subscription(user_id, context.bot)

        if subscribed:
            context.user_data["subscribed_once"] = True
            await query.message.edit_text("🎉 Подписка подтверждена!", reply_markup=main_menu())
        else:
            await query.message.reply_text("❌ Вы ещё не подписались!")
    except Exception as e:
        logger.error(f"❌ Ошибка подтверждения: {e}")

async def buy_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик покупки генераций через Telegram Stars"""
    try:
        query = update.callback_query
        try:
            await query.answer()
        except:
            pass

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
                prices=[LabeledPrice(label=f"{pkg['gens']} генераций", amount=pkg['stars'])],
                start_parameter=f"stars-payment-{pkg['gens']}"
            )
            logger.info(f"💰 Инвойс отправлен пользователю {query.from_user.id}: {pkg['gens']} ген за {pkg['stars']}⭐")
            
    except Exception as e:
        logger.error(f"❌ Ошибка отправки инвойса: {e}")

# ==================== ПЛАТЕЖИ ====================
async def pre_checkout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение предварительной проверки платежа"""
    query = update.pre_checkout_query
    await query.answer(ok=True)

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

# ==================== ОБРАБОТЧИК СООБЩЕНИЙ ====================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстовых сообщений и фото"""
    try:
        if not context.user_data.get("can_generate"):
            await update.message.reply_text("Главное меню:", reply_markup=main_menu())
            return

        user_id = update.effective_user.id
        balance = get_user(user_id)
        is_admin = user_id == ADMIN_ID

        if not is_admin and balance <= 0:
            await update.message.reply_text("⚠️ У вас закончились генерации!", reply_markup=main_menu())
            return

        prompt = update.message.caption or update.message.text
        if not prompt:
            await update.message.reply_text("📝 Пожалуйста, добавьте описание для генерации.")
            return

        await update.message.reply_text("⏳ Генерация изображения...")

        # Получаем фото, если есть
        images = []
        if update.message.photo:
            try:
                file = await update.message.photo[-1].get_file()
                images = [file.file_path]
            except Exception as e:
                logger.error(f"Ошибка получения фото: {e}")

        # Генерируем с повторными попытками
        result = await generate_image_with_retry(prompt, images if images else None)

        if isinstance(result, dict) and "error" in result:
            await update.message.reply_text(result["error"])
            context.user_data["can_generate"] = False
            return

        if not result:
            await update.message.reply_text("❌ Генерация не дала результата.")
            context.user_data["can_generate"] = False
            return

        # Отправляем результат
        try:
            await update.message.reply_photo(result)
        except Exception as photo_error:
            logger.error(f"Ошибка отправки фото: {photo_error}")
            await update.message.reply_text("❌ Ошибка при отправке изображения.")
            return
        
        # Списание (только для не-админов)
        if not is_admin:
            update_balance(user_id, -1, "spend")
            logger.info(f"📉 Списана 1 генерация у {user_id}")

        context.user_data["can_generate"] = False
        await update.message.reply_text("✅ Готово! Нажмите «Сгенерировать» для нового запроса.", reply_markup=main_menu())
            
    except Exception as e:
        logger.error(f"❌ Ошибка в handle_message: {e}")

# ==================== ОБРАБОТЧИК ОШИБОК ====================
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

# ==================== KEEP-ALIVE ====================
def start_keep_alive():
    """Запуск keep-alive для Render"""
    scheduler = BackgroundScheduler()
    
    def ping():
        try:
            if RENDER_URL and running:
                requests.get(f"{RENDER_URL}/", timeout=10)
        except:
            pass

    scheduler.add_job(ping, "interval", minutes=5)
    scheduler.start()
    logger.info("✅ Keep-alive запущен")

# ==================== ЗАПУСК ====================
def main():
    """Главная функция запуска"""
    global start_time
    start_time = time.time()
    
    # Инициализация БД
    init_db()
    
    # Проверка API ключа Replicate
    try:
        # Простой тестовый запрос для проверки ключа
        replicate_client.models.get("google/nano-banana")
        logger.info("✅ Replicate API ключ работает, модель доступна")
    except Exception as e:
        logger.error(f"❌ Ошибка подключения к Replicate: {e}")
        logger.error("Проверьте REPLICATE_API_TOKEN и доступность модели")
    
    # Создаём событийный цикл и устанавливаем его
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    # Создание приложения
    app = Application.builder().token(TOKEN).build()

    # ===== УБИРАЕМ ТОЛЬКО КНОПКУ МЕНЮ СПРАВА ОТ ПОЛЯ ВВОДА =====
    # Кнопки ВНУТРИ сообщений остаются!
    
    try:
        # Убираем кнопку меню справа от ввода (≡)
        loop.run_until_complete(app.bot.set_chat_menu_button(menu_button=None))
        logger.info("✅ Кнопка меню (≡) справа от ввода убрана")
        
        # НЕ удаляем команды! Они нужны для работы бота
        # loop.run_until_complete(app.bot.set_my_commands([]))  # ЭТО НЕ НУЖНО!
        
    except Exception as e:
        logger.error(f"❌ Ошибка при настройке: {e}")
    
    # НЕ ЗАКРЫВАЕМ ЦИКЛ! Он нужен для работы вебхука
    # loop.close()  # НЕ ЗАКРЫВАЕМ!

    # Команды (только /start для пользователей)
    app.add_handler(CommandHandler("start", start))
    
    # Админские команды
    if ADMIN_ID:
        app.add_handler(CommandHandler("stats", stats))
        app.add_handler(CommandHandler("test", test))
        app.add_handler(CommandHandler("diag", diagnose))
        app.add_handler(CommandHandler("check_replicate", check_replicate))

    # ===== INLINE КНОПКИ В СООБЩЕНИЯХ - ОСТАВЛЯЕМ! =====
    app.add_handler(CallbackQueryHandler(menu_handler, pattern="^(generate|balance|buy|help)$"))
    app.add_handler(CallbackQueryHandler(buy_handler, pattern="^buy_"))
    app.add_handler(CallbackQueryHandler(confirm_sub_handler, pattern="^confirm_sub$"))

    # Платежи
    app.add_handler(PreCheckoutQueryHandler(pre_checkout_handler))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))

    # Сообщения
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_message))

    # Обработчик ошибок
    app.add_error_handler(error_handler)

    # Keep-alive
    start_keep_alive()
    
    # Запуск вебхука - используем наш цикл
    port = int(os.environ.get("PORT", 10000))
    logger.info(f"🚀 Запуск вебхука на порту {port}")
    
    # Запускаем вебхук с нашим циклом
    loop.run_until_complete(
        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=TOKEN,
            webhook_url=f"{RENDER_URL}/{TOKEN}",
            allowed_updates=Update.ALL_TYPES
        )
    )

if __name__ == "__main__":
    main()
