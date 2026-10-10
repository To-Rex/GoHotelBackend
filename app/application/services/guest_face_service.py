"""Mehmonni yuzidan tanish — 1:N qidiruv dvigateli.

Bu modulning butun mavjudlik sababi: **server yuz tasvirini KO'RMAYDI**.
Og'ir ish (deteksiya + embedding) kamera agentida, mehmonxonaning o'z
kompyuterida bajariladi; serverga 512 baytlik vektor keladi va u faqat
vektor arifmetikasini qiladi. Shu sababli bitta VPS o'nlab filialni ko'taradi.

Uch qaror bu faylning shaklini belgilaydi:

1. **Indeks xotirada, BUTUN TIZIM uchun bitta.** Mehmonlar bazasi umumiy —
   Grand'da ro'yxatdan o'tgan mehmon Anna Hostel'ning istalgan filialiga
   kelsa ham tanilishi kerak, shuning uchun indeks mehmonxona bo'yicha
   bo'linmaydi (``hotel_id`` profilda faqat "qayerda biriktirilgan").
   Har so'rovda ``guest_face_profiles`` dan o'qish — minglab qatorni
   SQLAlchemy obyektiga aylantirish demak. O'rniga bir marta ``(N x 128)``
   numpy matritsasi quriladi va keyingi qidiruvlar bitta matritsa-vektor
   ko'paytmasiga aylanadi: N=10 000 uchun ~1 ms. Indeks versiya
   hisoblagichi bilan bekor qilinadi.

2. **Chegara 1:1 dan qat'iyroq, va bitta ball yetarli emas.** Xodim login
   qilganda (``face_service``) 0.40 yetarli: u allaqachon kim ekanini da'vo
   qilyapti. Bu yerda esa butun tizim mehmonlari ichidan qidiriladi va
   noto'g'ri moslik qabulxonada boshqa odamning broni ochilishi demak.
   "Tanilgan" deyish uchun TO'RT mustaqil shart birga bajarilishi kerak
   (``decide``): yuqori ball, boshqa mehmondan sezilarli **margin**,
   alohida kadrlarning **konsensusi** (o'rtacha shablon emas, har kadr ham
   o'sha mehmonni ko'rsatsin) va **statistik ustunlik** (eng yaxshi ball
   qolgan nomzodlar taqsimotidan ajralib tursin). Oxirgisi eng muhimi:
   kichik yoki xira yuzning vektori "o'rtacha yuz"ga yaqinlashib, HAMMA
   bilan birdek 0.5–0.6 o'xshaydi — shunda margin bor-u, ustunlik yo'q, va
   javob "aniq emas" bo'ladi, "u" emas. Sifatsiz kadr (kichik yuz, past
   sifat, aralash epizod) umuman "tanilgan" bo'la olmaydi.

3. **Klasterlash — server tomonda ham.** Agent 10 kadrdan shablon yasab
   yuboradi; lekin bir nechta ko'rinishdan qo'lda profil yig'ilganda ham
   xuddi shu mantiq kerak, shuning uchun u shu yerda ham turadi.
"""
from __future__ import annotations

import base64
import logging
import struct
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Sequence
from uuid import UUID

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models.guest import Guest
from app.infrastructure.database.models.guest_face_profile import GuestFaceProfile

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Doimiylar
# ---------------------------------------------------------------------------

#: SFace chiqaradigan vektor o'lchami. Model almashsa bu ham o'zgaradi va
#: eski vektorlar indeksdan tushib qoladi (``MODEL_NAME`` bo'yicha filtr).
EMBEDDING_DIM = 128
MODEL_NAME = "sface_2021dec"

#: Moslik deb tan olish uchun minimal kosinus o'xshashlik.
#:
#: SFace uchun rasmiy tavsiya 0.363, ``face_service`` xodim login uchun 0.40
#: ishlatadi. Bu yerda 1:N qidiruv — nomzodlar soni ortgan sari tasodifiy
#: yuqori ball chiqish ehtimoli ham ortadi, shuning uchun chegara ancha
#: yuqori. Pastroq qo'yish begonani mehmon deb ko'rsatishga olib keladi.
MATCH_THRESHOLD = 0.60

#: Eng yaxshi nomzod boshqa mehmonlarning eng yaxshisidan shuncha ustun
#: bo'lishi shart. Bu egizaklar va o'xshash yuzlarga qarshi asosiy himoya.
MATCH_MARGIN = 0.10

#: Shu balldan yuqori, lekin moslik shartlarini bajarmagan nomzod panelda
#: "tasdiqlang" belgisi bilan ko'rsatiladi — butunlay tashlab yuborilmaydi.
REVIEW_THRESHOLD = 0.50

#: Alohida kadr (namuna) o'sha mehmonga tegishli deyish chegarasi. O'rtacha
#: shablondan pastroq: bitta kadr tasodifiy soya/burchakni o'z ichiga oladi.
SAMPLE_THRESHOLD = 0.55
#: Namunalarning kamida shuncha ulushi o'sha mehmonni ko'rsatishi kerak.
SAMPLE_CONSENSUS = 0.6

#: Statistik ustunlik: eng yaxshi ball qolgan (boshqa mehmon) nomzodlarning
#: o'rtachasidan kamida shuncha standart og'ish yuqori bo'lsin. Indeksda
#: shuncha boshqa shablon bo'lmasa shart tekshirilmaydi (taqsimot yo'q).
OUTLIER_Z = 2.5
OUTLIER_MIN_OTHERS = 8

#: Kadr sifati darvozasi. Bundan kichik yuz (112x112 kirishga kattalashtirish
#: kamera ko'rmagan tafsilotni "ixtiro" qiladi) yoki past sifat ballidan
#: "tanilgan" chiqmaydi — ko'pi bilan "aniq emas", xodim o'zi tasdiqlaydi.
MIN_RECOGNITION_FACE_PIXELS = 72
MIN_RECOGNITION_QUALITY = 0.45

#: Bir mehmonga saqlanadigan shablonlar chegarasi. Ko'proq shablon = yaxshiroq
#: qamrov, lekin indeks kattalashadi va eski (o'zgargan tashqi ko'rinish)
#: shablonlar aniqlikni pasaytiradi.
MAX_PROFILES_PER_GUEST = 6

#: Shablonni avtomatik yangilash chegarasi: shu balldan yuqori moslikda yangi
#: ko'rinish qo'shiladi (soch turmagi, ko'zoynak, yorug'lik o'zgarishi).
#: Chegara ataylab yuqori — shubhali moslikdan o'rganish xatoni mustahkamlaydi.
ADAPTIVE_LEARN_THRESHOLD = 0.72

#: Klaster a'zoligi chegarasi: bitta epizoddagi kadrlar bir-biriga shundan
#: yuqori o'xshash bo'lishi kerak. Bitta odamning ketma-ket kadrlari odatda
#: 0.7+ beradi, shuning uchun 0.55 kengroq, lekin boshqa odamni qo'shmaydi.
CLUSTER_MIN_SIMILARITY = 0.55


# ---------------------------------------------------------------------------
# Vektorni paketlash
# ---------------------------------------------------------------------------


def l2_normalize(vector: np.ndarray) -> np.ndarray:
    """Vektorni birlik uzunlikka keltiradi.

    Barcha vektorlar normallashtirilgani uchun kosinus o'xshashlik oddiy
    skalyar ko'paytmaga aylanadi — qidiruvdagi bo'lish amali yo'qoladi.
    """
    vec = np.asarray(vector, dtype=np.float32).ravel()
    norm = float(np.linalg.norm(vec))
    if norm <= 1e-12:
        return vec
    return (vec / norm).astype(np.float32, copy=False)


def pack_embedding(values: Sequence[float] | np.ndarray) -> bytes:
    """float32 little-endian qatorga aylantiradi (128 o'lcham = 512 bayt)."""
    vec = l2_normalize(np.asarray(values, dtype=np.float32))
    if vec.size != EMBEDDING_DIM:
        raise ValueError(f"BAD_DIM:{vec.size}")
    return vec.astype("<f4", copy=False).tobytes()


def unpack_embedding(blob: bytes | memoryview | None) -> np.ndarray | None:
    """Paketlangan vektorni qaytaradi, buzuq bo'lsa ``None``."""
    if not blob:
        return None
    data = bytes(blob)
    if len(data) != EMBEDDING_DIM * 4:
        return None
    try:
        return np.frombuffer(data, dtype="<f4").astype(np.float32, copy=True)
    except Exception:  # noqa: BLE001 - buzuq qator indeksni to'xtatmasin
        return None


def decode_wire_embedding(text: str | None) -> np.ndarray | None:
    """Agent yuborgan base64 vektorni ochadi.

    Agent vektorni base64(float32 LE) sifatida yuboradi: JSON ro'yxatiga
    nisbatan ~3 barobar ixcham va parse qilish deyarli bepul. Eski yoki
    boshqa mijoz JSON ro'yxati yuborsa ham qabul qilamiz.
    """
    if not text or not isinstance(text, str):
        return None
    raw = text.strip()
    if not raw:
        return None
    try:
        data = base64.b64decode(raw, validate=True)
    except Exception:  # noqa: BLE001
        return None
    if len(data) != EMBEDDING_DIM * 4:
        return None
    try:
        return l2_normalize(np.frombuffer(data, dtype="<f4"))
    except Exception:  # noqa: BLE001
        return None


def decode_wire_list(values: Iterable[str] | None) -> list[np.ndarray]:
    """Bir nechta base64 vektorni ochadi, buzuqlarini tashlab ketadi."""
    if not values:
        return []
    out: list[np.ndarray] = []
    for item in values:
        vec = decode_wire_embedding(item)
        if vec is not None:
            out.append(vec)
    return out


def encode_wire_embedding(vector: np.ndarray) -> str:
    """Vektorni agent/mijoz tushunadigan base64 shaklga o'tkazadi."""
    return base64.b64encode(pack_embedding(vector)).decode("ascii")


# ---------------------------------------------------------------------------
# Klasterlash — "bir xillarini yig'ish"
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Template:
    """Bir nechta kadrdan yig'ilgan yakuniy shablon."""

    vector: np.ndarray
    sample_count: int
    """Shablonga KIRGAN kadrlar soni."""
    dropped: int
    """Chetlatilgan kadrlar — boshqa odam yoki juda boshqacha ko'rinish."""
    cohesion: float
    """A'zolarning markazga o'rtacha o'xshashligi, 0..1. Past qiymat —
    kadrlar bir odamga tegishli ekaniga ishonch kam."""

    @property
    def is_reliable(self) -> bool:
        return self.sample_count >= 2 and self.cohesion >= CLUSTER_MIN_SIMILARITY


def build_template(
    vectors: Sequence[np.ndarray],
    *,
    min_similarity: float = CLUSTER_MIN_SIMILARITY,
) -> Template | None:
    """Kadrlar to'plamidan bitta shablon yasaydi.

    Algoritm — kichik to'plamlar (odatda 3-10 kadr) uchun eng ishonchlisi:

    1. Har vektorni navbatma-navbat "urug'" deb olib, unga ``min_similarity``
       dan yaqin barcha vektorlarni yig'amiz.
    2. Eng katta guruh yutadi — bu kameraga eng ko'p ko'ringan odam. Fon
       o'tkinchisi yoki navbatdagi keyingi mehmon ozchilikda qoladi va o'z-o'zidan
       tushib ketadi.
    3. Guruh markazini hisoblab, undan uzoqlashgan a'zolarni yana bir marta
       chetlatamiz (markaz urug'dan aniqroq mo'ljal).
    4. Qolganlarning o'rtachasi — shablon.

    O'rtachalash muhim: bitta kadr tasodifiy soya yoki burchakni "esda
    saqlaydi", bir nechtasining o'rtachasi esa aynan shu tasodifni yo'qotadi.
    """
    cleaned = [l2_normalize(v) for v in vectors if v is not None and np.size(v) == EMBEDDING_DIM]
    if not cleaned:
        return None
    if len(cleaned) == 1:
        return Template(vector=cleaned[0], sample_count=1, dropped=0, cohesion=1.0)

    matrix = np.vstack(cleaned).astype(np.float32, copy=False)
    # Barcha juftliklar bir amalda: kichik N uchun eng tez yo'l.
    similarity = matrix @ matrix.T

    best_members: np.ndarray | None = None
    for seed in range(matrix.shape[0]):
        members = np.flatnonzero(similarity[seed] >= min_similarity)
        if best_members is None or members.size > best_members.size:
            best_members = members

    assert best_members is not None and best_members.size > 0
    centroid = l2_normalize(matrix[best_members].mean(axis=0))

    # Markazga nisbatan ikkinchi tozalash.
    scores = matrix[best_members] @ centroid
    keep = best_members[scores >= min_similarity]
    if keep.size == 0:
        keep = best_members

    final = l2_normalize(matrix[keep].mean(axis=0))
    cohesion = float(np.mean(matrix[keep] @ final))
    return Template(
        vector=final,
        sample_count=int(keep.size),
        dropped=int(matrix.shape[0] - keep.size),
        cohesion=round(cohesion, 4),
    )


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Ikki vektor orasidagi kosinus o'xshashlik."""
    va, vb = l2_normalize(a), l2_normalize(b)
    return float(np.dot(va, vb))


#: Ikki EPIZOD bir odamga tegishli deb hisoblanadigan chegara.
#:
#: ``CLUSTER_MIN_SIMILARITY`` dan yuqori, chunki u bitta epizod ichidagi
#: kadrlar uchun — ular bir necha soniya ichida, bir xil yorug'likda olingan
#: va tabiiy ravishda o'xshash. Turli vaqtdagi epizodlar uzoqroq bo'ladi,
#: lekin bu yerda xato qimmatroq: ikki odamni bitta guruhga qo'shish xodimga
#: birovning yuzini boshqasiga biriktirishga imkon beradi. Bir odamning
#: ikkita guruhga bo'linishi esa shunchaki bugungi holat — zarari yo'q.
GROUP_MIN_SIMILARITY = 0.58


@dataclass(slots=True)
class _Group:
    """Yig'ilayotgan guruh: a'zolar indeksi va ularning markazi."""

    members: list[int]
    centroid: np.ndarray


def group_embeddings(
    vectors: Sequence[np.ndarray],
    *,
    min_similarity: float = GROUP_MIN_SIMILARITY,
    order: Sequence[int] | None = None,
) -> list[list[int]]:
    """Vektorlarni odamlar bo'yicha guruhlaydi; indekslar ro'yxatini qaytaradi.

    "Leader" usuli: har vektor mavjud guruhlarning MARKAZLARI bilan
    solishtiriladi, eng yaqiniga qo'shiladi, hech biriga mos kelmasa yangi
    guruh ochadi.

    Nega bog'lanishli (single-linkage) klasterlash emas: u zanjir hosil
    qiladi — A va B o'xshash, B va C o'xshash, natijada A va C bir guruhda,
    garchi ular bir-biriga umuman o'xshamasa ham. Bir necha soatlik
    ko'rinishlarda bu ikki-uch odamni bitta guruhga yig'ib qo'yardi.
    Markazga solishtirish bunga yo'l qo'ymaydi.

    ``order`` — vektorlar qaysi tartibda ko'rilishi. Chaqiruvchi eng
    sifatlisini boshiga qo'yadi: birinchi a'zo guruhning boshlang'ich markazi
    bo'ladi, va sifatsiz kadrdan boshlangan guruh keyingilarini ham noto'g'ri
    tortadi.
    """
    if not vectors:
        return []
    indices = list(order) if order is not None else list(range(len(vectors)))
    groups: list[_Group] = []

    for index in indices:
        vector = l2_normalize(vectors[index])
        best_group: _Group | None = None
        best_score = min_similarity
        for group in groups:
            score = float(np.dot(group.centroid, vector))
            if score >= best_score:
                best_score = score
                best_group = group

        if best_group is None:
            groups.append(_Group(members=[index], centroid=vector))
            continue

        best_group.members.append(index)
        # Markaz yangilanadi, aks holda guruh birinchi a'zosiga bog'lanib
        # qolardi va keyingi a'zolar faqat o'shanga qarab baholanardi.
        members = np.vstack([l2_normalize(vectors[i]) for i in best_group.members])
        best_group.centroid = l2_normalize(members.mean(axis=0))

    return [g.members for g in groups]


def group_cohesion(vectors: Sequence[np.ndarray], members: Sequence[int]) -> float:
    """Guruh a'zolarining markazga o'rtacha o'xshashligi, 0..1.

    Past qiymat — guruhga boshqa odam qo'shilgan bo'lishi mumkin degan belgi;
    panel buni xodimga ko'rsatadi.
    """
    if not members:
        return 0.0
    if len(members) == 1:
        return 1.0
    matrix = np.vstack([l2_normalize(vectors[i]) for i in members])
    centroid = l2_normalize(matrix.mean(axis=0))
    return round(float(np.mean(matrix @ centroid)), 4)


# ---------------------------------------------------------------------------
# Umumiy indeks (barcha mehmonxona va filiallar)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _FaceIndex:
    """Tizimdagi barcha mehmonlarning yuz shablonlari — bitta matritsada."""

    matrix: np.ndarray          # (N, 128) float32, satrlari normallashtirilgan
    guest_ids: np.ndarray       # (N,) object — mehmon UUID'lari
    profile_ids: list[UUID]     # (N,) profil UUID'lari
    version: int
    built_at: datetime

    @property
    def size(self) -> int:
        return int(self.matrix.shape[0]) if self.matrix.size else 0


_index_lock = threading.Lock()
#: Bitta indeks — mehmonxona yoki filialga bo'linmaydi. Mehmon qayerda
#: ro'yxatdan o'tgan bo'lsa ham, istalgan mehmonxonaning istalgan kamerasi
#: uni taniydi.
_index: _FaceIndex | None = None
_version: int = 0


def invalidate_index() -> None:
    """Shablon qo'shilgan/o'chirilganda indeksni eskirgan deb belgilaydi.

    Indeks darhol qayta qurilmaydi — keyingi qidiruvda quriladi. Bu bir necha
    profilni ketma-ket o'chirishda indeksni har safar qayta yig'ishdan saqlaydi.
    """
    global _version
    with _index_lock:
        _version += 1


def index_stats() -> dict[str, object]:
    """Diagnostika uchun: indeks qurilganmi, nechta shablon bor (butun tizim)."""
    with _index_lock:
        index = _index
        version = _version
    if index is None:
        return {"loaded": False, "profiles": 0, "version": version}
    return {
        "loaded": True,
        "profiles": index.size,
        "version": index.version,
        "stale": index.version != version,
        "built_at": index.built_at.isoformat(),
    }


def index_statement():
    """Indeksga kiradigan profillar — HAMMA mehmonxona va filiallarniki.

    Mehmonxona yoki filial sharti ataylab yo'q: mehmonlar bazasi umumiy va
    yuz ham mehmonning o'zinikidir, u ro'yxatdan o'tgan joyniki emas.
    """
    return (
        select(
            GuestFaceProfile.id,
            GuestFaceProfile.guest_id,
            GuestFaceProfile.embedding,
        )
        .join(Guest, Guest.id == GuestFaceProfile.guest_id)
        .where(
            GuestFaceProfile.model == MODEL_NAME,
            GuestFaceProfile.dim == EMBEDDING_DIM,
            Guest.is_deleted.is_(False),
        )
    )


async def get_index(session: AsyncSession) -> _FaceIndex:
    """Umumiy indeksni qaytaradi, kerak bo'lsa qayta quradi."""
    global _index
    with _index_lock:
        current_version = _version
        cached = _index
        if cached is not None and cached.version == current_version:
            return cached

    rows = (await session.execute(index_statement())).all()

    vectors: list[np.ndarray] = []
    guest_ids: list[UUID] = []
    profile_ids: list[UUID] = []
    for profile_id, guest_id, blob in rows:
        vec = unpack_embedding(blob)
        if vec is None:
            logger.warning("Yuz profili %s buzuq — indeksdan chetlatildi", profile_id)
            continue
        vectors.append(vec)
        guest_ids.append(guest_id)
        profile_ids.append(profile_id)

    if vectors:
        matrix = np.vstack(vectors).astype(np.float32, copy=False)
    else:
        matrix = np.zeros((0, EMBEDDING_DIM), dtype=np.float32)

    index = _FaceIndex(
        matrix=matrix,
        guest_ids=np.array(guest_ids, dtype=object),
        profile_ids=profile_ids,
        version=current_version,
        built_at=datetime.now(timezone.utc),
    )
    with _index_lock:
        # Qurish davomida yana o'zgargan bo'lsa keshlamaymiz: keyingi qidiruv
        # yangisini quradi. Eskisini saqlab qo'yish jimgina eskirishga olib
        # kelardi.
        if _version == current_version:
            _index = index
    logger.debug("Yuz indeksi qurildi (butun tizim): shablon=%d", index.size)
    return index


# ---------------------------------------------------------------------------
# Qidiruv
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SearchResult:
    """1:N qidiruv natijasi."""

    status: str
    """``recognized`` | ``uncertain`` | ``unknown``"""
    guest_id: UUID | None
    profile_id: UUID | None
    score: float
    """Eng yaxshi nomzodning kosinus o'xshashligi."""
    margin: float
    """Eng yaxshi nomzod boshqa mehmonlarning eng yaxshisidan qancha ustun."""
    candidates: int
    """Indeksdagi shablonlar soni — natijani talqin qilishda kerak."""
    reason: str = ""
    """Nega "tanilgan" bo'lmadi (diagnostika, panel): ``threshold`` |
    ``margin`` | ``consensus`` | ``outlier`` | ``quality`` | ``cohesion``."""
    outlier_z: float = 0.0
    """Eng yaxshi ball boshqa nomzodlar taqsimotidan necha sigma yuqori."""

    @property
    def is_match(self) -> bool:
        return self.status == "recognized"

    def demoted(self, reason: str) -> "SearchResult":
        """Shu natijani "aniq emas"ga tushiradi (nomzod saqlanadi — xodim
        tasdiqlashi mumkin), sababi bilan."""
        if self.status != "recognized":
            return self
        return SearchResult(
            status="uncertain",
            guest_id=self.guest_id,
            profile_id=self.profile_id,
            score=self.score,
            margin=self.margin,
            candidates=self.candidates,
            reason=reason,
            outlier_z=self.outlier_z,
        )


#: ``argpartition`` uchun nechta eng yaxshi nomzod ko'riladi. Margin bir
#: mehmonning barcha shablonlaridan keyin BOSHQA mehmonni topishi kerak,
#: shuning uchun ``MAX_PROFILES_PER_GUEST`` dan sezilarli katta.
_TOP_K = 32


def search_index(index: _FaceIndex, vector: np.ndarray) -> SearchResult:
    """Vektorni indeksdagi barcha shablonlar bilan solishtiradi.

    Butun qidiruv — bitta matritsa-vektor ko'paytmasi. N=10 000 uchun bu
    1.28M ko'paytirish, ya'ni ~1 ms; bu yerda hech qanday optimallashtirishga
    ehtiyoj yo'q va ``pgvector`` faqat yuz minglab shablonda kerak bo'ladi.
    """
    empty = SearchResult(
        status="unknown", guest_id=None, profile_id=None,
        score=0.0, margin=0.0, candidates=0,
    )
    if index.size == 0:
        return empty

    query = l2_normalize(vector)
    if query.size != EMBEDDING_DIM:
        return empty

    scores = index.matrix @ query  # barcha satrlar birdan

    top_n = min(_TOP_K, scores.shape[0])
    # argpartition to'liq saralashdan tez: bizga faqat eng yaxshi bir nechtasi kerak.
    top = np.argpartition(-scores, top_n - 1)[:top_n]
    top = top[np.argsort(-scores[top])]

    best = int(top[0])
    best_score = float(scores[best])
    best_guest = index.guest_ids[best]

    # Margin BOSHQA mehmonga nisbatan o'lchanadi: bitta mehmonning oltita
    # shabloni tabiiy ravishda yuqori ball beradi va ular raqobatchi emas.
    runner_up = 0.0
    for idx in top[1:]:
        if index.guest_ids[int(idx)] != best_guest:
            runner_up = float(scores[int(idx)])
            break
    margin = best_score - runner_up

    # Statistik ustunlik: eng yaxshi ball BOSHQA mehmonlar ballari
    # taqsimotidan necha sigma yuqori. Haqiqiy moslikda bu 4-10 sigma;
    # kichik/xira yuzda esa hamma ball bir-biriga yaqin (0.5-0.6) va eng
    # yaxshisi ham 1-2 sigma ichida qoladi — demak u tasodif.
    others_mask = index.guest_ids != best_guest
    others = scores[others_mask]
    z = 0.0
    if others.size >= OUTLIER_MIN_OTHERS:
        spread = float(others.std())
        z = (best_score - float(others.mean())) / spread if spread > 1e-6 else float("inf")

    reason = ""
    if best_score < REVIEW_THRESHOLD:
        status = "unknown"
    elif best_score < MATCH_THRESHOLD:
        status, reason = "uncertain", "threshold"
    elif margin < MATCH_MARGIN:
        status, reason = "uncertain", "margin"
    elif others.size >= OUTLIER_MIN_OTHERS and z < OUTLIER_Z:
        status, reason = "uncertain", "outlier"
    else:
        status = "recognized"

    return SearchResult(
        status=status,
        guest_id=best_guest if status != "unknown" else None,
        profile_id=index.profile_ids[best] if status != "unknown" else None,
        score=round(best_score, 4),
        margin=round(margin, 4),
        candidates=index.size,
        reason=reason,
        outlier_z=round(z, 2) if np.isfinite(z) else 99.0,
    )


def sample_consensus(index: _FaceIndex, guest_id, samples: Sequence[np.ndarray]) -> tuple[int, int]:
    """Alohida kadrlardan nechtasi O'SHA mehmonni ko'rsatadi.

    Qaytaradi ``(mos, jami)``. Kadr mos deyiladi, agar uning eng yaqin
    shabloni shu mehmonniki va ball ``SAMPLE_THRESHOLD`` dan yuqori bo'lsa.
    O'rtacha shablon ikki odamning kadrlaridan yig'ilsa ham chegaradan
    oshishi mumkin — kadrlar alohida tekshirilganda bu ochiladi.
    """
    if index.size == 0 or not samples:
        return 0, 0
    agree = 0
    total = 0
    for sample in samples:
        vec = l2_normalize(sample)
        if vec.size != EMBEDDING_DIM:
            continue
        total += 1
        scores = index.matrix @ vec
        top = int(np.argmax(scores))
        if index.guest_ids[top] == guest_id and float(scores[top]) >= SAMPLE_THRESHOLD:
            agree += 1
    return agree, total


def decide(
    index: _FaceIndex,
    template: Template,
    samples: Sequence[np.ndarray] = (),
    *,
    quality: float | None = None,
    face_pixels: int | None = None,
) -> SearchResult:
    """To'liq qaror: qidiruv + konsensus + sifat darvozasi.

    Tartib muhim: avval ball/margin/ustunlik (``search_index``), keyin
    kadrlar konsensusi, oxirida sifat. Har bosqich "tanilgan"ni faqat
    "aniq emas"ga tushira oladi — nomzod yo'qolmaydi, xodim panelda
    tasdiqlashi mumkin. Hech bir bosqich "aniq emas"ni "tanilgan"ga
    ko'tarmaydi.
    """
    result = search_index(index, template.vector)
    if not result.is_match:
        return result

    # Epizod ichida kadrlar bir-biriga o'xshamagan (ikki odam almashgan,
    # yoki yuz qimirlab xira chiqqan) — o'rtacha shablon hech kimniki emas.
    if template.sample_count >= 2 and template.cohesion < CLUSTER_MIN_SIMILARITY:
        return result.demoted("cohesion")

    if len(samples) >= 2:
        agree, total = sample_consensus(index, result.guest_id, samples)
        if total >= 2 and agree < max(2, int(np.ceil(total * SAMPLE_CONSENSUS))):
            return result.demoted("consensus")

    if quality is not None and quality < MIN_RECOGNITION_QUALITY:
        return result.demoted("quality")
    if face_pixels is not None and 0 < face_pixels < MIN_RECOGNITION_FACE_PIXELS:
        return result.demoted("quality")
    return result


async def identify(session: AsyncSession, vector: np.ndarray) -> SearchResult:
    """Bitta vektorni BUTUN tizim mehmonlari ichidan izlaydi (faqat ball).

    Qaysi mehmonxona kamerasi ko'rgani ahamiyatsiz: Grand'da biriktirilgan
    yuz Anna Hostel filialida ham tanilishi kerak. Kamera epizodi uchun
    to'liq qaror — ``identify_episode`` (konsensus va sifat bilan).
    """
    index = await get_index(session)
    return search_index(index, vector)


async def identify_episode(
    session: AsyncSession,
    template: Template,
    samples: Sequence[np.ndarray] = (),
    *,
    quality: float | None = None,
    face_pixels: int | None = None,
) -> SearchResult:
    """Kamera epizodi bo'yicha to'liq qaror (``decide``)."""
    index = await get_index(session)
    return decide(index, template, samples, quality=quality, face_pixels=face_pixels)


async def conflicting_guest(
    session: AsyncSession, vector: np.ndarray, guest_id: UUID
) -> SearchResult | None:
    """Shu yuz BOSHQA mehmonga allaqachon biriktirilganmi.

    Biriktirishdan oldin tekshiriladi: bir yuz ikki mehmonda bo'lsa, keyin
    ikkalasi bir-birining marginini yeb, hech biri tanilmaydi — yoki yomoni,
    navbatma-navbat tanilib qabulxonani adashtiradi. Mos kelgan boshqa
    mehmon bo'lsa uning natijasi qaytadi, aks holda ``None``.
    """
    index = await get_index(session)
    if index.size == 0:
        return None
    query = l2_normalize(vector)
    scores = index.matrix @ query
    mask = index.guest_ids != guest_id
    if not mask.any():
        return None
    scores = np.where(mask, scores, -1.0)
    top = int(np.argmax(scores))
    if float(scores[top]) < MATCH_THRESHOLD:
        return None
    return SearchResult(
        status="recognized",
        guest_id=index.guest_ids[top],
        profile_id=index.profile_ids[top],
        score=round(float(scores[top]), 4),
        margin=0.0,
        candidates=index.size,
    )


# ---------------------------------------------------------------------------
# Shablon yozish
# ---------------------------------------------------------------------------


async def enroll(
    session: AsyncSession,
    *,
    hotel_id: UUID,
    guest_id: UUID,
    template: Template,
    quality: float = 0.0,
    source: str = "vision",
    camera_id: str | None = None,
    created_by: UUID | None = None,
) -> GuestFaceProfile:
    """Mehmonga yangi yuz shabloni biriktiradi.

    ``hotel_id`` — qayerda biriktirilgani (ma'lumot uchun); shablon
    hamma mehmonxonada taniydi. Chegaradan oshsa eng eski shablon
    o'chiriladi — mehmonning tashqi ko'rinishi vaqt bilan o'zgaradi va yangi
    ko'rinishlar foydaliroq.
    """
    profile = GuestFaceProfile(
        guest_id=guest_id,
        hotel_id=hotel_id,
        embedding=pack_embedding(template.vector),
        dim=EMBEDDING_DIM,
        model=MODEL_NAME,
        sample_count=template.sample_count,
        cohesion=template.cohesion,
        quality=round(float(quality), 4),
        source=source,
        camera_id=camera_id,
        created_by=created_by,
    )
    session.add(profile)
    await session.flush()

    existing = (
        (
            await session.execute(
                select(GuestFaceProfile)
                .where(GuestFaceProfile.guest_id == guest_id)
                .order_by(GuestFaceProfile.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    for stale in existing[MAX_PROFILES_PER_GUEST:]:
        await session.delete(stale)
    await session.flush()

    invalidate_index()
    return profile


async def learn_from_match(
    session: AsyncSession,
    *,
    hotel_id: UUID,
    guest_id: UUID,
    result: SearchResult,
    template: Template,
    quality: float,
    camera_id: str | None,
) -> GuestFaceProfile | None:
    """Ishonchli moslikdan yangi ko'rinishni o'rganadi; yozilgan shablonni
    qaytaradi (yozilmasa ``None``).

    Yangi shablon ko'rinishga bog'lanadi (``learned_profile_id``): xodim
    keyin "bu u emas" desa, aynan shu xato shablon o'chiriladi.

    Mehmon soch turmagini o'zgartirsa yoki ko'zoynak taqsa, eski shablon
    asta-sekin mos kelmay qoladi. Har ishonchli tanishda yangi ko'rinishni
    qo'shib borish buni o'z-o'zidan tuzatadi.

    Ikki qat'iy shart bor, chunki noto'g'ri o'rganish xatoni MUSTAHKAMLAYDI:
    ball oddiy chegaradan ancha yuqori bo'lishi va shablon ishonchli (bir
    nechta mos kadrdan yig'ilgan) bo'lishi kerak.
    """
    if result.score < ADAPTIVE_LEARN_THRESHOLD or not template.is_reliable:
        return None
    # Sifatsiz kadrdan o'rganish shablonni "o'rtacha yuz"ga yaqinlashtiradi —
    # keyin u hammaga o'xshab qoladi.
    if quality < MIN_RECOGNITION_QUALITY:
        return None

    count = len(
        (
            await session.execute(
                select(GuestFaceProfile.id).where(GuestFaceProfile.guest_id == guest_id)
            )
        )
        .scalars()
        .all()
    )
    if count >= MAX_PROFILES_PER_GUEST:
        return None

    # Mavjud shablonga juda yaqin bo'lsa yangilik qo'shmaydi, faqat indeksni
    # kattalashtiradi.
    if result.score > 0.90:
        return None

    profile = await enroll(
        session,
        hotel_id=hotel_id,
        guest_id=guest_id,
        template=template,
        quality=quality,
        source="vision",
        camera_id=camera_id,
    )
    logger.info(
        "Mehmon %s uchun yangi yuz ko'rinishi o'rganildi (ball %.3f)",
        guest_id,
        result.score,
    )
    return profile


async def forget_guest(session: AsyncSession, *, guest_id: UUID) -> int:
    """Mehmonning barcha biometrik ma'lumotlarini o'chiradi.

    Rozilikni qaytarib olish — huquqiy talab, shuning uchun bu amal to'liq
    va qaytarilmas: shablonlar ham (qaysi mehmonxonada biriktirilgan
    bo'lsa ham), saqlangan ko'rinishlardagi vektorlar ham o'chadi.
    """
    from sqlalchemy import delete, update

    from app.infrastructure.database.models.face_sighting import FaceSighting

    profiles = (
        (
            await session.execute(
                select(GuestFaceProfile.id).where(GuestFaceProfile.guest_id == guest_id)
            )
        )
        .scalars()
        .all()
    )
    if profiles:
        await session.execute(
            delete(GuestFaceProfile).where(GuestFaceProfile.guest_id == guest_id)
        )
    # Ko'rinishlar tarixi audit uchun qoladi, lekin biometriyasiz.
    await session.execute(
        update(FaceSighting)
        .where(FaceSighting.guest_id == guest_id)
        .values(embedding=None, thumbnail=None)
    )
    await session.flush()
    invalidate_index()
    return len(profiles)


# ---------------------------------------------------------------------------
# Zaxira yo'l: agent vektor yubormasa
# ---------------------------------------------------------------------------


def server_engine_available() -> bool:
    """Serverda cv2 bormi — agent vektorsiz rasm yuborgan holat uchun."""
    from app.application.services import face_service

    return face_service.engine_importable()


def embed_image(image_bytes: bytes) -> np.ndarray:
    """Rasmdan vektor hisoblaydi (faqat zaxira yo'l).

    Bu qimmat va global qulf ostida ishlaydi — normal ish rejimida agent
    vektorni o'zi hisoblab yuboradi va bu funksiya umuman chaqirilmaydi.

    Raises:
        ValueError("NO_FACE" | "BAD_IMAGE")
    """
    from app.application.services import face_service

    values = face_service.compute_embedding(image_bytes)
    return l2_normalize(np.asarray(values, dtype=np.float32))
