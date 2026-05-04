from __future__ import annotations

from io import BytesIO

import cv2
import numpy as np
import pytesseract
import pillow_avif  # noqa: F401 - registers AVIF support in Pillow.
from PIL import Image, UnidentifiedImageError

from .models import ProcessedPayment
from .parser import parse_payment_text


def decode_image(image_bytes: bytes) -> np.ndarray:
    if not image_bytes:
        raise ValueError("La imagen recibida esta vacia")

    buffer = np.frombuffer(image_bytes, np.uint8)
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if image is not None:
        return image

    try:
        with Image.open(BytesIO(image_bytes)) as pil_image:
            rgb = pil_image.convert("RGB")
            return cv2.cvtColor(np.array(rgb), cv2.COLOR_RGB2BGR)
    except UnidentifiedImageError as exc:
        raise ValueError(
            "No se pudo decodificar la imagen. Usa PNG, JPG, WebP o AVIF valido."
        ) from exc


def preprocess_image(image_bytes: bytes) -> np.ndarray:
    image = decode_image(image_bytes)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.bilateralFilter(gray, 9, 75, 75)
    return cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31,
        11,
    )


def process_image_bytes(image_bytes: bytes) -> ProcessedPayment:
    try:
        processed = preprocess_image(image_bytes)
        text = pytesseract.image_to_string(processed, lang="spa")
        payment = parse_payment_text(text)
        if not payment.texto_raw:
            payment.valido = False
            payment.estado = "Error OCR"
            payment.errores.append("OCR no devolvio texto")
        return payment
    except Exception as exc:
        return ProcessedPayment(
            valido=False,
            estado="Error OCR",
            errores=[str(exc)],
            texto_raw="",
        )
