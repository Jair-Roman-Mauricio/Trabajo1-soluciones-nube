#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${WPPCONNECT_PUBLIC_BASE_URL:-${WPPCONNECT_BASE_URL:-http://localhost:21465}}"
SESSION="${WPPCONNECT_SESSION:-cobrapp}"
SECRET_KEY="${WPPCONNECT_SECRET_KEY:-}"

if [[ -z "$SECRET_KEY" || "$SECRET_KEY" == "replace_with_your_secret_key" ]]; then
  echo "ERROR: define WPPCONNECT_SECRET_KEY antes de generar el token." >&2
  echo "Ejemplo:" >&2
  echo "  WPPCONNECT_SECRET_KEY='mi_clave_segura' ./scripts/generate-wppconnect-token.sh" >&2
  exit 1
fi

BASE_URL="${BASE_URL%/}"
curl -sS -X POST "${BASE_URL}/api/${SESSION}/${SECRET_KEY}/generate-token"
echo
