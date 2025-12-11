from __future__ import annotations

import io
from typing import Tuple, List

import fitz  # PyMuPDF
import numpy as np
from PIL import Image, ImageOps, ImageEnhance

from rapidocr_onnxruntime import RapidOCR


# Single OCR engine instance
_OCR = RapidOCR()


def _pdf_first_page_to_pil(pdf_bytes: bytes, zoom: float = 2.0) -> Image.Image:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc.load_page(0)
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    return Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")


def _preprocess_pil(img: Image.Image) -> Image.Image:
    """
    Cloud-safe preprocessing (PIL only).
    """
    img = img.convert("RGB")
    img = ImageOps.grayscale(img)
    img = ImageOps.autocontrast(img)

    # Boost contrast/sharpness to help OCR on receipts
    img = ImageEnhance.Contrast(img).enhance(1.7)
    img = ImageEnhance.Sharpness(img).enhance(1.4)

    return img.convert("RGB")


def _rapidocr_text(img: Image.Image) -> str:
    """
    RapidOCR returns a list of [box, text, score]. We join lines.
    """
    arr = np.array(img)
    result, _ = _OCR(arr)

    if not result:
        return ""

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
    Returns (preview_image, raw_text)
    Works for images + PDFs on Streamlit Cloud (no system tesseract needed).
    """
    lower = (file_name or "").lower()

    if lower.endswith(".pdf"):
        pil_img = _pdf_first_page_to_pil(file_bytes)
    else:
        pil_img = Image.open(io.BytesIO(file_bytes)).convert("RGB")

    pre = _preprocess_pil(pil_img)
    text = _rapidocr_text(pre)
    return pre, text



