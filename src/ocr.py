from __future__ import annotations

import io
import shutil
from typing import Tuple

import fitz  # PyMuPDF
import numpy as np
import cv2
from PIL import Image
import pytesseract


def _tesseract_available() -> bool:
    """
    Streamlit Community Cloud typically does NOT have the system 'tesseract' binary.
    Locally, you installed it via brew.
    """
    return shutil.which("tesseract") is not None


def pdf_extract_text(pdf_bytes: bytes, max_pages: int = 2) -> str:
    """
    Extract embedded text from a PDF (works for digitally-generated invoices).
    This does NOT require OCR and works on Streamlit Cloud.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    text_parts = []
    for i in range(min(len(doc), max_pages)):
        page = doc.load_page(i)
        t = page.get_text("text") or ""
        if t.strip():
            text_parts.append(t)
    return "\n".join(text_parts).strip()


def pdf_first_page_to_pil(pdf_bytes: bytes, zoom: float = 2.0) -> Image.Image:
    """
    Render first page of a PDF as an image (needed for OCR on scanned PDFs).
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc.load_page(0)
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
    return img


def preprocess_for_ocr(pil_img: Image.Image) -> Image.Image:
    """
    Light preprocessing to improve OCR accuracy on receipts.
    """
    img = np.array(pil_img)
    img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.bilateralFilter(gray, 9, 75, 75)

    thr = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31,
        10,
    )

    return Image.fromarray(thr).convert("RGB")


def ocr_image(pil_img: Image.Image) -> str:
    """
    OCR if tesseract is available; otherwise return empty string (no crash).
    """
    if not _tesseract_available():
        return ""

    config = "--oem 3 --psm 6"
    try:
        return pytesseract.image_to_string(pil_img, config=config)
    except Exception:
        return ""


def ocr_upload(file_name: str, file_bytes: bytes) -> Tuple[Image.Image, str]:
    """
    Returns (preview_image_used_for_ocr, raw_text)

    Behavior:
    - Images: OCR if tesseract exists; otherwise returns "" (manual entry fallback).
    - PDFs:
        1) First try embedded text extraction (works on Streamlit Cloud).
        2) If empty, then try OCR on rendered first page (requires tesseract).
        3) If OCR not available, do NOT crash; return "" and let UI handle manual entry.
    """
    lower = file_name.lower()

    # PDF path
    if lower.endswith(".pdf"):
        # 1) Try embedded text first (cloud-friendly)
        embedded = pdf_extract_text(file_bytes)
        if embedded.strip():
            # Make a preview image anyway (nice UI), but not required for extraction
            preview = pdf_first_page_to_pil(file_bytes)
            return preview, embedded

        # 2) If it's scanned (no embedded text), try OCR if available
        preview = pdf_first_page_to_pil(file_bytes)
        pre = preprocess_for_ocr(preview)
        text = ocr_image(pre)

        # 3) If OCR isn't available, text will be ""
        return pre, text

    # Image path
    pil_img = Image.open(io.BytesIO(file_bytes)).convert("RGB")
    pre = preprocess_for_ocr(pil_img)
    text = ocr_image(pre)
    return pre, text


