#!/usr/bin/env bash
# Gera monitoring/targets/api.json com o IP do MVP publicado.
# Uso:
#   scripts/gen-targets.sh              # lê o IP do output do Terraform (requer terraform init com o backend)
#   scripts/gen-targets.sh 3.85.10.20   # informa o IP manualmente
# O alvo é o hostname HTTPS <ip-com-hifens>.sslip.io:443 servido pelo Caddy.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IP="${1:-}"

if [ -z "$IP" ]; then
  IP="$(terraform -chdir="$ROOT/terraform" output -raw public_ip)"
fi

HOST="${IP//./-}.sslip.io"

mkdir -p "$ROOT/monitoring/targets"
cat > "$ROOT/monitoring/targets/api.json" <<EOF
[
  {
    "targets": ["${HOST}:443"],
    "labels": { "env": "production", "service": "boca-de-lobo-api" }
  }
]
EOF

echo "Alvo do Prometheus: https://${HOST}/metrics"
