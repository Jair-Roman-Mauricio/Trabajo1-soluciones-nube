from app.models import ProcessedPayment
from app.storage import HEADERS, build_report, list_payments, register_payment


def _payment(operation: str = "12345678", amount: float = 50.0) -> ProcessedPayment:
    return ProcessedPayment(
        valido=True,
        fecha="02/05/2026",
        hora="18:07",
        nombre="Juan Perez",
        monto=amount,
        tipo="Yape",
        operacion=operation,
        estado="Registrado",
        texto_raw="Yape S/ 50.00 Operacion 12345678",
    )


def test_registers_payment_and_lists_it(tmp_path, monkeypatch):
    monkeypatch.setenv("PAYMENTS_XLSX_PATH", str(tmp_path / "pagos.xlsx"))

    response = register_payment(_payment())
    rows = list_payments("2026-05-02")

    assert response.registrado is True
    assert response.estado == "Registrado"
    assert len(rows) == 1
    assert rows[0]["Nombre"] == "Juan Perez"


def test_excel_headers_match_required_columns():
    assert HEADERS == [
        "Fecha",
        "Hora",
        "Nombre",
        "Monto (S/.)",
        "Tipo",
        "N° Operación",
        "Estado",
    ]


def test_detects_duplicate_without_appending_second_row(tmp_path, monkeypatch):
    monkeypatch.setenv("PAYMENTS_XLSX_PATH", str(tmp_path / "pagos.xlsx"))

    first = register_payment(_payment())
    second = register_payment(_payment())
    rows = list_payments("2026-05-02")

    assert first.registrado is True
    assert second.registrado is False
    assert second.estado == "Duplicado"
    assert len(rows) == 1


def test_builds_daily_report(tmp_path, monkeypatch):
    monkeypatch.setenv("PAYMENTS_XLSX_PATH", str(tmp_path / "pagos.xlsx"))

    register_payment(_payment(operation="11111111", amount=50))
    register_payment(_payment(operation="22222222", amount=25))
    report = build_report("2026-05-02")

    assert report.cantidad_pagos == 2
    assert report.total_recaudado == 75
    assert report.total_yape == 75
    assert report.total_plin == 0
