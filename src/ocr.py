from __future__ import annotations

import io
from typing import Tuple

import fitz  # PyMuPDF
import numpy as np
import cv2
from PIL import Image
import pytesseract

def pdf_first_page_to_pil(pdf_bytes: bytes, zoom: float = 2.0) -> Image.Image:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc.load_page(0)
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
    return img

def preprocess_for_ocr(pil_img: Image.Image) -> Image.Image:
    # Convert to OpenCV
    img = np.array(pil_img)
    img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)

    # Gray + denoise + threshold
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.bilateralFilter(gray, 9, 75, 75)

    # Adaptive threshold is robust for receipts
    thr = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 10
    )

    # Convert back to PIL
    out = Image.fromarray(thr).convert("RGB")
    return out

def ocr_image(pil_img: Image.Image) -> str:
    # psm 6 often works well for blocky receipts; you can tweak later
    config = "--oem 3 --psm 6"
    return pytesseract.image_to_string(pil_img, config=config)

def ocr_upload(file_name: str, file_bytes: bytes) -> Tuple[Image.Image, str]:
    lower = file_name.lower()
    if lower.endswith(".pdf"):
        pil_img = pdf_first_page_to_pil(file_bytes)
    else:
        pil_img = Image.open(io.BytesIO(file_bytes)).convert("RGB")

    pre = preprocess_for_ocr(pil_img)
    text = ocr_image(pre)
    return pre, text

