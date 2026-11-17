import gzip
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main  # noqa: E402

PONTOS = [
    {"id_bueiro": "BL-1", "nivel_alerta": "EVITE A AREA"},
    {"id_bueiro": "BL-2", "nivel_alerta": "NORMAL"},
]


class FakeDynamo:
    def __init__(self, item=None, erro=None):
        self.item, self.erro, self.chamadas = item, erro, 0

    def get_item(self, **kw):
        self.chamadas += 1
        if self.erro:
            raise self.erro
        return {"Item": self.item} if self.item else {}


def item_dynamo():
    return {
        "pk": {"S": "mapa_atual"},
        "gerado_em": {"S": "2026-11-08 18:53:45.379444"},
        "fonte": {"S": "workspace.gs.ouro_vw_mapa_cidadao"},
        "limiar_chuva_mm": {"N": "10.0"},
        "pontos_gz": {"B": gzip.compress(json.dumps(PONTOS).encode())},
    }


@pytest.fixture(autouse=True)
def limpa_cache():
    main._cache.update({"mapa": None, "lido_em": 0.0})
    yield


def cliente(monkeypatch, fake):
    monkeypatch.setattr(main, "dynamo", lambda: fake)
    return TestClient(main.app)


def test_mapa_servido_do_dynamo(monkeypatch):
    with cliente(monkeypatch, FakeDynamo(item_dynamo())) as c:
        r = c.get("/api/v1/mapa")
        assert r.status_code == 200
        corpo = r.json()
        assert corpo["gerado_em"] == "2026-11-08 18:53:45.379444"
        assert corpo["limiar_chuva_mm"] == 10.0
        assert [p["id_bueiro"] for p in corpo["pontos"]] == ["BL-1", "BL-2"]
        assert c.get("/api/v1/mapa/resumo").json()["por_nivel"]["EVITE A AREA"] == 1
        metricas = c.get("/metrics").text
        assert "mapa_total_pontos 2.0" in metricas
        assert 'http_requests_total{endpoint="/api/v1/mapa",http_status="200",method="GET"}' in metricas


def test_sem_mapa_retorna_503(monkeypatch):
    with cliente(monkeypatch, FakeDynamo(None)) as c:
        assert c.get("/api/v1/mapa").status_code == 503
        assert c.get("/health").json()["mapa_carregado"] is False


def test_falha_no_dynamo_mantem_ultima_versao(monkeypatch):
    fake = FakeDynamo(item_dynamo())
    with cliente(monkeypatch, fake) as c:
        assert c.get("/api/v1/mapa").status_code == 200
        fake.erro = RuntimeError("dynamo fora")
        main.atualizar_cache()
        assert c.get("/api/v1/mapa").status_code == 200
        assert "mapa_leitura_falhas_total 1.0" in c.get("/metrics").text
