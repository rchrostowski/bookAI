from __future__ import annotations

from io import BytesIO
from typing import Tuple, Optional

from PIL import Image
import pytesseract


def ocr_upload(filename: str, file_bytes: bytes) -> Tuple[Optional[Image.Image], str]:
    """
    Returns:
      (preview_image, extracted_text)

    Guaranteed behavior:
    - For images: always attempts OCR
    - For PDFs: returns clear message instead of empty text
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
    # Image OCR
    # -----------------------
    try:
        img = Image.open(BytesIO(file_bytes)).convert("RGB")
    except Exception:
        return None, "Could not read image file."

    # Resize very large images (improves OCR reliability)
    max_side = max(img.size)
    if max_side > 2000:
        scale = 2000 / max_side
        img = img.resize(
            (int(img.size[0] * scale), int(img.size[1] * scale)),
            Image.LANCZOS,
        )

    # OCR config tuned for receipts
    config = "--psm 6"

    try:
        text = pytesseract.image_to_string(img, config=config)
    except Exception as e:
        return img, f"OCR error: {e}"

    text = (text or "").strip()

    if not text:
        return img, "OCR ran but did not detect text. Try a clearer photo."

    return img, text



