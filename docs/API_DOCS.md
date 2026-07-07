# GoHotel API Documentatsiya

**Base URL:** `https://gohotel-gohotel-backend-lhyen5-ecceab-13-140-185-49.sslip.io`

> **Izoh:** Ushbu documentatsiya faqat backendda hali mavjud bo'lmagan API larni o'z ichiga oladi. Login (`POST /api/v1/auth/login`) va joriy foydalanuvchi (`GET /api/v1/auth/me`) API lari backendda mavjud.

---

## Autentifikatsiya

Barcha so'rovlar `Authorization: Bearer {access_token}` header talab qiladi.

---

## 1. Vazifalar (Tasks)

### 1.1 Barcha Vazifalarni Olish

```
GET /api/v1/tasks
```

**Query Parametrlar (ixtiyoriy):**

| Param | Tur | Tavsif |
|-------|-----|--------|
| `status` | string | `pending`, `inProgress`, `completed` |
| `date` | string | `YYYY-MM-DD` formatidagi sana |

**Response 200:**

```json
[
  {
    "id": "task_1",
    "room_number": "402",
    "floor": "4-qavat",
    "room_type": "Standart Lyuks",
    "guest": "Norqulov A.",
    "guest_status": "Bo'shatilgan",
    "status": "inProgress",
    "progress": 60,
    "deadline": "14:00",
    "note": null,
    "is_urgent": false,
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

**Status enum qiymatlari:**

| Qiymat | Ma'nosi |
|--------|---------|
| `pending` | Kutilmoqda |
| `inProgress` | Jarayonda |
| `completed` | Yakunlangan |

---

### 1.2 Vazifa Detalini Olish

```
GET /api/v1/tasks/{id}
```

**Response 200:** Yuqoridagi vazifa obyekti bilan bir xil.

---

### 1.3 Vazifani Boshlash

```
PUT /api/v1/tasks/{id}/start
```

**Response 200:** Yangilangan vazifa obyekti (status `inProgress` ga o'zgargan).

---

### 1.4 Vazifa Progressini Yangilash

```
PUT /api/v1/tasks/{id}/progress
```

**Request Body (JSON):**

```json
{
  "progress": 100
}
```

> `progress` 0 dan 100 gacha.

**Response 200:** Yangilangan vazifa obyekti.

> Edge case: `progress >= 100` bo'lsa, `status` avtomatik `completed` bo'ladi.

---

### 1.5 Checklist Elementini Toggle Qilish

```
PUT /api/v1/tasks/{taskId}/checklist/{itemId}/toggle
```

**Response 200:** Yangilangan vazifa obyekti.

> `is_completed` qiymati teskarisiga o'zgaradi. Vazifa progressi checklist asosida qayta hisoblanadi.

---

### 1.6 Foto Hisobot Yuborish

```
POST /api/v1/tasks/{id}/report
```

**Content-Type:** `multipart/form-data`

**Form Fields:**

| Field | Tur | Tavsif |
|-------|-----|--------|
| `photos` | File[] | Rasm fayllari (kamida 1 ta) |
| `comment` | string | Izoh (ixtiyoriy) |

**Request misoli (multipart):**

```
photos: [@photo1.jpg, @photo2.jpg]
comment: "Xona tozalandi"
```

**Response 200:**

```json
{
  "success": true,
  "message": "Foto hisobot qabul qilindi",
  "task": { "...yangilangan vazifa..." }
}
```

---

## 2. Muammolar (Problems)

### 2.1 Muammo Xabarini Yuborish

```
POST /api/v1/problems
```

**Content-Type:** `multipart/form-data`

**Form Fields:**

| Field | Tur | Majburiy | Tavsif |
|-------|-----|----------|--------|
| `category` | string | Ha | Kategoriya nomi |
| `description` | string | Ha | Muammo tafsilotlari |
| `photos` | File[] | Ha | Rasm fayllari (max 3 ta) |
| `task_id` | string | Yo'q | Bog'langan vazifa ID si |
| `room_number` | string | Yo'q | Xona raqami |

**Kategoriya qiymatlari:**

| Qiymat | Tavsif |
|--------|--------|
| `Siniq buyum` | Mebel, eshik, deraza singan |
| `Texnik nosozlik` | Santexnika, lift va b. |
| `Suv sizishi` | Quvur, tomdan suv oqishi |
| `Chiroy kuygan` | Devor, pol, mebel rangi o'chgan |
| `Elektr nosozligi` | Chiroq, rozetka, sim |
| `Mexanizm buzilgan` | Konditsioner, TV, seyf |
| `Boshqa` | Boshqa turdagi muammolar |

**Request misoli (multipart):**

```
category: "Suv sizishi"
description: "Vannaxonada quvurdan suv oqmoqda"
photos: [@problem.jpg]
task_id: "task_3"
room_number: "108"
```

**Response 200:**

```json
{
  "success": true,
  "message": "Muammo qabul qilindi",
  "report_id": "pr_001"
}
```

---

## 3. Bildirishnomalar (Notifications)

### 3.1 Barcha Bildirishnomalarni Olish

```
GET /api/v1/notifications
```

**Response 200:**

```json
[
  {
    "id": "notif_1",
    "type": "critical",
    "title": "Favqulodda tozalash zarur",
    "message": "402-xonada suv toshqini yuz berdi.",
    "room_number": "402",
    "timestamp": "2026-07-07T09:15:00.000Z",
    "is_read": false,
    "has_actions": true
  }
]
```

**NotificationType enum qiymatlari:**

| Qiymat | Ma'nosi |
|--------|---------|
| `critical` | Favqulodda / shoshilinch |
| `newTask` | Yangi vazifa |
| `problemAccepted` | Muammo qabul qilindi |
| `inventory` | Inventarizatsiya |
| `system` | Tizim xabari |

---

### 3.2 Bildirishnomani O'qilgan Belgilash

```
PUT /api/v1/notifications/{id}/read
```

**Response 204:** No Content

---

### 3.3 Barcha Bildirishnomalarni O'qilgan Belgilash

```
PUT /api/v1/notifications/read-all
```

**Response 204:** No Content

---

## Xatolik Formati

Barcha xatolik javoblari quyidagi formatda qaytadi:

```json
{
  "error": "Xatolik matni",
  "code": 400
}
```

**HTTP Status kodlari:**

| Kod | Ma'nosi |
|-----|---------|
| 200 | Muvaffaqiyatli |
| 204 | Muvaffaqiyatli (javobsiz) |
| 400 | Noto'g'ri so'rov |
| 401 | Autentifikatsiya xatosi |
| 404 | Topilmadi |
| 422 | Validatsiya xatosi |
| 500 | Server xatosi |

---

## Qo'shilishi Kerak Bo'lgan API lar Xulosasi

| # | Method | Endpoint | Tavsif |
|---|--------|----------|--------|
| 1 | `GET` | `/api/v1/tasks` | Barcha vazifalar |
| 2 | `GET` | `/api/v1/tasks/{id}` | Vazifa detali |
| 3 | `PUT` | `/api/v1/tasks/{id}/start` | Vazifani boshlash |
| 4 | `PUT` | `/api/v1/tasks/{id}/progress` | Progress yangilash |
| 5 | `PUT` | `/api/v1/tasks/{taskId}/checklist/{itemId}/toggle` | Checklist toggle |
| 6 | `POST` | `/api/v1/tasks/{id}/report` | Foto hisobot (multipart) |
| 7 | `POST` | `/api/v1/problems` | Muammo xabari (multipart) |
| 8 | `GET` | `/api/v1/notifications` | Bildirishnomalar |
| 9 | `PUT` | `/api/v1/notifications/{id}/read` | O'qilgan belgilash |
| 10 | `PUT` | `/api/v1/notifications/read-all` | Hammasini o'qilgan |
