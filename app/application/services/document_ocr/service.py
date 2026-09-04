"""Hujjat skaneri: rasmdan mehmon ma'lumotlarini ajratish.

Qaror daraxti ataylab shunday tuzilgan — tezlik va ishonchlilik bir-biriga
qarshi qo'yilmasin:

  1. MRZ BANDI, deteksiyasiz. MRZ hujjatning pastida, qatorlari ma'lum, shuning
     uchun eng qimmat bosqich — matn qutilarini izlash — butunlay tashlab
     yuboriladi: bandni kesib, 2-3 qatorni tanish modeliga berish kifoya.
     Nazorat raqamlari toza o'tsa, natija shu yerda tugaydi (eng tez yo'l).
  2. TO'LIQ O'TISH. MRZ o'qilmasa, taxmin bilan tuzatilgan bo'lsa yoki hujjat
     old tomoni bo'lsa — butun rasm bo'yicha deteksiya + tanish ishlaydi.
     Bosma tomondan olingan qiymat MRZ'dagi taxminni MUSTAQIL tasdiqlaydi:
     ikkalasi bir xil chiqsa, taxmin ham ishonchli deb qabul qilinadi.

Natija — frontenddagi `ScannedDoc` shakli, ya'ni brauzerdagi mahalliy OCR bilan
bir xil shartnoma; frontend server javobini xuddi mahalliy natija kabi ko'radi.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import cv2
import numpy as np

from . import engine, mrz, verify, visual

logger = logging.getLogger(__name__)

#: Kirish rasmini shu kenglikka keltiramiz. Kattaroq rasm aniqlikni oshirmaydi
#: (matn allaqachon tanish modeli talab qiladigan balandlikdan katta), faqat
#: deteksiyani sekinlashtiradi.
MAX_WIDTH = 1600
MIN_WIDTH = 500

#: Laplas dispersiyasi shundan past bo'lsa rasm xira deb qaytariladi.
#: Chegara ATAYLAB juda past: matnli hujjatda odatda 100+ chiqadi, faqat
#: chinakam silkinib olingan kadr shundan pastga tushadi. Xira kadrni
#: o'qishga urinishdan ko'ra foydalanuvchidan qayta olishni so'rash
#: aniqlikni ko'proq oshiradi — xato o'qilgan maydonni hech kim sezmasligi
#: mumkin, qayta olingan tiniq kadr esa to'g'ri o'qiladi.
BLUR_THRESHOLD = 15.0

_MRZ_BANDS = {
    # (yuqori chegara, qatorlar soni) — hujjat turiga qarab
    "ID_CARD": [(0.55, 3), (0.42, 3)],
    "PASSPORT": [(0.68, 2), (0.55, 2)],
}


def _decode(image_bytes: bytes) -> np.ndarray:
    image = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("BAD_IMAGE")
    height, width = image.shape[:2]
    if width < MIN_WIDTH or height < 100:
        raise ValueError("IMAGE_TOO_SMALL")
    if width > MAX_WIDTH:
        scale = MAX_WIDTH / width
        image = cv2.resize(image, (MAX_WIDTH, int(round(height * scale))), interpolation=cv2.INTER_AREA)

    # Silkinib olingan kadr eng ko'p xatoning manbai: undan o'qilgan qiymat
    # ko'pincha "deyarli to'g'ri" chiqadi va tekshiruvdan sezilmay o'tadi.
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    if float(cv2.Laplacian(gray, cv2.CV_64F).var()) < BLUR_THRESHOLD:
        raise ValueError("IMAGE_BLURRY")
    return image


def rectify(image: np.ndarray, document_type: str) -> np.ndarray:
    """Hujjatni to'rtburchakka yozadi — faqat ishonchli konturda.

    Qiyshiq olingan kadr MRZ qatorlarini bir-biriga qo'shib yuboradi, shuning
    uchun bu bosqich aniqlikka sezilarli ta'sir qiladi. Kontur ishonchsiz
    bo'lsa, asl rasm qaytariladi — noto'g'ri kesish OCR uchun eng yomon holat.
    """
    expected_aspect = 125 / 88 if document_type == "PASSPORT" else 85.6 / 54
    height, width = image.shape[:2]
    scale = min(1.0, 900 / max(height, width))
    small = cv2.resize(image, (int(width * scale), int(height * scale)))
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 45, 140)
    edges = cv2.dilate(edges, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    image_area = small.shape[0] * small.shape[1]
    best = None
    best_score = 0.0
    for contour in contours:
        perimeter = cv2.arcLength(contour, True)
        approximated = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
        if len(approximated) != 4 or not cv2.isContourConvex(approximated):
            continue
        area = abs(cv2.contourArea(approximated))
        if area < image_area * 0.25:
            continue
        points = approximated.reshape(4, 2).astype(np.float32)
        ordered = engine.order_box(points)
        box_width = (
            np.linalg.norm(ordered[0] - ordered[1]) + np.linalg.norm(ordered[3] - ordered[2])
        ) / 2
        box_height = (
            np.linalg.norm(ordered[0] - ordered[3]) + np.linalg.norm(ordered[1] - ordered[2])
        ) / 2
        if box_height < 1:
            continue
        aspect = box_width / box_height
        if not 0.8 <= aspect <= 2.6:
            continue
        aspect_fit = max(0.0, 1 - abs(np.log(aspect / expected_aspect)))
        score = area / image_area * 0.7 + aspect_fit * 0.3
        if score > best_score:
            best_score = score
            best = ordered / scale

    if best is None or best_score < 0.45:
        return image

    target_width = int(min(MAX_WIDTH, max(600, np.linalg.norm(best[0] - best[1]))))
    target_height = int(max(380, target_width / expected_aspect))
    target = np.array(
        [[0, 0], [target_width, 0], [target_width, target_height], [0, target_height]],
        dtype=np.float32,
    )
    matrix = cv2.getPerspectiveTransform(best.astype(np.float32), target)
    return cv2.warpPerspective(
        image, matrix, (target_width, target_height), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )


def mrz_band_lines(image: np.ndarray, top_fraction: float, count: int) -> list[np.ndarray]:
    """MRZ bandini kesib, matn qatorlariga ajratadi (gorizontal proyeksiya).

    Deteksiya modelini ishga tushirmaydi — bu MRZ yo'lini bir necha barobar
    tezlashtiradigan asosiy hiyla.
    """
    height, width = image.shape[:2]
    band = image[int(height * top_fraction) :, int(width * 0.015) : int(width * 0.985)]
    if band.shape[0] < 24:
        return []
    gray = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, 15
    )
    profile = binary.sum(axis=1) / 255.0
    if not profile.size or profile.max() <= 0:
        return []
    threshold = max(3.0, float(profile.max()) * 0.12)

    runs: list[tuple[int, int]] = []
    start: int | None = None
    for y, value in enumerate(profile):
        if value >= threshold and start is None:
            start = y
        elif value < threshold and start is not None:
            if y - start >= band.shape[0] * 0.035:
                runs.append((start, y))
            start = None
    if start is not None:
        runs.append((start, len(profile)))

    runs = sorted(runs, key=lambda run: run[1] - run[0], reverse=True)[:count]
    runs.sort()
    padding = max(2, band.shape[0] // 60)
    return [
        band[max(0, top - padding) : min(band.shape[0], bottom + padding)]
        for top, bottom in runs
    ]


def _clahe(image: np.ndarray) -> np.ndarray:
    """Kontrasti past kadr uchun mahalliy kontrast kuchaytirish.

    Xira yoritilgan xonada olingan MRZ belgilar fondan ajralmay, model
    ularni chalkashtiradi. CLAHE har kichik hududning o'z kontrastini
    ko'taradi — soyaga tushgan band ham o'qiladigan bo'ladi.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    enhanced = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(gray)
    return cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)


def read_mrz_fast(image: np.ndarray, document_type: str) -> mrz.MrzResult | None:
    """MRZ'ni deteksiyasiz o'qishga urinadi.

    Ikki bosqich: avval asl kadr; nazorat raqamlari toza o'tmasa —
    kontrasti kuchaytirilgan nusxada QAYTA. Ikkinchi urinish faqat kerak
    bo'lgandagina ishlaydi, shuning uchun toza kadrlar avvalgidek tez
    o'qiladi, muammolilari esa endi ko'proq tiklanadi.
    """
    best: mrz.MrzResult | None = None
    for candidate in (image, None):
        source = candidate if candidate is not None else _clahe(image)
        for top_fraction, count in _MRZ_BANDS.get(document_type, _MRZ_BANDS["ID_CARD"]):
            crops = mrz_band_lines(source, top_fraction, count)
            if len(crops) < count:
                continue
            texts = [text for text, _confidence in engine.recognize(crops)]
            result = mrz.parse_lines(texts)
            if result is None:
                continue
            if result.verified:
                return result
            if best is None or result.score > best.score:
                best = result
    return best


@dataclass
class SideReading:
    """Bitta rasmdan o'qilgan hamma narsa."""

    side: str
    mrz: mrz.MrzResult | None = None
    printed: visual.VisualResult | None = None

    @property
    def mrz_fields(self) -> dict:
        return {k: v for k, v in (self.mrz.fields.items() if self.mrz else []) if v}

    @property
    def printed_fields(self) -> dict:
        return {k: v for k, v in (self.printed.fields.items() if self.printed else []) if v}


def read_side(image_bytes: bytes, document_type: str, side: str) -> SideReading:
    """Bitta rasmni o'qiydi: MRZ (bo'lsa) va bosma maydonlar.

    ID kartaning old tomonida MRZ yo'q, orqasida esa bosma maydon deyarli yo'q;
    passport sahifasida ikkalasi ham bor. Shuning uchun har tomonda ikkala
    manba ham izlanadi va qaysi biri chiqsa, o'sha ishlatiladi.
    """
    image = rectify(_decode(image_bytes), document_type)
    reading = SideReading(side=side)

    if side != "front":
        # Eng tez yo'l: MRZ joyi ma'lum, deteksiya kerak emas
        reading.mrz = read_mrz_fast(image, document_type)

    # Passportda bosma maydonlar ham kerak (otasining ismi, fuqarolik), ID
    # kartaning orqasida esa MRZ tez yo'ldan chiqmasa qutilar bo'yicha izlanadi.
    need_regions = side == "front" or document_type == "PASSPORT" or (
        reading.mrz is None or not reading.mrz.verified
    )
    if need_regions:
        regions = engine.read_regions(image)
        if regions:
            reading.printed = visual.parse_regions(regions)
            if side != "front":
                texts = [r["text"] for r in regions if mrz.looks_like_mrz(r["text"])]
                from_regions = mrz.parse_lines(texts) if texts else None
                if from_regions and (
                    reading.mrz is None or from_regions.score > reading.mrz.score
                ):
                    reading.mrz = from_regions
    return reading


def _confidence_for(name: str, sources: list[str], verified: bool) -> float:
    if len(sources) > 1:
        return 0.99
    if sources and sources[0] == "MRZ":
        return 0.95 if verified else 0.8
    return 0.65


def scan_document(images: dict[str, bytes], document_type: str = "ID_CARD") -> dict:
    """Hujjatni to'liq o'qiydi va tekshiradi.

    `images` — {"front": ..., "back": ...} (ID karta) yoki {"passport": ...}.
    ID kartaning ikkala tomoni BIR SO'ROVDA kelgani muhim: shundagina ular
    bir hujjatga tegishli ekanini tekshirish va bir tomondagi ma'lumotni
    ikkinchisi bilan tasdiqlash mumkin.

    Raises:
        ValueError("BAD_IMAGE" | "IMAGE_TOO_SMALL" | "NO_TEXT")
    """
    readings = {side: read_side(data, document_type, side) for side, data in images.items()}
    if not readings:
        raise ValueError("BAD_IMAGE")

    # MRZ qaysi tomonda bo'lsa — o'sha yetakchi manba
    mrz_reading = next(
        (r for r in readings.values() if r.mrz is not None),
        None,
    )
    mrz_result = mrz_reading.mrz if mrz_reading else None
    mrz_fields = mrz_reading.mrz_fields if mrz_reading else {}

    # Bosma manba: ID kartada bu old tomon, passportda esa o'sha sahifaning o'zi
    printed_reading = readings.get("front") or readings.get("passport") or mrz_reading
    printed_fields = printed_reading.printed_fields if printed_reading else {}

    if not mrz_fields and not printed_fields:
        raise ValueError("NO_TEXT")

    verification, fields = verify.cross_check(mrz_fields, printed_fields)

    # Taxmin bilan tiklangan MRZ maydonini faqat bosma tomon tasdiqlay oladi
    guessed = list(mrz_result.guessed_fields) if mrz_result else []
    confirmed_guesses = [
        name for name in guessed if len(verification.agreement.get(name, [])) > 1
    ]
    unconfirmed_guesses = [name for name in guessed if name not in confirmed_guesses]

    if mrz_result:
        if mrz_result.checks_ok:
            verification.add(
                "mrz.checkdigits",
                "MRZ nazorat raqamlari to‘g‘ri",
                verify.OK,
                f"{mrz_result.mrz_format} · {mrz_result.checks_passed}/{mrz_result.checks_total}",
            )
        else:
            verification.add(
                "mrz.checkdigits",
                "MRZ nazorat raqamlari mos kelmadi",
                verify.FAIL,
                f"{mrz_result.checks_passed}/{mrz_result.checks_total} to‘g‘ri",
            )
        for name in confirmed_guesses:
            verification.add(
                f"mrz.repaired.{name}",
                f"{verify.FIELD_LABELS.get(name, name)}: tiklandi va bosma tomon tasdiqladi",
                verify.OK,
            )
        for name in unconfirmed_guesses:
            verification.add(
                f"mrz.repaired.{name}",
                f"{verify.FIELD_LABELS.get(name, name)}: nazorat raqami bo‘yicha tiklandi",
                verify.WARN,
                "Mustaqil manba tasdiqlamadi — qiymatni hujjat bilan solishtiring",
            )
    else:
        verification.add(
            "mrz.present",
            "MRZ o‘qilmadi",
            verify.WARN,
            "Ma’lumot faqat hujjatning bosma tomonidan olindi",
        )

    pinfl_ok = verify.check_pinfl(verification, fields)
    verify.check_dates(verification, fields)
    verify.check_document_number(verification, fields)

    if document_type == "ID_CARD" and "front" in readings and "back" in readings:
        verify.check_sides_match(
            verification,
            readings["front"].printed_fields,
            {**readings["back"].mrz_fields, **readings["back"].printed_fields},
        )

    verified = not verification.failed and bool(mrz_result) and mrz_result.checks_ok and not unconfirmed_guesses

    confidence = {
        name: _confidence_for(name, verification.agreement.get(name, []), verified)
        for name in fields
        if name in verify.FIELD_LABELS
    }

    document: dict = {
        "documentType": document_type,
        "source": "merged" if mrz_fields and printed_fields else ("mrz" if mrz_fields else "visual"),
        "verified": verified,
        "requiresReview": not verified,
        "scannedSides": list(readings.keys()),
        "warnings": [c.detail or c.label for c in verification.failed + verification.warned],
        "checks": verification.as_list(),
        "fieldConfidence": confidence,
        "engine": "server",
    }
    if mrz_result:
        document["mrzFormat"] = mrz_result.mrz_format
    if pinfl_ok and fields.get("personalNumber"):
        document["pinflVerified"] = True
    for name in (
        "firstName", "lastName", "patronymic", "birthDate", "documentNumber",
        "personalNumber", "nationality", "issuingCountry", "expiryDate", "sex",
    ):
        value = fields.get(name)
        if value:
            document[name] = value
    return document


def scan(image_bytes: bytes, document_type: str = "ID_CARD", side: str = "front") -> dict:
    """Bitta rasm uchun qisqa yo'l (eski chaqiruvlar bilan moslik uchun)."""
    return scan_document({side: image_bytes}, document_type)
