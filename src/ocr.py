from __future__ import annotations

from io import BytesIO
from typing import Tuple, Optional, List

from PIL import Image, ImageOps, ImageEnhance, ImageFilter
import pytesseract


def _score_text(t: str) -> float:
    """
    Heuristic: prefer outputs that look like real receipt text.
    More letters/digits, more spaces/newlines, fewer weird symbols.
    """
    if not t:
        return 0.0
    t = t.strip()
    if not t:
        return 0.0

    n = len(t)
    letters = sum(ch.isalpha() for ch in t)
    digits = sum(ch.isdigit() for ch in t)
    spaces = sum(ch.isspace() for ch in t)
    newlines = t.count("\n")
    weird = sum((not ch.isalnum()) and (not ch.isspace()) and ch not in "$#&@.,:/()-" for ch in t)

    # Reward alnum density + structure, penalize junk
    score = 0.0
    score += 2.2 * (letters + digits)
    score += 0.5 * spaces
    score += 6.0 * newlines
    score -= 3.5 * weird

    # If it contains very receipt-y tokens, give bonus
    low = t.lower()
    bonuses = ["total", "visa", "mastercard", "amex", "discover", "tax", "subtotal", "date", "receipt", "store", "thank"]
    score += 60.0 * sum(1 for b in bonuses if b in low)

    # Normalize a bit so giant outputs don't dominate purely by length
    return score / max(1.0, n ** 0.15)


def _prep_variants(img: Image.Image) -> List[Image.Image]:
    """
    Generate multiple image variants to improve OCR:
    - grayscale
    - contrast boost
    - sharpen
    - binarized (threshold)
    Also returns a couple sizes (upscaled if needed).
    """
    variants: List[Image.Image] = []

    # Base: EXIF-corrected + grayscale
    base = ImageOps.exif_transpose(img)
    base = base.convert("L")

    # If image is small-ish, upscale (small receipt text needs it)
    w, h = base.size
    if max(w, h) < 1400:
        base_up = base.resize((int(w * 2.0), int(h * 2.0)), Image.LANCZOS)
        variants.append(base_up)
    variants.append(base)

    out: List[Image.Image] = []
    for im in variants:
        # 1) mild contrast
        c1 = ImageEnhance.Contrast(im).enhance(1.6)
        out.append(c1)

        # 2) stronger contrast + sharpen
        c2 = ImageEnhance.Contrast(im).enhance(2.2).filter(ImageFilter.UnsharpMask(radius=2, percent=180, threshold=3))
        out.append(c2)

        # 3) binarize (global threshold)
        # (works surprisingly well for receipts; avoids grey noise)
        bw = ImageEnhance.Contrast(im).enhance(2.4)
        bw = bw.point(lambda p: 255 if p > 165 else 0)
        out.append(bw)

        # 4) binarize + sharpen
        bw2 = bw.filter(ImageFilter.UnsharpMask(radius=2, percent=200, threshold=3))
        out.append(bw2)

    return out


def _run_tesseract(im: Image.Image) -> str:
    """
    Try multiple Tesseract configs and keep the best by scoring.
    """
    # Common best configs for receipts:
    # - psm 6: block of text
    # - psm 4: column-ish
    # - psm 11: sparse text
    # oem 1: LSTM engine
    configs = [
        "--oem 1 --psm 6",
        "--oem 1 --psm 4",
        "--oem 1 --psm 11",
        "--oem 1 --psm 3",
    ]

    best_text = ""
    best_score = -1e18

    for cfg in configs:
        try:
            t = pytesseract.image_to_string(im, config=cfg)
        except Exception:
            continue

        t = (t or "").strip()
        s = _score_text(t)
        if s > best_score:
            best_score = s
            best_text = t

    return best_text


def ocr_upload(filename: str, file_bytes: bytes) -> Tuple[Optional[Image.Image], str]:
    """
    Returns:
      (preview_image, extracted_text)

    Behavior:
    - Images: OCR with preprocessing + rotation search + multiple configs
    - PDFs: returns message (Cloud-safe)
    - Never crashes Streamlit Cloud
    """

    name = (filename or "").lower()

    # -----------------------
    # PDF handling (cloud-safe)
    # -----------------------
    if name.endswith(".pdf"):
        return None, (
            "PDF detected.\n"
            "PDF OCR is disabled on Streamlit Cloud.\n"
            "Please upload a photo (JPG or PNG) of the receipt."
        )

    # -----------------------
    # Load image
    # -----------------------
    try:
        img = Image.open(BytesIO(file_bytes))
        img = ImageOps.exif_transpose(img).convert("RGB")
    except Exception:
        return None, "Could not read image file."

    # Resize huge images down a bit (but not too much)
    max_side = max(img.size)
    if max_side > 2600:
        scale = 2600 / max_side
        img = img.resize((int(img.size[0] * scale), int(img.size[1] * scale)), Image.LANCZOS)

    # -----------------------
    # Generate variants + rotations
    # -----------------------
    variants = _prep_variants(img)

    # Try 0/90/180/270 rotations for each variant (receipt photos often sideways)
    rotations = [0, 90, 180, 270]

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

    best_text = (best_text or "").strip()

    if not best_text:
        return img, "OCR ran but did not detect text. Try a clearer photo (closer, brighter, less glare)."

    return img, best_text

