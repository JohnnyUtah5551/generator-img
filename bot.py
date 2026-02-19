import os
import sys
import time
import logging
import sqlite3
import traceback
import gc
import platform
from datetime import datetime
from pathlib import Path
from collections import defaultdict
import asyncio
from functools import wraps

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
from aiohttp import web
import psutil

# ==================== ЗАЩИТА ОТ ЦИКЛИЧЕСКИХ ПЕРЕЗАПУСКОВ ====================
CRASH_FILE = "/tmp/bot_crash_counter.txt"
MAX_CRASHES = 3
TIME_WINDOW = 300  # 5 минут

def check_crash_loop():
    """Проверка на циклические перезапуски"""
    try:
        current_time = time.time()
        crashes = []
        
        # Читаем историю крашей
        if os.path.exists(CRASH_FILE):
            with open(CRASH_FILE, 'r') as f:
                crashes = [float(line.strip()) for line in f.readlines() if line.strip()]
        
        # Оставляем только краши за последние TIME_WINDOW секунд
        crashes = [t for t in crashes if current_time - t < TIME_WINDOW]
        
        # Добавляем текущий краш
        crashes.append(current_time)
        
        # Записываем обновленную историю
        with open(CRASH_FILE, 'w') as f:
            for t in crashes[-MAX_CRASHES:]:
                f.write(f"{t}\n")
        
        # Проверяем количество крашей
        if len(crashes) > MAX_CRASHES:
            print(f"⚠️ Слишком много перезапусков ({len(crashes)}) за {TIME_WINDOW}с. Пауза 60с...")
            time.sleep(60)  # Пауза перед следующим запуском
            
    except Exception as e:
        print(f"Ошибка при проверке крашей: {e}")

# Вызываем проверку при старте
check_crash_loop()

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

# Отладочная информация
logger.info(f"Python version: {platform.python_version()}")
logger.info(f"Platform: {platform.platform()}")

# ==================== ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ====================
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
RENDER_URL = os.getenv("RENDER_URL")
REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN")

# Проверка обязательных переменных
if not TOKEN:
    logger.error("❌ TELEGRAM_BOT_TOKEN не установлен!")
    sys.exit(1)
if not RENDER_URL:
    logger.error("❌ RENDER_URL не установлен!")
    sys.exit(1)
if not REPLICATE_API_TOKEN:
    logger.error("❌ REPLICATE_API_TOKEN не установлен!")
    sys.exit(1)

# ==================== ИНИЦИАЛИЗАЦИЯ КЛИЕНТОВ ====================
replicate_client = replicate.Client(api_token=REPLICATE_API_TOKEN)

# ==================== НАСТРОЙКА БАЗЫ ДАННЫХ ====================
DB_FILE = "bot.db"
user_locks = defaultdict(asyncio.Lock)  # Блокировки для пользователей

def init_db():
    """Инициализация базы данных"""
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
    """Получение или создание пользователя с блокировкой"""
    async with user_locks[user_id]:
        try:
            conn = sqlite3.connect(DB_FILE, timeout=20)
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
                logger.info(f"👤 Новый пользователь: {user_id}")
            else:
                balance = row[1]
            
            conn.close()
            return balance
        except Exception as e:
            logger.error(f"❌ Ошибка get_user для {user_id}: {e}")
            return 0

def update_balance(user_id: int, delta: int, tx_type: str, payment_id: str = None):
    """Обновление баланса пользователя с блокировкой"""
    async with user_locks[user_id]:
        try:
            conn = sqlite3.connect(DB_FILE, timeout=20)
            cur = conn.cursor()
            
            # Обновляем баланс
            cur.execute("UPDATE users SET balance = balance + ? WHERE id=?", (delta, user_id))
            
            # Записываем транзакцию
            if payment_id:
                cur.execute(
                    "INSERT INTO transactions (user_id, type, amount, payment_id, created_at) VALUES (?, ?, ?, ?, datetime('now'))",
                    (user_id, tx_type, delta, payment_id)
                )
            else:
                cur.execute(
                    "INSERT INTO transactions (user_id, type, amount, created_at) VALUES (?, ?, ?, datetime('now'))",
                    (user_id, tx_type, delta)
                )
            
            conn.commit()
            conn.close()
            
            logger.info(f"💰 Баланс обновлён: user={user_id}, delta={delta}, type={tx_type}")
            return get_user(user_id)
        except Exception as e:
            logger.error(f"❌ Ошибка update_balance для {user_id}: {e}")
            return None

# ==================== ДЕКОРАТОР ДЛЯ ПОВТОРНЫХ ПОПЫТОК ====================
def retry_async(max_retries=3, delay=1):
    """Декоратор для повторных попыток при ошибках"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_retries - 1:
                        logger.error(f"❌ Все попытки исчерпаны для {func.__name__}: {e}")
                        raise
                    wait = delay * (attempt + 1)
                    logger.warning(f"⚠️ Попытка {attempt + 1} не удалась для {func.__name__}. Повтор через {wait}с")
                    await asyncio.sleep(wait)
            return None
        return wrapper
    return decorator

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
async def check_subscription(user_id, bot):
    """Проверка подписки на канал"""
    try:
        member = await bot.get_chat_member(chat_id="@imaigenpromts", user_id=user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception as e:
        logger.warning(f"⚠️ Ошибка проверки подписки для {user_id}: {e}")
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

# ==================== ГЕНЕРАЦИЯ ИЗОБРАЖЕНИЙ ====================
@retry_async(max_retries=3, delay=2)
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

# ==================== ОБРАБОТЧИКИ КОМАНД ====================
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

async def test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Тестовая команда для проверки работы бота"""
    if update.effective_user.id != ADMIN_ID:
        return
    
    try:
        # Информация о системе
        memory = psutil.virtual_memory()
        
        text = (
            f"✅ **Бот работает нормально!**\n\n"
            f"📊 **Статистика:**\n"
            f"• Время работы: {time.time() - context.application.start_time:.0f}с\n"
            f"• Память: {memory.percent}% ({memory.used/1024/1024:.0f}MB)\n"
            f"• Python: {platform.python_version()}\n"
        )
        
        await update.message.reply_text(text, parse_mode='Markdown')
        logger.info(f"📊 Тест от админа {update.effective_user.id}")
    except Exception as e:
        logger.error(f"❌ Ошибка в test: {e}")
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверка статуса бота"""
    if update.effective_user.id != ADMIN_ID:
        return
    
    try:
        # Статистика из БД
        conn = sqlite3.connect(DB_FILE)
        cur = conn.cursor()
        
        cur.execute("SELECT COUNT(*) FROM users")
        users_count = cur.fetchone()[0]
        
        cur.execute("SELECT COUNT(*) FROM transactions WHERE type='buy'")
        purchases = cur.fetchone()[0]
        
        cur.execute("SELECT SUM(amount) FROM transactions WHERE type='spend'")
        spent = abs(cur.fetchone()[0] or 0)
        
        cur.execute("SELECT start_time FROM bot_stats ORDER BY id DESC LIMIT 1")
        last_start = cur.fetchone()
        
        conn.close()
        
        memory = psutil.virtual_memory()
        
        text = (
            f"📊 **Статус бота:**\n\n"
            f"👥 Пользователей: {users_count}\n"
            f"💰 Покупок: {purchases}\n"
            f"🎨 Генераций: {spent}\n"
            f"⏱ Аптайм: {time.time() - context.application.start_time:.0f}с\n"
            f"💾 RAM: {memory.percent}%\n"
            f"🚀 Последний старт: {last_start[0] if last_start else 'N/A'}"
        )
        
        await update.message.reply_text(text, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"❌ Ошибка в status: {e}")

# ==================== ОБРАБОТЧИКИ КНОПОК ====================
async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на кнопки главного меню"""
    try:
        query = update.callback_query
        await query.answer()
        user_id = query.from_user.id
        
        logger.info(f"🔘 Нажатие кнопки {query.data} от {user_id}")

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
                            "🎁 Чтобы получить *3 бесплатные генерации*, подпишитесь на канал\n"
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

        elif query.data == "balance":
            balance = get_user(user_id)
            await query.message.reply_text(
                f"💰 У вас {balance} генераций.",
                reply_markup=main_menu()
            )

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
        logger.error(f"❌ Ошибка в menu_handler: {e}", exc_info=True)

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

async def buy_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик покупки генераций через Telegram Stars"""
    try:
        query = update.callback_query
        await query.answer()

        packages = {
            "buy_10": {"gens": 10, "stars": 40, "title": "10 генераций"},
            "buy_50": {"gens": 50, "stars": 200, "title": "50 генераций"},
            "buy_100": {"gens": 100, "stars": 400, "title": "100 генераций"},
        }

        if query.data in packages:
            pkg = packages[query.data]
            
            await query.message.reply_invoice(
                title=f"Покупка {pkg['gens']} генераций",
                description=f"✨ Пополнение баланса для генерации изображений\n"
                           f"🎨 {pkg['gens']} генераций нейросетью Nano Banana",
                payload=query.data,
                provider_token="",  # Обязательно для Stars
                currency="XTR",
                prices=[LabeledPrice(
                    label=f"{pkg['gens']} генераций", 
                    amount=pkg['stars']
                )],
                start_parameter=f"create_invoice_stars_{pkg['gens']}",
                need_name=False,
                need_phone_number=False,
                need_email=False,
                need_shipping_address=False,
                is_flexible=False,
                protect_content=False
            )
            logger.info(f"💰 Инвойс отправлен пользователю {query.from_user.id}: {pkg['gens']} ген за {pkg['stars']}⭐")
            
    except Exception as e:
        logger.error(f"❌ Ошибка отправки инвойса: {e}", exc_info=True)
        await query.message.reply_text(
            "❌ Ошибка при создании платежа. Пожалуйста, попробуйте позже.",
            reply_markup=main_menu()
        )

async def pre_checkout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение предварительной проверки платежа"""
    try:
        query: PreCheckoutQuery = update.pre_checkout_query
        logger.info(f"💳 Pre-checkout: {query.invoice_payload} от {query.from_user.id}")
        await query.answer(ok=True)
    except Exception as e:
        logger.error(f"❌ Ошибка в pre_checkout_handler: {e}")

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

        # Начисляем генерации
        update_balance(user_id, gens, "buy", payment_id)

        await update.message.reply_text(
            f"✅ Оплата прошла успешно! На ваш баланс добавлено {gens} генераций.",
            reply_markup=main_menu()
        )
        
    except Exception as e:
        logger.error(f"❌ Ошибка в successful_payment_handler: {e}", exc_info=True)
        await update.message.reply_text(
            "❌ Ошибка при обработке платежа. Обратитесь к администратору."
        )

async def end_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Завершение сессии генерации"""
    try:
        context.user_data["can_generate"] = False
        query = update.callback_query
        await query.answer()
        await query.message.reply_text("Главное меню:", reply_markup=main_menu())
    except Exception as e:
        logger.error(f"❌ Ошибка в end_handler: {e}")

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

        # Валидация промпта
        if len(prompt) > 1000:
            await update.message.reply_text("❌ Слишком длинный запрос (макс. 1000 символов)")
            return

        user = update.effective_user
        username = f"@{user.username}" if user.username else user.full_name
        logger.info(f"🎨 Генерация: {username} (ID {user.id}) → '{prompt[:50]}...'")

        await update.message.reply_text("⏳ Генерация изображения...")

        # Получаем изображение, если есть
        images = []
        if update.message.photo:
            try:
                file = await update.message.photo[-1].get_file()
                images = [file.file_path]
            except Exception as e:
                logger.error(f"❌ Ошибка получения фото: {e}")

        # Генерируем
        result = await generate_image(prompt, images if images else None)

        if not result or (isinstance(result, dict) and "error" in result):
            error_text = result["error"] if isinstance(result, dict) and "error" in result else \
                "⚠️ Генерация временно недоступна. Попробуйте позже."
            await update.message.reply_text(error_text)
        else:
            await update.message.reply_photo(result)
            
            if not is_admin:
                update_balance(user_id, -1, "spend")
                logger.info(f"📉 Списана 1 генерация у {user_id}, остаток: {get_user(user_id)}")

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
        logger.error(f"❌ Ошибка в handle_message: {e}", exc_info=True)
        await update.message.reply_text(
            "❌ Произошла ошибка. Попробуйте позже.",
            reply_markup=main_menu()
        )

# ==================== ОБРАБОТЧИК ОШИБОК ====================
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Глобальный обработчик ошибок"""
    try:
        # Логируем ошибку с полным traceback
        error_msg = f"❌ Ошибка: {context.error}\n"
        error_msg += f"Тип: {type(context.error).__name__}\n"
        error_msg += f"Traceback: {traceback.format_exc()}"
        logger.error(error_msg)
        
        # Отправляем админу
        if ADMIN_ID and update:
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=f"⚠️ Ошибка в боте:\n```\n{str(context.error)[:500]}\n```",
                    parse_mode='Markdown'
                )
            except:
                pass
                
        # Сохраняем в файл для анализа
        with open("/tmp/bot_errors.log", "a") as f:
            f.write(f"{datetime.now()}: {error_msg}\n")
            
        # Принудительная сборка мусора
        gc.collect()
        
    except Exception as e:
        logger.error(f"❌ Критическая ошибка в обработчике ошибок: {e}")

# ==================== HEALTH CHECK ====================
async def health_check(request):
    """Endpoint для проверки здоровья"""
    try:
        # Проверяем БД
        conn = sqlite3.connect(DB_FILE, timeout=5)
        conn.execute("SELECT 1").fetchone()
        conn.close()
        
        # Проверяем память
        memory = psutil.virtual_memory()
        if memory.percent > 90:
            return web.Response(text=f"WARNING: High memory usage ({memory.percent}%)", status=200)
        
        # Проверяем Replicate API
        try:
            replicate_client.run("google/nano-banana", input={"prompt": "test", "num_outputs": 1})
        except:
            pass  # Не критично для health check
            
        return web.Response(text="OK", status=200)
    except Exception as e:
        logger.error(f"❌ Health check failed: {e}")
        return web.Response(text=f"ERROR: {e}", status=500)

async def root_handler(request):
    """Корневой endpoint"""
    return web.Response(text="Bot is running! Use /health for status")

# ==================== KEEP-ALIVE ====================
def start_keep_alive(app):
    """Запуск keep-alive для Render"""
    try:
        scheduler = BackgroundScheduler()
        
        def ping():
            try:
                if RENDER_URL:
                    # Стучимся на health-check endpoint
                    r = requests.get(f"{RENDER_URL}/health", timeout=30)
                    logger.info(f"📡 Keep-alive ping: {r.status_code}")
            except Exception as e:
                logger.warning(f"⚠️ Keep-alive error: {e}")

        scheduler.add_job(ping, "interval", minutes=10)
        scheduler.start()
        logger.info("✅ Keep-alive scheduler запущен")
        
        # Добавляем health check endpoints
        app.web_app.router.add_get('/health', health_check)
        app.web_app.router.add_get('/', root_handler)
        
    except Exception as e:
        logger.error(f"❌ Ошибка запуска keep-alive: {e}")

# ==================== ЗАПУСК ПРИЛОЖЕНИЯ ====================
def main():
    """Главная функция запуска бота"""
    try:
        # Инициализация БД
        init_db()
        
        # Создание приложения
        app = Application.builder().token(TOKEN).build()
        
        # Сохраняем время старта
        app.start_time = time.time()

        # Команды
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("stats", stats))
        app.add_handler(CommandHandler("test", test))
        app.add_handler(CommandHandler("status", status))

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

        # Запуск keep-alive
        start_keep_alive(app)
        
        # Запуск вебхука
        port = int(os.environ.get("PORT", 10000))
        logger.info(f"🚀 Запуск вебхука на порту {port}, URL: {RENDER_URL}")
        
        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=TOKEN,
            webhook_url=f"{RENDER_URL}/{TOKEN}",
            allowed_updates=Update.ALL_TYPES
        )
        
    except Exception as e:
        logger.critical(f"❌ Критическая ошибка при запуске: {e}", exc_info=True)
        # Пишем в файл о краше
        with open("/tmp/bot_crash.txt", "a") as f:
            f.write(f"{time.time()}\n")
        # Ждём перед выходом
        time.sleep(5)
        sys.exit(1)

# Функция stats (была пропущена)
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

if __name__ == "__main__":
    main()
