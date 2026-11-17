"""
Lambda "ingestor" — recebe o export da camada Ouro (mapa_cidadao.json) e publica no DynamoDB.

Fluxo (disparado por s3:ObjectCreated em entrada/*.json):
  1. lê o arquivo do S3
  2. valida o conteúdo (estrutura, campos obrigatórios, domínio dos valores, coordenadas em SP)
  3. válido   -> grava o item "mapa_atual" no DynamoDB (pontos compactados com gzip) e move para processado/
     inválido -> move para quarentena/ com o motivo nos metadados e no log; o mapa segue com a última versão boa
  4. arquivo mais antigo que o publicado -> não sobrescreve (escrita condicional) e vai para processado/

Sem dependências além do boto3 que já vem no runtime da Lambda.
"""
from __future__ import annotations

import gzip
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import unquote_plus

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

TABELA = os.environ.get("TABLE_NAME", "boca-lobo-dynamo-mapa")
CHAVE_ITEM = "mapa_atual"
MAX_BYTES = 5 * 1024 * 1024  # export real tem ~130 KB; acima disso é erro de origem

NIVEIS = {"EVITE A AREA", "ATENCAO", "NORMAL"}
CAMPOS_OBRIGATORIOS = {
    "id_bueiro": str,
    "latitude": (int, float),
    "longitude": (int, float),
    "logradouro": str,
    "nivel_alerta": str,
    "recomendacao": str,
}
# Retângulo que contém a Região Metropolitana de São Paulo (mesmo do preview_local.py do time de dados)
LAT_MIN, LAT_MAX = -24.10, -23.20
LON_MIN, LON_MAX = -47.00, -46.20
# Exports antigos do notebook 03 saíam sem fuso, no horário de São Paulo (sessão Spark America/Sao_Paulo).
FUSO_PADRAO = timezone(timedelta(hours=-3))

_s3 = None
_dynamo = None


def s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def dynamo():
    global _dynamo
    if _dynamo is None:
        _dynamo = boto3.client("dynamodb")
    return _dynamo


class ArquivoInvalido(ValueError):
    """Conteúdo recusado pela validação."""


def gerado_em_utc(valor: str) -> str:
    """Normaliza gerado_em para UTC em formato fixo (comparável como texto). Sem fuso = horário de SP."""
    dt = datetime.fromisoformat(str(valor))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=FUSO_PADRAO)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def validar(doc: object) -> dict:
    """Valida o export da Ouro e devolve o documento. Lança ArquivoInvalido com o motivo."""
    if not isinstance(doc, dict):
        raise ArquivoInvalido("raiz do JSON deve ser um objeto")
    for campo in ("gerado_em", "pontos"):
        if campo not in doc:
            raise ArquivoInvalido(f"campo obrigatório ausente: {campo}")
    try:
        gerado_em_utc(doc["gerado_em"])
    except ValueError as exc:
        raise ArquivoInvalido(f"gerado_em inválido: {doc['gerado_em']!r}") from exc

    pontos = doc["pontos"]
    if not isinstance(pontos, list) or not pontos:
        raise ArquivoInvalido("pontos deve ser uma lista não vazia")

    ids = set()
    for i, p in enumerate(pontos):
        if not isinstance(p, dict):
            raise ArquivoInvalido(f"pontos[{i}] não é um objeto")
        for campo, tipo in CAMPOS_OBRIGATORIOS.items():
            valor = p.get(campo)
            if valor is None or not isinstance(valor, tipo) or isinstance(valor, bool):
                raise ArquivoInvalido(f"pontos[{i}].{campo} ausente ou com tipo inválido")
        if p["nivel_alerta"] not in NIVEIS:
            raise ArquivoInvalido(f"pontos[{i}].nivel_alerta desconhecido: {p['nivel_alerta']!r}")
        if not (LAT_MIN <= p["latitude"] <= LAT_MAX and LON_MIN <= p["longitude"] <= LON_MAX):
            raise ArquivoInvalido(f"pontos[{i}] ({p['id_bueiro']}) fora da área de São Paulo")
        if p["id_bueiro"] in ids:
            raise ArquivoInvalido(f"id_bueiro duplicado: {p['id_bueiro']}")
        ids.add(p["id_bueiro"])
    return doc


def montar_item(doc: dict, origem: str) -> dict:
    pontos_gz = gzip.compress(json.dumps(doc["pontos"], ensure_ascii=False).encode("utf-8"))
    item = {
        "pk": {"S": CHAVE_ITEM},
        "gerado_em": {"S": str(doc["gerado_em"])},
        "gerado_em_utc": {"S": gerado_em_utc(doc["gerado_em"])},
        "fonte": {"S": str(doc.get("fonte", "desconhecida"))},
        "total_pontos": {"N": str(len(doc["pontos"]))},
        "arquivo_origem": {"S": origem},
        "processado_em": {"S": datetime.now(timezone.utc).isoformat(timespec="seconds")},
        "pontos_gz": {"B": pontos_gz},
    }
    if doc.get("limiar_chuva_mm") is not None:
        item["limiar_chuva_mm"] = {"N": str(Decimal(str(doc["limiar_chuva_mm"])))}
    return item


def publicar(item: dict) -> bool:
    """Grava o item; devolve False se já existe uma versão mais nova publicada."""
    try:
        dynamo().put_item(
            TableName=TABELA,
            Item=item,
            # itens antigos não têm gerado_em_utc: podem ser substituídos
            ConditionExpression="attribute_not_exists(gerado_em_utc) OR gerado_em_utc <= :g",
            ExpressionAttributeValues={":g": item["gerado_em_utc"]},
        )
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def mover(bucket: str, chave: str, pasta: str, metadados: dict | None = None) -> str:
    nome = chave.split("/", 1)[-1]
    destino = f"{pasta}/{nome}"
    extra = {"MetadataDirective": "REPLACE", "Metadata": metadados} if metadados else {}
    s3().copy_object(Bucket=bucket, Key=destino, CopySource={"Bucket": bucket, "Key": chave}, **extra)
    s3().delete_object(Bucket=bucket, Key=chave)
    return destino


def processar(bucket: str, chave: str) -> dict:
    origem = f"s3://{bucket}/{chave}"
    try:
        obj = s3().get_object(Bucket=bucket, Key=chave)
        if obj["ContentLength"] > MAX_BYTES:
            raise ArquivoInvalido(f"arquivo maior que {MAX_BYTES} bytes")
        doc = validar(json.loads(obj["Body"].read()))
    except (ArquivoInvalido, json.JSONDecodeError, UnicodeDecodeError) as exc:
        motivo = str(exc)[:500]
        destino = mover(bucket, chave, "quarentena", {"motivo": motivo.encode("ascii", "replace").decode()})
        logger.error(json.dumps({"evento": "ingestao_rejeitada", "origem": origem, "destino": destino, "motivo": motivo}))
        return {"status": "rejeitado", "motivo": motivo, "destino": destino}

    publicado = publicar(montar_item(doc, origem))
    destino = mover(bucket, chave, "processado")
    status = "publicado" if publicado else "ignorado_mais_antigo"
    logger.info(json.dumps({
        "evento": "ingestao_concluida", "status": status, "origem": origem, "destino": destino,
        "gerado_em": str(doc["gerado_em"]), "total_pontos": len(doc["pontos"]),
    }))
    return {"status": status, "total_pontos": len(doc["pontos"]), "destino": destino}


def lambda_handler(event, _context):
    resultados = []
    for registro in event.get("Records", []):
        bucket = registro["s3"]["bucket"]["name"]
        chave = unquote_plus(registro["s3"]["object"]["key"])
        resultados.append(processar(bucket, chave))
    return {"resultados": resultados}
