FROM python:3.11.10-slim

WORKDIR /app

COPY..

RUN pip install --no-cache-dir -r requirements.txt

CMD "python", "bot.py"
