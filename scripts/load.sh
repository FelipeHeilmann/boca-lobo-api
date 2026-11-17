#!/usr/bin/env bash
# Gera tráfego contra o MVP para popular o dashboard dos 4 sinais de ouro.
# Uso: scripts/load.sh <host> [duração_em_segundos]
#   ex.: scripts/load.sh 98-86-45-71.sslip.io 300
set -euo pipefail

TARGET="${1:?informe o host, ex.: 98-86-45-71.sslip.io}"
DURATION="${2:-300}"
END=$(( $(date +%s) + DURATION ))

echo "Gerando carga em https://${TARGET} por ${DURATION}s (Ctrl+C para parar)..."
while [ "$(date +%s)" -lt "$END" ]; do
  for _ in 1 2 3 4 5; do
    curl -s -o /dev/null "https://${TARGET}/health" &
  done
  curl -s -o /dev/null "https://${TARGET}/api/v1/mapa" &
  curl -s -o /dev/null "https://${TARGET}/rota-inexistente" &   # gera 404 (não-2xx)
  wait
  sleep 0.5
done
echo "Fim."
