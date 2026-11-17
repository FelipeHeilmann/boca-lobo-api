#!/usr/bin/env bash
# Dispara o alerta ApiDown parando o container no EC2 via SSM (sem SSH),
# aguarda o alerta e depois restaura o serviço.
# Requer credenciais AWS locais com permissão de ssm:SendCommand na instância.
# Uso: scripts/trigger-alert.sh <instance-id> [segundos_parado]
set -euo pipefail

INSTANCE_ID="${1:?informe o instance-id (terraform output -raw instance_id)}"
DOWN_FOR="${2:-150}"

run() {
  aws ssm send-command --instance-ids "$INSTANCE_ID" --document-name AWS-RunShellScript \
    --parameters "commands=[\"cd /opt/boca-de-lobo && docker compose $1\"]" \
    --query Command.CommandId --output text
}

echo ">> Parando a API ($(run stop))"
echo ">> Acompanhe: http://localhost:9090/alerts | http://localhost:9093 | Grafana"
sleep "$DOWN_FOR"
echo ">> Restaurando a API ($(run 'up -d'))"
