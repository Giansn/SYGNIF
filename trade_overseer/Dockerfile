FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY trade_overseer/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY trade_overseer /app/trade_overseer

WORKDIR /app/trade_overseer

EXPOSE 8090

CMD ["python", "overseer.py"]
