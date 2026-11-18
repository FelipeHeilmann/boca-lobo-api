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
    """Uma entrada por tabela: item_rpa responde por boca-lobo-dynamo-rpa, item pelo mapa."""

    def __init__(self, item=None, erro=None, item_rpa=None, erro_rpa=None):
        self.item, self.erro, self.item_rpa, self.erro_rpa, self.chamadas = item, erro, item_rpa, erro_rpa, 0

    def get_item(self, **kw):
        self.chamadas += 1
        rpa = kw["TableName"] == main.TABELA_RPA
        erro, item = (self.erro_rpa, self.item_rpa) if rpa else (self.erro, self.item)
        if erro:
            raise erro
        return {"Item": item} if item else {}


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
    main._cache.update({"mapa": None, "rpa": None, "lido_em": 0.0})
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


def test_idade_do_mapa_usa_gerado_em_utc(monkeypatch):
    item = item_dynamo()
    item["gerado_em"] = {"S": "2026-11-17T23:00:00+00:00"}
    item["gerado_em_utc"] = {"S": "2026-11-17T23:00:00.000000Z"}
    with cliente(monkeypatch, FakeDynamo(item)) as c:
        assert "mapa_gerado_em_timestamp_seconds 1.7949564e+09" in c.get("/metrics").text


def test_export_antigo_sem_fuso_e_horario_de_sp():
    assert main._epoch("2026-11-08 18:53:45") == main._epoch("2026-11-08T21:53:45Z")


# ----------------------------------- fluxo rpa -----------------------------------
ORDENS = [
    {"ordem": 1, "id_bueiro": "BL-001", "prioridade": "CRITICA", "status": "ABERTA", "equipe_id": "EQ-01",
     "reinspecionar": False},
    {"ordem": 2, "id_bueiro": "BL-002", "prioridade": "ALTA", "status": "AGUARDANDO_REFORCO", "equipe_id": None,
     "reinspecionar": True},
]
EXECUCAO = {"id": "EX20261118T142640", "inicio": "2026-11-18T14:26:40", "status": "CONCLUIDO_COM_PENDENCIAS",
            "nivel": "ALERTA", "chuva_24h_mm": 75.0}


def item_rpa():
    return {
        "pk": {"S": "rpa_atual"},
        "inicio_utc": {"S": "2026-11-18T17:26:40.000000Z"},
        "processado_em": {"S": "2026-11-18T17:27:00+00:00"},
        "dados_gz": {"B": gzip.compress(json.dumps({"execucao": EXECUCAO, "ordens_servico": ORDENS}).encode())},
    }


def test_rpa_servido_da_tabela_do_rpa(monkeypatch):
    with cliente(monkeypatch, FakeDynamo(item_dynamo(), item_rpa=item_rpa())) as c:
        corpo = c.get("/api/v1/rpa").json()
        assert corpo["execucao"]["nivel"] == "ALERTA"
        assert [o["id_bueiro"] for o in corpo["ordens_servico"]] == ["BL-001", "BL-002"]
        assert corpo["publicado_em"] == "2026-11-18T17:27:00+00:00"
        resumo = c.get("/api/v1/rpa/resumo").json()
        assert resumo["por_prioridade"] == {"CRITICA": 1, "ALTA": 1, "MEDIA": 0}
        assert resumo["por_status"] == {"ABERTA": 1, "AGUARDANDO_REFORCO": 1}
        assert (resumo["total_ordens"], resumo["reinspecionar"]) == (2, 1)
        assert c.get("/health").json()["rpa_carregado"] is True
        metricas = c.get("/metrics").text
        assert "rpa_total_ordens 2.0" in metricas
        assert "rpa_execucao_inicio_timestamp_seconds 1.7950228e+09" in metricas


def test_sem_rpa_retorna_503_e_mapa_segue_no_ar(monkeypatch):
    with cliente(monkeypatch, FakeDynamo(item_dynamo())) as c:
        assert c.get("/api/v1/rpa").status_code == 503
        assert c.get("/api/v1/rpa/resumo").status_code == 503
        assert c.get("/api/v1/mapa").status_code == 200


def test_falha_no_rpa_nao_afeta_o_mapa_e_mantem_ultima_versao(monkeypatch):
    fake = FakeDynamo(item_dynamo(), item_rpa=item_rpa())
    with cliente(monkeypatch, fake) as c:
        assert c.get("/api/v1/rpa").status_code == 200
        fake.erro_rpa = RuntimeError("tabela rpa fora")
        fake.item = dict(item_dynamo(), gerado_em={"S": "2026-11-09 06:00:00"})
        main.atualizar_cache()
        assert c.get("/api/v1/rpa").status_code == 200                       # última versão boa do RPA
        assert c.get("/api/v1/mapa").json()["gerado_em"] == "2026-11-09 06:00:00"  # mapa atualizou
        metricas = c.get("/metrics").text
        assert "rpa_leitura_falhas_total 1.0" in metricas
