FROM python:3.11-slim

WORKDIR /app

# System deps: gcc (psycopg2 uchun), postgresql-client (pg_isready uchun)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Source code
COPY . .

# entrypoint.sh ni executable qilish
RUN chmod +x /app/entrypoint.sh

EXPOSE 8000

# PostgreSQL tayyor bo'lguncha kutadi → alembic upgrade head → server
ENTRYPOINT ["/app/entrypoint.sh"]
