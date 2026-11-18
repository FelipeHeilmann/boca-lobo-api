#!/usr/bin/env bash
# Envia um export da camada Ouro (mapa_cidadao.json) para o bucket de ingestão.
# A Lambda "ingestor" valida o arquivo e publica no DynamoDB; a API passa a servi-lo em até 60 s.
#
# Usos:
#   - carga inicial do mapa
#   - plano B quando o Databricks não consegue enviar direto (baixe o JSON do Volume e rode este script)
#
# Uso: scripts/publicar_mapa.sh <caminho/mapa_cidadao.json> [bucket]
#   bucket padrão: terraform output ingestao_bucket
set -euo pipefail

ARQUIVO="${1:?informe o caminho do mapa_cidadao.json}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUCKET="${2:-$(terraform -chdir="$ROOT/terraform" output -raw ingestao_bucket)}"
FLUXO="databricks"
NOME="mapa_cidadao_$(date -u +%Y%m%dT%H%M%SZ).json"
CHAVE="${FLUXO}/entrada/${NOME}"

python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(f\"{len(d['pontos'])} pontos, gerado_em={d['gerado_em']}\")" "$ARQUIVO"

aws s3 cp "$ARQUIVO" "s3://${BUCKET}/${CHAVE}" --content-type application/json
echo "Enviado para s3://${BUCKET}/${CHAVE}"

echo "Aguardando a Lambda processar..."
for _ in $(seq 1 20); do
  sleep 3
  if aws s3api head-object --bucket "$BUCKET" --key "${FLUXO}/processado/${NOME}" > /dev/null 2>&1; then
    status=$(aws s3api head-object --bucket "$BUCKET" --key "${FLUXO}/processado/${NOME}" --query 'Metadata.status' --output text)
    case "$status" in
      publicado) echo "OK: publicado (${FLUXO}/processado/${NOME})"; exit 0 ;;
      ignorado_mais_antigo) echo "IGNORADO: o mapa no ar é mais recente que este arquivo (gerado_em mais antigo)"; exit 2 ;;
      *) echo "Processado (status: ${status})"; exit 0 ;;
    esac
  fi
  if aws s3api head-object --bucket "$BUCKET" --key "${FLUXO}/quarentena/${NOME}" > /dev/null 2>&1; then
    motivo=$(aws s3api head-object --bucket "$BUCKET" --key "${FLUXO}/quarentena/${NOME}" --query 'Metadata.motivo' --output text)
    echo "REJEITADO: ${motivo}"; exit 1
  fi
done
echo "Sem resposta da Lambda em 60 s: veja os logs em /aws/lambda/boca-lobo-lambda-ingestor"; exit 1
