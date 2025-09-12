import os
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import replicate

# Настройка логов
logging.basicConfig(level=logging.INFO)

# Получаем токены
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN")

if not TELEGRAM_TOKEN:
 print("❌ Ошибка: TELEGRAM_TOKEN не найден")
 exit(1)

if not REPLICATE_API_TOKEN:
 print("❌ Ошибка: REPLICATE_API_TOKEN не найден")
 exit(1)

# Устанавливаем токен Replicate
replicate.Client(api_token=REPLICATE_API_TOKEN)

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
 await update.message.reply_text("UsageId: /generate ваш запрос")

# Команда /generate
async def generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
 prompt = " ".join(context.args)
 if not prompt:
 await update.message.reply_text("UsageId: /generate киберпанк-город ночью")
 return

 await update.message.reply_text("🎨 Генерирую изображение... Это займёт 10–20 секунд.")

 try:
 output = replicate.run(
 "stability-ai/sdxl:39ed52f2a78e934b3ba6e2a89f5b1c71116187a96637a03f7799ec3442e5fb9f",
 input={"prompt": prompt}
 )
 await update.message.reply_photo(photo=output0)
 except Exception as e:
 await update.message.reply_text(f"❌ Ошибка: {e}")

# Запуск бота
if name == "main":
 app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

 app.add_handler(CommandHandler("start", start))
 app.add_handler(CommandHandler("generate", generate))

 print("🚀 Бот запускается...")
 app.run_polling()

