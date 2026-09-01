# Guest Face Recognition (`/api/v1/vision`)

Mehmonni yuzidan tanish. Kamera agenti ([GoHotelsVision](../../GoHotelsVision))
filialdagi kompyuterda ishlaydi, mehmonni ko'radi, uni **o'zi tanib** 512
baytlik vektor yuboradi; server esa faqat vektor arifmetikasini qiladi.

Mavjud **xodim** yuz-kirishi (`/api/v1/auth/face/*`, `user_face_profiles`)
o'zgarmagan va bu tizimdan mustaqil ishlaydi.

---

## Nega server rasm ko'rmaydi

Yuzdan vektor hisoblash ~10-15 ms CPU oladi va OpenCV modellari thread-safe
emas — ya'ni serverda bu ish global qulf ostida, ketma-ket bajariladi. Bir
necha filial kameralari bir vaqtda ishlaganda shift aynan shu yerda paydo
bo'ladi: sekundiga ~20-30 kadr, jami.

Og'ir ishni agentga ko'chirish buni butunlay olib tashlaydi. Har filial o'z
bo'sh CPU'sini sarflaydi; serverda qoladigan ish — bitta matritsa-vektor
ko'paytmasi:

| O'lcham | Qiymat |
|---|---|
| Bitta shablon | 512 bayt (128 x float32) |
| 5 000 mehmon x 3 shablon | 7.3 MB xotira |
| 1:N qidiruv (15 000 shablon) | **~1 ms** |
| Hodisa payloadi (rasmsiz) | ~1.3 KB |

Chegara mehmonlar sonida emas. `pgvector` faqat yuz minglab shablonda kerak
bo'ladi.

---

## Ma'lumot modeli

| Jadval | Vazifasi |
|---|---|
| `guest_face_profiles` | Mehmon shabloni: paketlangan `float32` (`BYTEA`), `hotel_id`, `model`, `cohesion` |
| `face_sightings` | Kamera ko'rgan bitta epizod: holat, o'xshashlik, surat, vektor |
| `vision_devices` | Kamera agentlari uchun muddatsiz qurilma tokenlari |
| `guests.face_consent_at` | Biometrik rozilikning yagona manbasi |

Vektor **JSON matn emas**, paketlangan `float32`. Bu ataylab: 1:N qidiruvda
minglab qatorni `json.loads` qilish soniyalarga aylanadi, `np.frombuffer` esa
nusxasiz ishlaydi. Butun mehmonxona indeksi bitta `numpy` matritsasiga
yig'iladi va o'zgarganda versiya hisoblagichi bilan bekor qilinadi.

---

## Chegaralar va nega ular shunday

| Doimiy | Qiymat | Sabab |
|---|---|---|
| `MATCH_THRESHOLD` | 0.52 | Xodim login (1:1) uchun 0.40 yetarli — u kim ekanini allaqachon da'vo qilyapti. Bu yerda yuzlab mehmon ichidan qidiriladi, va nomzodlar ko'paygan sari tasodifiy yuqori ball ehtimoli ortadi. |
| `MATCH_MARGIN` | 0.05 | Eng yaxshi nomzod **boshqa mehmonning** eng yaxshisidan shuncha ustun bo'lishi shart. Bu o'xshash yuzlarga qarshi asosiy himoya: yuqori ball + kichik margin "bu u" emas, "bu shu ikkitadan biri" degani. |
| `REVIEW_THRESHOLD` | 0.42 | Shu orada qolgan nomzod `uncertain` bo'lib panelda ko'rsatiladi, lekin unga tayanib ish qilinmaydi. |
| `ADAPTIVE_LEARN_THRESHOLD` | 0.66 | Ishonchli moslikda yangi ko'rinish o'rganiladi (soch, ko'zoynak, yorug'lik). Chegara ataylab yuqori: shubhali moslikdan o'rganish xatoni mustahkamlaydi. |
| `MAX_PROFILES_PER_GUEST` | 6 | Ko'proq shablon = yaxshiroq qamrov, lekin indeks kattalashadi va eskirgan ko'rinishlar aniqlikni pasaytiradi. |

Sintetik, realistik tarqalishdagi ma'lumotda (bir odamning ikki kadri ~0.63,
begonalar ~0.08) 5 000 mehmon va 15 000 shablonli indeksda: **300/300 tanildi,
0 noto'g'ri shaxs, 300 begonadan 0 qabul.**

---

## Agent endpointlari (qurilma tokeni)

Autentifikatsiya: `Authorization: Bearer <qurilma tokeni>`. Xodim JWT'si emas —
agent oylab ishlaydi. Token bazada SHA-256 xeshi bo'lib yotadi va
`hotel_id` ga bog'langan; qidiruv doirasi ham shu yerdan keladi, ya'ni bir
mehmonxona agenti boshqasining mehmonlarini hech qachon ko'rmaydi.

### `GET /vision/health`
Tiriklik tekshiruvi. Ataylab tokenli: agent uchun "server ishlayaptimi" va
"tokenim hali yaroqlimi" bir xil savol.

### `POST /vision/events`
Asosiy yo'l. `multipart/form-data`: `camera_id`, `capture_id`, `track_uid`,
`timestamp`, `confidence`, `quality_score`, `device_id`, `metadata` (JSON) va
ixtiyoriy `image`.

`metadata.recognition` ichida:

```json
{
  "model": "sface_2021dec",
  "dim": 128,
  "template": "<base64 float32 LE, 684 belgi>",
  "samples": ["<base64>", "..."],
  "cohesion": 0.91,
  "sample_count": 5,
  "dropped": 1
}
```

`template` — bir epizoddagi kadrlardan yig'ilgan yakuniy vektor;
`dropped` — kelishmagan kadrlar soni (odatda orqadan o'tgan odam).

Vektor bo'lmasa va rasm bo'lsa — server o'zi hisoblaydi (zaxira yo'l, sekin).
Model nomi mos kelmasa so'rov ochiq rad etiladi: turli model vektorlarini
solishtirish jimgina noto'g'ri natija berardi.

**Javob:**

```json
{
  "status": "recognized",
  "sighting_id": "...",
  "guest": {
    "guest_id": "...", "name": "Aziz Karimov",
    "phone": "+998901234567", "has_active_reservation": true
  },
  "similarity": 0.81, "margin": 0.19, "candidates": 1420, "learned": false
}
```

| `status` | Ma'nosi |
|---|---|
| `recognized` | Ishonchli moslik |
| `uncertain` | Ehtimol, lekin ishonchsiz — ko'rsating, ish qilmang |
| `unknown` | Indeksda mos keluvchi yo'q |
| `duplicate` | Bu `track_uid` allaqachon qayd etilgan (offline navbat qayta yubordi) |

`track_uid` UNIQUE — offline navbat bir epizodni qayta yuborsa panel bir
odamni ikki marta ko'rsatmaydi.

### `POST /vision/events/json`
Rasmsiz, faqat vektor. ~700 bayt trafik.

### `POST /vision/sightings/{id}/thumbnail`
Agentning `send_image: unknown_only` rejimining ikkinchi yarmi: surat faqat
server tanimagan odam uchun keladi. Idempotent va kechirimli — agent buni
qayta urinmaydi.

---

## Panel endpointlari (xodim tokeni)

| Endpoint | Ruxsat | Vazifasi |
|---|---|---|
| `GET /vision/sightings` | `guest.view` | Oxirgi ko'rinishlar; `minutes`, `limit`, `include_acknowledged`, `only_matched` |
| `GET /vision/sightings/{id}/image` | `guest.view` | Paneldagi yuz surati |
| `POST /vision/sightings/{id}/ack` | `guest.view` | Ko'rib chiqildi — paneldan olinadi |
| `POST /vision/sightings/{id}/enroll` | `guest.update` | Tanilmaganni mehmonga biriktirish |
| `GET /vision/guests/{id}/face` | `guest.view` | Yuz profili holati |
| `DELETE /vision/guests/{id}/face` | `guest.update` | Biometriyani butunlay o'chirish |
| `GET /vision/stats` | `guest.view` | Indeks holati, shablonlar, kameralar |
| `GET,POST /vision/devices` | `employee.manage` | Qurilmalar; `DELETE /devices/{id}` bekor qiladi |

**Yangi ruxsat kodi qo'shilmadi** — mavjud `guest.view`, `guest.update` va
`employee.manage` ishlatiladi, ya'ni xodim shablonlari o'zgarishsiz ishlayveradi.

`GET /vision/sightings` og'ir `thumbnail` ustunini tanlamaydi va indekslangan
`(hotel_id, seen_at)` bo'yicha ketadi — panel uni bir necha soniyada bir marta
so'rab tursa ham arzon. WebSocket ataylab ishlatilmadi: panel bir necha soniya
kechikishga bardosh beradi va polling mavjud TanStack Query bilan bir qatorda
ishlaydi.

---

## Rozilik va saqlash muddati

- Shablon **faqat** `enroll` orqali, `consent: true` bilan yaratiladi.
  Rozilik `guests.face_consent_at` da yozib qo'yiladi.
- `DELETE /vision/guests/{id}/face` hamma narsani o'chiradi: shablonlar,
  ko'rinishlardagi vektorlar va suratlar. Rozilikni qaytarib olish — huquqiy
  talab, shuning uchun bu amal to'liq va qaytarilmas.
- Ko'rinishlar **12 soatdan keyin** o'chadi (`SIGHTING_TTL_HOURS`).
  Tozalash rejalashtiruvchisi `AUTO_CHECKOUT_ENABLED` dan **mustaqil** ishlaydi:
  u bron chiqishini emas, ma'lumot saqlash muddatini ta'minlaydi.
- Rasm hech qachon shablon sifatida saqlanmaydi — faqat vektor.

---

## O'rnatish

```bash
# 1. Migratsiya
alembic upgrade head

# 2. Kamera agenti uchun token (bir marta ko'rsatiladi)
python -m scripts.create_vision_device --hotel <HOTEL_ID> --name "Qabulxona PC"

# 3. Agent mashinasida
gohotels-vision download-model --recognition     # ~37 MB
gohotels-vision secrets set api_token            # yuqoridagi token
```

Agent `config.yaml` sida:

```yaml
server:
  base_url: https://api.gohotels.uz
  upload_path: /api/v1/vision/events
  health_path: /api/v1/vision/health
  api_token: ${keyring:api_token}

recognition:
  enabled: true
  samples_per_track: 5
  send_image: always        # always | unknown_only | never
```

---

## Diagnostika

`GET /vision/stats` indeks qurilganmi, nechta shablon bor va u eskirganmi
ko'rsatadi. Tanish ishlamayotgan bo'lsa tekshirish tartibi:

1. `stats.profiles` — umuman shablon bormi;
2. Kelayotgan ko'rinishlarning `quality_score` va `similarity` qiymatlari;
3. `sample_count` va `cohesion` — past kogeziya kadrlar bir odamga tegishli
   emasligini bildiradi;
4. Agent loglarida `face_pixels` — **eng ko'p uchraydigan sabab shu**. SFace
   112x112 kutadi; 50-60 px dan kengaytirilgan yuz kuchsiz vektor beradi.
   Bu dasturiy emas, fizik muammo: kamerani yaqinlashtirish yoki zoom kerak.
