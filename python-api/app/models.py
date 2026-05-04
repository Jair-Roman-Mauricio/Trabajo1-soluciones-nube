from pydantic import BaseModel, Field


class PaymentSource(BaseModel):
    canal: str = "whatsapp"
    chat_id: str | None = None
    message_id: str | None = None


class ProcessedPayment(BaseModel):
    valido: bool = False
    fecha: str | None = None
    hora: str | None = None
    nombre: str | None = None
    monto: float | None = None
    tipo: str | None = None
    operacion: str | None = None
    estado: str = "Invalido"
    registrado_por: str = "Bot automático"
    errores: list[str] = Field(default_factory=list)
    texto_raw: str = ""
    source: PaymentSource = Field(default_factory=PaymentSource)


class RegisterPaymentRequest(ProcessedPayment):
    pass


class RegisterPaymentResponse(BaseModel):
    registrado: bool
    estado: str
    mensaje: str
    pago: ProcessedPayment
    reporte: dict | None = None


class ReportResponse(BaseModel):
    fecha: str
    total_recaudado: float
    cantidad_pagos: int
    total_yape: float
    total_plin: float
    duplicados: int
    errores_ocr: int
    invalidos: int
