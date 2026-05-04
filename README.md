# CobrarApp MVP

Automatización de registro de pagos Yape/Plin enviados como capturas a un grupo de WhatsApp. El flujo recibe la imagen, extrae los datos con OCR, registra el pago en Excel y responde automáticamente al grupo.

**Stack:** WPPConnect → n8n → FastAPI (Python) → Excel `.xlsx`

## Arquitectura

```text
Grupo WhatsApp
      │
      ▼
 WPPConnect          ← pasarela WhatsApp Web gratuita
      │  POST /whatsapp/n8n-bridge
      ▼
     n8n              ← orquestador del flujo
      │  POST /whatsapp/procesar-evento
      ▼
  FastAPI OCR         ← pytesseract + OpenCV
      │
      ├──▶ pagos/pagos.xlsx      ← registro de pagos (7 columnas cliente)
      └──▶ pagos/reporte.xlsx    ← reporte diario acumulado con fila TOTAL
      │
      ▼
 WPPConnect → Grupo WhatsApp    ← confirmación automática
```

## Requisitos

- Docker y Docker Compose
- WhatsApp activo para escanear el QR de WPPConnect

## Configuración inicial

### 1. Variables de entorno

```bash
cp .env.example .env
```

Edita `.env`:

```env
N8N_BASIC_AUTH_USER=admin
N8N_BASIC_AUTH_PASSWORD=change_me
PAYMENTS_XLSX_PATH=/app/pagos/pagos.xlsx
WPPCONNECT_BASE_URL=http://wppconnect:21465
WPPCONNECT_SESSION=cobrapp
WPPCONNECT_SECRET_KEY=replace_with_your_secret_key
WPPCONNECT_TOKEN=replace_with_wppconnect_bearer_token
WHATSAPP_GROUP_ID=120363000000000000@g.us
```

### 2. Levantar servicios

```bash
docker compose up --build
```

### 3. Generar token de WPPConnect

```bash
WPPCONNECT_SECRET_KEY='TU_SECRET_KEY' ./scripts/generate-wppconnect-token.sh
```

O directamente:

```bash
curl -X POST http://localhost:21465/api/cobrapp/TU_SECRET_KEY/generate-token
```

Copia el campo `token` de la respuesta (sin el prefijo `cobrapp:`) en `.env`:

```env
WPPCONNECT_TOKEN='$2b$10$...'
```

### 4. Vincular WhatsApp

Abre `http://localhost:21465` y escanea el QR con el teléfono que estará en el grupo.

### 5. Configurar webhook en WPPConnect

Apunta el webhook de WPPConnect a:

```text
http://python-api:8000/whatsapp/n8n-bridge
```

Activa `WPP_WEBHOOK_ON_SELF_MESSAGE=true` para recibir también mensajes enviados desde tu propio número.

### 6. Importar workflow en n8n

Abre `http://localhost:5678` e importa:

```text
n8n/cobrapp-whatsapp-wppconnect-workflow.json
```

Activa el workflow. Cuando quieras ver la ejecución en canvas, usa **Execute workflow**.

### 7. Probar

Envía una captura Yape o Plin al grupo. El bot responderá:

```
✅ Tu Yape fue validado por S/. 17.50 de Zair Tri. Operación 15994719.
```

Si la operación ya estaba registrada:

```
⚠️ Tu Yape de S/. 17.50 ya fue registrado anteriormente. Operación 15994719.
```

## Archivos Excel

### `pagos/pagos.xlsx` — registro de pagos

Se crea automáticamente. Columnas visibles al cliente:

| Columna | Descripción |
|---|---|
| Fecha | Fecha del pago |
| Hora | Hora del pago |
| Nombre | Nombre del pagador |
| Monto (S/.) | Monto en soles |
| Tipo | Yape o Plin |
| N° Operación | Código de operación |
| Estado | Registrado / Duplicado / Inválido |

Cabecera azul oscuro (`#1F4E79`), filas con colores por estado, bordes y formato de moneda. Fila congelada en la parte superior.

### `pagos/reporte.xlsx` — reporte diario acumulado

Una fila por día. Se actualiza en cada pago registrado. Contiene una fila **TOTAL** al fondo con fórmulas `=SUM(...)`:

| Columna | Descripción |
|---|---|
| Fecha | Día del reporte |
| Total recaudado (S/.) | Suma Yape + Plin del día |
| Pagos Yape (S/.) | Total Yape del día |
| Pagos Plin (S/.) | Total Plin del día |
| Cantidad de pagos | Pagos registrados |
| Duplicados | Capturas duplicadas |

Descarga directa: `GET http://localhost:8000/reporte/excel`

## API endpoints

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/health` | Estado del servicio |
| POST | `/whatsapp/procesar-evento` | Procesa evento WhatsApp |
| POST | `/whatsapp/n8n-bridge` | Puente WPPConnect → n8n |
| POST | `/procesar-imagen` | OCR de imagen (multipart) |
| POST | `/registrar-pago` | Registra pago manual |
| GET | `/pagos` | Lista pagos (`?fecha=2026-05-03`) |
| GET | `/reporte` | Reporte diario JSON |
| GET | `/reporte/excel` | Descarga `reporte.xlsx` actualizado |
| GET | `/` | Frontend web local |

## Detección de duplicados

Un pago se marca `Duplicado` si ya existe otro con el mismo `tipo + operacion + monto`. No se agrega una segunda fila registrada.

## Desarrollo local

```bash
cd python-api
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export PAYMENTS_XLSX_PATH="$(pwd)/../pagos/pagos.xlsx"
uvicorn app.main:app --reload
```

Pruebas:

```bash
cd python-api
pytest          # 28 tests
```

## Workflows disponibles

| Archivo | Uso |
|---|---|
| `n8n/cobrapp-whatsapp-wppconnect-workflow.json` | **Principal** — WhatsApp grupo vía WPPConnect |
| `n8n/cobrapp-webhook-upload-workflow.json` | Demo por formulario web / webhook |
| `n8n/cobrapp-telegram-workflow.json` | Alternativa Telegram (opcional) |

