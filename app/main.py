"""
API do MVP Boca de Lobo.

Serve ao frontend (mapa do cidadão) o retrato mais recente da camada Ouro, publicado no DynamoDB pela
Lambda "ingestor". A leitura usa cache em memória (padrão 60 s) atualizado em segundo plano: o DynamoDB é
consultado ~1x por minuto independentemente do tráfego, e se ele falhar a API continua servindo a última
versão válida.
"""
import asyncio
import gzip
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import boto3
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("citizen-tech-api")

TABELA = os.getenv("DYNAMODB_TABLE", "boca-lobo-dynamo-mapa")
REGIAO = os.getenv("AWS_REGION", "us-east-1")
CACHE_TTL = int(os.getenv("CACHE_TTL_SECONDS", "60"))
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]

# ---------------------------------------------------------------------------
# Métricas de Ouro (Golden Signals)
#   Latência   -> http_request_duration_seconds (histogram)
#   Tráfego    -> http_requests_total (counter)
#   Erros      -> http_requests_total{http_status=~"5.."}
#   Saturação  -> http_requests_in_progress + process_* (CPU/memória, coletor padrão)
# Dados do mapa (frescor do pipeline de dados)
#   mapa_gerado_em_timestamp_seconds, mapa_total_pontos, mapa_leitura_falhas_total
# ---------------------------------------------------------------------------
REQUEST_COUNT = Counter("http_requests_total", "Total HTTP Requests", ["method", "endpoint", "http_status"])
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP Request Latency in seconds",
    ["method", "endpoint"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
IN_PROGRESS = Gauge("http_requests_in_progress", "Requisições HTTP em processamento")
MAPA_GERADO_EM = Gauge("mapa_gerado_em_timestamp_seconds", "Momento (epoch) em que a camada Ouro gerou o mapa servido")
MAPA_TOTAL_PONTOS = Gauge("mapa_total_pontos", "Quantidade de bocas de lobo no mapa servido")
MAPA_FALHAS = Counter("mapa_leitura_falhas_total", "Falhas ao ler o mapa do DynamoDB")

_dynamo = None
_cache: dict = {"mapa": None, "lido_em": 0.0}


def dynamo():
    global _dynamo
    if _dynamo is None:
        _dynamo = boto3.client("dynamodb", region_name=REGIAO)
    return _dynamo


def _epoch(gerado_em: str) -> float:
    dt = datetime.fromisoformat(gerado_em)
    if dt.tzinfo is None:  # current_timestamp() do Databricks é UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def ler_mapa_dynamo() -> dict | None:
    resp = dynamo().get_item(TableName=TABELA, Key={"pk": {"S": "mapa_atual"}})
    item = resp.get("Item")
    if not item:
        return None
    mapa = {
        "gerado_em": item["gerado_em"]["S"],
        "fonte": item.get("fonte", {}).get("S"),
        "limiar_chuva_mm": float(item["limiar_chuva_mm"]["N"]) if "limiar_chuva_mm" in item else None,
        "pontos": json.loads(gzip.decompress(item["pontos_gz"]["B"])),
    }
    MAPA_GERADO_EM.set(_epoch(mapa["gerado_em"]))
    MAPA_TOTAL_PONTOS.set(len(mapa["pontos"]))
    return mapa


def atualizar_cache() -> None:
    try:
        mapa = ler_mapa_dynamo()
        if mapa is not None:
            _cache["mapa"] = mapa
        _cache["lido_em"] = time.time()
    except Exception as exc:  # mantém a última versão válida
        MAPA_FALHAS.inc()
        logger.error("Falha ao ler o mapa do DynamoDB (%s): %s", TABELA, exc)


async def _loop_cache() -> None:
    while True:
        await asyncio.to_thread(atualizar_cache)
        await asyncio.sleep(CACHE_TTL)


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(_loop_cache())
    yield
    task.cancel()


app = FastAPI(
    title="CitizenTech - Boca de Lobo API",
    version="3.0.0",
    description="Serve ao mapa do cidadão o retrato mais recente das bocas de lobo em risco (camada Ouro).",
    lifespan=lifespan,
)

if CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["GET"], allow_headers=[], max_age=600
    )


@app.middleware("http")
async def golden_signals_middleware(request: Request, call_next):
    if request.url.path == "/metrics":
        return await call_next(request)

    IN_PROGRESS.inc()
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        # Usa o template da rota para evitar alta cardinalidade
        route = request.scope.get("route")
        endpoint = getattr(route, "path", "unmatched")
        REQUEST_LATENCY.labels(request.method, endpoint).observe(time.perf_counter() - start)
        REQUEST_COUNT.labels(request.method, endpoint, str(status)).inc()
        IN_PROGRESS.dec()


@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "service": "CitizenTech Core API",
        "environment": os.getenv("ENVIRONMENT", "production"),
        "mapa_carregado": _cache["mapa"] is not None,
    }


@app.get("/api/v1/mapa")
def mapa(response: Response):
    """Retrato atual do mapa do cidadão, no mesmo formato do export da camada Ouro."""
    if _cache["mapa"] is None:
        raise HTTPException(status_code=503, detail="Mapa ainda não disponível.")
    response.headers["Cache-Control"] = f"public, max-age={CACHE_TTL}"
    return _cache["mapa"]


@app.get("/api/v1/mapa/resumo")
def resumo():
    """Contagem de pontos por nível de alerta (para o bot de alertas e painéis)."""
    m = _cache["mapa"]
    if m is None:
        raise HTTPException(status_code=503, detail="Mapa ainda não disponível.")
    contagem = {"EVITE A AREA": 0, "ATENCAO": 0, "NORMAL": 0}
    for p in m["pontos"]:
        contagem[p.get("nivel_alerta")] = contagem.get(p.get("nivel_alerta"), 0) + 1
    return {"gerado_em": m["gerado_em"], "total_pontos": len(m["pontos"]), "por_nivel": contagem}


@app.get("/metrics")
def metrics():
    """Endpoint padrão para raspagem do Prometheus (Métricas de Ouro)."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
