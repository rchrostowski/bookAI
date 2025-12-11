FROM python:3.11-slim

# System deps:
# - tesseract-ocr: OCR engine
# - libgl1 + libglib2.0-0: common runtime deps for imaging libs
# - tesseract-ocr-eng: English language pack
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY . /app

# Render sets $PORT automatically
CMD streamlit run app.py --server.address 0.0.0.0 --server.port $PORT --server.headless true
