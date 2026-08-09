"""Yuz bilan kirish uchun yengil tanish dvigateli.

OpenCV'ning YuNet (yuz aniqlash, ~350KB) va SFace (yuz tanish, ~37MB, LFW
aniqligi 99.6%) ONNX modellari ishlatiladi — kompilyatsiya talab qilmaydi,
CPU'da millisekundlarda ishlaydi. Modellar birinchi ishlatilganda yuklab
olinadi va xotirada qoladi. cv2 o'rnatilmagan bo'lsa server baribir ishlayveradi
— faqat yuz-kirish "mavjud emas" deb qaytadi.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import urllib.request

logger = logging.getLogger(__name__)

# Rasmiy opencv_zoo modellari
_DET_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/"
    "face_detection_yunet/face_detection_yunet_2023mar.onnx"
)
_REC_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/"
    "face_recognition_sface/face_recognition_sface_2021dec.onnx"
)
_MODEL_DIR = os.path.join(os.path.expanduser("~"), ".gohotel_face_models")
_DET_PATH = os.path.join(_MODEL_DIR, "yunet_2023mar.onnx")
_REC_PATH = os.path.join(_MODEL_DIR, "sface_2021dec.onnx")

# SFace uchun rasmiy tavsiya — kosinus 0.363; biroz qat'iyroq chegara olamiz
# (begonani qabul qilish xavfini kamaytirish uchun)
MATCH_THRESHOLD = 0.40

_lock = threading.Lock()
_detector = None
_recognizer = None


def engine_importable() -> bool:
    """cv2 o'rnatilganmi — model yuklamasdan tez tekshiruv."""
    try:
        import cv2  # noqa: F401

        return True
    except Exception:
        return False


def _download(url: str, path: str) -> None:
    tmp = path + ".part"
    urllib.request.urlretrieve(url, tmp)
    os.replace(tmp, path)


def _get_engines():
    """Detektor va tanish modellarini (bir marta) tayyorlaydi."""
    global _detector, _recognizer
    if _detector is not None and _recognizer is not None:
        return _detector, _recognizer

    import cv2

    os.makedirs(_MODEL_DIR, exist_ok=True)
    if not os.path.exists(_DET_PATH):
        logger.info("Yuz aniqlash modeli yuklanmoqda...")
        _download(_DET_URL, _DET_PATH)
    if not os.path.exists(_REC_PATH):
        logger.info("Yuz tanish modeli yuklanmoqda...")
        _download(_REC_URL, _REC_PATH)

    _detector = cv2.FaceDetectorYN.create(_DET_PATH, "", (320, 320), 0.8, 0.3, 5000)
    _recognizer = cv2.FaceRecognizerSF.create(_REC_PATH, "")
    logger.info("Yuz tanish dvigateli tayyor")
    return _detector, _recognizer


def compute_embedding(image_bytes: bytes) -> list[float]:
    """Rasmdan eng katta yuzning embeddingini hisoblaydi.

    Raises:
        ValueError("BAD_IMAGE") — rasm o'qilmadi;
        ValueError("NO_FACE") — yuz topilmadi.
    """
    import cv2
    import numpy as np

    img = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("BAD_IMAGE")

    # Katta rasmni kichraytirish — tezlik uchun (sifatga deyarli ta'sir yo'q)
    h, w = img.shape[:2]
    max_side = max(h, w)
    if max_side > 640:
        scale = 640 / max_side
        img = cv2.resize(img, (int(w * scale), int(h * scale)))
        h, w = img.shape[:2]

    # cv2 modellari thread-safe emas — bitta qulf ostida ishlatamiz
    with _lock:
        detector, recognizer = _get_engines()
        detector.setInputSize((w, h))
        _, faces = detector.detect(img)
        if faces is None or len(faces) == 0:
            raise ValueError("NO_FACE")
        # Eng katta (kameraga eng yaqin) yuz olinadi
        face = max(faces, key=lambda f: float(f[2]) * float(f[3]))
        aligned = recognizer.alignCrop(img, face)
        feat = recognizer.feature(aligned)

    return [float(x) for x in feat.flatten()]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    import numpy as np

    va = np.asarray(a, dtype=np.float32)
    vb = np.asarray(b, dtype=np.float32)
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    if denom == 0:
        return 0.0
    return float(np.dot(va, vb) / denom)


def serialize_embedding(embedding: list[float]) -> str:
    return json.dumps(embedding)


def parse_embedding(raw: str) -> list[float] | None:
    try:
        data = json.loads(raw)
        if isinstance(data, list) and data:
            return [float(x) for x in data]
    except Exception:
        pass
    return None
