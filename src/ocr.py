from __future__ import annotations

from io import BytesIO
from typing import Tuple, Optional, List

from PIL import Image, ImageOps, ImageEnhance, ImageFilter
import pytesseract


def _score_text(t: str) -> float:
    if not t:
        return 0.0
    t = t.strip()
    if not t:
        return 0.0

    letters = sum(ch.isalpha() for ch in t)
    digits = sum(ch.isdigit() for ch in t)
    newlines = t.count("\n")
    low = t.lower()

    bonus = 0
    for kw in ["total", "sale", "subtotal", "tax", "visa", "discover", "auth", "expdate", "card#"]:
        if kw in low:
            bonus += 1

    # strong receipt-y structure
    return (letters * 1.0) + (digits * 1.4) + (newlines * 8.0) + (bonus * 80.0)


def _prep_variants(img: Image.Image) -> List[Image.Image]:
    base = ImageOps.exif_transpose(img).convert("L")

    # upscale small receipts
    w, h = base.size
    if max(w, h) < 1600:
        base = base.resize((int(w * 2.0), int(h * 2.0)), Image.LANCZOS)

    # fewer variants (speed), but strong ones
    v1 = ImageEnhance.Contrast(base).enhance(2.0)
    v1 = v1.filter(ImageFilter.UnsharpMask(radius=2, percent=180, threshold=3))

    v2 = ImageEnhance.Contrast(base).enhance(2.6)
    v2 = v2.point(lambda p: 255 if p > 165 else 0)

    return [v1, v2]


def _run_tesseract(im: Image.Image) -> str:
    configs = [
        "--oem 1 --psm 6",
        "--oem 1 --psm 4",
    ]

    best_text = ""
    best_score = -1e18
    for cfg in configs:
        try:
            t = pytesseract.image_to_string(im, config=cfg) or ""
        except Exception:
            continue
        s = _score_text(t)
        if s > best_score:
            best_score = s
            best_text = t
        # early exit: if it already looks like a real receipt, stop trying configs
        if best_score >= 650:
            break

    return best_text.strip()


def ocr_upload(filename: str, file_bytes: bytes) -> Tuple[Optional[Image.Image], str]:
    name = (filename or "").lower()

    if name.endswith(".pdf"):
        return None, (
            "PDF detected.\n"
            "PDF OCR is disabled on Streamlit Cloud.\n"
            "Please upload a photo (JPG or PNG) of the receipt."
        )

    try:
        img = Image.open(BytesIO(file_bytes))
        img = ImageOps.exif_transpose(img).convert("RGB")
    except Exception:
        return None, "Could not read image file."

    # mild downscale only if enormous
    max_side = max(img.size)
    if max_side > 2600:
        scale = 2600 / max_side
        img = img.resize((int(img.size[0] * scale), int(img.size[1] * scale)), Image.LANCZOS)

    variants = _prep_variants(img)

    # rotations — but early stop once we hit a great score
    rotations = [0, 90, 270, 180]

    best_text = ""
    best_score = -1e18

    for v in variants:
        for deg in rotations:
            im2 = v if deg == 0 else v.rotate(deg, expand=True)
            t = _run_tesseract(im2)
            s = _score_text(t)
            if s > best_score:
                best_score = s
                best_text = t
            # early exit: strong receipt detected
            if best_score >= 850:
                break
        if best_score >= 850:
            break

    best_text = (best_text or "").strip()
    if not best_text:
        return img, "OCR ran but did not detect text. Try a clearer photo (closer, brighter, less glare)."

    return img, best_text


