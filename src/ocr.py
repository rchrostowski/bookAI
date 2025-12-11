from __future__ import annotations

import io
from typing import Tuple

import fitz  # PyMuPDF
import numpy as np
import cv2
from PIL import Image
import pytesseract


def _pdf_first_page_to_pil(pdf_bytes: bytes, zoom: float = 2.0) -> Image.Image:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc.load_page(0)
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
    return img


def _preprocess_for_ocr(pil_img: Image.Image) -> Image.Image:
    """
    Strong receipt-friendly preprocessing:
    - grayscale
    - denoise
    - adaptive threshold
    """
    img = np.array(pil_img)
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    gray = cv2.bilateralFilter(gray, 9, 75, 75)

    thr = cv2.adaptiveThreshold(
        gray, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31, 10
    )
    return Image.fromarray(thr).convert("RGB")


def _tesseract_ocr(pil_img: Image.Image) -> str:
    config = "--oem 3 --psm 6"
    try:
        return pytesseract.image_to_string(pil_img, config=config) or ""
    except Exception:
        return ""


def ocr_upload(file_name: str, file_bytes: bytes) -> Tuple[Image.Image, str]:
    """
    Returns (preview_image, raw_text)
    - Images: OCR directly
    - PDFs: render first page -> OCR
    """
    lower = (file_name or "").lower()

    if lower.endswith(".pdf"):
        pil_img = _pdf_first_page_to_pil(file_bytes)
    else:
        pil_img = Image.open(io.BytesIO(file_bytes)).convert("RGB")

    pre = _preprocess_for_ocr(pil_img)
    text = _tesseract_ocr(pre)
    return pre, text



