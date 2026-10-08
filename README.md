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

Mehmonxona ichida esa **filiallar to'liq ajratilgan** — har so'rov bitta
filial ichida ishlaydi (pastda "Filiallar to'liq ajratilgan" bo'limi).

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

### Kunlik bron hisobi: 12 yoki 24 soatlik

Sozlamalar → "Bron va mehmonlar" → "Kunlik bron hisobi"
(`GET/PUT /hotels/booking-settings`, maydon `daily_unit`, saqlanadi:
`hotels.settings["booking"]["daily_unit"]`). **Standart — `12h`**.

- `12h` — avvalgi tartib: kalendarda tanlangan oxirgi kun chiqish kuni,
  1 kecha = xona turi narxi.
- `24h` — har tanlangan kun to'liq 24 soat va xona narxi 12 soatlik deb
  olinadi: **1 kun = narx × 2** (250 000 so'mlik xona — kuniga 500 000).
  Soatlik bronlarga tegishli emas.
- Rejim bron YARATILGANDA unga yoziladi (`reservations.daily_unit`) va
  keyingi barcha qayta hisoblar (xona almashtirish, chiqish, hisob-kitob,
  hisob-faktura) shu qiymat bilan — sozlama o'zgarsa ham mavjud bronlar
  narxi o'zgarmaydi. Sozlamadan oldingi bronlar — `12h`. Hisob-faktura
  qatorida birlik narxi ham × 2 bilan yoziladi.

Kod: `app/application/services/daily_unit.py`, migratsiya `d3a4b5c6d7e8`,
test: `pytest tests/test_daily_unit_pricing.py`.

### Filiallar to'liq ajratilgan

Bitta mehmonxonaning filiallari bir-birini ko'rmaydi: xonalar, qavatlar,
xona turlari va narxlari, xizmatlar, mehmonlar, bronlar, moliya, do'kon,
kassa va smenalar, xo'jalik vazifalari, xabarlar, bildirishnomalar,
qurilmalar, kameralar, hisobotlar va **sozlamalar** — har filialniki.

- **Kim qaysi filialda.** Xodim — doim o'z yozuvidagi filialda (boshqa
  filialga o'tkazilsa darhol, token yangilanishini kutmay). Administrator
  boshqa filialni faqat TANLAB ko'radi: `POST /auth/context` (sozlovchi
  kabi, lekin faqat o'z mehmonxonasi filiallari). Sozlovchi va tizim
  ma'muri — tanlagan mehmonxona va filiali.
- **Qanday ishlaydi** (`app/infrastructure/tenant/branch_scope.py`).
  `get_current_user` so'rov filialini sessiyaga yozadi. `BranchScoped`
  belgili modellarning har SELECT/UPDATE/DELETE so'roviga filial sharti
  avtomatik qo'shiladi (`with_loader_criteria`), yangi yozuvga filial
  avtomatik yoziladi; boshqa filialga yozish — 403 `BRANCH_SCOPE`.
  Servis va endpointlar kodi o'zgarmadi — chegarani biror joyda unutib
  qoldirib bo'lmaydi. Xodimlar ro'yxatlari (xodimlar sahifasi, xabar
  oluvchilar, farrosh tanlash) filial bo'yicha aniq filtrlanadi;
  administratorlar hamma filial xabarini oladi.
- **Sozlamalar filialda** (`branches.settings`,
  `app/application/services/branch_settings.py`): `settings_owner()`
  so'rov (yoki yozuv) filialini qaytaradi — `.settings` o'qiladi va yoziladi.
- **Fon vazifalari** (avto-chiqish, qarz eslatmalari) har bron/filial
  uchun o'sha filial ichida ishlaydi: sozlama, farrosh, xabar oluvchilar —
  shu filialniki. Kamera faqat o'z filiali mehmonlarini taniydi.
- **Yangi filial** asosiy filialning sozlama va kataloglari nusxasi bilan
  boshlanadi (`branch_provisioning.py`): xona turlari, yoqilgan turlar,
  qulayliklar, xizmat narxlari, cheklistlar, hisob rejasi, do'kon
  mahsulotlari (zaxirasiz). Faqat shu nusxalar bo'lgan filialni panel
  o'chira oladi; ish ma'lumoti bor filial — 409 `BRANCH_NOT_EMPTY`.
- **"Ma'lumotlarni tozalash"** faqat joriy filialni tozalaydi.
- **Migratsiya** `a6b7c8d9e0f1` mavjud ma'lumotni yo'qotmasdan taqsimlaydi:
  filialni aniq ko'rsatadigan bog'lanish bo'yicha (bron, xona, xodim...),
  bo'lmasa asosiy filialga; bir necha filialda bron qilgan mehmon har
  filialga alohida yozuvga ajratiladi (bronlari, hisoblari, hamrohlari
  o'sha yozuvga ulanadi); kataloglar har filialga nusxalanadi; do'kon
  zaxirasi asosiy filialda qoladi, boshqa filialda sotilgan miqdor o'sha
  filialga "o'tkazilgan" partiya bo'ladi; sozlamalar har filialga
  nusxalanadi; filiali yo'q mehmonxonaga "Asosiy filial" ochiladi.

Testlar: `tests/test_branch_migration.py` (migratsiya — alohida
vaqtinchalik bazada), `tests/test_branch_isolation_api.py` (80+ ro'yxat
endpointi ikki filial xodimi va filial almashtirgan administrator nomidan:
boshqa filialning birorta ID'si chiqmasligi), `tests/test_branch_provisioning.py`.
Uchalasi `GOHOTEL_TEST_PG_URL` (lokal test bazasi) bilan ishlaydi.

### Boshqaruv paneli: to'liq boshqaruv

Panel egasi hamma narsani paneldan boshqaradi:

- **Mehmonxonaga kirish** (Mehmonxonalar → "Kirish", filial kartasida
  "Kirish" / "Sozlamalar"): asosiy tizim shu mehmonxona/filialda sozlovchi
  huquqida yangi oynada ochiladi (`app/superadmin/enter_service.py`). Har
  panel foydalanuvchisi uchun asosiy tizimda yashirin `CONFIGURATOR` hisobi
  (`panel__<id>`, parol bilan kirib bo'lmaydi, sozlovchilar ro'yxatida
  ko'rinmaydi) yuritiladi; token sozlovchi `POST /auth/context` qilgandek
  beriladi. Panel foydalanuvchisi to'xtatilsa/o'chirilsa yashirin hisob
  ham to'xtaydi, sessiyalari yopiladi.
- **Xodimni filialga o'tkazish** — mehmonxona sahifasi, Xodimlar jadvali
  (`PATCH /superadmin/staff/{id}/branch`).
- **Tizim holati** (`GET /superadmin/system`): baza, MinIO, push,
  rejalashtiruvchi, versiya, yozuvlar soni.
- **E'lonlar** (`POST /superadmin/broadcast`): barcha mehmonxonalarga yoki
  tanlangan mehmonxona/filialga — administratorlarga yoki barcha xodimlarga,
  bildirishnoma + push (`app/superadmin/broadcast_service.py`).

Test: `GOHOTEL_TEST_PG_URL=... pytest tests/test_panel_control.py`.

### Dasturlar do'koni: bo'laklab yuklash (boshqaruv paneli)

Panel → Ilovalar. O'rnatish fayllari katta (APK ~100 MB); bitta so'rovda
yuborilsa yo'ldagi proksi (Traefik v3, standart `readTimeout` 60 s) tanasi
60 soniyadan uzoq kelayotgan so'rovni 504 bilan uzadi — sekin tarmoqdan
yangi versiya umuman qo'shib bo'lmas edi. Endi panel faylni 4 MB'lik
bo'laklarga bo'lib yuboradi (`app/application/services/app_upload_service.py`):

- `POST /superadmin/apps/uploads` — sessiya; `PUT .../chunks/{n}` — har
  bo'lak alohida qisqa so'rov (xato bergan bo'lak qayta yuboriladi);
  `POST .../complete` — bo'laklar yig'ilib MinIO'ga OQIM bilan yoziladi
  (butun fayl xotiraga olinmaydi), do'kon yozuvi yaratiladi.
- Bo'laklar server diskidagi vaqtinchalik papkada; tugallanmagan yuklash
  6 soatdan keyin o'chiriladi. Eski bir martalik `POST /superadmin/apps`
  ham ishlayveradi.

Test: `pytest tests/test_app_upload_chunks.py`.

### Mehmonxonani butunlay o'chirish (boshqaruv paneli)

Panel → Mehmonxonalar → "O'chirish". "To'xtatish" (`DELETE /superadmin/hotels/{id}`)
avvalgidek faqat to'xtatadi; butunlay o'chirish alohida
(`app/superadmin/hotel_purge_service.py`):

- **Avval ko'rib chiqish** (`GET .../purge-preview`) — nima o'chishi jadvallar
  bo'yicha sanaladi, bazada hech narsa o'zgarmaydi.
- **To'siqlar** (`POST .../purge`): faqat tizim egasi (`is_root`), o'z panel
  paroli va mehmonxona kodini aniq yozish; mehmonxona oldin to'xtatilgan
  (`INACTIVE`) bo'lishi shart.
- **Nima o'chadi** — `hotels` qatori va unga tegishli HAMMA qator: `hotel_id`
  ustunli jadvallar va ularga FK orqali bog'langanlar (ro'yxat bazaning o'z
  katalogidan olinadi — yangi jadval qo'shilsa ham qamrab olinadi). Global
  ma'lumotlarga (qulayliklar, ruxsatlar, xizmatlar katalogi, umumiy xona
  turlari, ilova relizlari, panel hisoblari) tegilmaydi.
- **Boshqa mehmonxonaga tegilmaydi.** Ikkala mehmonxonada bron qilgan mehmon
  o'chmaydi — uni eng ko'p ishlatgan mehmonxonaga o'tkaziladi. Tizim
  akkauntlari (SUPER_ADMIN, sozlovchi) ham o'chmaydi — faqat mehmonxonadan
  ajratiladi (`hotel_id` bo'shatiladi). Boshqa mehmonxona
  qatori shu mehmonxonaga RESTRICT/CASCADE bilan qarasa — o'chirish to'xtaydi
  (409 `CROSS_HOTEL_REFERENCES`), hech narsa o'zgarmaydi.
- Hammasi bitta tranzaksiyada; MinIO fayllari commit'dan keyin o'chiriladi.

Test: `pytest tests/test_hotel_purge_gates.py`; algoritm haqiqiy PostgreSQL'da —
`GOHOTEL_TEST_PG_URL=postgresql+asyncpg://postgres@127.0.0.1:55432/gohotel_test
pytest tests/test_hotel_purge.py` (har jadval to'ldiriladi, boshqa mehmonxona
qatorlari baytma-bayt solishtiriladi; o'zgaruvchi berilmasa o'tkazib yuboriladi).

### Qarzlar: sababi, eslatmalar, chiqishda tekshiruv

Maqsad — mijoz bilan qarz qolib ketmasin. Qarz hamma joyda SABABI bilan
ko'rinadi va unutilmaydi (`app/application/services/debt_service.py`):

- **Sabab.** To'lovlar haqlarni vaqt tartibida yopadi (xona → uzaytirilgan
  muddat → xizmat → jarima); yopilmay qolgan qism — sabab. Bronga yozilgan
  do'kon savdosining qoldig'i ham mehmon qarzi. Kirgan mehmon uchun summa
  `check_out` formulasi bilan (uzaytirish chiqishda qo'shilardi — endi oldindan
  ko'rinadi). `GET /reservations/{id}/debt`, `GET /finance/debtors` (`reasons`).
- **Chiqishda tekshiruv.** Resepsiya qarzdor mehmonni chiqara olmaydi (409
  `CHECKOUT_DEBT`): to'lov olinadi yoki sababi yozilib qarz bilan chiqariladi
  (`debt_ack_*` — kim, qachon, qancha, nima uchun; migratsiya `f5c6d7e8f9a0`).
  Farrosh tugmasi va avtomatik chiqish to'xtatilmaydi — xabar ketadi.
- **Oldindan undirish.** Kirgan mehmon to'lov qilganda jami chiqishdagi
  hisobga ko'tariladi — uzaytirish puli ketishdan oldin olinadi.
- **Eslatmalar** (`debt_reminder_service.py`, avtomatlashtirish tik'idan, har
  5 daqiqada): "qarz bilan chiqib ketdi" va "bugun chiqadi, qarzi bor" —
  har bron uchun bir marta; qarzdorlar ro'yxati — sozlamadagi oraliqda
  (`/hotels/debt-settings`, standart 2 soat, 08:00–22:00). Oluvchilar —
  administratorlar va `finance.payment.create` egalari (push + bildirishnoma).

Test: `pytest tests/test_debts.py`.

### Hujjat suratini saqlash (pasport, ID karta)

Pasport yoki ID karta qayerda skanerlansa ham surati MinIO'ga
(`MINIO_BUCKET_GUESTS`, `{hotel}/{guest|document_scan}/{id}/document-<tomon>-<vaqt>.jpg`)
saqlanadi va `file_attachments` orqali biriktiriladi
(`category="document_passport|front|back"`):

- **Qabulxona telefoni** — `POST /reception/scans`: surat skan yozuviga
  (`entity_type="document_scan"`), hujjat raqami bo'yicha mehmon topilsa
  mehmonga ham. Javobda `images_saved`. Takror skan (90 soniya) nusxa
  yozmaydi. Yangi mehmon vebda yaratilgach
  `POST /reception/scans/{id}/link-guest` `{"guest_id"}` surati unga
  bog'laydi. Mobil ilova o'zgarmagan — rasm serverga baribir kelardi.
- **Veb skaneri** — mehmon yaratilgach/tanlangach
  `POST /guests/{id}/document-images` (multipart `passport|front|back`).
- Ko'rish: `GET /guests/{id}/document-images` va
  `.../{file_id}` (rasm API orqali, faqat shu mehmonxona suratlari).
- O'chirish: skaner sozlamasida `store_images` (`PUT /guests/scan-settings`,
  standart — yoqiq; yuborilmasa saqlangani o'zgarmaydi).
- Saqlash skanerni to'xtatmaydi: fayl ombori ishlamasa skan natijasi
  baribir qaytadi (SAVEPOINT, xato faqat logda).

Kod: `app/application/services/document_images.py`, test:
`pytest tests/test_document_images.py`.

### Do'kon: qisman va aralash to'lov

Bronga yozilgan do'kon savdosi istalgancha marta QISMAN to'lanadi, har
to'lov bir yoki bir necha usulda (masalan naqd + karta). Qoldiq qolgan
ekan savdo `PENDING` (qarz), to'liq to'langanda `PAID`.

- `POST /shop/sales/{id}/pay`: `{"payment_method": "CASH", "amount": 20000}`
  (summasiz — butun qoldiq, avvalgidek) yoki `{"payments": [{"amount":
  10000, "payment_method": "CASH"}, {"amount": 5000, "payment_method":
  "CARD"}]}` — jami qoldiqdan oshmaydi (422 `SHOP_PAYMENT_EXCEEDS_REMAINING`).
- `POST /shop/sales` bronga yozishda `payments` — hozir to'lanadigan qism
  (ixtiyoriy, jami summadan oshmaydi). Oddiy sotuvda `payments` avvalgidek
  jami summaga teng bo'lishi shart.
- Javobda `paid_amount`, `remaining_amount`; `payments` — har to'lov
  qachon (`paid_at`), qaysi usulda va kim qabul qilgani.
- Har to'lov `shop_sale_payments` da (migratsiya `e4b5c6d7e8f9` mavjud
  to'langan savdolarni ko'chiradi). Do'kon tushumi, kunlik grafik,
  xodimlar kesimi, smena kassasi va shaxsiy hisobot SHU jadvaldan: qisman
  to'lov o'sha kuni va pulni QABUL QILGAN xodim kassasiga tushadi. Do'kon
  qarzi — to'lanmagan qoldiq.

Kod: `app/application/services/shop_payments.py`, test:
`pytest tests/test_shop_partial_payments.py`.

### Jarimalar (kech chiqish, shikast)

Mehmon kech chiqsa yoki biror narsani sindirsa bronga jarima yoziladi.
Vebda: Bronlar → bronni boshqarish oynasi → "Jarimalar" bo'limi;
kiritilgan summalar shu yerda, bron tafsilotida va Moliya sahifasida
ko'rinadi.

- Turlari: `LATE_CHECKOUT` (kech chiqish), `DAMAGE` (shikast/sindirilgan
  narsa), `OTHER`. Faqat `CHECKED_IN` va `CHECKED_OUT` bronlarga (chiqib
  ketgan mehmonga keyin topilgan shikast uchun ham).
- Qo'shish — `reservation.update` ruxsati (resepshn), bekor qilish — faqat
  ADMIN/SUPER_ADMIN yoki `shift.force_close` (menejer). Jarima
  o'chirilmaydi: bekor qilingani sababi va kim qilgani bilan qoladi.
- Summa `reservations.penalty_amount` ga va bron jamiga qo'shiladi,
  hisob-fakturada `PENALTY` qatori bo'ladi; chiqish, xona ko'chirish,
  hisob-kitob va hisob-faktura yaratishda qayta hisoblansa ham saqlanadi.
  To'lov odatdagi "hisob-kitob" orqali olinadi (qarz sifatida ko'rinadi).
- Kech chiqish taklifi (ixtiyoriy): Sozlamalar → "Bron va mehmonlar" →
  "Jarimalar" (`GET/PUT /hotels/penalty-settings`,
  `hotels.settings["penalty"]`): soatiga summa va imtiyozli daqiqalar.
  **Standart — o'chiq (0)**. Yoqilsa server kechikkan soatlarni (yuqoriga
  yaxlitlab) hisoblab taklif qiladi, xodim summani o'zgartira oladi.
- Moliya: `GET /finance/penalties?date_from&date_to` (jurnal + turlar
  bo'yicha jami), `/finance/summary` da `penalty_total`, `penalty_count`.

Kod: `app/application/services/reservation_penalty_service.py`, migratsiya
`c2f3a4b5c6d7`, test: `pytest tests/test_reservation_penalties.py`.

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

### Mobil "Moliya" sahifasi (admin/menejer)

Mobil ilovada puls sahifasidagi moliya kartasi bosilsa alohida sahifa
ochiladi: bugun / kecha / 7 kun / shu oy / ixtiyoriy davr, oldingi davr
bilan solishtirish, xarajat, sof natija (tushum + do'kon − xarajat — veb
bilan bir xil), qarzdorlik, qaytarimlar, kassada hozir qancha pul, naqd
pul harakati, kunlik grafik, to'lov usullari, xarajat toifalari,
qarzdorlar va smenalardagi kamomad/ortiqcha. Ikkita yangi endpoint:

- `GET /finance/daily?date_from&date_to` — kunlik qator bitta so'rovda
  (`/finance/summary` bilan bir xil kun ta'rifi, ko'pi bilan 366 kun).
- `GET /shifts/cash-overview` — ochiq kassalarda hozir bo'lishi kerak
  bo'lgan naqd pul (tarkibi bilan; admin yoki `shift.force_close`).

Test: `pytest tests/test_finance_overview.py`.

**Kassada hozir va smenadan smenaga o'tgan pullar (veb).** Moliya va
Smenalar sahifalarida "Kassada hozir" kartasi (`/shifts/cash-overview`,
har daqiqada yangilanadi). Smenalar sahifasida "Smenadan smenaga o'tgan
pullar": `GET /shifts/handovers?date_from&date_to` — har yopilgan kassa
kimdan kimga o'tgani (qabul qiluvchining boshlang'ich kassasi bilan),
kunlik kesimda yoki majburiy yopishda kassadan chiqqan pul, kutilgan
summa va farq. Ikkalasi ham faqat admin yoki `shift.force_close` ("ko'r
sanash" saqlanadi). Test: `pytest tests/test_shift_handovers.py`.

**Tushum xodimlar kesimida.** Moliya sahifasida "Tushum — xodimlar
bo'yicha" kartasi: kim qancha bron to'lovi va do'kon savdosi olgan,
naqd/karta, qaytarim va xarajat. `GET /finance/by-staff?date_from&date_to`
— pul yozuvning o'zidan bog'lanadi (`Payment/ShopSalePayment/Expense.created_by`,
"Mening hisobotim" va smena kassasi bilan bir xil), sana ta'rifi
`/finance/summary` bilan bir xil, ya'ni xodimlar yig'indisi jami tushumga
teng. Ko'rish — admin yoki `finance.view` / `shift.force_close` /
`report.view`. Test: `pytest tests/test_finance_by_staff.py`.

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
