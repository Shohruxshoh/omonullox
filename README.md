# post_api — To'liq Qo'llanma

Telegram postiga **views / reactions / shares** yuborish uchun tizim.  
Sessiyalar Redis da. Har bir so'rov API key + prioritet navbat orqali ishlaydi.

---

## Fayl tuzilmasi

```
post_api/
├── database.py           SQLAlchemy engine, Base, SessionLocal
├── models.py             ORM modellari: User, UserSession, ApiKey
│
├── user_store.py         Foydalanuvchi CRUD + login/token
├── api_key_store.py      API kalit CRUD + prioritet
├── account_queue.py      Per-account asyncio.Lock (parallel bloklash)
├── post_services.py      views / reactions / shares funksiyalari
├── redis_main.py         Redis session manager
│
├── api_server.py         FastAPI server (asosiy kirish nuqtasi)
├── worker.py             CLI orqali ishlatish
│
├── alembic.ini           Alembic konfiguratsiyasi
├── migrations/
│   ├── env.py            Base.metadata + render_as_batch=True
│   └── versions/         Migratsiya fayllari
│
├── data.db               SQLite DB (users, sessions, api_keys)
└── requirements.txt      Kutubxonalar
```

---

## Ishga tushirish

```bash
# 1. O'rnatish (birinchi marta)
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# 2. DB yaratish
alembic upgrade head

# 3. Serverni ishga tushirish
python api_server.py
# → http://localhost:8000
# → http://localhost:8000/docs  (Swagger UI)
```

> Server birinchi yoqilganda **admin / admin123** avtomatik yaratiladi.

---

## Qanday ishlaydi

### Auth tizimi
```
POST /auth/login → token (UUID)
Token → X-Token headerida yuboriladi
Admin endpointlar: role="admin" bo'lishi shart
```

### Priority Queue
```
API key ga priority belgilanadi (default=1000, kichik = oldinroq)
POST /task keladi → PriorityQueue ga (priority, timestamp, task)
Worker: kichik priority → oldin; teng priority → FIFO
```

### Per-account xavfsizlik
```
Bitta akkaunt hech qachon parallel ishlamaydi.
asyncio.Lock() — Task 2 Task 1 tugamaguncha kutadi.
```

### Post link formati
```
https://t.me/mychannel/123       → ochiq kanal
https://t.me/c/1234567890/456    → yopiq kanal (private)
```

---

## API Endpointlar

### Auth
| Method | URL | Tavsif |
|--------|-----|--------|
| POST | `/auth/register` | Ro'yxatdan o'tish |
| POST | `/auth/login` | Login → token |
| POST | `/auth/logout` | Logout |
| GET | `/auth/me` | Hozirgi user |

### Task (`X-API-Key` header kerak)
| Method | URL | Tavsif |
|--------|-----|--------|
| POST | `/task` | Yangi vazifa |
| GET | `/status/{id}` | Task holati |
| GET | `/tasks` | Barcha tasklar |
| GET | `/queue` | Navbat uzunligi |
| GET | `/locks` | Band akkauntlar soni |

### Admin (`X-Token` + `role=admin` kerak)
| Method | URL | Tavsif |
|--------|-----|--------|
| POST | `/admin/keys` | UUID kalit yaratish |
| GET | `/admin/keys` | Kalitlar ro'yxati |
| PATCH | `/admin/keys/{key}` | Prioritet o'zgartirish |
| DELETE | `/admin/keys/{key}` | Kalit o'chirish |
| GET | `/admin/users` | Foydalanuvchilar |
| POST | `/admin/users` | User yaratish |
| DELETE | `/admin/users/{id}` | User o'chirish |

---

## To'liq ish oqimi (misol)

```bash
# 1. Login
POST /auth/login
{"username": "admin", "password": "admin123"}
→ {"token": "abc-123-..."}

# 2. API key yaratish (priority=1 → VIP)
POST /admin/keys
X-Token: abc-123-...
{"priority": 1}
→ {"api_key": "550e8400-...", "priority": 1}

# 3. Task yuborish
POST /task
X-API-Key: 550e8400-...
{"post_link": "https://t.me/mychannel/123", "service": "views"}
→ {"task_id": "a1b2c3d4", "priority": 1, "status": "queued"}

# 4. Holat
GET /status/a1b2c3d4
→ {"status": "done", "total": 450, "done": 450}
```

---

## CLI orqali (API siz)

```bash
python worker.py --link "https://t.me/mychannel/123" --service views
python worker.py --link "https://t.me/mychannel/123" --service reactions --accounts 100
```

---

## Migratsiya

```bash
alembic current                              # joriy versiya
alembic upgrade head                         # oxirgiga o'tish
alembic revision --autogenerate -m "tavsif" # yangi migratsiya
alembic downgrade -1                         # bir qadam orqaga
alembic history                              # tarix
```
