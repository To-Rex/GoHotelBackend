"""Sun'iy intellekt yordamchisi ("Yordamchi" sahifasi).

Tuzilishi:

* `crypto` — API kalitini shifrlash (Fernet, SECRET_KEY dan).
* `providers` — ChatGPT (OpenAI), Claude (Anthropic), DeepSeek, Deploy.com
  va OpenAI bilan mos boshqa xizmatlar; hammasi bitta interfeys orqali.
* `config_service` — sozlama, kim ishlata olishi, limitlar, token sarfi.
* `catalog` — AI chaqira oladigan API: o'qish uchun ochiq yo'llar va
  YOZISH uchun registr (tasdiq + orqaga qaytarish qoidalari bilan).
* `executor` — API ni JARAYON ICHIDA xodimning O'Z tokeni bilan chaqirish:
  ruxsat, filial, ish vaqti — hammasi oddiy so'rovdagidek tekshiriladi.
* `results` / `render` / `excel` — natijani ixchamlash (token tejash),
  jadval, grafik, ko'rsatkich, Excel bloklari.
* `safety` — xavfli so'rovlarni aniqlash (jurnal uchun).
* `prompt` / `tools` / `agent` — suhbat navbati: model, asboblar, oqim.
* `actions_service` — tasdiqlash, rad etish, orqaga qaytarish.
* `plans_service` — rejalar va eslatmalar.

Asosiy xavfsizlik qoidasi: AI bazaga to'g'ridan-to'g'ri tegmaydi va
hech narsani o'zi yozmaydi — yozuvchi amalni faqat TAKLIF qiladi, xodim
tasdiqlaganda o'sha xodim huquqi bilan API orqali bajariladi. O'chirish,
sozlamalar, foydalanuvchilar va boshqaruv paneli registrda yo'q.
"""
