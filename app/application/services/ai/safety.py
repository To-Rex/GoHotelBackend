"""Xavfli so'rovlarni aniqlash (jurnal uchun) — uch manba.

1. `heuristic` — xodim xabaridagi aniq belgilar: ma'lumotni ommaviy
   o'chirish/tozalash, qo'rqitish va bosim ("serveringiz yonib ketadi"),
   yo'riqnomani chetlab o'tishga urinish, huquq yoki maxfiy ma'lumot
   so'rash. Lotin, kirill (o'zbek/rus) va ingliz tilida.
2. `model` — AI o'zi `flag_incident` asbobi bilan belgilaydi (nozik
   holatlar, masalan, boshqa xodimning maoshini bilishga urinish).
3. `server` — AI yopiq yo'lga yoki registrda yo'q yozuvchi amalga
   urinsa (masalan DELETE) — bu server tomonda to'xtatiladi va yoziladi.

Muhim: aniqlash HECH NARSANI ruxsat qilmaydi va bloklamaydi — himoya
arxitekturada (AI faqat registrdagi amalni TAKLIF qiladi, xodim
tasdiqlaydi, o'chirish umuman yo'q). Bu modul faqat superadmin va
sozlovchi ko'radigan jurnal uchun.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

CATEGORIES = (
    "data_destruction", "manipulation", "prompt_injection", "privilege", "secrets",
    "out_of_role", "other",
)

CATEGORY_LABELS = {
    "data_destruction": "Ma'lumotni o'chirish/buzish",
    "manipulation": "Qo'rqitish yoki bosim",
    "prompt_injection": "Qoidalarni chetlab o'tish",
    "privilege": "Huquqni oshirish",
    "secrets": "Maxfiy ma'lumot",
    "out_of_role": "Rolidan tashqari so'rov",
    "other": "Boshqa",
}


@dataclass(frozen=True)
class Flag:
    category: str
    reason: str


def _rx(*parts: str) -> re.Pattern:
    return re.compile("|".join(parts), re.IGNORECASE | re.UNICODE)


# Apostrof variantlari: ' ʻ ʼ ` ‘ ’
_A = "['ʻʼ`‘’]?"

_RULES: tuple[tuple[str, str, re.Pattern], ...] = (
    (
        "data_destruction",
        "Ma'lumotlarni ommaviy o'chirish yoki tozalash so'raldi",
        _rx(
            rf"\b(hamma|barcha|butun|jami)\w*\b.{{0,40}}\b(o{_A}chir|tozala|yo{_A}q qil|sil|o{_A}ldir)",
            rf"\b(baza|bazani|ma{_A}lumotlar\w*|database|db)\b.{{0,30}}\b(o{_A}chir|tozala|yo{_A}q qil|buz)",
            rf"\b(o{_A}chir|tozala)\w*\b.{{0,30}}\b(hamma|barcha|butun|bazani|hammasini)",
            r"\b(drop\s+(table|database)|truncate\s+table|delete\s+from|rm\s+-rf)\b",
            r"\b(delete|wipe|erase|purge)\b.{0,20}\b(all|every|database|everything)\b",
            r"(удали|очисти|сотри|снеси)\w*.{0,30}(все|всё|базу|данные)",
            r"(ўчир|тозала)\w*.{0,30}(ҳамма|барча|базани)",
        ),
    ),
    (
        "manipulation",
        "Qo'rqitish yoki bosim bilan majburlash urinishi",
        _rx(
            r"yonib\s*ket", r"portla", r"o['ʻʼ`‘’]?ldir(aman|amiz|ish)", r"sudga\s+ber",
            r"ishdan\s+(haydat|bo['ʻʼ`‘’]?shat)", r"aks\s+holda", r"bajarmasang\b", r"bo['ʻʼ`‘’]?lmasa\s+(sen|server)",
            r"\b(or else|i will (destroy|kill|sue|report)|your server will)\b",
            r"(сгорит|взорв|убью|уничтож|иначе\s)", r"(ёниб\s*кет|портла)",
            r"\bshoshilinch\b.{0,40}\b(qoidani|ruxsatsiz|tasdiqsiz)",
        ),
    ),
    (
        "prompt_injection",
        "Yordamchi qoidalarini chetlab o'tishga urinish",
        _rx(
            r"\b(ignore|forget|disregard)\b.{0,30}\b(previous|prior|above|all|your)\b.{0,20}\b(instruction|rule|prompt)",
            r"\b(system prompt|developer mode|jailbreak|you are now|act as (an? )?(admin|root|developer)|DAN mode)\b",
            rf"(oldingi|avvalgi|barcha)\s+(ko{_A}rsatma|qoida|buyruq)\w*\s+(unut|bekor|e{_A}tibor\s*berma)",
            r"(qoidalaringni|rolingni|yo['ʻʼ`‘’]?riqnomangni)\s+(unut|bekor|ko['ʻʼ`‘’]?rsat)",
            r"\bsen\s+endi\b.{0,30}\b(admin|cheklovsiz|erkin)",
            r"(забудь|игнорируй).{0,30}(инструкц|правил)", r"(системн\w+ промпт|режим разработчика)",
        ),
    ),
    (
        "privilege",
        "O'z huquqidan yuqori ruxsat yoki rol so'raldi",
        _rx(
            rf"\b(meni|o{_A}zimni|menga)\b.{{0,30}}\b(admin|administrator|superadmin|sozlovchi|direktor)\w*\b.{{0,20}}\b(qil|ber|tayinla)",
            rf"\b(ruxsat|huquq|rol)\w*\b.{{0,20}}\b(ber|qo{_A}sh|oshir|o{_A}zgartir)\b.{{0,20}}\b(menga|o{_A}zimga)",
            r"\b(make me|give me|grant me)\b.{0,20}\b(admin|root|superuser|permission|access)\b",
            r"(сделай меня|дай мне).{0,20}(админ|права|доступ)",
        ),
    ),
    (
        "secrets",
        "Parol, kalit yoki boshqa maxfiy ma'lumot so'raldi",
        _rx(
            r"\b(parol|parolini|parolni|password|пароль)\w*\b",
            r"\b(api[\s_-]?key|api kalit|secret key|access token|refresh token|jwt)\b",
            r"\b(\.env|database_url|db parol|server parol|root parol|ssh)\b",
            r"(ключ api|токен доступа|секретн\w+ ключ)",
        ),
    ),
)

#: Bitta so'zli zararsiz holatlar — "parolni o'zgartirish qayerda?" kabi
#: o'z profili haqidagi savol maxfiy ma'lumot so'rovi emas
_BENIGN = _rx(
    rf"\b(parol\w*|password)\b.{{0,30}}\b(o{_A}zgartir|almashtir|tiklash|unutdim|qayerda|qanday)",
    r"(как|где).{0,20}(сменить|изменить|восстановить).{0,10}пароль",
)


def scan(text: str) -> list[Flag]:
    """Xodim xabaridagi xavfli belgilar (bir toifa — bir marta)."""
    t = (text or "").strip()
    if not t:
        return []
    flags: list[Flag] = []
    for category, reason, pattern in _RULES:
        if pattern.search(t):
            if category == "secrets" and _BENIGN.search(t):
                continue
            flags.append(Flag(category, reason))
    return flags


def clean_category(value: str | None) -> str:
    v = (value or "").strip().lower()
    return v if v in CATEGORIES else "other"


__all__ = ["CATEGORIES", "CATEGORY_LABELS", "Flag", "clean_category", "scan"]
