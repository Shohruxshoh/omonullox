#!/bin/sh
set -e

echo "[STARTUP] PostgreSQL tayyor bo'lguncha kutilmoqda..."

# DATABASE_URL dan host:port ajratib olamiz
DB_HOST=$(echo "$DATABASE_URL" | sed -E 's|.*@([^:/]+).*|\1|')
DB_PORT=$(echo "$DATABASE_URL" | sed -E 's|.*:([0-9]+)/.*|\1|')
DB_PORT=${DB_PORT:-5432}

# PostgreSQL tayyor bo'lguncha kutish (max 60s)
i=0
until pg_isready -h "$DB_HOST" -p "$DB_PORT" -q || [ $i -ge 30 ]; do
  i=$((i+1))
  echo "[STARTUP] PostgreSQL kutilmoqda... ($i/30)"
  sleep 2
done

if ! pg_isready -h "$DB_HOST" -p "$DB_PORT" -q; then
  echo "[ERROR] PostgreSQL $DB_HOST:$DB_PORT ga ulanib bo'lmadi"
  exit 1
fi

echo "[STARTUP] PostgreSQL tayyor. Migratsiyalar ishga tushirilmoqda..."
alembic upgrade head

echo "[STARTUP] Migratsiyalar bajarildi. Server ishga tushirilmoqda..."
exec python api_server.py
