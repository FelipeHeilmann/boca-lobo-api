import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager

import requests
from fastapi import FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

# Configuração de logging estruturado
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("citizen-tech-api")

RAIN_API_URL = os.getenv("RAIN_API_URL", "https://api.open-meteo.com/v1/forecast")
RAIN_PROBE_INTERVAL = int(os.getenv("RAIN_PROBE_INTERVAL", "60"))

# ---------------------------------------------------------------------------
# Métricas de Ouro (Golden Signals)
#   Latência   -> http_request_duration_seconds (histogram)
#   Tráfego    -> http_requests_total (counter)
#   Erros      -> http_requests_total{http_status=~"5.."}
#   Saturação  -> http_requests_in_progress + process_* (CPU/memória, coletor padrão)
# ---------------------------------------------------------------------------
REQUEST_COUNT = Counter(
    "http_requests_total", "Total HTTP Requests", ["method", "endpoint", "http_status"]
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP Request Latency in seconds",
    ["method", "endpoint"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
IN_PROGRESS = Gauge("http_requests_in_progress", "Requisições HTTP em processamento")
RAIN_API_UP = Gauge("rain_api_up", "Status da API externa de Previsão de Chuva (1=Online, 0=Offline)")

# Pontos críticos de monitoramento (São Paulo - Coordenadas Reais)
PONTOS_MONITORADOS = [
    {"id": "BL-042", "bairro": "Itaquera", "lat": -23.5400, "lon": -46.4500},
    {"id": "BL-109", "bairro": "Grajaú", "lat": -23.7500, "lon": -46.6800},
    {"id": "BL-215", "bairro": "Vila Prudente", "lat": -23.5800, "lon": -46.5700},
]


def _consultar_precipitacao(lat: float, lon: float) -> requests.Response:
    return requests.get(
        RAIN_API_URL,
        params={"latitude": lat, "longitude": lon, "current": "precipitation,rain"},
        timeout=4,
    )


def _probe_rain_api() -> None:
    """Verifica a disponibilidade da API meteorológica e atualiza o gauge."""
    ponto = PONTOS_MONITORADOS[0]
    try:
        ok = _consultar_precipitacao(ponto["lat"], ponto["lon"]).status_code == 200
    except requests.RequestException as exc:
        logger.error("Probe da API meteorológica falhou: %s", exc)
        ok = False
    RAIN_API_UP.set(1 if ok else 0)


async def _probe_loop() -> None:
    while True:
        await asyncio.to_thread(_probe_rain_api)
        await asyncio.sleep(RAIN_PROBE_INTERVAL)


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(_probe_loop())
    yield
    task.cancel()


app = FastAPI(
    title="CitizenTech - Drenagem Urbana & Mitigação de Enchentes",
    version="2.2.0",
    description="API de monitoramento preventivo de bueiros integrada a dados meteorológicos em tempo real.",
    lifespan=lifespan,
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
        # Usa o template da rota (ex.: /api/v1/bocas-risco) para evitar alta cardinalidade
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
    }


@app.get("/api/v1/bocas-risco")
def consultar_risco_bocas():
    """
    Consulta em tempo real a precipitação meteorológica via Open-Meteo API
    para classificar o risco de alagamento nos pontos monitorados.
    """
    resultados = []
    api_funcional = True

    for ponto in PONTOS_MONITORADOS:
        try:
            response = _consultar_precipitacao(ponto["lat"], ponto["lon"])

            if response.status_code == 200:
                current_data = response.json().get("current", {})
                precipitacao = current_data.get("precipitation", 0.0)

                # Motor de decisão baseado em dados reais de chuva (mm)
                if precipitacao > 4.0:
                    nivel_risco = "Crítico (Alagamento Iminente)"
                    status_operacional = "Drenagem Saturada"
                elif precipitacao > 1.0:
                    nivel_risco = "Moderado"
                    status_operacional = "Alerta de Vazão"
                else:
                    nivel_risco = "Baixo"
                    status_operacional = "Operando Normalmente"
            else:
                api_funcional = False
                precipitacao = 0.0
                nivel_risco = "Indeterminado"
                status_operacional = "Falha na telemetria"
        except requests.RequestException as exc:
            logger.error("Falha de comunicação com o serviço meteorológico para o ponto %s: %s", ponto["id"], exc)
            api_funcional = False
            precipitacao = 0.0
            nivel_risco = "Desconhecido"
            status_operacional = "Offline por Timeout"

        resultados.append(
            {
                "id": ponto["id"],
                "bairro": ponto["bairro"],
                "localizacao": {"lat": ponto["lat"], "lon": ponto["lon"]},
                "precipitacao_atual_mm": precipitacao,
                "status_operacional": status_operacional,
                "nivel_risco": nivel_risco,
            }
        )

    # Atualiza métrica de observabilidade para o Prometheus / Alerta de Falha Silenciosa
    RAIN_API_UP.set(1 if api_funcional else 0)

    return {
        "fonte_dados": "Open-Meteo Real-Time Telemetry",
        "status_conexao_externa": "conectado" if api_funcional else "degradado",
        "total_pontos_monitorados": len(resultados),
        "registros": resultados,
    }


@app.get("/metrics")
def metrics():
    """Endpoint padrão para raspagem do Prometheus (Métricas de Ouro)."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
