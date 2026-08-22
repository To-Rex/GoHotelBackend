"""PP-OCR (PaddleOCR) matn aniqlash va tanish dvigateli.

Modellar — PaddleOCR PP-OCRv3'ning ONNX ko'rinishi (Apache-2.0), repozitoriy
ichida `app/assets/ocr/` da yotadi. Ular ataylab repozitoriyga qo'shilgan:
serverda internet bo'lmasa ham, tashqi havola o'lgan taqdirda ham skaner
ishlayveradi va deploy natijasi takrorlanadigan bo'ladi.

Bog'liqlik — faqat `onnxruntime`. PaddleOCR'ning o'zi ham, RapidOCR ham
`opencv-python` (GUI) ni tortadi, u serverda `libGL` talab qiladi va mavjud
`opencv-python-headless` bilan to'qnashadi; shuning uchun det/rec bosqichlari
shu yerda qo'lda yozilgan — ular baribir hujjat uchun moslashtirilishi kerak
edi (MRZ deteksiyasiz o'qiladi, quyidagi `recognize` ga qarang).

Tanish lug'ati ONNX faylining metama'lumotida saqlanadi, shuning uchun
alohida lug'at fayli kerak emas.
"""
from __future__ import annotations

import logging
import os
import threading

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# .../app/application/services/document_ocr/engine.py -> .../app/assets/ocr
_APP_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
_ASSET_DIR = os.path.join(_APP_DIR, "assets", "ocr")
MODEL_DIR = os.getenv("GOHOTEL_OCR_MODEL_DIR") or _ASSET_DIR
DET_PATH = os.path.join(MODEL_DIR, "ppocr_v3_det.onnx")
REC_PATH = os.path.join(MODEL_DIR, "ppocr_v3_rec.onnx")

# Tanish modeli 48 piksel balandlikdagi qatorlarni kutadi
REC_HEIGHT = 48
# Juda uzun qatorni bo'lib yubormaslik uchun keng chegara (MRZ 30-44 belgi)
REC_MAX_WIDTH = 1600
# Deteksiya kirishi: katta rasm sifatni oshirmaydi, faqat sekinlashtiradi
DET_LIMIT_SIDE = 736
DET_BIN_THRESHOLD = 0.3
DET_BOX_THRESHOLD = 0.5
DET_UNCLIP_RATIO = 1.7

_lock = threading.Lock()
_det_session = None
_rec_session = None
_charset: list[str] = []


def models_present() -> bool:
    return os.path.exists(DET_PATH) and os.path.exists(REC_PATH)


def engine_importable() -> bool:
    """onnxruntime o'rnatilganmi va modellar joyidami — sessiya ochmasdan."""
    if not models_present():
        return False
    try:
        import onnxruntime  # noqa: F401

        return True
    except Exception:
        return False


def _session(path: str):
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    # Ko'p oqim bu modellarda foyda bermaydi (kichik konvolyutsiyalar), lekin
    # bir nechta so'rov parallel kelganda serverni butunlay band qilib qo'yadi.
    options.intra_op_num_threads = max(1, min(2, (os.cpu_count() or 2)))
    options.inter_op_num_threads = 1
    return ort.InferenceSession(path, options, providers=["CPUExecutionProvider"])


def _ensure_loaded():
    """Modellarni bir marta yuklaydi.

    Yuklash HAMMASI-YOKI-HECH NARSA: tanish modeli 10.7 MB va u yuklanmay
    qolishi mumkin. Sessiyalar bittalab tayinlansa, keyingi chaqiruv "deteksiya
    tayyor" deb o'ylab, lug'ati bo'sh tanishga kirib ketadi va har bir hujjat
    bo'sh matn bilan qaytadi.
    """
    global _det_session, _rec_session, _charset
    if _det_session is not None and _rec_session is not None and _charset:
        return _det_session, _rec_session
    with _lock:
        if _det_session is None or _rec_session is None or not _charset:
            if not models_present():
                raise RuntimeError("OCR modellari topilmadi")
            logger.info("PP-OCR modellari yuklanmoqda…")
            detector = _session(DET_PATH)
            recognizer = _session(REC_PATH)
            characters = recognizer.get_modelmeta().custom_metadata_map.get("character", "")
            if not characters:
                raise RuntimeError("Tanish modelida lug'at topilmadi")
            # CTC: 0-indeks — blank, oxirida bo'sh joy belgisi
            _charset = ["", *characters.splitlines(), " "]
            _det_session = detector
            _rec_session = recognizer
            logger.info("PP-OCR tayyor (lug'at: %d belgi)", len(_charset))
    return _det_session, _rec_session


def warm_up() -> None:
    """Birinchi so'rov sekin bo'lmasligi uchun modellarni oldindan yuklaydi."""
    try:
        _ensure_loaded()
    except Exception as exc:  # noqa: BLE001
        logger.warning("PP-OCR isitilmadi: %s", exc)


# ------------------------------------------------------------------ deteksiya


def _det_input(image: np.ndarray) -> tuple[np.ndarray, float, float]:
    height, width = image.shape[:2]
    scale = min(DET_LIMIT_SIDE / max(height, width), 1.0)
    # Model kirishi 32 ga karrali bo'lishi shart
    new_h = max(32, int(round(height * scale / 32)) * 32)
    new_w = max(32, int(round(width * scale / 32)) * 32)
    resized = cv2.resize(image, (new_w, new_h))
    blob = resized.astype(np.float32) / 255.0
    blob -= np.array([0.485, 0.456, 0.406], dtype=np.float32)
    blob /= np.array([0.229, 0.224, 0.225], dtype=np.float32)
    blob = blob.transpose(2, 0, 1)[None]
    return blob, width / new_w, height / new_h


def _unclip(rect, ratio: float):
    """DB chiqargan "qisqartirilgan" to'rtburchakni asl matn o'lchamiga qaytaradi.

    DB modeli matn maydonini ataylab kichraytirib bashorat qiladi, shuning uchun
    uni kengaytirish shart. PaddleOCR pyclipper bilan HAR TOMONNI perpendikular
    ravishda `maydon * ratio / perimetr` ga suradi — to'rtburchak uchun bu shunchaki
    eni va bo'yiga 2*d qo'shish demakdir.

    Burchaklarni markazdan radial surish MUMKIN EMAS: uzun-ingichka qatorda
    burchak yo'nalishi deyarli gorizontal bo'lgani uchun balandlik kengaymay
    qoladi va harflarning usti-osti kesilib, tanish buziladi.
    """
    (center_x, center_y), (width, height), angle = rect
    perimeter = 2.0 * (width + height)
    if perimeter <= 0:
        return rect
    distance = width * height * ratio / perimeter
    return ((center_x, center_y), (width + 2 * distance, height + 2 * distance), angle)


def detect(image: np.ndarray) -> list[np.ndarray]:
    """Rasmdagi matn qutilarini topadi (har biri 4 nuqtali ko'pburchak)."""
    det_session, _ = _ensure_loaded()
    blob, scale_x, scale_y = _det_input(image)
    probability = det_session.run(None, {det_session.get_inputs()[0].name: blob})[0][0, 0]
    mask = (probability > DET_BIN_THRESHOLD).astype(np.uint8)
    contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    boxes: list[tuple[float, np.ndarray]] = []
    for contour in contours:
        if cv2.contourArea(contour) < 12:
            continue
        rect = cv2.minAreaRect(contour)
        if min(rect[1]) < 3:
            continue
        points = cv2.boxPoints(rect).astype(np.float32)

        # Quti ichidagi o'rtacha ehtimollik — ishonch bahosi
        score_mask = np.zeros(probability.shape, dtype=np.uint8)
        cv2.fillPoly(score_mask, [points.astype(np.int32)], 1)
        if not score_mask.any():
            continue
        score = float(probability[score_mask.astype(bool)].mean())
        if score < DET_BOX_THRESHOLD:
            continue

        enlarged = cv2.boxPoints(_unclip(rect, DET_UNCLIP_RATIO)).astype(np.float32)
        enlarged[:, 0] *= scale_x
        enlarged[:, 1] *= scale_y
        boxes.append((score, enlarged))

    return [box for _score, box in boxes]


def order_box(points: np.ndarray) -> np.ndarray:
    """Nuqtalarni chap-yuqori, o'ng-yuqori, o'ng-past, chap-past tartibida."""
    ordered = np.zeros((4, 2), dtype=np.float32)
    total = points.sum(axis=1)
    ordered[0] = points[np.argmin(total)]
    ordered[2] = points[np.argmax(total)]
    diff = np.diff(points, axis=1).ravel()
    ordered[1] = points[np.argmin(diff)]
    ordered[3] = points[np.argmax(diff)]
    return ordered


def crop_box(image: np.ndarray, points: np.ndarray) -> np.ndarray:
    """Qiyshiq qutini to'g'ri to'rtburchakka yozadi (perspektiv)."""
    ordered = order_box(points)
    width = int(
        max(np.linalg.norm(ordered[0] - ordered[1]), np.linalg.norm(ordered[3] - ordered[2]))
    )
    height = int(
        max(np.linalg.norm(ordered[0] - ordered[3]), np.linalg.norm(ordered[1] - ordered[2]))
    )
    width = max(width, 1)
    height = max(height, 1)
    target = np.array(
        [[0, 0], [width, 0], [width, height], [0, height]], dtype=np.float32
    )
    matrix = cv2.getPerspectiveTransform(ordered, target)
    crop = cv2.warpPerspective(
        image, matrix, (width, height), borderMode=cv2.BORDER_REPLICATE
    )
    # Tik yozilgan qutini yotqizamiz
    if height > 0 and width / height < 0.5:
        crop = cv2.rotate(crop, cv2.ROTATE_90_CLOCKWISE)
    return crop


# --------------------------------------------------------------------- tanish


def _rec_input(crop: np.ndarray) -> np.ndarray:
    if crop.ndim == 2:
        crop = cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)
    height, width = crop.shape[:2]
    target_w = max(8, min(REC_MAX_WIDTH, int(np.ceil(width * REC_HEIGHT / max(height, 1)))))
    resized = cv2.resize(crop, (target_w, REC_HEIGHT))
    blob = resized.astype(np.float32) / 255.0
    blob = (blob - 0.5) / 0.5
    return blob.transpose(2, 0, 1)


def _ctc_decode(logits: np.ndarray) -> tuple[str, float]:
    indices = logits.argmax(axis=1)
    confidences = logits.max(axis=1)
    characters: list[str] = []
    scores: list[float] = []
    previous = -1
    for position, index in enumerate(indices):
        if index != previous and index != 0:
            if index < len(_charset):
                characters.append(_charset[index])
                scores.append(float(confidences[position]))
        previous = int(index)
    return "".join(characters), float(np.mean(scores)) if scores else 0.0


def recognize(crops: list[np.ndarray], batch_size: int = 8) -> list[tuple[str, float]]:
    """Kesilgan matn qatorlarini o'qiydi.

    Deteksiyadan mustaqil: MRZ kabi joyi oldindan ma'lum matnlar uchun quti
    izlashning hojati yo'q — bu eng qimmat bosqichni butunlay tashlab yuboradi.
    """
    if not crops:
        return []
    _ensure_loaded()
    assert _rec_session is not None
    input_name = _rec_session.get_inputs()[0].name

    prepared = [_rec_input(crop) for crop in crops]
    # Bir xil nisbatdagilarni yonma-yon qo'ysak, to'ldirish (padding) kamayadi
    order = sorted(range(len(prepared)), key=lambda i: prepared[i].shape[2])
    results: list[tuple[str, float]] = [("", 0.0)] * len(prepared)

    for start in range(0, len(order), batch_size):
        chunk = order[start : start + batch_size]
        max_width = max(prepared[i].shape[2] for i in chunk)
        batch = np.zeros((len(chunk), 3, REC_HEIGHT, max_width), dtype=np.float32)
        for position, index in enumerate(chunk):
            item = prepared[index]
            batch[position, :, :, : item.shape[2]] = item
        outputs = _rec_session.run(None, {input_name: batch})[0]
        for position, index in enumerate(chunk):
            results[index] = _ctc_decode(outputs[position])
    return results


def read_regions(image: np.ndarray, batch_size: int = 8) -> list[dict]:
    """To'liq quvur: qutilarni topadi va ularni o'qiydi.

    Har element — {"text", "confidence", "box"} (box: 4x2 asl koordinatalar).
    """
    boxes = detect(image)
    if not boxes:
        return []
    crops = [crop_box(image, box) for box in boxes]
    texts = recognize(crops, batch_size=batch_size)
    regions = []
    for box, (text, confidence) in zip(boxes, texts):
        cleaned = text.strip()
        if not cleaned:
            continue
        regions.append({"text": cleaned, "confidence": confidence, "box": order_box(box)})
    # O'qish tartibi: yuqoridan pastga, keyin chapdan o'ngga
    regions.sort(key=lambda r: (round(float(r["box"][:, 1].mean()) / 12), float(r["box"][:, 0].min())))
    return regions
