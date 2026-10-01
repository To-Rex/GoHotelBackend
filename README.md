# GoHotel ERP Backend

Hotel boshqaruv tizimi (PMS) uchun FastAPI asosidagi backend. Ko'p mehmonxonali (multi-tenant) SaaS arxitektura, permission-based access control, double-entry finance, housekeeping, reservation tizimi.

## Texnologiyalar

| Qatlam | Texnologiya |
|--------|-------------|
| Framework | FastAPI (async) |
| ORM | SQLAlchemy 2.0 (async) |
| Database | PostgreSQL |
| Migratsiya | Alembic |
| Validatsiya | Pydantic v2 |
| Autentifikatsiya | JWT (python-jose) |
| Parol | bcrypt |
| Fayl saqlash | MinIO |
| Python | 3.12+ |

## Arxitektura

```
app/
├── core/            # Konfiguratsiya, database, xavfsizlik
├── domain/          # Enum'lar
├── infrastructure/  # ORM modellar, repository'lar, auth, MinIO
├── application/     # DTO'lar, biznes logika (service layer)
├── presentation/    # API router'lar, middleware
├── shared/          # Mixin'lar, utility'lar
└── main.py          # Kirish nuqtasi
```

**Arxitektura pattern'lari**: Clean Architecture, Repository Pattern, Service Layer

## Ishga tushirish

```bash
# 1. Repositoriyani klonlash
git clone <repo-url> && cd GoHotelBackend

# 2. Virtual muhit
python3 -m venv .venv && source .venv/bin/activate

# 3. Bog'liqliklarni o'rnatish
pip install -r requirements.txt

# 4. .env fayl yaratish
cp .env.example .env
# .env ichidagi DATABASE_URL, MINIO_ENDPOINT va JWT_SECRET_KEY ni sozlang

# 5. Ma'lumotlar bazasi jadvallarini yaratish
alembic upgrade head

# 6. Boshlang'ich ma'lumotlarni to'ldirish
python -m scripts.seed_permissions
python -m scripts.seed_ledgers

# 7. Super Admin yaratish
python -c "
import asyncio
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.database.models.user import User
from app.infrastructure.auth.password import hash_password

async def main():
    async with async_session_factory() as s:
        s.add(User(user_type='SUPER_ADMIN', username='admin',
            password_hash=hash_password('admin123'),
            first_name='Super', last_name='Admin', status='ACTIVE'))
        await s.commit()
asyncio.run(main())
"





# 8. Serverni ishga tushirish
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Swagger UI: `http://localhost:8000/docs`

## Test qilish

```bash
source .venv/bin/activate && python tests/run_all_tests.py
```


Barcha modullar bo'yicha **86 ta endpoint** avtomatik testdan o'tkaziladi: Auth, Hotels, Branches, Floors, RoomTypes, Rooms, Guests, Reservations, Employees, Permissions, Services, Housekeeping, Finance, Reports, AuditLogs, Notifications, Files.

## Foydalanuvchi turlari

| Tur | Tavsif |
|-----|--------|
| `SUPER_ADMIN` | Barcha mehmonxonalarni boshqaradi. Admin va xodim yaratadi. Global room type, amenity yaratadi. |
| `ADMIN` | Bitta mehmonxonani boshqaradi. Xodim yaratadi, ruxsat beradi. |
| `EMPLOYEE` | Ruxsatlar orqali amallarni bajaradi (reception, housekeeping, finance). |

Rollarga asoslangan emas — **ruxsatlarga asoslangan** (permission-based) tizim. Har bir xodimga individual ruxsatlar beriladi.

## Permission'lar

| Modul | Ruxsatlar |
|-------|-----------|
| `reservation` | create, update, cancel, view |
| `guest` | create, update, view, checkin, checkout |
| `room` | view, status.update, manage |
| `room_types` | create, update, delete |
| `housekeeping` | task.create, task.assign, task.update, cleaning.start, cleaning.complete |
| `finance` | view, invoice.create, invoice.manage, payment.create, journal.create |
| `report` | view, export |
| `employee` | view, create, update, manage |
| `service` | view, manage |
| `hotels` | create, update |

## Ma'lumotlar bazasi

**32 ta jadval**: hotels, branches, floors, room_types, hotel_room_types, rooms, room_status_history, users, user_sessions, permissions, user_permissions, guests, reservations, services, hotel_services, reservation_services, housekeeping_tasks, ledgers, journal_entries, journal_entry_lines, invoices, invoice_line_items, payments, amenities, hotel_amenities, room_amenities, audit_logs, file_attachments, notifications, reports.

**Global modellar** (`hotel_id` yo'q, M2M orqali biriktiriladi):
- `room_types` — `hotel_room_types` jadvali orqali mehmonxonaga biriktiriladi
- `amenities` (Qulayliklar) — `hotel_amenities` orqali mehmonxonaga, `room_amenities` orqali xonaga biriktiriladi

**Per-room narx**: Har bir xonada `base_price` — individual narx belgilash imkoniyati. Bron vaqtida shu narx ishlatiladi.

## Muhim endpoint'lar

### Auth
```
POST /api/v1/auth/login      # Kirish
POST /api/v1/auth/refresh    # Token yangilash
POST /api/v1/auth/logout     # Chiqish
GET  /api/v1/auth/me         # Profil
```

### Room Types (Global, SUPER_ADMIN)
```
GET    /api/v1/room-types                       # Barcha xona turlari
POST   /api/v1/room-types                       # Yaratish
GET    /api/v1/room-types/{id}                  # Ko'rish
PUT    /api/v1/room-types/{id}                  # Tahrirlash
DELETE /api/v1/room-types/{id}                  # O'chirish
PATCH  /api/v1/room-types/{id}/status           # Aktivlashtirish/o'chirish
GET    /api/v1/hotels/{id}/room-types           # Mehmonxona xona turlari
POST   /api/v1/hotels/{id}/room-types           # Mehmonxonaga biriktirish
DELETE /api/v1/hotels/{id}/room-types/{rtid}    # Ajratish
```

### Amenities / Qulayliklar (Global, SUPER_ADMIN)
```
GET    /api/v1/amenities                        # Barcha qulayliklar
POST   /api/v1/amenities                        # Yaratish
PUT    /api/v1/amenities/{id}                   # Tahrirlash
DELETE /api/v1/amenities/{id}                   # O'chirish
GET    /api/v1/hotels/{id}/amenities            # Mehmonxona qulayliklari
POST   /api/v1/hotels/{id}/amenities            # Mehmonxonaga biriktirish
DELETE /api/v1/hotels/{id}/amenities/{aid}      # Ajratish
POST   /api/v1/rooms/{id}/amenities             # Xonaga biriktirish
DELETE /api/v1/rooms/{id}/amenities/{aid}       # Xonadan ajratish
```

### Reservations (Bron)
```
POST   /api/v1/reservations                          # Bron yaratish
GET    /api/v1/reservations                          # Ro'yxat
GET    /api/v1/reservations/{id}                     # Batafsil
GET    /api/v1/reservations/calendar                 # Kalendar (daily/weekly/monthly)
GET    /api/v1/reservations/availability             # Bo'sh xonalar
POST   /api/v1/reservations/{id}/check-in            # Check-in
POST   /api/v1/reservations/{id}/check-out           # Check-out
POST   /api/v1/reservations/{id}/cancel              # Bekor qilish
```

Bron yaratishda yangi imkoniyatlar:
- **booking_type**: `DAILY` (kunlik) yoki `HOURLY` (soatlik)
- **Narx avtomatik hisoblanadi**: kunlik = `base_price × kun`, soatlik = `base_price / 24 × soat`
- **payment_amount + payment_method**: Bron bilan birga to'lov qabul qilish (invoice + payment bir tranzaksiyada)
- **payments**: Qisman (bo'lib) to'lov — `[{amount, payment_method}, ...]` ro'yxati; har bir bo'lak alohida payment bo'lib yoziladi (masalan bir qismi `CASH`, qolgani `CREDIT_CARD`). Berilsa `payment_amount`/`payment_method` o'rniga shu ishlatiladi
- **payment_status**: `UNPAID`, `PARTIALLY_PAID`, `PAID`
- **payment_method**: `CASH`, `CREDIT_CARD`, `DEBIT_CARD`, `BANK_TRANSFER`, `MOBILE_PAYMENT`, `ONLINE`
- Javobda `total_amount`, `paid_amount`, `payment_status`, `booking_type` qaytadi

### Rooms
```
POST   /api/v1/rooms                               # Xona yaratish
GET    /api/v1/rooms                               # Ro'yxat (branch_id, floor_id, status filter)
PUT    /api/v1/rooms/{id}                          # Tahrirlash (base_price, capacity, notes)
PATCH  /api/v1/rooms/{id}/status                   # Status o'zgartirish
DELETE /api/v1/rooms/{id}                          # Soft-delete
```
Xona yaratishda: `room_number`, `room_type_id`, `base_price` (individual narx), `capacity` (sig'im).

### Finance
```
GET    /api/v1/finance/ledgers                       # Hisob-kitob rejasi
POST   /api/v1/finance/invoices                      # Hisob-faktura
POST   /api/v1/finance/invoices/{id}/pay             # To'lov qayd etish
GET    /api/v1/finance/journal-entries               # Jurnal yozuvlari
```

## Multi-tenant ishlash prinsipi

API so'rovlarda `hotel_id` query parametri orqali yoki JWT token ichidagi `hotel_id` orqali tenant aniqlanadi:

- **SUPER_ADMIN**: `hotel_id` query parametri orqali istalgan mehmonxonani tanlaydi. Parametrsiz — barcha mehmonxonalar bo'yicha.
- **ADMIN / EMPLOYEE**: JWT token'dagi `hotel_id` avtomatik qo'llaniladi.

Global modellar (`room_types`, `amenities`) SUPER_ADMIN tomonidan yaratiladi va `hotel_id` talab qilmaydi. Mehmonxonalarga M2M jadval orqali biriktiriladi.

## Muhit o'zgaruvchilari (.env)

| O'zgaruvchi | Tavsif | Default |
|-------------|--------|---------|
| `DATABASE_URL` | PostgreSQL async URL | `postgresql+asyncpg://...` |
| `DATABASE_URL_SYNC` | PostgreSQL sync URL | `postgresql://...` |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | Ulanishlar puli: doimiy / qo'shimcha | 20 / 10 |
| `DB_POOL_RECYCLE` | Ulanish shu yoshga (s) yetsa yangilanadi | 1800 |
| `DB_POOL_TIMEOUT` | Pool to'la bo'lsa kutish (s) | 30 |
| `DB_POOL_PRE_PING` | Pool'dan olishdan oldin ulanishni tekshirish | `true` |
| `DB_CONNECT_TIMEOUT` | Bazaga ulanish muddati (s) | 10 |
| `DB_APPLICATION_NAME` | `pg_stat_activity` dagi nom | `gohotel-backend` |
| `JWT_SECRET_KEY` | JWT imzo kaliti | — |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | Access token muddati | 120 |
| `JWT_REFRESH_TOKEN_EXPIRE_DAYS` | Refresh token muddati | 7 |
| `MINIO_ENDPOINT` | MinIO server manzili | `localhost:9000` |
| `MINIO_ACCESS_KEY` | MinIO access kaliti | `minioadmin` |
| `MINIO_BUCKET_DOCUMENTS` | Hujjatlar bucket | `hotel-documents` |
| `MINIO_BUCKET_GUESTS` | Mehmon bucket | `hotel-guests` |
| `CORS_ORIGINS` | Ruxsat etilgan origin'lar | `["http://localhost:3000"]` |
| `SMS_API_BASE` | Xabarchi **backend** (API) manzili — veb-sayt emas | `https://manager-xabarchi-backend-…sslip.io/api/v1` |
| `APP_TZ_OFFSET_MINUTES` | Mehmonxona mahalliy vaqti ofseti (daqiqa, UTC'ga nisbatan) — ish vaqti, vazifa taqsimlash va ish vaqtidan tashqarida to'sish shu soat bilan hisoblanadi | `300` (UTC+5) |

### Ish vaqtidan tashqarida ishlash (sozlama)

Veb: Sozlamalar → "Xodimlar" guruhi (`GET/PUT /api/v1/hotels/work-hours-settings`, `{"enforce": bool}`,
saqlanadi: `hotels.settings["work_hours"]`). **Standart — o'chiq**: yoqilmaguncha
hech narsa o'zgarmaydi.

- Yoqilganda `EMPLOYEE` o'z ish vaqti (`work_start`–`work_end`, mahalliy
  soat `APP_TZ_OFFSET_MINUTES` bilan; tungi smena ham) dan tashqarida har
  so'rovga **403 `OUTSIDE_WORK_HOURS`** oladi — klient "ish vaqtingiz emas"
  ekranini ko'rsatadi va vaqt kelganda o'zi tiklanadi.
- To'silMAYDI: `ADMIN`/`SUPER_ADMIN`; "Ish vaqtidan tashqari ham ishlay
  oladi" belgili xodim (`users.allow_outside_work_hours`, faqat administrator
  qo'yadi — `POST/PUT /employees`); FAOL smena sessiyasi bor xodim
  (qabulxonaning "Davom etish" oqimi o'zgarmaydi); ish vaqti 24 soatlik
  (boshlanish = tugash) yoki yozilmagan xodim. Mehmonxona to'xtatilgan bo'lsa
  `HOTEL_*` xatosi ustun.
- Ochiq yo'llar: `/auth/me`, `/auth/logout`, `/notifications/register-device`,
  `/hotels/work-hours-settings`, `/auth/face/status`, `/auth/webauthn/*`.
  Kirish (login) va `/auth/refresh` hech qachon to'silmaydi.
- `GET /auth/me` qo'shimcha qaytaradi: `allow_outside_work_hours`,
  `work_hours_enforced`, `work_hours_blocked` (server soati bilan).

Kod: `app/application/services/work_hours_access.py` (`get_current_user`
oxirida chaqiriladi), migratsiya `a0d1e2f3a4b5`, test:
`pytest tests/test_work_hours_access.py`.

### Xona almashtirishda chegirma (sozlama)

Mehmon xonani yoqtirmay QIMMATROQ xonaga o'tsa, resepshn narx farqidan
chegirma bera oladi. Veb: Sozlamalar → "Bron va mehmonlar" → "Xona
almashtirishda chegirma" (`GET/PUT /api/v1/hotels/move-discount-settings`,
`{"enabled": bool, "max_percent": 0-100, "max_amount": so'm}`, saqlanadi:
`hotels.settings["room_move_discount"]`). **Standart — o'chiq**.

- `max_percent` — narx FARQIDAN foiz, `max_amount` — bir ko'chirishda so'm;
  0 — cheklovsiz (farqning hammasigacha). Ikkalasi birga ishlaydi. "Farq" —
  mehmon AMALDA qancha ko'p to'lashi: bron chegirmasi foizda bo'lsa, farq
  ham shu foizga kamayadi (mehmon avvalgi xonadagidan kam to'lamaydi).
- Xodim: `POST /reservations/{id}/move-room` ga `discount_amount` (so'm)
  yuboradi — sozlama o'chiq bo'lsa yoki chegaradan oshsa rad etiladi.
  `ADMIN`/`SUPER_ADMIN` sozlamaga bog'lanmaydi, lekin chegirma hech qachon
  narx farqidan oshmaydi; arzonroq xonaga chegirma yo'q.
- Chegirma `reservations.move_discount_amount` da to'planadi (bron
  chegirmasidan alohida), chiqishda va hisob-faktura yaratishda ham
  ayiriladi, `invoice.discount_amount` ga qo'shiladi. Arzonroq xonaga
  qaytilsa avval shu chegirma kamayadi. Qayta hisoblarda chegirma
  boshlang'ich (arzon) xona narxi farqidan oshmaydi — muddat qisqartirilsa
  ham mehmon o'sha xona narxidan kam to'lamaydi. Har ko'chirish
  `room_moves` da `price_increase`, `discount_amount`, `move_discount_total`,
  `discount_baseline_price` bilan yoziladi.

Kod: `app/application/services/move_discount_policy.py`, migratsiya
`b1e2f3a4b5c6`, test: `pytest tests/test_move_discount_policy.py`.

### Sozlovchi (CONFIGURATOR) roli

Sozlovchi — mehmonxonaga bog'lanmagan hisob (`users.user_type =
"CONFIGURATOR"`, `hotel_id` bo'sh). U **faqat boshqaruv panelida**
yaratiladi (Panel → Obyektlar → Sozlovchilar; `GET/POST
/superadmin/configurators`, `DELETE /superadmin/configurators/{id}`, holati
va paroli `/superadmin/staff/{id}/status|password`).

- Asosiy tizimga login/parol bilan kiradi, mehmonxona va filialni tanlaydi
  (`GET /auth/context/options`, `POST /auth/context` — yangi token
  juftligi). Tanlov tokenda turadi va `/auth/refresh` da saqlanadi; veb
  Navbar'dagi tugma orqali istalgan payt almashtiradi.
- Tanlangan mehmonxonada ADMINISTRATOR kabi ishlaydi: `get_current_user`
  uning `user_type` ini "ADMIN" qilib beradi, haqiqiy turi
  `actual_user_type` da (`configurator_access.py`). Qurilma tasdig'i va
  "mehmonxona to'xtatilgan" to'sig'i unga qo'llanmaydi.
- **Sozlamalar faqat sozlovchi va SUPER_ADMIN da.** Sozlamalarni yozuvchi
  barcha endpointlar (`assert_can_manage_settings`) mehmonxona
  administratoriga ham 403 `SETTINGS_CONFIGURATOR_ONLY` qaytaradi; o'qish
  hammaga ochiq. Vebda Sozlamalar sahifasi boshqa rollarda umuman
  ko'rinmaydi. SUPER_ADMIN ham mehmonxona tanlay oladi (ixtiyoriy) —
  `hotel_id: null` bilan avvalgi "barcha mehmonxonalar" holatiga qaytadi.
- Mobil ilovada sozlovchiga faqat "veb-ilovada ishlang" sahifasi chiqadi.

Kod: `app/application/services/configurator_access.py`, test:
`pytest tests/test_configurator_access.py`. Migratsiya kerak emas
(`users.user_type` oddiy satr).

### Mijozga SMS (Xabarchi)

Mijozga SMS [Xabarchi](https://github.com/To-Rex/Xabarchi-Backend) orqali
ketadi — u SMS'ni hisobga ulangan Android telefon (SIM) bilan yuboradi.

- **Kalit** har filialga alohida: Sozlamalar → "SMS xabarnomalar" (yoki
  `PUT /branches/{id}/sms`, ruxsat `branch.update`). Xabarchi'da kalit
  `sms.send` ruxsati bilan yaratiladi. Bazada Fernet bilan shifrlangan
  saqlanadi; javoblarda faqat niqoblangan ko'rinishi qaytadi.
- **Qachon ketadi**: bron yaratilganda (tasdiqlash), qo'shimcha to'lov
  qabul qilinganda (`settle-payment`, `PAY`) va bron hisob-fakturasi Moliya
  bo'limidan to'langanda (`POST /finance/invoices/{id}/pay`). Qaytarimda
  SMS yo'q. Mehmon telefoni O'zbekiston raqami bo'lmasa SMS yuborilmaydi.
- **Fire-and-forget**: SMS xatosi bron yoki to'lovni hech qachon buzmaydi —
  sababi logga bir qatorda yoziladi: `SMS yuborilmadi (bron): +998… —
  [auth_error] …`. Kalit kiritilmagan filial avvalgidek, SMS'siz ishlaydi.
- **Sinov**: `POST /branches/{id}/sms/test {phone}` natijani darhol
  qaytaradi (`status: queued` — Xabarchi navbatiga tushdi); xato bo'lsa
  sababi aniq matn bilan (`kalit noto'g'ri`, `oylik limit tugagan`,
  `manzil noto'g'ri sozlangan` …).
- **Manzil**: `SMS_API_BASE` Xabarchi **backend**'iga qaratilishi shart.
  Veb-sayt (dashboard) domeni POST'ga 405 qaytaradi — SMS ketmaydi.
  Tekshirish: `GET {manzil}/healthz` → `{"status":"ok"}`.

Kod: `app/application/services/sms_service.py`, test:
`python tests/test_sms_service.py`.

### Baza bilan aloqa uzilganda

Baza serveri qayta ishga tushsa, tarmoq uzilsa yoki pool'dagi ulanishni
server yopib qo'ysa, so'rov **503 `DB_UNAVAILABLE`** qaytaradi (ilgari 500
`INTERNAL_ERROR` va "connection is closed" matni edi). Klient buni "qayta
urinib ko'ring" deb ko'rsatadi. Pool har ulanishni berishdan oldin tekshiradi
(`pool_pre_ping`), shu sababli baza tiklangach so'rovlar o'zi ishlab ketadi —
serverni qayta ishga tushirish shart emas. `GET /health` javobidagi
`database` maydoni (`ok` / `down`) baza holatini ko'rsatadi. Aniqlash
qoidalari: `app/core/db_errors.py`.

## Migratsiyalar

```bash
# Yangi migratsiya yaratish
alembic revision --autogenerate -m "description"

# Migratsiyalarni qo'llash
alembic upgrade head

# Orqaga qaytarish
alembic downgrade -1
```

Migratsiya versiyalari:
| Versiya | Tavsif |
|---------|--------|
| `250c3edb6dfe` | Initial schema (26 ta jadval) |
| `4dc5c4f1ccf6` | Fix refresh token column size |
| `a1b2c3d4e5f6` | Bron narxlash, soatlik bron, to'lov statuslari |
| `b2c3d4e5f6a7` | Amenity moduli + room capacity |
| `c3d4e5f6a7b8` | Amenity global qilindi + hotel_amenities M2M |
| `d4e5f6a7b8c9` | RoomType global qilindi + hotel_room_types M2M |
| `e5f6a7b8c9d0` | Xonaga individual base_price |

## Kelajakdagi kengaytirishlar

- Bayram/xafta oxiri uchun alohida narx (room_type_prices jadvali)
- Online booking (B2C)
- To'lov tizimlari integratsiyasi (Stripe, PayPal)
- Email/SMS notification
- Shift boshqaruvi va ish haqi
- OTA channel manager (Booking.com, Expedia)
- WebSocket orqali real-time yangilanishlar
- Hisobotlar uchun materialized view'lar
