from fastapi.testclient import TestClient
import httpx

from app.main import app, is_unregistered_webhook, n8n_error_detail


def test_health_returns_ok():
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_frontend_is_served():
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "CobrarApp" in response.text


def test_process_image_handles_invalid_file():
    client = TestClient(app)

    response = client.post(
        "/procesar-imagen",
        files={"file": ("not-image.txt", b"not an image", "text/plain")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["valido"] is False
    assert payload["estado"] == "Error OCR"


def test_process_image_accepts_data_field():
    client = TestClient(app)

    response = client.post(
        "/procesar-imagen",
        files={"data": ("not-image.txt", b"not an image", "text/plain")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["valido"] is False
    assert payload["estado"] == "Error OCR"


def test_process_image_handles_empty_body():
    client = TestClient(app)

    response = client.post("/procesar-imagen", content=b"")

    assert response.status_code == 200
    payload = response.json()
    assert payload["valido"] is False
    assert payload["estado"] == "Error OCR"
    assert "vacia" in payload["errores"][0]


def test_n8n_webhook_error_is_human_readable():
    response = httpx.Response(
        404,
        json={
            "code": 404,
            "message": "The requested webhook \"POST cobrapp-pago\" is not registered.",
            "hint": "The workflow must be active for a production URL to run successfully.",
        },
    )

    detail = n8n_error_detail(response)

    assert "Activa el workflow" in detail
    assert "/webhook-test/cobrapp-pago" in detail


def test_detects_unregistered_n8n_webhook():
    response = httpx.Response(
        404,
        json={"message": "The requested webhook \"POST cobrapp-pago\" is not registered."},
    )

    assert is_unregistered_webhook(response) is True


def test_register_payment_and_report(tmp_path, monkeypatch):
    monkeypatch.setenv("PAYMENTS_XLSX_PATH", str(tmp_path / "pagos.xlsx"))
    client = TestClient(app)

    payment = {
        "valido": True,
        "fecha": "02/05/2026",
        "hora": "18:07",
        "nombre": "Juan Perez",
        "monto": 50,
        "tipo": "Yape",
        "operacion": "12345678",
        "estado": "Registrado",
        "texto_raw": "Yape S/ 50 Operacion 12345678",
    }
    register_response = client.post("/registrar-pago", json=payment)
    report_response = client.get("/reporte?fecha=2026-05-02")

    assert register_response.status_code == 200
    assert register_response.json()["registrado"] is True
    assert "reporte" in register_response.json()
    assert report_response.status_code == 200
    assert report_response.json()["total_recaudado"] == 50


def test_whatsapp_ignores_private_chat_event():
    client = TestClient(app)

    response = client.post(
        "/whatsapp/procesar-evento",
        json={"body": {"from": "51999999999@c.us", "type": "image"}},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["estado"] == "Ignorado"
    assert payload["debe_confirmar"] is False


def test_whatsapp_requests_image_when_group_message_is_not_image():
    client = TestClient(app)

    response = client.post(
        "/whatsapp/procesar-evento",
        json={"body": {"from": "120363000000000000@g.us", "type": "chat"}},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["estado"] == "Ignorado"
    assert payload["debe_confirmar"] is True
    assert payload["group_id"] == "120363000000000000@g.us"


def test_whatsapp_uses_configured_group_for_outgoing_image_without_chat_id(monkeypatch):
    monkeypatch.setenv("WHATSAPP_GROUP_ID", "120363000000000000@g.us")
    client = TestClient(app)

    response = client.post(
        "/whatsapp/procesar-evento",
        json={
            "body": {
                "event": "onack",
                "from": "51999999999@c.us",
                "to": "204363352018991@lid",
                "type": "image",
            }
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["estado"] == "Error OCR"
    assert payload["debe_confirmar"] is True
    assert payload["group_id"] == "120363000000000000@g.us"


def test_whatsapp_decodes_outgoing_image_body_base64(tmp_path, monkeypatch):
    monkeypatch.setenv("WHATSAPP_GROUP_ID", "120363000000000000@g.us")
    monkeypatch.setenv("PAYMENTS_XLSX_PATH", str(tmp_path / "pagos.xlsx"))
    client = TestClient(app)

    response = client.post(
        "/whatsapp/procesar-evento",
        json={
            "body": {
                "event": "onack",
                "from": "51999999999@c.us",
                "to": "204363352018991@lid",
                "type": "image",
                "mimetype": "image/png",
                "body": "bm90LWFuLWltYWdl",
            }
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["estado"] == "Error OCR"
    assert payload["group_id"] == "120363000000000000@g.us"
    assert "No se pudo validar" in payload["confirmacion"]


def test_whatsapp_ignores_own_non_image_group_message():
    client = TestClient(app)

    response = client.post(
        "/whatsapp/procesar-evento",
        json={
            "body": {
                "event": "onack",
                "fromMe": True,
                "chatId": "120363000000000000@g.us",
                "type": "chat",
                "body": "CobrarApp conectado",
            }
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["estado"] == "Ignorado"
    assert payload["debe_confirmar"] is False
    assert payload["mensaje"] == "Mensaje propio ignorado."


def test_whatsapp_ignores_events_from_other_groups(monkeypatch):
    monkeypatch.setenv("WHATSAPP_GROUP_ID", "120363000000000000@g.us")
    client = TestClient(app)

    response = client.post(
        "/whatsapp/procesar-evento",
        json={"body": {"from": "120363999999999999@g.us", "type": "image"}},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["estado"] == "Ignorado"
    assert payload["debe_confirmar"] is False
    assert payload["mensaje"] == "El evento pertenece a otro grupo de WhatsApp."


def test_whatsapp_bridge_filters_noise_before_n8n(monkeypatch):
    monkeypatch.setenv("WHATSAPP_GROUP_ID", "120363000000000000@g.us")
    client = TestClient(app)

    response = client.post(
        "/whatsapp/n8n-bridge",
        json={"event": "onpresencechanged", "isGroup": False, "type": "chat"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["estado"] == "Ignorado"
    assert payload["forwarded"] is False


def test_whatsapp_bridge_filters_non_payment_image(tmp_path, monkeypatch):
    monkeypatch.setenv("WHATSAPP_GROUP_ID", "120363000000000000@g.us")
    monkeypatch.setenv("PAYMENTS_XLSX_PATH", str(tmp_path / "pagos.xlsx"))
    client = TestClient(app)

    response = client.post(
        "/whatsapp/n8n-bridge",
        json={
            "event": "onack",
            "from": "51999999999@c.us",
            "to": "204363352018991@lid",
            "type": "image",
            "mimetype": "image/png",
            "body": "bm90LWFuLWltYWdl",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["estado"] == "Ignorado"
    assert payload["forwarded"] is False
    assert "Imagen ignorada" in payload["mensaje"]
