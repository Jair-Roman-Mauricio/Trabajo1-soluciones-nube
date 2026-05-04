import os
import base64
import logging
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .models import RegisterPaymentRequest, RegisterPaymentResponse, ReportResponse
from .models import PaymentSource
from .ocr import decode_image, process_image_bytes
from .storage import build_report, list_payments, register_payment, report_path, save_daily_report

logger = logging.getLogger("cobrapp.whatsapp")

app = FastAPI(
    title="CobrarApp API",
    description="API OCR para registrar pagos Yape/Plin desde n8n.",
    version="1.0.0",
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def n8n_payment_webhook_url() -> str:
    return os.getenv("N8N_PAYMENT_WEBHOOK_URL", "http://n8n:5678/webhook/cobrapp-pago")


def n8n_payment_webhook_test_url() -> str:
    return os.getenv(
        "N8N_PAYMENT_WEBHOOK_TEST_URL",
        "http://n8n:5678/webhook-test/cobrapp-pago",
    )


def n8n_whatsapp_webhook_url() -> str:
    return os.getenv(
        "N8N_WHATSAPP_WEBHOOK_URL",
        "http://n8n:5678/webhook/cobrapp-whatsapp",
    )


def n8n_whatsapp_webhook_test_url() -> str:
    return os.getenv(
        "N8N_WHATSAPP_WEBHOOK_TEST_URL",
        "http://n8n:5678/webhook-test/cobrapp-whatsapp",
    )


def wppconnect_base_url() -> str:
    return os.getenv("WPPCONNECT_BASE_URL", "http://wppconnect:21465")


def wppconnect_session() -> str:
    return os.getenv("WPPCONNECT_SESSION", "cobrapp")


def wppconnect_token() -> str | None:
    return os.getenv("WPPCONNECT_TOKEN")


def whatsapp_group_id_fallback() -> str | None:
    value = os.getenv("WHATSAPP_GROUP_ID")
    return value.strip() if value and value.strip().endswith("@g.us") else None


def n8n_error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = {}

    message = str(payload.get("message") or response.text)
    hint = str(payload.get("hint") or "")
    if response.status_code == 404 and "webhook" in message.lower():
        return (
            "El Webhook de n8n no esta registrado. Activa el workflow en n8n con el toggle "
            "Active de la esquina superior derecha. Si estas usando Execute workflow, llama la "
            "URL /webhook-test/cobrapp-pago o usa el boton Procesar directo."
        )
    return " ".join(part for part in [message, hint] if part).strip()


def is_unregistered_webhook(response: httpx.Response) -> bool:
    if response.status_code != 404:
        return False
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    message = str(payload.get("message") or response.text).lower()
    return "webhook" in message and "not registered" in message


def process_and_register_direct(image_bytes: bytes):
    payment = process_image_bytes(image_bytes)
    response = register_payment(payment)
    return {
        "ok": response.registrado,
        "estado": response.estado,
        "mensaje": response.mensaje,
        "pago": response.pago.model_dump(),
        "reporte": response.reporte,
        "modo": "directo-backend",
    }


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _message_from_whatsapp_event(event: dict[str, Any]) -> dict[str, Any]:
    body = _as_dict(event.get("body"))
    if body:
        return body
    data = _as_dict(event.get("data"))
    if data:
        return data
    return event


def _first_text(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _message_id(message: dict[str, Any]) -> str | None:
    raw_id = message.get("id")
    if isinstance(raw_id, dict):
        return _first_text(raw_id.get("_serialized"), raw_id.get("id"))
    return _first_text(raw_id, message.get("messageId"), message.get("idMessage"))


def _short_text(value: Any, max_length: int = 80) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip().replace("\n", " ")
    if len(value) <= max_length:
        return value
    return f"{value[:max_length]}..."


def _whatsapp_group_id(message: dict[str, Any]) -> str | None:
    chat = _as_dict(message.get("chat"))
    candidates = [
        message.get("chatId"),
        chat.get("id"),
        message.get("from"),
        message.get("to"),
    ]
    for candidate in candidates:
        if isinstance(candidate, dict):
            candidate = candidate.get("_serialized") or candidate.get("id")
        if isinstance(candidate, str) and candidate.endswith("@g.us"):
            return candidate
    return None


def _is_whatsapp_group_message(message: dict[str, Any]) -> bool:
    return bool(
        message.get("isGroupMsg")
        or message.get("isGroup")
        or _whatsapp_group_id(message)
    )


def _is_image_message(message: dict[str, Any]) -> bool:
    message_type = str(message.get("type") or "").lower()
    mimetype = str(message.get("mimetype") or "").lower()
    return (
        message_type == "image"
        or mimetype.startswith("image/")
        or bool(message.get("isMedia") and "image" in mimetype)
    )


def _is_from_me(message: dict[str, Any]) -> bool:
    raw_id = message.get("id")
    return bool(
        message.get("fromMe")
        or str(message.get("event") or "").lower() == "onack"
        or (isinstance(raw_id, dict) and raw_id.get("fromMe"))
    )


def _prepared_whatsapp_message(event: dict[str, Any]) -> tuple[dict[str, Any], str | None, bool]:
    message = _message_from_whatsapp_event(event)
    group_id = _whatsapp_group_id(message)
    is_image = _is_image_message(message)
    configured_group_id = whatsapp_group_id_fallback()
    if not group_id and is_image:
        group_id = configured_group_id
    return message, group_id, is_image


def _whatsapp_event_summary(
    event: dict[str, Any],
    message: dict[str, Any],
    group_id: str | None,
    is_image: bool,
) -> dict[str, Any]:
    raw_id = message.get("id")
    participant = raw_id.get("participant") if isinstance(raw_id, dict) else None
    return {
        "event": message.get("event") or event.get("event"),
        "type": message.get("type"),
        "mimetype": message.get("mimetype"),
        "from": message.get("from"),
        "to": message.get("to"),
        "chatId": message.get("chatId"),
        "participant": participant,
        "isGroupMsg": message.get("isGroupMsg"),
        "fromMe": _is_from_me(message),
        "group_id": group_id,
        "is_image": is_image,
        "message_id": _message_id(message),
        "caption": _short_text(message.get("caption")),
        "body": _short_text(message.get("body")),
    }


def _should_process_whatsapp_event(message: dict[str, Any], group_id: str | None, is_image: bool) -> bool:
    configured_group_id = whatsapp_group_id_fallback()
    if not is_image:
        return False
    if not _is_whatsapp_group_message(message) and not group_id:
        return False
    if configured_group_id and group_id != configured_group_id:
        return False
    return True


def _base64_candidates(payload: dict[str, Any]):
    for key in ("base64", "mediaBase64", "imageBase64", "body", "file", "data"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            yield value.strip()

    for key in ("media", "mediaData"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            yield from _base64_candidates(nested)


def _extract_base64_value(payload: dict[str, Any]) -> str | None:
    candidates = []
    seen = set()
    for candidate in _base64_candidates(payload):
        if candidate not in seen:
            seen.add(candidate)
            candidates.append(candidate)

    valid_images: list[tuple[int, str]] = []
    for candidate in candidates:
        try:
            image_bytes = _decode_base64_image(candidate)
            decode_image(image_bytes)
        except Exception:
            continue
        valid_images.append((len(image_bytes), candidate))

    if valid_images:
        return max(valid_images, key=lambda item: item[0])[1]
    return candidates[0] if candidates else None


def _decode_base64_image(value: str) -> bytes:
    if "," in value and value.lstrip().startswith("data:"):
        value = value.split(",", 1)[1]
    return base64.b64decode(value, validate=False)


async def _download_wppconnect_media(message_id: str) -> bytes | None:
    token = wppconnect_token()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    url = f"{wppconnect_base_url().rstrip('/')}/api/{wppconnect_session()}/download-media"
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(url, json={"messageId": message_id}, headers=headers)
    if response.status_code >= 400:
        return None
    try:
        payload = response.json()
    except ValueError:
        return response.content if response.content else None
    encoded = _extract_base64_value(_as_dict(payload))
    return _decode_base64_image(encoded) if encoded else None


async def _image_bytes_from_message(message: dict[str, Any], message_id: str | None) -> bytes | None:
    encoded = _extract_base64_value(message)
    image_bytes = _decode_base64_image(encoded) if encoded else None
    if image_bytes is None and message_id:
        image_bytes = await _download_wppconnect_media(message_id)
    return image_bytes


def _looks_like_payment_capture(payment) -> bool:
    raw_lower = payment.texto_raw.lower()
    return bool(payment.tipo or "yape" in raw_lower or "plin" in raw_lower)


async def _process_whatsapp_image(message: dict[str, Any], message_id: str | None):
    image_bytes = await _image_bytes_from_message(message, message_id)
    if not image_bytes:
        return None, None

    payment = process_image_bytes(image_bytes)
    if _looks_like_payment_capture(payment) or not message_id:
        return payment, image_bytes

    downloaded = await _download_wppconnect_media(message_id)
    if not downloaded:
        return payment, image_bytes

    downloaded_payment = process_image_bytes(downloaded)
    if _looks_like_payment_capture(downloaded_payment) or len(downloaded_payment.texto_raw) > len(payment.texto_raw):
        return downloaded_payment, downloaded
    return payment, image_bytes


def _confirmation_message(response: RegisterPaymentResponse) -> str:
    payment = response.pago
    tipo = payment.tipo or "pago"
    monto = f"S/. {float(payment.monto or 0):.2f}"
    nombre = payment.nombre or "cliente"
    operacion = payment.operacion or "sin código"
    if response.estado == "Registrado":
        return (
            f"✅ Tu {tipo} fue validado por {monto} de {nombre}. "
            f"Operación {operacion}."
        )
    if response.estado == "Duplicado":
        return (
            f"⚠️ Tu {tipo} de {monto} ya fue registrado anteriormente. "
            f"Operación {operacion}."
        )
    return (
        "❌ No se pudo validar la captura de pago. "
        f"Errores: {', '.join(payment.errores) if payment.errores else response.estado}."
    )


@app.get("/")
def frontend() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/procesar-imagen")
async def procesar_imagen(
    request: Request,
    file: UploadFile | None = File(default=None),
    data: UploadFile | None = File(default=None),
):
    upload = file or data
    if upload:
        content = await upload.read()
    else:
        content = await request.body()
    return process_image_bytes(content)


@app.post("/activar-flujo")
async def activar_flujo(data: UploadFile = File(...)):
    content = await data.read()
    files = {
        "data": (
            data.filename or "captura.png",
            content,
            data.content_type or "application/octet-stream",
        )
    }
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(n8n_payment_webhook_url(), files=files)
            mode = "production-webhook"
            if is_unregistered_webhook(response):
                response = await client.post(n8n_payment_webhook_test_url(), files=files)
                mode = "test-webhook"
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"No se pudo conectar con n8n: {exc}",
        ) from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=n8n_error_detail(response),
        )

    try:
        payload = response.json()
        if isinstance(payload, dict):
            payload.setdefault("modo", mode)
        return payload
    except ValueError:
        return {"ok": True, "estado": "Procesado", "mensaje": response.text, "modo": mode}


@app.post("/procesar-directo")
async def procesar_directo(data: UploadFile = File(...)):
    content = await data.read()
    return process_and_register_direct(content)


@app.post("/whatsapp/n8n-bridge")
async def whatsapp_n8n_bridge(event: dict[str, Any]):
    message, group_id, is_image = _prepared_whatsapp_message(event)
    message_id = _message_id(message)
    summary = _whatsapp_event_summary(event, message, group_id, is_image)
    logger.warning("wpp_bridge_received %s", summary)
    if not _should_process_whatsapp_event(message, group_id, is_image):
        logger.warning("wpp_bridge_ignored reason=not_processable %s", summary)
        return {
            "ok": False,
            "estado": "Ignorado",
            "forwarded": False,
            "group_id": group_id,
            "mensaje": "Evento ignorado por el puente antes de llegar a n8n.",
        }

    preview_payment, image_bytes = await _process_whatsapp_image(message, message_id)
    if not image_bytes or not preview_payment:
        logger.warning("wpp_bridge_ignored reason=no_image_bytes %s", summary)
        return {
            "ok": False,
            "estado": "Ignorado",
            "forwarded": False,
            "group_id": group_id,
            "mensaje": "Imagen ignorada porque no se pudo leer antes de llamar a n8n.",
        }

    if not _looks_like_payment_capture(preview_payment):
        logger.warning(
            "wpp_bridge_ignored reason=not_payment group_id=%s preview=%r",
            group_id,
            preview_payment.texto_raw[:120],
        )
        return {
            "ok": False,
            "estado": "Ignorado",
            "forwarded": False,
            "group_id": group_id,
            "mensaje": "Imagen ignorada porque no parece una captura Yape o Plin.",
            "ocr_preview": preview_payment.texto_raw[:120],
        }

    test_url = n8n_whatsapp_webhook_test_url()
    prod_url = n8n_whatsapp_webhook_url()
    async with httpx.AsyncClient(timeout=90) as client:
        response = await client.post(test_url, json=event)
        mode = "test-webhook"
        if is_unregistered_webhook(response):
            response = await client.post(prod_url, json=event)
            mode = "production-webhook"
            if is_unregistered_webhook(response):
                logger.warning("wpp_bridge_not_forwarded reason=n8n_not_registered %s", summary)
                return {
                    "ok": False,
                    "estado": "n8n-no-registrado",
                    "forwarded": False,
                    "group_id": group_id,
                    "mensaje": (
                        "n8n no tiene registrado el webhook de prueba ni el de produccion. "
                        "Haz clic en Execute workflow o activa/publica el workflow."
                    ),
                }
        if response.status_code >= 400:
            response = await client.post(prod_url, json=event)
            mode = "production-webhook"

    if response.status_code >= 400:
        logger.warning(
            "wpp_bridge_n8n_error status=%s mode=%s %s",
            response.status_code,
            mode,
            summary,
        )
        return {
            "ok": False,
            "estado": "Error n8n",
            "forwarded": False,
            "group_id": group_id,
            "status_code": response.status_code,
            "mensaje": n8n_error_detail(response),
        }

    try:
        payload = response.json()
    except ValueError:
        payload = {"raw": response.text}
    logger.warning("wpp_bridge_forwarded mode=%s %s", mode, summary)
    return {
        "ok": True,
        "estado": "Forwarded",
        "forwarded": True,
        "mode": mode,
        "group_id": group_id,
        "n8n": payload,
    }


@app.post("/whatsapp/procesar-evento")
async def whatsapp_procesar_evento(event: dict[str, Any]):
    message, group_id, is_image = _prepared_whatsapp_message(event)
    message_id = _message_id(message)
    configured_group_id = whatsapp_group_id_fallback()
    summary = _whatsapp_event_summary(event, message, group_id, is_image)
    logger.warning("wpp_process_received %s", summary)

    if not _is_whatsapp_group_message(message) and not group_id:
        logger.warning("wpp_process_ignored reason=not_group %s", summary)
        return {
            "ok": False,
            "estado": "Ignorado",
            "debe_confirmar": False,
            "mensaje": "El evento no proviene de un grupo de WhatsApp.",
        }

    if configured_group_id and group_id != configured_group_id:
        logger.warning("wpp_process_ignored reason=other_group %s", summary)
        return {
            "ok": False,
            "estado": "Ignorado",
            "debe_confirmar": False,
            "group_id": group_id,
            "mensaje": "El evento pertenece a otro grupo de WhatsApp.",
        }

    if _is_from_me(message) and not is_image:
        logger.warning("wpp_process_ignored reason=own_non_image %s", summary)
        return {
            "ok": False,
            "estado": "Ignorado",
            "debe_confirmar": False,
            "group_id": group_id,
            "mensaje": "Mensaje propio ignorado.",
        }

    if not is_image:
        logger.warning("wpp_process_ignored reason=non_image %s", summary)
        return {
            "ok": False,
            "estado": "Ignorado",
            "debe_confirmar": True,
            "group_id": group_id,
            "confirmacion": "Envia una captura de pago Yape o Plin como imagen.",
        }

    payment, image_bytes = await _process_whatsapp_image(message, message_id)
    if not image_bytes or not payment:
        logger.warning("wpp_process_error reason=no_image_bytes %s", summary)
        return {
            "ok": False,
            "estado": "Error OCR",
            "debe_confirmar": True,
            "group_id": group_id,
            "confirmacion": "No pude descargar la imagen del mensaje de WhatsApp.",
        }

    payment.source = PaymentSource(canal="whatsapp", chat_id=group_id, message_id=message_id)
    response = register_payment(payment)
    confirmation = _confirmation_message(response)
    logger.warning(
        "wpp_process_result estado=%s group_id=%s message_id=%s monto=%s tipo=%s operacion=%s",
        response.estado,
        group_id,
        message_id,
        response.pago.monto,
        response.pago.tipo,
        response.pago.operacion,
    )
    return {
        "ok": response.registrado,
        "estado": response.estado,
        "debe_confirmar": True,
        "group_id": group_id,
        "message_id": message_id,
        "confirmacion": confirmation,
        "pago": response.pago.model_dump(),
        "reporte": response.reporte,
    }


@app.post("/registrar-pago", response_model=RegisterPaymentResponse)
def registrar_pago(payment: RegisterPaymentRequest) -> RegisterPaymentResponse:
    return register_payment(payment)


@app.get("/pagos")
def pagos(fecha: str | None = None) -> list[dict]:
    return list_payments(fecha)


@app.get("/reporte", response_model=ReportResponse)
def reporte(fecha: str | None = None) -> ReportResponse:
    return build_report(fecha)


@app.get("/reporte/excel")
def reporte_excel(fecha: str | None = None) -> FileResponse:
    """Return (and refresh) the daily report Excel file."""
    report = build_report(fecha)
    save_daily_report(report)
    path = report_path()
    if not path.exists():
        raise HTTPException(status_code=404, detail="Reporte no encontrado.")
    return FileResponse(
        path=str(path),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="reporte_diario.xlsx",
    )
