from __future__ import annotations

import re
import unicodedata
from datetime import datetime

from .models import ProcessedPayment


def _strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value)
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")


def normalize_text(text: str) -> str:
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def _today() -> str:
    return datetime.now().strftime("%d/%m/%Y")


def _now_time() -> str:
    return datetime.now().strftime("%H:%M")


def _parse_amount_str(value: str) -> float | None:
    """Parse a string like '17.50' or '17,50' to float, return None if invalid."""
    try:
        result = float(value.replace(",", "."))
        return result if result > 0 else None
    except ValueError:
        return None


def extract_amount(text: str) -> float | None:
    lines = [line.strip() for line in normalize_text(text).splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if not re.search(r"(yapeaste|plinaste)", _strip_accents(line), flags=re.IGNORECASE):
            continue
        for candidate in lines[index + 1 : index + 4]:
            # 1. Try explicit S/ pattern first — preserves decimals correctly
            m = re.search(
                r"(?:s\s*/\s*\.?|s/\.?)\s*([0-9]{1,5}(?:[.,][0-9]{1,2})?)",
                candidate,
                flags=re.IGNORECASE,
            )
            if m:
                val = _parse_amount_str(m.group(1))
                if val is not None:
                    return val
            # 2. Standalone decimal number on the candidate line (e.g. "17.50")
            m2 = re.search(r"(?<!\d)([0-9]{1,5}[.,][0-9]{2})(?!\d)", candidate)
            if m2:
                val = _parse_amount_str(m2.group(1))
                if val is not None:
                    return val
            # 3. Legacy: digit-strip with phone-prefix handling
            digits = re.sub(r"\D", "", candidate)
            if not digits:
                continue
            if digits.startswith("91") and len(digits) >= 3:
                return float(digits[2:])
            if digits.startswith("9") and len(digits) >= 3:
                return float(digits[1:])

    patterns = [
        r"(?:s\s*/\s*\.?|s/\.?|soles?)\s*([0-9]{1,5}(?:[.,][0-9]{1,2})?)",
        r"(?:monto|importe|total|pago)\D{0,20}([0-9]{1,5}(?:[.,][0-9]{1,2})?)",
        r"\b([0-9]{1,5}[.,][0-9]{2})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            val = _parse_amount_str(match.group(1))
            if val is not None:
                return val
    return None


def extract_payment_type(text: str) -> str | None:
    lowered = _strip_accents(text).lower()
    if "yape" in lowered:
        return "Yape"
    if "plin" in lowered:
        return "Plin"
    return None


def extract_operation(text: str) -> str | None:
    patterns = [
        r"(?:nro\.?\s+de\s+operaci[oó]n|n[°º]\s+de\s+operaci[oó]n|operaci[oó]n|operacion)\D{0,24}([A-Z0-9-]{5,})",
        r"(?:constancia)\D{0,18}([A-Z0-9-]{5,})",
        r"\b([0-9]{6,14})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return re.sub(r"\D", "", match.group(1)) or match.group(1).strip()
    return None


def extract_date(text: str) -> str:
    match = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b", text)
    if not match:
        months = {
            "ene": 1,
            "feb": 2,
            "mar": 3,
            "abr": 4,
            "may": 5,
            "jun": 6,
            "jul": 7,
            "ago": 8,
            "sep": 9,
            "set": 9,
            "oct": 10,
            "nov": 11,
            "dic": 12,
        }
        month_match = re.search(
            r"\b(\d{1,2})\s+(ene|feb|mar|abr|may|jun|jul|ago|sep|set|oct|nov|dic)\.?\s+(\d{2,4})\b",
            _strip_accents(text).lower(),
        )
        if not month_match:
            return _today()
        day, month_name, year = month_match.groups()
        month = str(months[month_name])
    else:
        day, month, year = match.groups()
    if len(year) == 2:
        year = "20" + year
    return f"{int(day):02d}/{int(month):02d}/{year}"


def extract_time(text: str) -> str:
    match = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", text)
    if not match:
        return _now_time()
    hour, minute = match.groups()
    return f"{int(hour):02d}:{minute}"


def extract_name(text: str) -> str | None:
    lines = [line.strip() for line in normalize_text(text).splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if not re.search(r"(yapeaste|plinaste)", _strip_accents(line), flags=re.IGNORECASE):
            continue
        amount_seen = False
        for candidate_line in lines[index + 1 : index + 6]:
            if not amount_seen:
                amount_seen = bool(re.search(r"\d", candidate_line))
                continue
            candidate = re.sub(r"[^A-Za-zÁÉÍÓÚÑáéíóúñ' -]", "", candidate_line)
            candidate = re.sub(r"\s+", " ", candidate).strip()
            lowered = _strip_accents(candidate).lower()
            if (
                len(candidate) >= 4
                and not re.search(r"\d", candidate)
                and not any(word in lowered for word in ["codigo", "seguridad", "datos", "transaccion"])
            ):
                return candidate[:60]

    for index, line in enumerate(lines[:-1]):
        if extract_amount(line) is None:
            continue
        candidate = re.sub(r"[^A-Za-zÁÉÍÓÚÑáéíóúñ' -]", "", lines[index + 1])
        candidate = re.sub(r"\s+", " ", candidate).strip()
        lowered = _strip_accents(candidate).lower()
        if (
            len(candidate) >= 4
            and not re.search(r"\d", candidate)
            and not any(
                word in lowered
                for word in [
                    "compartir",
                    "codigo",
                    "seguridad",
                    "datos",
                    "transaccion",
                    "destino",
                    "operacion",
                    "yape tienda",
                    "hasta",
                    "descto",
                    "tecnologia",
                    "captura",
                    "flujo",
                    "probar",
                    "cobrapp",
                ]
            )
        ):
            return candidate[:60]

    patterns = [
        r"(?:de|pagado por|nombre|cliente)\s*:?\s*([A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ' -]{3,60})",
        r"(?:enviado por|recibiste de)\s*:?\s*([A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ' -]{3,60})",
    ]
    for line in lines:
        line_lowered = _strip_accents(line).lower()
        if any(word in line_lowered for word in ["codigo", "seguridad", "datos", "transaccion"]):
            continue
        for pattern in patterns:
            match = re.search(pattern, line, flags=re.IGNORECASE)
            if match:
                value = re.sub(r"\s+", " ", match.group(1)).strip(" .-")
                if not any(word in value.lower() for word in ["yape", "plin", "soles"]):
                    return value[:60]
    return None


def parse_payment_text(text: str) -> ProcessedPayment:
    raw = normalize_text(text)
    amount = extract_amount(raw)
    payment_type = extract_payment_type(raw)
    operation = extract_operation(raw)
    errors: list[str] = []

    if amount is None:
        errors.append("No se encontro monto")
    elif amount <= 0:
        errors.append("El monto debe ser mayor a cero")
    if payment_type is None:
        errors.append("No se encontro tipo de pago Yape o Plin")
    if operation is None:
        errors.append("No se encontro numero de operacion")

    valid = not errors
    return ProcessedPayment(
        valido=valid,
        fecha=extract_date(raw),
        hora=extract_time(raw),
        nombre=extract_name(raw),
        monto=amount,
        tipo=payment_type,
        operacion=operation,
        estado="Registrado" if valid else "Invalido",
        errores=errors,
        texto_raw=raw,
    )
