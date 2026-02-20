import os
import logging
import sqlite3
import time
import signal
import sys
import gc
import random
import asyncio
from datetime import datetime, timedelta
from collections import defaultdict
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    LabeledPrice,
    PreCheckoutQuery,
    BotCommand,
    BotCommandScopeDefault,
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
from aiohttp import web
import psutil
import platform

# ==================== НАСТРОЙКА ЛОГИРОВАНИЯ ====================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('/tmp/bot.log')
    ]
)
logger = logging.getLogger(__name__)

# ==================== ЗАЩИТА ОТ ФЛУДА ОШИБКАМИ ====================
error_counters = defaultdict(list)
MAX_ERRORS_PER_MINUTE = 5

def check_error_rate(user_id: int) -> bool:
    """Проверка частоты ошибок для пользователя"""
    now = datetime.now()
    # Очищаем старые ошибки (старше 1 минуты)
    error_counters[user_id] = [
        t for t in error_counters[user_id] 
        if now - t < timedelta(minutes=1)
    ]
    
    # Если слишком много ошибок за минуту
    if len(error_counters[user_id]) >= MAX_ERRORS_PER_MINUTE:
        return False
    
    return True

def add_error(user_id: int):
    """Добавляем ошибку в счётчик"""
    error_counters[user_id].append(datetime.now())

# ==================== ОБРАБОТЧИКИ СИГНАЛОВ ====================
running = True
start_time = time.time()

def signal_handler(sig, frame):
    """Обработчик сигналов остановки"""
    global running
    logger.info("📴 Получен сигнал остановки, завершаем работу...")
    running = False
    
    # Сохраняем счётчик перезапусков
    try:
        with open("/tmp/restart_count.txt", "r") as f:
            count = int(f.read().strip())
        with open("/tmp/restart_count.txt", "w") as f:
            f.write(str(count + 1))
    except:
        with open("/tmp/restart_count.txt", "w") as f:
            f.write("1")
    
    time.sleep(2)
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# ==================== ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ====================
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
if not REPLICATE_API_TOKEN:
    logger.error("❌ REPLICATE_API_TOKEN не установлен!")
    sys.exit(1)

logger.info(f"🐍 Python version: {platform.python_version()}")
logger.info(f"🚀 Render URL: {RENDER_URL}")

# ==================== ИНИЦИАЛИЗАЦИЯ КЛИЕНТОВ ====================
# Используем синхронный клиент
replicate_client = replicate.Client(api_token=REPLICATE_API_TOKEN)

# ==================== НАСТРОЙКА БАЗЫ ДАННЫХ ====================
DB_FILE = "bot.db"

def init_db():
    """Создание базы данных"""
    try:
        conn = sqlite3.connect(DB_FILE, timeout=20)
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
        
        # Таблица для статистики запусков
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bot_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                start_time TEXT,
                restart_reason TEXT
            )
        """)
        
        # Записываем время запуска
        cur.execute(
            "INSERT INTO bot_stats (start_time, restart_reason) VALUES (?, ?)",
            (datetime.now().isoformat(), "normal_start")
        )
        
        conn.commit()
        conn.close()
        logger.info("✅ База данных инициализирована")
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации БД: {e}")
        time.sleep(5)
        sys.exit(1)

def get_user(user_id: int):
    """Получение или создание пользователя"""
    try:
        conn = sqlite3.connect(DB_FILE, timeout=20)
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
    except Exception as e:
        logger.error(f"❌ Ошибка get_user для {user_id}: {e}")
        return 0

def update_balance(user_id: int, delta: int, tx_type: str, payment_id: str = None):
    """Обновление баланса пользователя"""
    try:
        conn = sqlite3.connect(DB_FILE, timeout=20)
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
    except Exception as e:
        logger.error(f"❌ Ошибка update_balance для {user_id}: {e}")
        return False

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
async def check_subscription(user_id, bot):
    """Проверка подписки на канал"""
    try:
        member = await bot.get_chat_member(chat_id="@imaigenpromts", user_id=user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception as e:
        logger.warning(f"⚠️ Ошибка проверки подписки: {e}")
        return False

def main_menu():
    """Главное меню бота"""
    keyboard = [
        [InlineKeyboardButton("🎨 Сгенерировать/Generate", callback_data="generate")],
        [InlineKeyboardButton("💰 Баланс/Balance", callback_data="balance")],
        [InlineKeyboardButton("⭐ Купить генерации/Buy generations", callback_data="buy")],
        [InlineKeyboardButton("ℹ️ Помощь/Help", callback_data="help")],
    ]
    return InlineKeyboardMarkup(keyboard)

def check_memory():
    """Проверка использования памяти"""
    try:
        process = psutil.Process()
        memory = process.memory_percent()
        cpu = process.cpu_percent(interval=0.5)
        
        if memory > 80:
            logger.warning(f"⚠️ Высокая память: {memory:.1f}%")
            
            # Принудительная сборка мусора
            if memory > 90:
                gc.collect()
                logger.info("🧹 Сборка мусора выполнена")
        
        return memory
    except Exception as e:
        logger.warning(f"⚠️ Ошибка мониторинга памяти: {e}")
        return 0

# ==================== ГЕНЕРАЦИЯ ИЗОБРАЖЕНИЙ ====================
async def generate_image(prompt: str, images: list = None):
    """Генерация изображения через Replicate (запускаем в потоке)"""
    try:
        input_data = {"prompt": prompt}
        if images:
            input_data["image_input"] = images

        logger.info(f"🎨 Отправка запроса в Replicate: {prompt[:50]}...")
        
        # Запускаем синхронный вызов в отдельном потоке
        loop = asyncio.get_event_loop()
        output = await loop.run_in_executor(
            None,  # используем ThreadPoolExecutor по умолчанию
            lambda: replicate_client.run(
                "google/nano-banana",
                input=input_data,
            )
        )

        if output is None:
            return {
                "error": "❌ Модель не вернула результат. Попробуйте позже.",
                "type": "no_result"
            }
            
        if isinstance(output, list) and len(output) > 0:
            return output[0]
        return output
        
    except Exception as e:
        error_msg = str(e).lower()
        logger.error(f"❌ Ошибка генерации: {error_msg}")
        
        # Разные типы ошибок с понятными сообщениями
        if "insufficient credit" in error_msg:
            return {
                "error": "⚠️ Недостаточно средств на аккаунте Replicate. Свяжитесь с администратором.",
                "type": "credit"
            }
        elif "flagged as sensitive" in error_msg:
            return {
                "error": "🚫 Запрос отклонён цензурой.\n\n"
                        "Пожалуйста, измените формулировку запроса и попробуйте снова.\n"
                        "Избегайте сцен насилия, откровенных изображений и запрещённого контента.",
                "type": "moderation"
            }
        elif "rate limit" in error_msg:
            return {
                "error": "⏳ Слишком много запросов. Подождите минуту и попробуйте снова.",
                "type": "rate_limit"
            }
        elif "timeout" in error_msg or "timed out" in error_msg:
            return {
                "error": "⌛ Генерация заняла слишком много времени. Попробуйте ещё раз.",
                "type": "timeout"
            }
        elif "model not found" in error_msg:
            return {
                "error": "🔧 Модель временно недоступна. Попробуйте позже.",
                "type": "model_error"
            }
        else:
            return {
                "error": "❌ Извините, произошла ошибка при генерации. Попробуйте позже.",
                "type": "unknown",
                "details": str(e)
            }

# ==================== HEALTH CHECK ====================
async def health_check(request):
    """Проверка здоровья бота"""
    try:
        # Проверяем БД
        conn = sqlite3.connect(DB_FILE, timeout=5)
        conn.execute("SELECT 1").fetchone()
        conn.close()
        
        # Проверяем память
        memory = check_memory()
        
        # Проверяем время работы
        uptime = time.time() - start_time
        
        return web.Response(
            text=f"OK. Uptime: {uptime:.0f}s, Memory: {memory:.1f}%",
            status=200
        )
    except Exception as e:
        logger.error(f"❌ Health check failed: {e}")
        return web.Response(text=f"ERROR: {e}", status=500)

async def root_handler(request):
    """Корневой endpoint"""
    return web.Response(text="🤖 Bot is running! Use /health for status")

# ==================== КОМАНДЫ ====================
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
        logger.exception(f"❌ Ошибка в start: {e}")

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

        # Информация о памяти
        memory = check_memory()
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
            f"💾 Память: {memory:.1f}%\n"
            f"🔄 Перезапусков: {get_restart_count()}"
        )
        await update.message.reply_text(text, parse_mode='Markdown')
    except Exception as e:
        logger.exception(f"❌ Ошибка в stats: {e}")

async def diagnose(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Диагностика проблем"""
    if update.effective_user.id != ADMIN_ID:
        return
    
    try:
        memory = check_memory()
        uptime = time.time() - start_time
        restart_count = get_restart_count()
        
        # Информация о системе
        text = (
            f"🔍 **Диагностика:**\n\n"
            f"⏱ Uptime: {uptime:.0f} сек ({uptime/3600:.1f} ч)\n"
            f"💾 Память: {memory:.1f}%\n"
            f"🔄 Перезапусков: {restart_count}\n"
            f"🐍 Python: {platform.python_version()}\n"
            f"📦 Render: {RENDER_URL}\n"
            f"🆔 Admin: {ADMIN_ID}\n"
            f"✅ Running: {running}\n"
            f"📊 Ошибок в минуту: {sum(len(v) for v in error_counters.values())}"
        )
        
        await update.message.reply_text(text, parse_mode='Markdown')
        
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Тестовая команда"""
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text("✅ Бот работает!")

def get_restart_count():
    """Получение количества перезапусков"""
    try:
        with open("/tmp/restart_count.txt", "r") as f:
            return int(f.read().strip())
    except:
        return 0

# ==================== ОБРАБОТЧИКИ КНОПОК ====================
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
            # Пробуем удалить сообщение, игнорируем ошибку
            try:
                await query.message.delete()
            except:
                pass

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
        logger.exception(f"❌ Ошибка в menu_handler: {e}")

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
        logger.exception(f"❌ Ошибка в confirm_sub_handler: {e}")

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
        logger.exception(f"❌ Ошибка отправки инвойса: {e}")
        await query.message.reply_text(
            "❌ Ошибка при создании платежа. Пожалуйста, попробуйте позже.",
            reply_markup=main_menu()
        )

# ==================== ПЛАТЕЖИ ====================
async def pre_checkout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение предварительной проверки платежа"""
    query: PreCheckoutQuery = update.pre_checkout_query
    logger.info(f"💳 Pre-checkout: {query.invoice_payload}")
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
        logger.exception(f"❌ Ошибка в successful_payment_handler: {e}")

# ==================== ОБРАБОТЧИК СООБЩЕНИЙ ====================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстовых сообщений и фото"""
    try:
        user_id = update.effective_user.id
        
        # Проверка на флуд ошибками
        if not check_error_rate(user_id):
            logger.warning(f"⚠️ Слишком много ошибок от пользователя {user_id}, пропускаем")
            await update.message.reply_text(
                "⚠️ Обнаружено слишком много ошибок. Подождите минуту и попробуйте снова."
            )
            return
            
        if not context.user_data.get("can_generate"):
            await update.message.reply_text("Главное меню:", reply_markup=main_menu())
            return

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
                "📝 Пожалуйста, добавьте описание для генерации."
            )
            return

        user = update.effective_user
        username = f"@{user.username}" if user.username else user.full_name
        logger.info(f"🎨 Генерация: {username} (ID {user.id}) → '{prompt[:50]}...'")

        await update.message.reply_text("⏳ Генерация изображения...")

        # Получаем фото, если есть
        images = []
        if update.message.photo:
            try:
                file = await update.message.photo[-1].get_file()
                images = [file.file_path]
            except Exception as e:
                logger.error(f"Ошибка получения фото: {e}")
                await update.message.reply_text("❌ Ошибка при загрузке фото.")
                return

        # Генерируем
        result = await generate_image(prompt, images if images else None)

        if isinstance(result, dict) and "error" in result:
            # Показываем пользователю понятное сообщение об ошибке
            await update.message.reply_text(result["error"])
            add_error(user_id)
            context.user_data["can_generate"] = False
            return

        if not result:
            await update.message.reply_text("❌ Генерация не дала результата. Попробуйте позже.")
            add_error(user_id)
            context.user_data["can_generate"] = False
            return

        # Отправляем результат
        try:
            await update.message.reply_photo(result)
        except Exception as photo_error:
            logger.error(f"Ошибка отправки фото: {photo_error}")
            await update.message.reply_text("❌ Ошибка при отправке изображения.")
            add_error(user_id)
            return
        
        # Списание (только для не-админов)
        if not is_admin:
            update_balance(user_id, -1, "spend")
            logger.info(f"📉 Списана 1 генерация у {user_id}")

        # Простое сообщение без кнопок для продолжения
        await update.message.reply_text(
            "✅ Готово! Чтобы сгенерировать ещё раз, нажмите /start и выберите «Сгенерировать»."
        )
        
        # Сбрасываем флаг генерации
        context.user_data["can_generate"] = False
            
    except Exception as e:
        logger.exception(f"❌ Ошибка в handle_message: {e}")
        add_error(update.effective_user.id if update.effective_user else 0)
        # Отправляем сообщение об ошибке пользователю
        try:
            await update.message.reply_text(
                "❌ Произошла внутренняя ошибка. Пожалуйста, попробуйте позже."
            )
        except:
            pass

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
        logger.exception(f"❌ Необработанная ошибка: {e}")

# ==================== KEEP-ALIVE ====================
def setup_web_app():
    """Создание и настройка aiohttp приложения для health check"""
    web_app = web.Application()
    web_app.router.add_get('/health', health_check)
    web_app.router.add_get('/', root_handler)
    return web_app

def start_keep_alive():
    """Запуск keep-alive с защитой (отдельный планировщик)"""
    try:
        scheduler = BackgroundScheduler()
        
        def ping():
            try:
                if not running:
                    return
                    
                # Проверяем память
                memory = check_memory()
                
                # Стучимся на health-check с увеличенным таймаутом
                if RENDER_URL:
                    base_url = RENDER_URL.rstrip('/')
                    
                    # Пробуем до 3 раз с увеличивающимся таймаутом
                    for attempt in range(3):
                        try:
                            timeout = 15 + (attempt * 5)  # 15, 20, 25 сек
                            r = requests.get(f"{base_url}/health", timeout=timeout)
                            if r.status_code == 200:
                                logger.info(f"📡 Keep-alive OK: {r.status_code}, память: {memory:.1f}%")
                                break
                            else:
                                logger.warning(f"⚠️ Keep-alive статус {r.status_code}, попытка {attempt+1}")
                        except requests.Timeout:
                            if attempt == 2:  # Последняя попытка
                                logger.error(f"❌ Keep-alive timeout после {attempt+1} попыток")
                            else:
                                logger.warning(f"⏱ Keep-alive timeout (попытка {attempt+1}), повтор...")
                        except Exception as e:
                            logger.warning(f"⚠️ Keep-alive error (попытка {attempt+1}): {e}")
                            break  # Другие ошибки не ретраим
                            
            except Exception as e:
                logger.warning(f"⚠️ Keep-alive error: {e}")

        # Добавляем задачу каждые 8 минут (чаще, чем 10)
        scheduler.add_job(ping, "interval", minutes=8, jitter=60)
        scheduler.start()
        
        logger.info("✅ Keep-alive планировщик запущен")
        
    except Exception as e:
        logger.error(f"❌ Ошибка запуска keep-alive планировщика: {e}")

# ==================== ЗАПУСК ПРИЛОЖЕНИЯ ====================
async def main_async():
    """Асинхронная главная функция"""
    global start_time, running
    start_time = time.time()
    
    try:
        # Инициализация БД
        init_db()
        
        # Создание приложения
        app = Application.builder().token(TOKEN).build()

        # Удаляем все команды из меню (убираем кнопку "Меню" снизу)
        try:
            await app.bot.set_my_commands([], scope=BotCommandScopeDefault())
            logger.info("✅ Команды бота очищены (меню снизу убрано)")
        except Exception as e:
            logger.error(f"❌ Ошибка при очистке команд: {e}")

        # Команды
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("stats", stats))
        app.add_handler(CommandHandler("test", test))
        app.add_handler(CommandHandler("diag", diagnose))

        # Меню
        app.add_handler(CallbackQueryHandler(menu_handler, pattern="^(generate|balance|buy|help)$"))
        app.add_handler(CallbackQueryHandler(buy_handler, pattern="^(buy_10|buy_50|buy_100)$"))
        app.add_handler(CallbackQueryHandler(confirm_sub_handler, pattern="^confirm_sub$"))

        # Оплата
        app.add_handler(PreCheckoutQueryHandler(pre_checkout_handler))
        app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))

        # Сообщения
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        app.add_handler(MessageHandler(filters.PHOTO, handle_message))

        # Обработчик ошибок
        app.add_error_handler(error_handler)

        # Создаём отдельное aiohttp приложение для health check
        web_app = setup_web_app()
        
        # Запуск keep-alive планировщика
        start_keep_alive()
        
        # Запуск вебхука с нашим aiohttp приложением
        port = int(os.environ.get("PORT", 10000))
        logger.info(f"🚀 Запуск вебхука на порту {port}")
        
        await app.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=TOKEN,
            webhook_url=f"{RENDER_URL}/{TOKEN}",
            allowed_updates=Update.ALL_TYPES,
            web_app=web_app  # передаём наше приложение
        )
        
    except Exception as e:
        logger.critical(f"❌ Критическая ошибка: {e}", exc_info=True)
        running = False
        time.sleep(5)
        sys.exit(1)

def main():
    """Точка входа"""
    asyncio.run(main_async())

if __name__ == "__main__":
    main()
