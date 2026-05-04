from app.parser import parse_payment_text


def test_extracts_yape_payment_with_amount_operation_and_name():
    text = """
    Yape
    Pagado por Juan Perez
    S/ 50.00
    Operacion 12345678
    02/05/2026 18:07
    """

    payment = parse_payment_text(text)

    assert payment.valido is True
    assert payment.tipo == "Yape"
    assert payment.monto == 50.0
    assert payment.operacion == "12345678"
    assert payment.nombre == "Juan Perez"
    assert payment.fecha == "02/05/2026"
    assert payment.hora == "18:07"


def test_extracts_plin_payment_with_codigo_variant():
    text = """
    PLIN
    Cliente: Maria Lopez
    Monto pagado S/.75,50
    Codigo 99887766
    """

    payment = parse_payment_text(text)

    assert payment.valido is True
    assert payment.tipo == "Plin"
    assert payment.monto == 75.5
    assert payment.operacion == "99887766"
    assert payment.nombre == "Maria Lopez"


def test_extracts_amount_without_currency_when_decimal_is_present():
    text = "Yape\nRecibiste de Luis Rojas\n50.00\nNro 55667788"

    payment = parse_payment_text(text)

    assert payment.valido is True
    assert payment.monto == 50.0
    assert payment.operacion == "55667788"


def test_invalid_when_required_fields_are_missing():
    payment = parse_payment_text("Gracias por tu mensaje, nos vemos manana")

    assert payment.valido is False
    assert payment.estado == "Invalido"
    assert "No se encontro monto" in payment.errores
    assert "No se encontro tipo de pago Yape o Plin" in payment.errores
    assert "No se encontro numero de operacion" in payment.errores


def test_parses_realistic_yape_receipt_text_without_security_code_confusion():
    text = """
    ¡Yapeaste! < Compartir?
    S/25 :
    Amanda Rod*
    8 17 dic. 2025 | O) 11:31 p.m.
    TD Gracias por el trabajo ¿Uy
    CÓDIGO DE SEGURIDAD ()
    3
    DATOS DE LA TRANSACCIÓN
    Nro. de celular FR kk 321
    Destino Yape
    Nro. de operación 26101992
    Yape Tienda
    """

    payment = parse_payment_text(text)

    assert payment.valido is True
    assert payment.tipo == "Yape"
    assert payment.monto == 25
    assert payment.nombre == "Amanda Rod"
    assert payment.operacion == "26101992"
    assert payment.fecha == "17/12/2025"


def test_parses_yape_amount_when_ocr_reads_currency_as_9():
    text = """
    CobrarApp conectado. Envia una
    captura Yape o Plin para probar el
    flujo. 8:23p.m.
    ¡Yapeaste! Compartir
    9116
    Luz Roj*
    30 abr. 2026 | 8:43 p.m.
    CÓDIGO DE SEGURIDAD
    DATOS DE LA TRANSACCIÓN
    Destino Yape
    Nro. de operación 30066920
    """

    payment = parse_payment_text(text)

    assert payment.valido is True
    assert payment.monto == 16
    assert payment.nombre == "Luz Roj"
    assert payment.tipo == "Yape"
    assert payment.operacion == "30066920"
