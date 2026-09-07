# Talab, taklif va shikoyatlar (`/api/v1/feedback`)

Mehmon murojaatlari kitobi. Qabulxona mehmon aytganini yozadi (talab, taklif
yoki shikoyat), menejer ko'rib chiqib javob beradi va yopadi. Yangi shikoyat
mehmonxona administratorlari va menejerlariga (`shift.force_close`) push
bo'lib boradi; mas'ul biriktirilsa — unga alohida.

Barcha so'rovlar `Authorization: Bearer {access_token}` talab qiladi.
SUPER_ADMIN `?hotel_id=` bilan mehmonxonani ko'rsatadi.

---

## Kim nima qila oladi

Qoida `app/application/services/feedback_service.py` da bitta joyda.

| Amal | Kim |
|---|---|
| Ko'rish | ADMIN/SUPER_ADMIN · qabulxona (`reservation.create/update/view`) · menejer (`shift.force_close`) · `feedback.view/create/manage` |
| Kiritish | yuqoridagilar, **`reservation.view` yetarli emas** ("faqat ko'rish" roli yozmaydi) |
| Holat, javob, mas'ul | `feedback.manage` · `reservation.update` · `shift.force_close` · ADMIN |
| O'chirish | ADMIN/SUPER_ADMIN · `feedback.manage` |

Qabulxona kodlari ataylab kiritilgan: modul deploy bo'lishi bilan mavjud
xodimlar yangi ruxsat berilmasdan ishlaydi. `feedback.*` kodlari boshqa
rollarga (masalan buxgalter) alohida berish uchun — ular migratsiyada
(`f9c0d1e2f3a4`) va `scripts/seed_permissions.py` da.

Farrosh va texnik xodim bo'limni ko'rmaydi — murojaatda mehmon ismi va
telefoni bor.

---

## Qiymatlar

| Maydon | Qiymatlar |
|---|---|
| `feedback_type` | `REQUEST` (talab) · `SUGGESTION` (taklif) · `COMPLAINT` (shikoyat) |
| `status` | `NEW` → `IN_PROGRESS` → `RESOLVED` \| `REJECTED`; yopilgani `IN_PROGRESS` ga qayta ochiladi |
| `priority` | `LOW` · `MEDIUM` · `HIGH` |

Yopishda (`RESOLVED`/`REJECTED`) `resolution` (nima qilindi) **shart** —
`422 RESOLUTION_REQUIRED`. Noto'g'ri o'tish — `422 INVALID_STATUS_TRANSITION`.
`IN_PROGRESS` ga olgan xodim mas'ul bo'lib qoladi (agar mas'ul yo'q bo'lsa).

Mehmon, bron va xona bog'lamalari ixtiyoriy. Bron berilsa mehmon va xona
undan to'ldiriladi. Ism va xona raqami **nusxa** sifatida saqlanadi
(`guest_name`, `room_number`) — mehmon o'chirilsa ham kitobdagi yozuv qoladi.

---

## Endpointlar

### `GET /feedback/`
Filtrlar: `status`, `feedback_type`, `assigned_to`, `date_from`, `date_to`
(mehmonxona kuni bo'yicha), `query` (mavzu, matn, ism, telefon, xona),
`skip`, `limit` (≤1000). Javob: `{ "items": [...], "total": n }`, eng yangisi
birinchi.

### `POST /feedback/` → 201
```json
{
  "feedback_type": "COMPLAINT",
  "subject": "Konditsioner ishlamaydi",
  "body": "Kechasi xona juda issiq bo'ldi, texnik kelmadi",
  "priority": "HIGH",
  "reservation_id": "…",        // ixtiyoriy — mehmon va xona undan olinadi
  "room_id": "…",               // ixtiyoriy
  "guest_id": "…",              // ixtiyoriy
  "guest_name": "…",            // ro'yxatsiz mehmon uchun
  "guest_phone": "…",
  "assigned_to": "…"            // ixtiyoriy mas'ul xodim
}
```

### `GET /feedback/{id}`

### `PATCH /feedback/{id}`
Faqat yuborilgan maydonlar o'zgaradi: `subject`, `body`, `feedback_type`,
`priority`, `guest_id`, `reservation_id`, `room_id`, `guest_name`,
`guest_phone`, `assigned_to` (`null` — mas'ulni olib tashlash), `resolution`.

### `PATCH /feedback/{id}/status`
```json
{ "status": "RESOLVED", "resolution": "Konditsioner almashtirildi, mehmondan uzr so'raldi" }
```

### `DELETE /feedback/{id}`
Yumshoq o'chirish (`is_deleted`) — yozuv bazada qoladi.

---

## Javob obyekti

```json
{
  "id": "…", "hotel_id": "…", "branch_id": "…",
  "guest_id": "…", "reservation_id": "…", "reservation_number": "RES-…",
  "room_id": "…", "room_number": "101",
  "feedback_type": "COMPLAINT", "status": "IN_PROGRESS", "priority": "HIGH",
  "subject": "…", "body": "…",
  "guest_name": "…", "guest_phone": "…",
  "assigned_to": "…", "assigned_to_name": "…",
  "resolution": null, "resolved_at": null, "resolved_by": null, "resolved_by_name": null,
  "created_by": "…", "created_by_name": "…",
  "created_at": "2026-09-08T07:12:00+00:00", "updated_at": "…"
}
```
