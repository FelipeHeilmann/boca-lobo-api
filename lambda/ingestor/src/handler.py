"""
Lambda "ingestor" — recebe os arquivos de cada fluxo de dados e publica no DynamoDB que a API lê.

O bucket tem uma pasta por fluxo, todas com a mesma estrutura:

  <fluxo>/entrada/     <- quem produz o dado grava aqui (cada fluxo com o seu usuário IAM)
  <fluxo>/processado/  <- arquivos aceitos (histórico)
  <fluxo>/quarentena/  <- arquivos recusados, com o motivo nos metadados

  | fluxo        | conteúdo                                   | tabela DynamoDB         | item         |
  |--------------|--------------------------------------------|-------------------------|--------------|
  | databricks/  | export da camada Ouro (mapa_cidadao.json)  | boca-lobo-dynamo-mapa   | mapa_atual   |
  | rpa/         | resultado do bot RPA (execução + ordens)   | boca-lobo-dynamo-rpa    | rpa_atual    |

Passos (disparado por s3:ObjectCreated em <fluxo>/entrada/*.json):
  1. lê o arquivo do S3 e identifica o fluxo pela pasta
  2. valida o conteúdo com as regras daquele fluxo
  3. válido   -> grava o item na tabela do fluxo (conteúdo compactado com gzip) e move para <fluxo>/processado/
     inválido -> move para <fluxo>/quarentena/ com o motivo nos metadados e no log; a API segue com a última versão boa
  4. arquivo mais antigo que o publicado -> não sobrescreve (escrita condicional) e vai para <fluxo>/processado/

Sem dependências além do boto3 que já vem no runtime da Lambda.
"""
from __future__ import annotations

import gzip
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable
from urllib.parse import unquote_plus

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

MAX_BYTES = 5 * 1024 * 1024  # export real da Ouro tem ~130 KB e o do RPA ~40 KB; acima disso é erro de origem

# Retângulo que contém a Região Metropolitana de São Paulo (mesmo do preview_local.py do time de dados)
LAT_MIN, LAT_MAX = -24.10, -23.20
LON_MIN, LON_MAX = -47.00, -46.20
# Datas sem fuso (exports antigos da Ouro e horários do bot RPA) estão no horário de São Paulo.
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


def para_utc(valor: str) -> str:
    """Normaliza uma data ISO para UTC em formato fixo (comparável como texto). Sem fuso = horário de SP."""
    dt = datetime.fromisoformat(str(valor))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=FUSO_PADRAO)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


gerado_em_utc = para_utc  # nome usado pelo fluxo do mapa


def _numero(valor) -> bool:
    return isinstance(valor, (int, float)) and not isinstance(valor, bool)


def _em_sao_paulo(lat, lon) -> bool:
    return LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX


def _data(doc: dict, campo: str, onde: str) -> str:
    try:
        return para_utc(doc[campo])
    except (ValueError, TypeError) as exc:
        raise ArquivoInvalido(f"{onde}{campo} inválido: {doc[campo]!r}") from exc


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# =============================== fluxo databricks ===============================
NIVEIS_MAPA = {"EVITE A AREA", "ATENCAO", "NORMAL"}
CAMPOS_PONTO = {
    "id_bueiro": str,
    "latitude": (int, float),
    "longitude": (int, float),
    "logradouro": str,
    "nivel_alerta": str,
    "recomendacao": str,
}


def validar_mapa(doc: object) -> dict:
    """Valida o export da Ouro e devolve o documento. Lança ArquivoInvalido com o motivo."""
    if not isinstance(doc, dict):
        raise ArquivoInvalido("raiz do JSON deve ser um objeto")
    for campo in ("gerado_em", "pontos"):
        if campo not in doc:
            raise ArquivoInvalido(f"campo obrigatório ausente: {campo}")
    _data(doc, "gerado_em", "")

    pontos = doc["pontos"]
    if not isinstance(pontos, list) or not pontos:
        raise ArquivoInvalido("pontos deve ser uma lista não vazia")

    ids = set()
    for i, p in enumerate(pontos):
        if not isinstance(p, dict):
            raise ArquivoInvalido(f"pontos[{i}] não é um objeto")
        for campo, tipo in CAMPOS_PONTO.items():
            valor = p.get(campo)
            if valor is None or not isinstance(valor, tipo) or isinstance(valor, bool):
                raise ArquivoInvalido(f"pontos[{i}].{campo} ausente ou com tipo inválido")
        if p["nivel_alerta"] not in NIVEIS_MAPA:
            raise ArquivoInvalido(f"pontos[{i}].nivel_alerta desconhecido: {p['nivel_alerta']!r}")
        if not _em_sao_paulo(p["latitude"], p["longitude"]):
            raise ArquivoInvalido(f"pontos[{i}] ({p['id_bueiro']}) fora da área de São Paulo")
        if p["id_bueiro"] in ids:
            raise ArquivoInvalido(f"id_bueiro duplicado: {p['id_bueiro']}")
        ids.add(p["id_bueiro"])
    return doc


def montar_item_mapa(doc: dict, origem: str) -> dict:
    pontos_gz = gzip.compress(json.dumps(doc["pontos"], ensure_ascii=False).encode("utf-8"))
    item = {
        "pk": {"S": "mapa_atual"},
        "gerado_em": {"S": str(doc["gerado_em"])},
        "gerado_em_utc": {"S": para_utc(doc["gerado_em"])},
        "fonte": {"S": str(doc.get("fonte", "desconhecida"))},
        "total_pontos": {"N": str(len(doc["pontos"]))},
        "arquivo_origem": {"S": origem},
        "processado_em": {"S": _agora()},
        "pontos_gz": {"B": pontos_gz},
    }
    if doc.get("limiar_chuva_mm") is not None:
        item["limiar_chuva_mm"] = {"N": str(Decimal(str(doc["limiar_chuva_mm"])))}
    return item


# ================================== fluxo rpa ===================================
NIVEIS_CHUVA = {"NORMAL", "ATENCAO", "ALERTA"}
PRIORIDADES = {"CRITICA", "ALTA", "MEDIA"}
STATUS_ORDEM = {"ABERTA", "AGUARDANDO_REFORCO"}
# Só estes campos seguem para o DynamoDB/API (minimização): alertas, contatos e textos livres do bot ficam de fora.
CAMPOS_EXECUCAO = (
    "id", "inicio", "fim", "status", "nivel", "justificativa", "fonte_previsao",
    "chuva_24h_mm", "pico_mm_h", "probabilidade_max_pct",
    "linhas_lidas", "inspecoes_validas", "rejeitados", "duplicados", "arquivos_invalidos",
    "ordens_geradas", "excedentes", "alertas_enviados", "alertas_ignorados", "falhas_envio", "codigo_saida",
)
CONTADORES_EXECUCAO = (
    "chuva_24h_mm", "pico_mm_h", "probabilidade_max_pct", "linhas_lidas", "inspecoes_validas", "rejeitados",
    "duplicados", "arquivos_invalidos", "ordens_geradas", "excedentes", "alertas_enviados", "alertas_ignorados",
    "falhas_envio",
)
CAMPOS_ORDEM = (
    "ordem", "id_bueiro", "endereco", "tipo", "area_id", "area", "nivel_risco_area", "lat", "lon",
    "nivel_obstrucao", "tipo_residuo", "data_inspecao", "score", "prioridade", "equipe_id", "status",
    "reinspecionar", "observacao",
)


def validar_rpa(doc: object) -> dict:
    """Valida o resultado do bot RPA e devolve só os campos publicados. Lança ArquivoInvalido com o motivo."""
    if not isinstance(doc, dict):
        raise ArquivoInvalido("raiz do JSON deve ser um objeto")
    for campo in ("execucao", "ordens_servico"):
        if campo not in doc:
            raise ArquivoInvalido(f"campo obrigatório ausente: {campo}")

    ex = doc["execucao"]
    if not isinstance(ex, dict):
        raise ArquivoInvalido("execucao deve ser um objeto")
    for campo in ("id", "inicio", "status"):
        if not isinstance(ex.get(campo), str) or not ex[campo].strip():
            raise ArquivoInvalido(f"execucao.{campo} ausente ou com tipo inválido")
    _data(ex, "inicio", "execucao.")
    if ex.get("fim") is not None:
        _data(ex, "fim", "execucao.")
    if ex.get("nivel") is not None and ex["nivel"] not in NIVEIS_CHUVA:   # falha de previsão chega sem nível
        raise ArquivoInvalido(f"execucao.nivel desconhecido: {ex['nivel']!r}")
    for campo in CONTADORES_EXECUCAO:
        valor = ex.get(campo)
        if valor is not None and (not _numero(valor) or valor < 0):
            raise ArquivoInvalido(f"execucao.{campo} deve ser um número >= 0")

    ordens = doc["ordens_servico"]
    if not isinstance(ordens, list):                                   # lista vazia é válida (SEM_ACAO, falhas)
        raise ArquivoInvalido("ordens_servico deve ser uma lista")
    ids = set()
    for i, o in enumerate(ordens):
        onde = f"ordens_servico[{i}]"
        if not isinstance(o, dict):
            raise ArquivoInvalido(f"{onde} não é um objeto")
        if not isinstance(o.get("id_bueiro"), str) or not o["id_bueiro"].strip():
            raise ArquivoInvalido(f"{onde}.id_bueiro ausente ou com tipo inválido")
        for campo in ("lat", "lon", "nivel_obstrucao", "score"):
            if not _numero(o.get(campo)):
                raise ArquivoInvalido(f"{onde}.{campo} ausente ou com tipo inválido")
        if not _em_sao_paulo(o["lat"], o["lon"]):
            raise ArquivoInvalido(f"{onde} ({o['id_bueiro']}) fora da área de São Paulo")
        for campo in ("nivel_obstrucao", "score"):
            if not 0 <= o[campo] <= 100:
                raise ArquivoInvalido(f"{onde}.{campo} fora de 0-100: {o[campo]!r}")
        if o.get("prioridade") not in PRIORIDADES:
            raise ArquivoInvalido(f"{onde}.prioridade desconhecida: {o.get('prioridade')!r}")
        if o.get("status") not in STATUS_ORDEM:
            raise ArquivoInvalido(f"{onde}.status desconhecido: {o.get('status')!r}")
        if not isinstance(o.get("reinspecionar", False), bool):
            raise ArquivoInvalido(f"{onde}.reinspecionar deve ser verdadeiro/falso")
        if o["id_bueiro"] in ids:
            raise ArquivoInvalido(f"id_bueiro duplicado: {o['id_bueiro']}")
        ids.add(o["id_bueiro"])

    return {
        "execucao": {c: ex.get(c) for c in CAMPOS_EXECUCAO if c in ex},
        "ordens_servico": [{c: o.get(c) for c in CAMPOS_ORDEM} for o in ordens],
    }


def montar_item_rpa(doc: dict, origem: str) -> dict:
    ex = doc["execucao"]
    dados_gz = gzip.compress(json.dumps(doc, ensure_ascii=False).encode("utf-8"))
    item = {
        "pk": {"S": "rpa_atual"},
        "execucao_id": {"S": ex["id"]},
        "inicio": {"S": ex["inicio"]},
        "inicio_utc": {"S": para_utc(ex["inicio"])},
        "status": {"S": ex["status"]},
        "total_ordens": {"N": str(len(doc["ordens_servico"]))},
        "arquivo_origem": {"S": origem},
        "processado_em": {"S": _agora()},
        "dados_gz": {"B": dados_gz},
    }
    if ex.get("nivel"):
        item["nivel"] = {"S": ex["nivel"]}
    return item


# ============================== roteamento por pasta =============================
@dataclass(frozen=True)
class Fluxo:
    nome: str
    tabela: str
    validar: Callable[[object], dict]
    montar_item: Callable[[dict, str], dict]
    campo_versao: str          # atributo comparado na escrita condicional (UTC, texto)
    resumo: Callable[[dict], dict]


FLUXOS = {
    "databricks": Fluxo(
        "databricks", os.environ.get("TABLE_NAME", "boca-lobo-dynamo-mapa"), validar_mapa, montar_item_mapa,
        "gerado_em_utc", lambda d: {"gerado_em": str(d["gerado_em"]), "total_pontos": len(d["pontos"])},
    ),
    "rpa": Fluxo(
        "rpa", os.environ.get("TABLE_NAME_RPA", "boca-lobo-dynamo-rpa"), validar_rpa, montar_item_rpa,
        "inicio_utc", lambda d: {"execucao_id": d["execucao"]["id"], "total_ordens": len(d["ordens_servico"])},
    ),
}


def identificar(chave: str) -> tuple[Fluxo, str] | None:
    """'rpa/entrada/x.json' -> (fluxo rpa, 'x.json'). Fora de <fluxo>/entrada/ -> None."""
    partes = chave.split("/", 2)
    if len(partes) == 3 and partes[1] == "entrada" and partes[0] in FLUXOS and partes[2]:
        return FLUXOS[partes[0]], partes[2]
    return None


def publicar(fluxo: Fluxo, item: dict) -> bool:
    """Grava o item; devolve False se já existe uma versão mais nova publicada."""
    try:
        dynamo().put_item(
            TableName=fluxo.tabela,
            Item=item,
            # itens antigos sem o campo de versão podem ser substituídos
            ConditionExpression=f"attribute_not_exists({fluxo.campo_versao}) OR {fluxo.campo_versao} <= :v",
            ExpressionAttributeValues={":v": item[fluxo.campo_versao]},
        )
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def mover(bucket: str, chave: str, fluxo: Fluxo, nome: str, pasta: str, metadados: dict | None = None) -> str:
    destino = f"{fluxo.nome}/{pasta}/{nome}"
    extra = {"MetadataDirective": "REPLACE", "Metadata": metadados} if metadados else {}
    s3().copy_object(Bucket=bucket, Key=destino, CopySource={"Bucket": bucket, "Key": chave}, **extra)
    s3().delete_object(Bucket=bucket, Key=chave)
    return destino


def processar(bucket: str, chave: str) -> dict:
    origem = f"s3://{bucket}/{chave}"
    rota = identificar(chave)
    if rota is None:  # a notificação só dispara em <fluxo>/entrada/; isto é configuração errada, não dado ruim
        logger.warning(json.dumps({"evento": "ingestao_ignorada", "origem": origem, "motivo": "pasta sem fluxo"}))
        return {"status": "ignorado_pasta_desconhecida", "origem": origem}
    fluxo, nome = rota

    try:
        obj = s3().get_object(Bucket=bucket, Key=chave)
        if obj["ContentLength"] > MAX_BYTES:
            raise ArquivoInvalido(f"arquivo maior que {MAX_BYTES} bytes")
        doc = fluxo.validar(json.loads(obj["Body"].read()))
    except (ArquivoInvalido, json.JSONDecodeError, UnicodeDecodeError) as exc:
        motivo = str(exc)[:500]
        destino = mover(bucket, chave, fluxo, nome, "quarentena",
                        {"motivo": motivo.encode("ascii", "replace").decode()})
        logger.error(json.dumps({"evento": "ingestao_rejeitada", "fluxo": fluxo.nome, "origem": origem,
                                 "destino": destino, "motivo": motivo}))
        return {"status": "rejeitado", "fluxo": fluxo.nome, "motivo": motivo, "destino": destino}

    publicado = publicar(fluxo, fluxo.montar_item(doc, origem))
    status = "publicado" if publicado else "ignorado_mais_antigo"
    destino = mover(bucket, chave, fluxo, nome, "processado", {"status": status})
    resumo = fluxo.resumo(doc)
    logger.info(json.dumps({"evento": "ingestao_concluida", "fluxo": fluxo.nome, "status": status,
                            "origem": origem, "destino": destino, **resumo}))
    return {"status": status, "fluxo": fluxo.nome, "destino": destino, **resumo}


def lambda_handler(event, _context):
    resultados = []
    for registro in event.get("Records", []):
        bucket = registro["s3"]["bucket"]["name"]
        chave = unquote_plus(registro["s3"]["object"]["key"])
        resultados.append(processar(bucket, chave))
    return {"resultados": resultados}
