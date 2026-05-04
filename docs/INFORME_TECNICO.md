# Informe Técnico — CobrarApp

## 1. Introducción

CobrarApp automatiza el registro de pagos Yape y Plin para grupos de WhatsApp. El proceso manual original obligaba al administrador a revisar capturas enviadas al grupo y anotarlas manualmente en Excel. CobrarApp elimina ese trabajo: recibe la imagen, extrae los datos con OCR, registra el pago y confirma automáticamente al grupo.

## 2. Arquitectura

```text
Grupo WhatsApp
      │
      ▼
 WPPConnect (pasarela WhatsApp Web)
      │  POST /whatsapp/n8n-bridge
      ▼
     n8n (orquestador)
      │  POST /whatsapp/procesar-evento
      ▼
  FastAPI OCR (Python)
      ├──▶ pagos/pagos.xlsx        ← registro individual de pagos
      └──▶ pagos/reporte.xlsx      ← reporte diario acumulado
      │
      ▼
 WPPConnect → Grupo WhatsApp (confirmación automática)
```

## 3. Componentes

| Componente | Tecnología | Función |
|---|---|---|
| Mensajería | WhatsApp + WPPConnect | Recibir capturas y enviar confirmación |
| Orquestador | n8n | Coordinar el flujo automático |
| API | FastAPI (Python 3.11) | Procesar eventos, OCR y registro |
| OCR | pytesseract + OpenCV + Pillow + AVIF | Extraer texto de capturas |
| Registro | openpyxl | Escribir `pagos.xlsx` y `reporte.xlsx` |
| Despliegue | Docker Compose | n8n, API y WPPConnect en contenedores |

## 4. Flujo de procesamiento

1. El usuario envía una captura Yape o Plin al grupo de WhatsApp.
2. WPPConnect detecta el mensaje (incluyendo mensajes propios, con `WPP_WEBHOOK_ON_SELF_MESSAGE=true`) y lo envía a `POST /whatsapp/n8n-bridge`.
3. El bridge valida que el evento contenga imagen y parezca una captura de pago; si no, lo descarta antes de llamar a n8n.
4. n8n recibe el evento y llama a `POST /whatsapp/procesar-evento`.
5. FastAPI descarga la imagen (con fallback `download-media` si el base64 es inválido), aplica OCR y extrae: nombre, monto, fecha, hora, tipo (Yape/Plin) y número de operación.
6. FastAPI verifica duplicados por `tipo + operación + monto`.
7. El pago se registra en `pagos.xlsx` y `reporte.xlsx` se actualiza (upsert por fecha + fila TOTAL al fondo con `=SUM(...)`).
8. FastAPI devuelve el mensaje de confirmación. n8n lo envía al grupo vía WPPConnect.

## 5. Archivos Excel generados

### `pagos/pagos.xlsx`

Registro individual de cada pago. Columnas orientadas al cliente (sin datos internos del sistema):

| Columna | Descripción |
|---|---|
| Fecha | Fecha del pago |
| Hora | Hora del pago |
| Nombre | Nombre del pagador extraído por OCR |
| Monto (S/.) | Monto en soles con formato `#,##0.00` |
| Tipo | Yape o Plin |
| N° Operación | Código de operación |
| Estado | `Registrado` / `Duplicado` / `Inválido` |

Formato visual: cabecera azul oscuro (`#1F4E79`), filas alternadas, colores por estado (verde/amarillo/salmón), bordes finos, fila congelada.

### `pagos/reporte.xlsx`

Una fila por día, actualizada en cada registro. Fila **TOTAL** al fondo con fórmulas `=SUM(...)`:

| Columna | Descripción |
|---|---|
| Fecha | Día del reporte |
| Total recaudado (S/.) | Suma total del día |
| Pagos Yape (S/.) | Subtotal Yape |
| Pagos Plin (S/.) | Subtotal Plin |
| Cantidad de pagos | Pagos registrados |
| Duplicados | Capturas duplicadas detectadas |

Cabecera verde oscuro (`#375623`). Disponible para descarga en `GET /reporte/excel`.

## 6. Decisiones técnicas

| Decisión | Justificación |
|---|---|
| WPPConnect en lugar de Twilio | Twilio no soporta grupos de WhatsApp; WPPConnect sí |
| Excel local en lugar de Google Sheets | Demo reproducible offline, sin credenciales de Google Cloud |
| pytesseract | Especificado en el enunciado del proyecto |
| Soporte AVIF | WPPConnect entrega algunas capturas en formato AVIF |
| Fallback `download-media` | Cuando WPPConnect envía base64 inválido, se descarga la imagen directamente |
| `WPP_WEBHOOK_ON_SELF_MESSAGE=true` | Permite probar el flujo desde el mismo número vinculado |
| Columnas reducidas en `pagos.xlsx` | Los datos internos (texto OCR raw, chat ID, message ID) son innecesarios para el cliente |
| Fila TOTAL con fórmulas `=SUM` | La suma se recalcula automáticamente en Excel al abrir; no depende de la API |

## 7. Parser OCR — lógica de extracción de monto

El bug original convertía `"S/ 17.50"` en `"1750"` al eliminar todos los no-dígitos, y luego fallaba la comparación de prefijo `"9x"`. El fallback de regex entonces capturaba `"5117.50"` (el prefijo `+51` de Perú mezclado con el monto por el OCR).

**Solución implementada** (en `parser.py → extract_amount`):

1. Busca el patrón `S/` explícito primero (preserva el punto decimal).
2. Si no, busca un número decimal independiente en la línea (`17.50`).
3. Como último recurso, aplica el antiguo strip de dígitos con detección de prefijo telefónico.

## 8. Detección de duplicados

Un pago se marca `Duplicado` si ya existe una fila con `estado=Registrado` y la misma combinación `tipo + operación + monto`. El duplicado no agrega una segunda fila registrada; sí queda registrado con estado `Duplicado` para auditoría.

## 9. Mensajes de confirmación al grupo

| Estado | Mensaje |
|---|---|
| Registrado | `✅ Tu Yape fue validado por S/. 17.50 de Zair Tri. Operación 15994719.` |
| Duplicado | `⚠️ Tu Yape de S/. 17.50 ya fue registrado anteriormente. Operación 15994719.` |
| Error | `❌ No se pudo validar la captura de pago. Errores: ...` |

El total recaudado **no** se envía al grupo; solo se actualiza en `reporte.xlsx`.

## 10. Pruebas

28 tests automatizados en `python-api/tests/`:

| Módulo | Qué cubre |
|---|---|
| `test_parser.py` | Extracción de monto, nombre, operación, fecha, tipo |
| `test_storage.py` | Registro, duplicados, reporte, cabeceras Excel |
| `test_ocr.py` | Decodificación de imagen, texto OCR |
| `test_api.py` | Endpoints HTTP, flujo WhatsApp, bridge n8n |

## 11. Dificultades y soluciones

| Dificultad | Solución |
|---|---|
| WhatsApp grupo no soportado por Twilio | WPPConnect con sesión WhatsApp Web |
| Mensajes propios no llegaban al webhook | `WPP_WEBHOOK_ON_SELF_MESSAGE=true` + ajuste en `docker-compose.yml` |
| WPPConnect enviaba base64 inválido en algunas imágenes | Fallback `download-media` en `main.py` |
| OCR mezclaba prefijo `+51` con el monto | Nuevo parser con detección de `S/` primero |
| Excel con columnas técnicas innecesarias | Esquema reducido a 7 columnas orientadas al cliente |
| `reporte.xlsx` no se creaba | Bug en `_report_worksheet`: creaba el archivo pero no retornaba la hoja, luego fallaba `load_workbook` |

## 12. Mejoras futuras

- Integración con Google Sheets para acceso remoto sin descarga.
- Panel web de reportes con gráficas por día/mes.
- Fallback OCR con `easyocr` para capturas de baja calidad.
- Validación adicional con OpenAI Vision API.
- Soporte Telegram como canal alternativo.

## 13. Conclusiones

El MVP cumple el flujo completo: recibe capturas desde un grupo de WhatsApp, extrae datos con OCR en Python, registra en dos archivos Excel (pagos individuales y reporte diario), confirma automáticamente al grupo y detecta duplicados. El sistema es auditable, reproducible con Docker Compose y no requiere servicios de pago externos.

