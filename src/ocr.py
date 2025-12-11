from __future__ import annotations

import io
from typing import Tuple, List

import fitz  # PyMuPDF
import numpy as np
from PIL import Image, ImageOps, ImageEnhance

from rapidocr_onnxruntime import RapidOCR


# Create one OCR engine instance (cached by Python module import)
_OCR = RapidOCR()


def _pdf_first_page_to_pil(pdf_bytes: bytes, zoom: float = 2.0) -> Image.Image:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc.load_page(0)
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    return Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")


def _preprocess_pil(img: Image.Image) -> Image.Image:
    """
    Lightweight preprocessing using PIL only (cloud-safe).
    """
    img = img.convert("RGB")
    img = ImageOps.grayscale(img)
    img = ImageOps.autocontrast(img)

    # Slightly boost contrast/sharpness
    img = ImageEnhance.Contrast(img).enhance(1.6)
    img = ImageEnhance.Sharpness(img).enhance(1.4)

    return img.convert("RGB")


def _rapidocr_text(img: Image.Image) -> str:
    """
    RapidOCR returns list of detected text lines with confidence.
    We'll join them into a single raw_text block for parsing.
    """
    arr = np.array(img)
    result, _elapse = _OCR(arr)

    if not result:
        return ""

    # result items look like: [ [box_points], "text", score ]
    lines: List[str] = []
    for item in result:
        try:
            text = item[1]
            if text and str(text).strip():
                lines.append(str(text).strip())
        except Exception:
            continue

    return "\n".join(lines).strip()


def ocr_upload(file_name: str, file_bytes: bytes) -> Tuple[Image.Image, str]:
    """
    Returns: (preview_image, raw_text)
    Works on Streamlit Cloud (no tesseract/cv2).
    """
    lower = (file_name or "").lower()

    if lower.endswith(".pdf"):
        pil_img = _pdf_first_page_to_pil(file_bytes)
    else:
        pil_img = Image.open(io.BytesIO(file_bytes)).convert("RGB")

    pre = _preprocess_pil(pil_img)
    text = _rapidocr_text(pre)

    return pre, text



