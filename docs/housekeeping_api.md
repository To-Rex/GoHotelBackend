# Xo'jalik Xizmati (Housekeeping) API Documentatsiyasi

**Base URL:** `https://gohotel-gohotel-backend-lhyen5-ecceab-13-140-185-49.sslip.io`

Barcha so'rovlar `Authorization: Bearer {access_token}` header talab qiladi.

> Login: `POST /api/v1/auth/login` (`username` + `password`) → `access_token`

---

## Umumiy Ma'lumot

### Status qiymatlari

| Qiymat | Ma'nosi |
|--------|---------|
| `OPEN` | Yangi, hali boshlanmagan |
| `IN_PROGRESS` | Jarayonda |
| `COMPLETED` | Yakunlangan |
| `CANCELLED` | Bekor qilingan |

### Task Type qiymatlari

| Qiymat | Ma'nosi |
|--------|---------|
| `CLEANING` | Oddiy tozalash |
| `DEEP_CLEANING` | To'liq general tozalash |
| `MAINTENANCE` | Ta'mirlash |
| `INSPECTION` | Tekshirish |
| `TURN_DOWN` | Kechki tayyorgarlik |

### Priority qiymatlari

| Qiymat | Ma'nosi |
|--------|---------|
| `LOW` | Past |
| `MEDIUM` | O'rta |
| `HIGH` | Yuqori |
| `URGENT` | Shoshilinch |

### Kerakli Ruxsatlar (Permissions)

| Ruxsat kodi | Kerak bo'lgan API lar |
|-------------|----------------------|
| `housekeeping.create` | Task yaratish |
| `housekeeping.update` | Task tahrirlash, status o'zgartirish |
| `housekeeping.assign` | Taskni farroshga biriktirish |

---

## 1. Barcha Tasklarni Ko'rish

```
GET /api/v1/housekeeping/tasks
```

**Query Parametrlar (ixtiyoriy):**

| Param | Tur | Tavsif |
|-------|-----|--------|
| `status` | string | `OPEN`, `IN_PROGRESS`, `COMPLETED`, `CANCELLED` |
| `room_id` | UUID | Xona ID si bo'yicha filter |
| `branch_id` | UUID | Filial ID si bo'yicha filter |
| `assigned_to` | UUID | Farrosh ID si bo'yicha filter |
| `hotel_id` | UUID | Mehmonxona ID (SUPER_ADMIN uchun majburiy) |
| `skip` | int | O'tkazib yuborish (default: 0) |
| `limit` | int | Cheklash (default: 50, max: 200) |

**Response 200:**
```json
[
  {
    "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "hotel_id": "47a84ddc-dabf-4b6d-b4a4-10009d736926",
    "branch_id": "21aa908e-5ee2-41cc-b946-095090aed569",
    "room_id": "a5887f3b-0184-4d42-b17c-13c22fb860cd",
    "task_type": "DEEP_CLEANING",
    "status": "IN_PROGRESS",
    "priority": "HIGH",
    "assigned_to": "a7cb04c5-1a55-43fd-8748-bf169c22b78f",
    "notes": "General tozalash kerak",
    "scheduled_date": "2026-07-07",
    "started_at": "2026-07-07T10:30:00Z",
    "completed_at": null,
    "created_by": "33840bca-0a28-497c-9f70-2c02c8a95b7c",
    "created_at": "2026-07-07T09:00:00Z"
  }
]
```

---

## 2. Yangi Task Yaratish

```
POST /api/v1/housekeeping/tasks
```

**Ruxsat:** `housekeeping.create`

**Request Body (JSON):**
```json
{
  "branch_id": "21aa908e-5ee2-41cc-b946-095090aed569",
  "room_id": "a5887f3b-0184-4d42-b17c-13c22fb860cd",
  "task_type": "DEEP_CLEANING",
  "priority": "HIGH",
  "assigned_to": "a7cb04c5-1a55-43fd-8748-bf169c22b78f",
  "notes": "Xonani to'liq tozalash",
  "scheduled_date": "2026-07-07"
}
```

| Field | Tur | Majburiy | Tavsif |
|-------|-----|----------|--------|
| `branch_id` | UUID | Ha | Filial ID |
| `room_id` | UUID | Ha | Xona ID |
| `task_type` | string | Ha | `CLEANING` / `DEEP_CLEANING` / `MAINTENANCE` / `INSPECTION` / `TURN_DOWN` |
| `priority` | string | Yo'q | `LOW` / `MEDIUM` / `HIGH` / `URGENT` (default: `MEDIUM`) |
| `assigned_to` | UUID | Yo'q | Farrosh user ID |
| `notes` | string | Yo'q | Izoh |
| `scheduled_date` | date | Yo'q | Rejalashtirilgan sana (`YYYY-MM-DD`) |

**Query Parametr:**

| Param | Tur | Tavsif |
|-------|-----|--------|
| `hotel_id` | UUID | SUPER_ADMIN uchun majburiy |

**Response 200:** TaskResponse obyekti (yuqoridagi formatda).

---

## 3. Bitta Taskni Ko'rish

```
GET /api/v1/housekeeping/tasks/{task_id}
```

| Param | Joylashuvi | Tavsif |
|-------|-----------|--------|
| `task_id` | Path | Task UUID |
| `hotel_id` | Query | SUPER_ADMIN uchun ixtiyoriy |

**Response 200:** TaskResponse obyekti.

---

## 4. Taskni Tahrirlash

```
PUT /api/v1/housekeeping/tasks/{task_id}
```

**Ruxsat:** `housekeeping.update`

**Request Body (JSON):** — barcha fieldlar ixtiyoriy, faqat berilganlari yangilanadi.

```json
{
  "task_type": "MAINTENANCE",
  "priority": "URGENT",
  "assigned_to": "a7cb04c5-1a55-43fd-8748-bf169c22b78f",
  "notes": "Santexnika muammosi bor",
  "scheduled_date": "2026-07-08"
}
```

**Response 200:** Yangilangan TaskResponse obyekti.

---

## 5. Task Statusini O'zgartirish

```
PATCH /api/v1/housekeeping/tasks/{task_id}/status
```

**Ruxsat:** `housekeeping.update`

**Request Body (JSON):**
```json
{
  "status": "COMPLETED",
  "notes": "Tozalash yakunlandi"
}
```

| Field | Tur | Majburiy | Tavsif |
|-------|-----|----------|--------|
| `status` | string | Ha | `OPEN` / `IN_PROGRESS` / `COMPLETED` / `CANCELLED` |
| `notes` | string | Yo'q | Status o'zgarishi sababi |

**Muhim:** Status `COMPLETED` bo'lganda va task_type `CLEANING` yoki `DEEP_CLEANING` bo'lsa, xona avtomatik ravishda `AVAILABLE` holatiga o'tadi.

**Response 200:** Yangilangan TaskResponse obyekti.

---

## 6. Taskni Farroshga Biriktirish

```
POST /api/v1/housekeeping/tasks/{task_id}/assign
```

**Ruxsat:** `housekeeping.assign`

**Request Body (JSON):**
```json
{
  "assigned_to": "a7cb04c5-1a55-43fd-8748-bf169c22b78f"
}
```

**Response 200:** Yangilangan TaskResponse obyekti.

---

## 7. Ochiq Tasklar Ro'yxati

```
GET /api/v1/housekeeping/tasks/open
```

Statusi `OPEN` yoki `IN_PROGRESS` bo'lgan tasklarni qaytaradi.

**Query Parametrlar:**

| Param | Tur | Tavsif |
|-------|-----|--------|
| `branch_id` | UUID | Filial bo'yicha filter |
| `skip` | int | O'tkazib yuborish |
| `limit` | int | Cheklash |
| `hotel_id` | UUID | SUPER_ADMIN uchun ixtiyoriy |

**Response 200:** TaskResponse massivi.

---

## 8. Mening Tasklarim (Farrosh Uchun)

```
GET /api/v1/housekeeping/tasks/my-tasks
```

Joriy foydalanuvchiga biriktirilgan tasklarni qaytaradi.

**Query Parametrlar:**

| Param | Tur | Tavsif |
|-------|-----|--------|
| `skip` | int | O'tkazib yuborish |
| `limit` | int | Cheklash |
| `hotel_id` | UUID | SUPER_ADMIN uchun ixtiyoriy |

**Response 200:** TaskResponse massivi.

---

## 9. Mobil Tasklar (Boyitilgan Ma'lumot)

```
GET /api/v1/tasks
```

Farrosh mobil ilovasi uchun. Xona, qavat, mehmon ma'lumotlari bilan boyitilgan.

**Query Parametrlar:**

| Param | Tur | Tavsif |
|-------|-----|--------|
| `status` | string | `pending` / `inProgress` / `completed` |
| `date` | string | `YYYY-MM-DD` |
| `hotel_id` | UUID | SUPER_ADMIN uchun ixtiyoriy |

**Response 200:**
```json
[
  {
    "id": "3fa85f64-...",
    "room_number": "101",
    "floor": "1-qavat",
    "room_type": "Standart",
    "guest": "N. Norqulov",
    "guest_status": "Bo'shatilgan",
    "status": "inProgress",
    "progress": 60,
    "deadline": "14:00",
    "note": "General tozalash",
    "is_urgent": true,
    "checklist": [
      { "id": "chk_1", "title": "To'shaklarni almashtirish", "is_completed": true },
      { "id": "chk_2", "title": "Changlarni artish", "is_completed": true },
      { "id": "chk_3", "title": "Vannaxonani tozalash", "is_completed": false },
      { "id": "chk_4", "title": "Mini-barni to'ldirish", "is_completed": false },
      { "id": "chk_5", "title": "Pollarni yuvish", "is_completed": false }
    ]
  }
]
```

### 9.1 Vazifani Boshlash

```
PUT /api/v1/tasks/{task_id}/start
```

Status `OPEN` → `inProgress` ga o'tadi.

**Response 200:** Yangilangan MobileTaskResponse.

### 9.2 Progress Yangilash

```
PUT /api/v1/tasks/{task_id}/progress
```

**Request Body:**
```json
{
  "progress": 50
}
```

| Field | Tur | Tavsif |
|-------|-----|--------|
| `progress` | int | 0-100 oralig'ida. 100 bo'lsa avtomatik `completed` |

**Response 200:** Yangilangan MobileTaskResponse.

### 9.3 Checklist Elementini Toggle Qilish

```
PUT /api/v1/tasks/{task_id}/checklist/{item_id}/toggle
```

Checklist elementi `is_completed` ni teskarisiga o'zgartiradi. Progress avtomatik qayta hisoblanadi. Barcha elementlar bajarilganda (progress=100%) status avtomatik `completed` bo'ladi.

**Response 200:** Yangilangan MobileTaskResponse.

### 9.4 Foto Hisobot Yuborish

```
POST /api/v1/tasks/{task_id}/report
```

**Content-Type:** `multipart/form-data`

| Field | Tur | Tavsif |
|-------|-----|--------|
| `photos` | File[] | Rasm fayllari |
| `comment` | string | Izoh (ixtiyoriy) |

**Response 200:**
```json
{
  "success": true,
  "message": "Foto hisobot qabul qilindi",
  "task": { "...vazifa obyekti..." }
}
```

---

## 10. Muammo Xabari Yuborish

```
POST /api/v1/problems
```

**Content-Type:** `multipart/form-data`

| Field | Tur | Majburiy | Tavsif |
|-------|-----|----------|--------|
| `category` | string | Ha | `Siniq buyum` / `Texnik nosozlik` / `Suv sizishi` / `Chiroy kuygan` / `Elektr nosozligi` / `Mexanizm buzilgan` / `Boshqa` |
| `description` | string | Ha | Muammo tavsifi |
| `photos` | File[] | Yo'q | Rasm fayllari |
| `task_id` | string | Yo'q | Bog'langan vazifa ID |
| `room_number` | string | Yo'q | Xona raqami |

**Response 200:**
```json
{
  "success": true,
  "message": "Muammo qabul qilindi",
  "report_id": "abc123..."
}
```

---

## 11. Bildirishnomalar

### 11.1 Barcha Bildirishnomalar

```
GET /api/v1/notifications/
```

**Query Parametrlar:**

| Param | Tur | Tavsif |
|-------|-----|--------|
| `unread_only` | bool | Faqat o'qilmaganlar (default: false) |
| `skip` | int | O'tkazib yuborish |
| `limit` | int | Cheklash |

**Response 200:**
```json
[
  {
    "id": "notif_1",
    "type": "newTask",
    "title": "Yangi vazifa",
    "message": "101-xona uchun tozalash kerak",
    "room_number": null,
    "timestamp": "2026-07-07T09:15:00Z",
    "is_read": false,
    "has_actions": true
  }
]
```

**NotificationType enum:**

| Qiymat | Ma'nosi |
|--------|---------|
| `critical` | Shoshilinch |
| `newTask` | Yangi vazifa |
| `problemAccepted` | Muammo qabul qilindi |
| `inventory` | Inventarizatsiya |
| `system` | Tizim xabari |

### 11.2 Bitta Bildirishnomani O'qilgan Belgilash

```
PUT /api/v1/notifications/{notification_id}/read
```

**Response 200:**
```json
{
  "message": "Notification marked as read"
}
```

### 11.3 Barcha Bildirishnomalarni O'qilgan Belgilash

```
PUT /api/v1/notifications/read-all
```

**Response 200:**
```json
{
  "message": "All notifications marked as read"
}
```

---

## Xatolik Formati

Barcha xatolik javoblari quyidagi formatda:

```json
{
  "detail": "Xatolik matni",
  "error_code": "ERROR_CODE"
}
```

**HTTP Status kodlari:**

| Kod | Ma'nosi |
|-----|---------|
| 200 | Muvaffaqiyatli |
| 204 | Muvaffaqiyatli (javobsiz) |
| 400 | Noto'g'ri so'rov |
| 401 | Autentifikatsiya xatosi |
| 403 | Ruxsat yo'q |
| 404 | Topilmadi |
| 422 | Validatsiya xatosi |
| 500 | Server xatosi |

---

## Ish Jarayoni Misollari

### Manager yangi DEEP_CLEANING yaratadi

```bash
# 1. Login
curl -X POST https://.../api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "admin123"}'

# 2. Task yaratish
curl -X POST "https://.../api/v1/housekeeping/tasks?hotel_id=47a84ddc-..." \
  -H "Authorization: Bearer {TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "branch_id": "21aa908e-...",
    "room_id": "a5887f3b-...",
    "task_type": "DEEP_CLEANING",
    "priority": "HIGH",
    "assigned_to": "a7cb04c5-...",
    "notes": "General tozalash"
  }'

# 3. Farrux ishni boshlaydi (mobil ilova orqali)
curl -X PUT "https://.../api/v1/tasks/{task_id}/start" \
  -H "Authorization: Bearer {FARRUX_TOKEN}"

# 4. Manager statusni kuzatadi
curl "https://.../api/v1/housekeeping/tasks?status=IN_PROGRESS" \
  -H "Authorization: Bearer {TOKEN}"

# 5. Farrux tugatadi (progress 100)
curl -X PUT "https://.../api/v1/tasks/{task_id}/progress" \
  -H "Authorization: Bearer {FARRUX_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"progress": 100}'
# → Status avtomatik "completed", xona AVAILABLE bo'ladi

# 6. Manager tasdiqlaydi (agar kerak bo'lsa)
curl -X PATCH "https://.../api/v1/housekeeping/tasks/{task_id}/status" \
  -H "Authorization: Bearer {TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"status": "COMPLETED"}'
```

### Manager barcha farroshlar ishini kuzatadi

```bash
# Barcha ochiq tasklar
curl "https://.../api/v1/housekeeping/tasks/open" \
  -H "Authorization: Bearer {TOKEN}"

# Faqat bir farroshga tegishli tasklar
curl "https://.../api/v1/housekeeping/tasks?assigned_to=a7cb04c5-..." \
  -H "Authorization: Bearer {TOKEN}"

# Faqat bir filialdagi tasklar
curl "https://.../api/v1/housekeeping/tasks?branch_id=21aa908e-..." \
  -H "Authorization: Bearer {TOKEN}"

# Bugungi tasklar (mobil formatda)
curl "https://.../api/v1/tasks?date=2026-07-07" \
  -H "Authorization: Bearer {TOKEN}"
```

---

## API Xulosasi

| # | Method | Endpoint | Ruxsat |
|---|--------|----------|--------|
| 1 | `GET` | `/api/v1/housekeeping/tasks` | Auth |
| 2 | `POST` | `/api/v1/housekeeping/tasks` | `housekeeping.create` |
| 3 | `GET` | `/api/v1/housekeeping/tasks/{id}` | Auth |
| 4 | `PUT` | `/api/v1/housekeeping/tasks/{id}` | `housekeeping.update` |
| 5 | `PATCH` | `/api/v1/housekeeping/tasks/{id}/status` | `housekeeping.update` |
| 6 | `POST` | `/api/v1/housekeeping/tasks/{id}/assign` | `housekeeping.assign` |
| 7 | `GET` | `/api/v1/housekeeping/tasks/open` | Auth |
| 8 | `GET` | `/api/v1/housekeeping/tasks/my-tasks` | Auth |
| 9 | `GET` | `/api/v1/tasks` | Auth |
| 10 | `PUT` | `/api/v1/tasks/{id}/start` | Auth |
| 11 | `PUT` | `/api/v1/tasks/{id}/progress` | Auth |
| 12 | `PUT` | `/api/v1/tasks/{id}/checklist/{itemId}/toggle` | Auth |
| 13 | `POST` | `/api/v1/tasks/{id}/report` | Auth |
| 14 | `POST` | `/api/v1/problems` | Auth |
| 15 | `GET` | `/api/v1/notifications/` | Auth |
| 16 | `PUT` | `/api/v1/notifications/{id}/read` | Auth |
| 17 | `PUT` | `/api/v1/notifications/read-all` | Auth |
