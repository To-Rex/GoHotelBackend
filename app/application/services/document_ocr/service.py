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

import cv2
import numpy as np

from . import engine, mrz, visual

logger = logging.getLogger(__name__)

#: Kirish rasmini shu kenglikka keltiramiz. Kattaroq rasm aniqlikni oshirmaydi
#: (matn allaqachon tanish modeli talab qiladigan balandlikdan katta), faqat
#: deteksiyani sekinlashtiradi.
MAX_WIDTH = 1600
MIN_WIDTH = 500

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


def read_mrz_fast(image: np.ndarray, document_type: str) -> mrz.MrzResult | None:
    """MRZ'ni deteksiyasiz o'qishga urinadi."""
    best: mrz.MrzResult | None = None
    for top_fraction, count in _MRZ_BANDS.get(document_type, _MRZ_BANDS["ID_CARD"]):
        crops = mrz_band_lines(image, top_fraction, count)
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


def _merge(
    mrz_result: mrz.MrzResult | None,
    visual_result: visual.VisualResult | None,
    document_type: str,
    side: str,
) -> dict:
    """MRZ va bosma tomon natijalarini birlashtiradi.

    MRZ ustun turadi — uning nazorat raqamlari bor. Lekin MRZ'dagi TAXMINIY
    tuzatishni faqat bosma tomondan kelgan bir xil qiymat tasdiqlay oladi:
    ular mustaqil manba, shuning uchun ikkalasining mos kelishi tasodif emas.
    """
    fields: dict = {}
    confidence: dict = {}
    warnings: list[str] = []
    confirmed_guesses: set[str] = set()

    visual_fields = dict(visual_result.fields) if visual_result else {}
    mrz_fields = {k: v for k, v in (mrz_result.fields.items() if mrz_result else []) if v}

    for name, value in visual_fields.items():
        if value:
            fields[name] = value
            confidence[name] = 0.72 if name in (visual_result.by_pattern if visual_result else set()) else 0.6

    for name, value in mrz_fields.items():
        guessed = bool(mrz_result and name in mrz_result.guessed_fields)
        if guessed and visual_fields.get(name) and visual_fields[name] != value:
            # Ikki manba qarama-qarshi — hech birini ishonchli deb bo'lmaydi
            warnings.append(f"{name}: MRZ va bosma tomon mos kelmadi")
            confidence[name] = 0.45
            continue
        if guessed and visual_fields.get(name) == value:
            confirmed_guesses.add(name)
        fields[name] = value
        confidence[name] = 0.99 if not guessed else (0.95 if name in confirmed_guesses else 0.7)

    unconfirmed = [
        name for name in (mrz_result.guessed_fields if mrz_result else []) if name not in confirmed_guesses
    ]

    # JSHSHIR faqat o'z tuzilishidan o'tgan bo'lsa qoladi
    personal = fields.get("personalNumber")
    pinfl_verified = bool(personal and visual.valid_pinfl(personal))
    if personal and not pinfl_verified:
        fields.pop("personalNumber", None)
        confidence.pop("personalNumber", None)

    # JSHSHIR ichidagi tug'ilgan sana mustaqil tekshiruv beradi
    if pinfl_verified:
        from_pinfl = visual.pinfl_birth_date(personal)
        if from_pinfl and fields.get("birthDate") and fields["birthDate"] != from_pinfl:
            warnings.append("Tug‘ilgan sana JSHSHIR ichidagi sanaga mos kelmadi")
        elif from_pinfl and not fields.get("birthDate"):
            fields["birthDate"] = from_pinfl
            confidence["birthDate"] = 0.9

    verified = bool(mrz_result and mrz_result.checks_ok and not unconfirmed)
    if mrz_result and mrz_result.checks_ok and unconfirmed:
        warnings.append(
            "MRZ'dagi " + ", ".join(unconfirmed) + " nazorat raqami bo‘yicha tiklandi — tekshiring"
        )
    if mrz_result and not mrz_result.checks_ok:
        warnings.append("MRZ nazorat raqamlari to‘liq mos kelmadi — ma’lumotni tekshiring")
    if not mrz_result:
        warnings.append("MRZ o‘qilmadi — ma’lumot hujjatning bosma tomonidan olindi")

    source = "merged" if mrz_result and visual_result and visual_result.filled else ("mrz" if mrz_result else "visual")
    document: dict = {
        "documentType": document_type,
        "source": source,
        "verified": verified,
        "requiresReview": not verified,
        "scannedSides": [side],
        "warnings": warnings,
        "fieldConfidence": confidence,
        "engine": "server",
    }
    if mrz_result:
        document["mrzFormat"] = mrz_result.mrz_format
    if pinfl_verified:
        document["pinflVerified"] = True
    for name in (
        "firstName", "lastName", "birthDate", "documentNumber", "personalNumber",
        "nationality", "issuingCountry", "expiryDate", "sex",
    ):
        value = fields.get(name)
        if value:
            document[name] = value
    return document


def scan(image_bytes: bytes, document_type: str = "ID_CARD", side: str = "front") -> dict:
    """Rasmni o'qib, `ScannedDoc` shaklidagi natija qaytaradi.

    Raises:
        ValueError("BAD_IMAGE" | "IMAGE_TOO_SMALL") — rasm yaroqsiz.
    """
    image = _decode(image_bytes)
    image = rectify(image, document_type)

    mrz_result: mrz.MrzResult | None = None
    visual_result: visual.VisualResult | None = None

    if side != "front":
        mrz_result = read_mrz_fast(image, document_type)
        if mrz_result is not None and mrz_result.verified:
            # Eng tez yo'l: nazorat raqamlari toza, taxmin yo'q — tugadi
            return _merge(mrz_result, None, document_type, side)

    regions = engine.read_regions(image)
    if regions:
        visual_result = visual.parse_regions(regions)
        if side != "front":
            texts = [region["text"] for region in regions if mrz.looks_like_mrz(region["text"])]
            from_regions = mrz.parse_lines(texts) if texts else None
            if from_regions and (mrz_result is None or from_regions.score > mrz_result.score):
                mrz_result = from_regions

    if mrz_result is None and visual_result is None:
        raise ValueError("NO_TEXT")
    return _merge(mrz_result, visual_result, document_type, side)
