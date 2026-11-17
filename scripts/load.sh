#!/usr/bin/env bash
# Gera tráfego contra o MVP para popular o dashboard dos 4 sinais de ouro.
# Uso: scripts/load.sh <host:porta> [duração_em_segundos]
#   ex.: scripts/load.sh 3.85.10.20:8000 300
set -euo pipefail

TARGET="${1:?informe host:porta, ex.: 3.85.10.20:8000}"
DURATION="${2:-300}"
END=$(( $(date +%s) + DURATION ))

echo "Gerando carga em http://${TARGET} por ${DURATION}s (Ctrl+C para parar)..."
while [ "$(date +%s)" -lt "$END" ]; do
  for _ in 1 2 3 4 5; do
    curl -s -o /dev/null "http://${TARGET}/health" &
  done
  curl -s -o /dev/null "http://${TARGET}/api/v1/bocas-risco" &
  curl -s -o /dev/null "http://${TARGET}/rota-inexistente" &   # gera 404 (não-2xx)
  wait
  sleep 0.5
done
echo "Fim."
