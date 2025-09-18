import os
import logging
import requests
from datetime import datetime
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
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
)
import replicate

# === ЛОГИ ===
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === Конфиг ===
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
REPLICATE_API_KEY = os.getenv("REPLICATE_API_KEY")
PORT = int(os.environ.get("PORT", 5000))
RENDER_URL = os.getenv("RENDER_URL")  # Убедитесь, что это ваш https://... onrender.com
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "webhook")
ADMIN_ID = int(os.getenv("ADMIN_ID")) if os.getenv("ADMIN_ID") else None

FREE_GENERATIONS = 3

if not TELEGRAM_BOT_TOKEN:
    raise ValueError("Не найден TELEGRAM_BOT_TOKEN в окружении")
if not REPLICATE_API_KEY:
    raise ValueError("Не найден REPLICATE_API_KEY в окружении")
if not RENDER_URL:
    raise ValueError("Не найден RENDER_URL в окружении. Установите полный публичный URL (https://...)")

if ADMIN_ID is None:
    logger.warning("ADMIN_ID не задан — уведомления админу работать не будут.")

# Клиент Replicate (Nano Banana через replicate)
client = replicate.Client(api_token=REPLICATE_API_KEY)

# Простое хранилище в оперативке (можно заменить на sqlite позже)
user_generations = {}   # user_id -> remaining generations
user_purchases = {}     # user_id -> total purchased
daily_stats = {"purchases": 0, "generations": 0}

# === Вспомогательные ===
def get_replicate_balance():
    try:
        url = "https://api.replicate.com/v1/account"
        headers = {"Authorization": f"Token {REPLICATE_API_KEY}"}
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            return data.get("credits", {}).get("usd_cents", 0) / 100
        return None
    except Exception as e:
        logger.error(f"Ошибка получения баланса Replicate: {e}")
        return None

async def notify_admin(context: ContextTypes.DEFAULT_TYPE, message: str):
    if ADMIN_ID:
        balance = get_replicate_balance()
        balance_text = f"\n💰 Баланс Replicate: {balance:.2f}$" if balance is not None else ""
        try:
            await context.bot.send_message(chat_id=ADMIN_ID, text=message + balance_text)
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление админу: {e}")

# === Команды и хэндлеры ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # инициализация лимита
    if user_id not in user_generations:
        user_generations[user_id] = FREE_GENERATIONS

    kb = [
        [InlineKeyboardButton("Создать изображение", callback_data="menu_generate")],
        [InlineKeyboardButton("Баланс", callback_data="menu_balance"),
         InlineKeyboardButton("Купить генерации", callback_data="menu_buy")],
    ]
    text = (
        "👋 Привет! Я бот для генерации и редактирования изображений с помощью нейросети "
        "**Nano Banana (Google Gemini 2.5 Flash)** — одна из самых мощных моделей для генерации изображений.\n\n"
        f"✨ У тебя есть {user_generations.get(user_id, 0)} бесплатных генераций.\n\n"
        "Выбери действие:"
    )
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    count = user_generations.get(user_id, FREE_GENERATIONS)
    await update.message.reply_text(f"📊 У тебя осталось {count} генераций.")

async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "menu_generate":
        kb = [[InlineKeyboardButton("Отправить текст", callback_data="generate_text")],
              [InlineKeyboardButton("Загрузить фото (до 4)", callback_data="generate_photos")]]
        await query.message.reply_text("Как хочешь начать генерацию?", reply_markup=InlineKeyboardMarkup(kb))
        return
    if data == "menu_balance":
        await balance(update, context)
        return
    if data == "menu_buy":
        await buy(update, context)
        return

    # кнопки покупки пакетов (callback buy_...)
    if data.startswith("buy_"):
        await buy_button(update, context)
        return

async def generate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /generate <prompt>
    user_id = update.effective_user.id
    if user_id not in user_generations:
        user_generations[user_id] = FREE_GENERATIONS
    if user_generations[user_id] <= 0:
        await update.message.reply_text("❌ У тебя закончились генерации. Используй покупку через /buy или кнопку.")
        return

    prompt = " ".join(context.args) if context.args else None
    if not prompt:
        await update.message.reply_text("Напиши описание после команды: /generate <текст_описания>")
        return

    await update.message.reply_text("⏳ Генерирую изображение...")
    try:
        output = client.run(
            "google/nano-banana:latest",  # модель Nano Banana на Replicate — замените тег, если нужен конкретный version id
            input={"prompt": prompt},
        )
        if isinstance(output, list):
            for url in output:
                await update.message.reply_photo(photo=url)
        elif isinstance(output, str):
            await update.message.reply_photo(photo=output)
        else:
            await update.message.reply_text(f"Неожиданный ответ от модели: {output}")

        # уменьшаем лимит
        user_generations[user_id] = max(0, user_generations.get(user_id, FREE_GENERATIONS) - 1)
        daily_stats["generations"] += 1

        # уведомляем админа
        username = update.effective_user.username or "Без ника"
        await notify_admin(
            context,
            f"👤 Пользователь @{username} (ID: {user_id})\n📝 Промпт: {prompt}\n🎯 Осталось генераций: {user_generations[user_id]}",
        )

        await update.message.reply_text("Напишите в чат, если нужно изменить что-то ещё.")
    except Exception as e:
        logger.exception("Ошибка генерации (текст):")
        await update.message.reply_text("❌ Ошибка генерации. Попробуй позже.")
        username = update.effective_user.username or "Без ника"
        await notify_admin(context, f"❌ Ошибка у @{username} (ID: {user_id}): {e}")

# === Покупки ===
async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("10 генераций — 20⭐", callback_data="buy_10")],
        [InlineKeyboardButton("50 генераций — 100⭐", callback_data="buy_50")],
        [InlineKeyboardButton("100 генераций — 200⭐", callback_data="buy_100")],
    ]
    if update.callback_query:
        await update.callback_query.message.reply_text("Выбери пакет:", reply_markup=InlineKeyboardMarkup(kb))
    else:
        await update.message.reply_text("Выбери пакет:", reply_markup=InlineKeyboardMarkup(kb))

async def buy_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        choice = query.data
    else:
        choice = context.args[0] if context.args else None

    if choice == "buy_10":
        amount = 10
        price_stars = 20
    elif choice == "buy_50":
        amount = 50
        price_stars = 100
    else:
        amount = 100
        price_stars = 200

    # Здесь мы делаем заглушку — реальная интеграция Telegram Stars требует provider_token и настройки.
    # Для совместимости с Telegram Invoices нужно заполнить provider_token в переменных окружения.
    provider_token = os.getenv("PROVIDER_TOKEN", "")
    if not provider_token:
        # уведомление пользователю: оплата не настроена
        if query:
            await query.message.reply_text("⚠️ Оплата не настроена. Свяжись с администратором.")
        else:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="⚠️ Оплата не настроена.")
        return

    prices = [LabeledPrice(f"{amount} генераций", int(price_stars))]  # пример — единица измерения зависит от провайдера
    await context.bot.send_invoice(
        chat_id=query.message.chat_id if query else update.effective_chat.id,
        title=f"Покупка {amount} генераций",
        description=f"Пакет {amount} генераций за {price_stars}⭐",
        payload=f"buy_{amount}",
        provider_token=provider_token,
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

        await update.message.reply_text(f"✅ Успешная покупка! Добавлено {amount} генераций.")
        await notify_admin(
            context,
            f"💸 Покупка у @{username} (ID: {user_id}): +{amount} генераций. Теперь у него: {user_generations[user_id]}",
        )

# === Статистика (админ) ===
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # только админ
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Команда доступна только администратору.")
        return
    total_purchases = sum(user_purchases.values())
    total_generations = sum(user_generations.values())
    await update.message.reply_text(
        f"📊 Статистика:\nПокупок (всего): {total_purchases}\nГенераций (всего остаток): {total_generations}"
    )

# === MAIN ===
def main():
    # Собираем webhook URL
    webhook_url = f"{RENDER_URL.rstrip('/')}/{WEBHOOK_PATH.lstrip('/')}"

    # Пробуем зарегистрировать webhook в Telegram. Если не удаётся — останавливаем запуск и логируем ошибку.
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook",
            json={"url": webhook_url},
            timeout=15,
        )
        logger.info("Ответ setWebhook: %s %s", r.status_code, r.text)
        if r.status_code != 200 or not r.json().get("ok"):
            logger.error("Не удалось установить webhook в Telegram. Проверьте RENDER_URL/WEBHOOK_PATH.")
            raise SystemExit("setWebhook failed: " + r.text)
    except Exception as e:
        logger.exception("Ошибка при попытке зарегистрировать webhook:")
        raise SystemExit(f"Не могу зарегистрировать webhook: {e}")

    # Создаём приложение PTB и регистрируем хэндлеры
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("balance", balance))
    application.add_handler(CommandHandler("generate", generate_command))
    application.add_handler(CommandHandler("buy", buy))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CallbackQueryHandler(menu_callback))
    application.add_handler(CallbackQueryHandler(buy_button, pattern="^buy_"))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    application.add_handler(PreCheckoutQueryHandler(precheckout_callback))

    logger.info("Запускаю webhook-сервер на %s:%s, url_path=%s", "0.0.0.0", PORT, WEBHOOK_PATH)
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=WEBHOOK_PATH,
        webhook_url=webhook_url,
    )

if __name__ == "__main__":
    logger.info("🚀 Бот запускается...")
    main()

if __name__ == "__main__":
    main()


