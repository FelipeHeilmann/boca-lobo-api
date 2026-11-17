#!/usr/bin/env bash
# Gera monitoring/targets/api.json com o IP do MVP publicado.
# Uso:
#   scripts/gen-targets.sh              # lê o IP do output do Terraform (requer terraform init com o backend)
#   scripts/gen-targets.sh 3.85.10.20   # informa o IP manualmente
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IP="${1:-}"

if [ -z "$IP" ]; then
  IP="$(terraform -chdir="$ROOT/terraform" output -raw public_ip)"
fi

mkdir -p "$ROOT/monitoring/targets"
cat > "$ROOT/monitoring/targets/api.json" <<EOF
[
  {
    "targets": ["${IP}:8000"],
    "labels": { "env": "production", "service": "boca-de-lobo-api" }
  }
]
EOF

echo "Alvo do Prometheus: http://${IP}:8000/metrics"
