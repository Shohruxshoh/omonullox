# Loyiha Strukturasi — Telegram Post Engagement API

---

## Maqsad

Telegram kanal va guruh postlariga **views, reactions, shares** yuborish xizmati.
Yuzlab Telegram akkauntlarni parallel boshqarib, vazifalarni priority navbat orqali tartibli, xavfsiz va ishonchli bajaradi.

---

## Texnologiyalar

| Komponent | Texnologiya |
|-----------|-------------|
| Backend | Python 3.11+, FastAPI, Uvicorn |
| Telegram client | Telethon (StringSession) |
| Ma'lumotlar bazasi | PostgreSQL 16 (SQLAlchemy + Alembic) |
| Cache / Session pool | Redis 7 (Fernet shifrlangan) |
| Konteyner | Docker, Docker Compose |
| Rate limiting | slowapi |
| Shifrlash | cryptography (Fernet) |
| Parol | PBKDF2-HMAC-SHA256 |

---

## Fayl Strukturasi

```
post_api/
│
├── api_server.py          — Asosiy FastAPI server (endpointlar, worker loop, circuit breaker)
├── post_services.py       — Telegram amallar (views, reactions, shares, checker)
├── redis_main.py          — Redis session manager (Fernet shifrlash)
├── session_store.py       — Session holati (flood, ban, dedup, statistika)
├── task_log_store.py      — Task tarixi CRUD (PostgreSQL)
├── account_queue.py       — Per-account asyncio.Lock registry
├── models.py              — SQLAlchemy ORM modellari (5 jadval)
├── database.py            — SQLAlchemy engine va SessionLocal
├── user_store.py          — Foydalanuvchi va token boshqaruvi
├── api_key_store.py       — API key CRUD
│
├── migrations/
│   ├── env.py             — Alembic muhit konfiguratsiyasi
│   └── versions/
│       ├── bcc3db564546   — Initial schema (users, sessions, api_keys)
│       ├── a1b2c3d4e5f6   — Task logs jadvali
│       ├── c7d8e9f0a1b2   — Telegram sessions va dedup jadvallari
│       ├── d4e5f6a7b8c9   — task_id String(8) → String(36) (to'liq UUID)
│       └── e5f6a7b8c9d0   — task_meta ustuni (JSON meta)
│
├── sessions/              — JSON formatdagi session fayllari (upload uchun)
├── sessions.example.json  — Session fayl namunasi
│
├── docker-compose.yml     — PostgreSQL + Redis + App + Uploader
├── Dockerfile             — Python 3.11 slim, psycopg2, pip deps
├── entrypoint.sh          — Ishga tushirish: pg_isready → alembic upgrade → server
├── alembic.ini            — Alembic konfiguratsiyasi
├── requirements.txt       — Python dependencies
└── .env                   — Muhit o'zgaruvchilari (parollar, URL lar)
```

---

## Ma'lumotlar Bazasi Jadvallari

### `users` — Tizim foydalanuvchilari
| Ustun | Tip | Izoh |
|-------|-----|------|
| id | Integer PK | Auto increment |
| username | String(50) | Unique, indexed |
| password_hash | Text | PBKDF2-HMAC-SHA256 (`salt:dk` hex) |
| role | String(20) | `admin` yoki `user` |
| created_at | String(50) | ISO UTC+00:00 |

### `sessions` — Login tokenlari
| Ustun | Tip | Izoh |
|-------|-----|------|
| token | String(36) PK | UUID token |
| user_id | Integer FK | → users.id (cascade delete) |
| created_at | String(50) | ISO UTC+00:00 |

### `api_keys` — Task yuborish kalitlari
| Ustun | Tip | Izoh |
|-------|-----|------|
| api_key | String(36) PK | UUID |
| priority | Integer | 1–10000 (kichik = muhimroq), default 1000 |
| created_at | String(50) | ISO UTC+00:00 |

### `task_logs` — Task tarixi
| Ustun | Tip | Izoh |
|-------|-----|------|
| task_id | String(36) PK | To'liq UUID |
| api_key | String(36) | Indexed |
| service | String(20) | `views` / `reactions` / `shares` |
| post_link | Text | Telegram post URL |
| priority | Integer | Navbat prioriteti |
| status | String(20) | `queued` / `running` / `done` / `error` / `rejected` |
| total | Integer | Jami akkauntlar soni |
| done | Integer | Muvaffaqiyatli yuborilganlar |
| error | Text | Xato xabari (nullable) |
| task_meta | Text | JSON: `{"accounts": N, "emoji": "👍"}` (nullable) |
| created_at | String(50) | ISO UTC+00:00 |
| started_at | String(50) | Boshlangan vaqt (nullable) |
| finished_at | String(50) | Tugagan vaqt (nullable) |

### `telegram_sessions` — Telegram akkauntlar registri
| Ustun | Tip | Izoh |
|-------|-----|------|
| id | Integer PK | |
| uid | String(64) | Unique, indexed — tashqi tizim ID |
| api_id | String(20) | Telegram app_id |
| api_hash | String(64) | Telegram app_hash |
| hash_session | Text | Telethon StringSession |
| phone_num | String(32) | Indexed |
| user_id | String(32) | Indexed |
| username | String(64) | @username (nullable) |
| is_working | String(32) | `active` / `sleep` / `banned` / `flood` |
| flood_until | String(50) | ISO — qachon flood blok tugaydi |
| flood_count_today | Integer | Bugungi flood soni |
| flood_date | String(10) | YYYY-MM-DD |
| is_premium | String(4) | `0` yoki `1` |
| is_scam | String(8) | `yes` yoki `no` |
| phone_model | String(128) | Qurilma modeli (nullable) |
| app_version, app_name, lang_code | String | Ilovaning meta ma'lumotlari |
| date_last_online | String(50) | Oxirgi faollik (nullable) |

### `post_session_logs` — Deduplikatsiya
| Ustun | Tip | Izoh |
|-------|-----|------|
| id | Integer PK | |
| session_uid | String(64) | Indexed |
| post_link | Text | |
| service | String(20) | |
| done_date | String(10) | YYYY-MM-DD |
| **UniqueConstraint** | | `(session_uid, post_link, service, done_date)` |

---

## API Endpointlari

### Autentifikatsiya

| Metod | Yo'l | Auth | Tavsif |
|-------|------|------|--------|
| POST | `/auth/register` | — | Ro'yxatdan o'tish |
| POST | `/auth/login` | — | Login → UUID token qaytaradi |
| POST | `/auth/logout` | X-Token | Tokenni bekor qilish |
| GET | `/auth/me` | X-Token | Joriy foydalanuvchi ma'lumoti |

### Task yuborish (X-API-Key, 30/daqiqa limit)

| Metod | Yo'l | So'rov tanasi | Tavsif |
|-------|------|---------------|--------|
| POST | `/task/views` | `{post_link, accounts?}` | Views yuborish |
| POST | `/task/reactions` | `{post_link, reaction, accounts?}` | Reactions yuborish |
| POST | `/task/shares` | `{post_link, accounts?}` | Shares yuborish |

**Javob:** `{task_id, status, priority, message}`

### Task holati

| Metod | Yo'l | Tavsif |
|-------|------|--------|
| GET | `/status/{task_id}` | Task holati: done/skipped/flooded/banned_count/percent |
| GET | `/tasks?limit=50&offset=0` | Task ro'yxati (pagination) |
| GET | `/queue` | Navbatdagi tasklar soni |
| GET | `/locks` | Akkaunt lock statistikasi |

### Admin (X-Token + role=admin)

**API kalitlar:**

| Metod | Yo'l | Tavsif |
|-------|------|--------|
| POST | `/admin/keys` | Yangi API key yaratish |
| GET | `/admin/keys` | Barcha kalitlar ro'yxati |
| PATCH | `/admin/keys/{key}` | Prioritet o'zgartirish (1–10000) |
| DELETE | `/admin/keys/{key}` | Kalit o'chirish |
| GET | `/admin/stats` | Har bir key bo'yicha statistika |
| GET | `/admin/tasks?limit=50&offset=0` | Barcha tasklar |

**Foydalanuvchilar:**

| Metod | Yo'l | Tavsif |
|-------|------|--------|
| GET | `/admin/users` | Foydalanuvchilar ro'yxati |
| POST | `/admin/users` | Yangi foydalanuvchi yaratish |
| DELETE | `/admin/users/{id}` | Foydalanuvchini o'chirish (o'zini o'chira olmaydi) |

**Sessionlar:**

| Metod | Yo'l | Tavsif |
|-------|------|--------|
| POST | `/admin/sessions/upload-folder` | JSON fayllardan Redis ga yuklash |
| POST | `/admin/sessions/upload-db` | DB dan Redis ga yuklash (`only_active=true`) |
| DELETE | `/admin/sessions/clear-redis` | Redis ni tozalash (⚠️ barcha sessionlar o'chadi) |
| POST | `/admin/sessions/import-csv` | CSV dan `telegram_sessions` ga import |
| GET | `/admin/sessions/stats` | Session statistikasi |

**Tizim:**

| Metod | Yo'l | Tavsif |
|-------|------|--------|
| GET | `/admin/system/status` | Circuit breaker holati |
| POST | `/admin/sessions/resume` | To'xtatilgan tizimni davom ettirish |

---

## Arxitektura: Qanday Ishlaydi

```
1. Klient → POST /task/reactions  (X-API-Key: abc123)
              ↓
2. validate_api_key() → priority = 500 (DB dan)
              ↓
3. task_id = uuid4()
   task_status[task_id] = {status: "queued", ...}   ← RAM
   tlog.create(task_id, meta={"emoji":"👍",...})     ← PostgreSQL
   task_queue.put((500, timestamp, task_dict))        ← PriorityQueue
              ↓
4. worker_loop() (background) dequeue qiladi
              ↓
5. redis_manager.get_all_sessions() → list[dict]     ← Redis (shifrdan chiqarilgan)
              ↓
6. reactions uchun: checker account (max 3 ta sinab ko'riladi)
   check_reaction_available() → "ok" | "reactions_disabled" | "reaction_not_allowed"
              ↓
7. asyncio.gather() — barcha sessionlar parallel:
   ┌─ semaphore (max 400 concurrent)
   ├─ is_blocked() → flood/banned bo'lsa skip
   ├─ is_done_ever() → reactions uchun abadiy dedup
   ├─ get_lock(session) → per-account lock (bitta vaqtda faqat 1 ta)
   └─ send_reactions_to_post(session, channel, msg_id, emoji)
              ↓
8. Natijalar:
   "ok"      → done++, mark_done_today()
   "flood:N" → mark_flood(), flooded++
   "banned"  → mark_banned(), remove_session_by_id(), banned_count++
   "skip"    → skipped++
              ↓
9. task holati: "done" → tlog.set_done()
```

### Priority Queue

```
(priority=1, ts) → birinchi bajariladi  ← muhim mijoz
(priority=500, ts) → keyinroq
(priority=1000, ts) → eng oxirida
Teng prioritetlarda: FIFO (arrival time bo'yicha)
```

---

## Xavfsizlik Qatlamlari

### Kirish himoyasi
| # | Himoya | Mexanizm |
|---|--------|----------|
| 1 | Redis paroli | `--requirepass`, URL da ham parol |
| 2 | Session shifrlash | Fernet (AES-128-CBC), faqat `session` maydoni |
| 3 | Rate limiting | 30 task/daqiqa per IP (X-Forwarded-For orqali haqiqiy IP) |
| 4 | API Key auth | Har bir task uchun valid X-API-Key majburiy |
| 5 | Token auth | Admin/user amallari uchun UUID token (X-Token) |
| 6 | Parol xeshlash | PBKDF2-HMAC-SHA256, 100k iteratsiya, random salt |
| 7 | Docker network | Redis va PostgreSQL faqat ichki `app_net` da, tashqariga port yo'q |
| 8 | Admin paroli | `.env`dagi `ADMIN_PASSWORD`; o'rnatilmasa UUID generatsiya + log |

### Akkaunt himoyasi
| # | Himoya | Mexanizm |
|---|--------|----------|
| 9 | Flood himoya | 1-2x → `wait+1soat`; 3x/kun → 24 soat to'liq blok |
| 10 | Flood release | Har 60 soniyada muddati o'tgan bloklar avtomatik ochiladi |
| 11 | Ban himoya | `UserDeactivatedBanError`, `AuthKeyError` → doimiy ban |
| 12 | Ban → Redis | Ban bo'lgan akkaunt Redis dan avtomatik o'chiriladi |
| 13 | Per-account lock | Bir akkaunt bir vaqtda faqat 1 ta coroutine ishlatadi |
| 14 | Upload filtri | Session yuklashda DB dagi banned akkauntlar o'tkazib yuboriladi |

### Task himoyasi
| # | Himoya | Mexanizm |
|---|--------|----------|
| 15 | Dedup (reactions) | 1 akkaunt 1 postga umuman 1 marta (`is_done_ever`) |
| 16 | Dedup (views/shares) | 1 akkaunt 1 postga kuniga 1 marta (`is_done_today`) |
| 17 | Reaction checker | Max 3 checker, emoji mumkin bo'lmasa `rejected` status |
| 18 | Circuit breaker | 5 daqiqada 10+ xato → worker to'xtaydi (`IS_PAUSED=True`) |
| 19 | Semaphore | Max 400 akkaunt parallel (`PARALLEL_ACCOUNTS`) |
| 20 | Closure fix | `_e=emoji` default arg — noto'g'ri emoji yuborilishi oldini olindi |

### Tizim ishonchliligi
| # | Himoya | Mexanizm |
|---|--------|----------|
| 21 | Queue persistence | Restart → `queued` tasklar DB dan qayta yuklanadi |
| 22 | Restart recovery | `running` tasklar `error` ga o'tkaziladi |
| 23 | Task meta | `emoji`, `accounts` DB da saqlanadi → restart da yo'qolmaydi |
| 24 | Timezone-aware | Barcha `created_at` UTC+00:00 formatida |
| 25 | Pagination | `/tasks`, `/admin/tasks` limit/offset bilan |

---

## Konfiguratsiya (.env)

| O'zgaruvchi | Tavsif | Misol |
|-------------|--------|-------|
| `POSTGRES_DB` | DB nomi | `postapi` |
| `POSTGRES_USER` | DB foydalanuvchisi | `postapi` |
| `POSTGRES_PASSWORD` | DB paroli | `kuchli_parol` |
| `DATABASE_URL` | SQLAlchemy URL | `postgresql://user:pass@postgres:5432/db` |
| `REDIS_PASSWORD` | Redis paroli | `kuchli_parol` |
| `REDIS_URL` | Redis URL (parolli) | `redis://:pass@redis:6379` |
| `REDIS_KEY` | Redis set kaliti | `telegram:sessions:full` |
| `PARALLEL_ACCOUNTS` | Max parallel akkaunt | `400` |
| `ADMIN_PASSWORD` | Birinchi admin paroli | `kuchli_parol` |
| `SESSION_FERNET_KEY` | Fernet kalit (base64) | `G61_PJEg...=` |
| `CB_THRESHOLD` | Circuit breaker xato chegarasi | `10` |
| `CB_WINDOW` | Circuit breaker vaqt oynasi (soniya) | `300` |

**Fernet kalit yaratish:**
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

---

## Ishga Tushirish

### Docker (tavsiya etilgan)
```bash
# 1. .env ni sozlash
cp .env .env.backup
# REDIS_PASSWORD, ADMIN_PASSWORD, SESSION_FERNET_KEY ni o'zgartiring

# 2. Ishga tushirish
docker compose up -d

# 3. Sessionlarni yuklash
docker compose run --rm uploader
# yoki API orqali:
# POST /admin/sessions/upload-folder?clear_first=true  (X-Token: admin_token)

# 4. Loglarni ko'rish
docker compose logs -f app
```

### Local Development
```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
alembic upgrade head
python api_server.py               # http://localhost:8000
```

### Migration buyruqlari
```bash
alembic upgrade head               # Barcha pending migrationlarni qo'llash
alembic current                    # Hozirgi versiya
alembic history                    # Barcha versiyalar
alembic revision --autogenerate -m "tavsif"  # Yangi migration yaratish
alembic downgrade -1               # Bir qadam orqaga
```

---

## Migration Tarixi

| Revision | Sana | O'zgarish |
|----------|------|-----------|
| `bcc3db564546` | 2026-04-01 | Initial: `users`, `sessions`, `api_keys` |
| `a1b2c3d4e5f6` | 2026-04-01 | `task_logs` jadvali |
| `c7d8e9f0a1b2` | 2026-04-02 | `telegram_sessions`, `post_session_logs` (dedup) |
| `d4e5f6a7b8c9` | 2026-04-07 | `task_id` String(8) → String(36) to'liq UUID |
| `e5f6a7b8c9d0` | 2026-04-07 | `task_logs.task_meta` ustuni (JSON meta) |

---

## Deploy

### Portlar

| Port | Xizmat | Ko'rinish | Tavsif |
|------|--------|-----------|--------|
| **8000** | FastAPI app | Host ga ochiq | Nginx reverse proxy shu portga ulanadi |
| **5432** | PostgreSQL | Faqat ichki (`app_net`) | Tashqariga ochiq emas |
| **6379** | Redis | Faqat ichki (`app_net`) | Tashqariga ochiq emas |

> **Muhim:** PostgreSQL va Redis hech qachon tashqariga ochilmaydi.
> Faqat `app_net` Docker tarmog'i ichida ko'rinadi.

---

### Server tayyorlash (birinchi marta)

```bash
# 1. Docker va Docker Compose o'rnatish
apt update && apt install -y docker.io docker-compose-plugin

# 2. Loyihani klonlash
git clone <repo> /opt/post_api
cd /opt/post_api

# 3. .env ni sozlash
cp .env.example .env   # yoki to'g'ridan .env yarating
nano .env
```

**`.env` da albatta o'zgartirish kerak:**
```
POSTGRES_PASSWORD=kuchli_parol_yozing
REDIS_PASSWORD=kuchli_parol_yozing
ADMIN_PASSWORD=kuchli_parol_yozing
SESSION_FERNET_KEY=<python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">
```

---

### Docker bilan ishga tushirish

```bash
# Barcha xizmatlarni build qilib ishga tushirish
docker compose up -d

# Ishga tushish jarayoni (avtomatik):
# 1. PostgreSQL tayyor bo'lguncha kutadi (pg_isready, max 60s)
# 2. alembic upgrade head — DB migratsiyalari qo'llanadi
# 3. python api_server.py — server ishga tushadi (port 8000)

# Holat tekshirish
docker compose ps
docker compose logs -f app

# Sessionlarni yuklash
docker compose run --rm uploader
```

---

### Nginx konfiguratsiyasi

```nginx
server {
    listen 80;
    server_name yourdomain.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name yourdomain.com;

    ssl_certificate     /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;

        # Katta so'rovlar uchun (CSV import)
        client_max_body_size 50M;
    }
}
```

> **Eslatma:** `X-Forwarded-For` headeri muhim — rate limiting haqiqiy mijoz
> IP sini shu header orqali aniqlaydi.

---

### SSL sertifikat (Let's Encrypt)

```bash
apt install certbot python3-certbot-nginx
certbot --nginx -d yourdomain.com
```

---

### Yangilanishlar (update)

```bash
cd /opt/post_api

# Yangi kodni olish
git pull

# Containerni qayta qurish
docker compose up -d --build

# Faqat migratsiyalarni qo'llash (agar kerak bo'lsa)
docker compose exec app alembic upgrade head

# Loglarni kuzatish
docker compose logs -f app
```

---

### Foydali buyruqlar

```bash
# Barcha xizmatlarni to'xtatish
docker compose down

# Ma'lumotlar bilan birga to'xtatish (⚠️ DB o'chadi)
docker compose down -v

# Container ichiga kirish
docker compose exec app bash
docker compose exec postgres psql -U postapi -d postapi

# Redis ga kirish
docker compose exec redis redis-cli -a $REDIS_PASSWORD

# Sessionlar sonini ko'rish
docker compose exec redis redis-cli -a $REDIS_PASSWORD SCARD telegram:sessions:full

# DB backup
docker compose exec postgres pg_dump -U postapi postapi > backup.sql

# DB restore
docker compose exec -T postgres psql -U postapi postapi < backup.sql
```

---

### Ishga tushgandan keyin bajarish keraklar

```
1. POST /auth/login  {"username": "admin", "password": "<ADMIN_PASSWORD>"}
   → token oling

2. POST /admin/keys  {"priority": 1000}
   X-Token: <token>
   → api_key oling

3. POST /admin/sessions/upload-folder?clear_first=true
   X-Token: <token>
   → sessionlarni Redis ga yuklang

4. POST /task/views  {"post_link": "https://t.me/channel/123", "accounts": 100}
   X-API-Key: <api_key>
   → task_id oling

5. GET /status/<task_id>
   → natijani kuzating
```

---

## Task Holatlari

| Status | Ma'nosi |
|--------|---------|
| `queued` | Navbatda kutmoqda |
| `running` | Hozir bajarilmoqda |
| `done` | Muvaffaqiyatli tugadi |
| `error` | Texnik xato (Redis, network...) |
| `rejected` | Checker tomonidan rad etildi (reaction mumkin emas) |

---

## Session Holatlari

| Holat | Ma'nosi |
|-------|---------|
| `active` | Ishga tayyor |
| `sleep` | Kutish rejimida (DB da bor, lekin ishlatilmaydi) |
| `flood` | Vaqtincha blok (flood_until gacha) |
| `banned` | Doimiy ban (qayta ishlatilmaydi) |
