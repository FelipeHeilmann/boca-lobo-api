import gzip
import io
import json
import sys
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import handler  # noqa: E402


def ponto(**extra):
    base = {
        "id_bueiro": "BL-0001", "latitude": -23.56, "longitude": -46.50,
        "logradouro": "Rua Teste", "nivel_alerta": "NORMAL", "recomendacao": "ok",
    }
    base.update(extra)
    return base


def doc(**extra):
    base = {"gerado_em": "2026-11-08 18:53:45.379444", "fonte": "ouro", "limiar_chuva_mm": 10.0, "pontos": [ponto()]}
    base.update(extra)
    return base


class FakeS3:
    def __init__(self, conteudo: bytes):
        self.conteudo = conteudo
        self.copias, self.removidos = [], []

    def get_object(self, Bucket, Key):
        return {"ContentLength": len(self.conteudo), "Body": io.BytesIO(self.conteudo)}

    def copy_object(self, Bucket, Key, CopySource, **kw):
        self.copias.append((Key, kw.get("Metadata")))

    def delete_object(self, Bucket, Key):
        self.removidos.append(Key)


class FakeDynamo:
    def __init__(self, condicao_falha=False):
        self.itens, self.condicao_falha = [], condicao_falha

    def put_item(self, **kw):
        if self.condicao_falha:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem")
        self.itens.append(kw)


@pytest.fixture
def ambiente(monkeypatch):
    def configurar(conteudo, condicao_falha=False):
        fs3, fdb = FakeS3(conteudo), FakeDynamo(condicao_falha)
        monkeypatch.setattr(handler, "s3", lambda: fs3)
        monkeypatch.setattr(handler, "dynamo", lambda: fdb)
        return fs3, fdb
    return configurar


def evento(chave="databricks/entrada/mapa_cidadao_2026-11-08.json"):
    return {"Records": [{"s3": {"bucket": {"name": "b"}, "object": {"key": chave}}}]}


def test_arquivo_valido_publica_e_move_para_processado(ambiente):
    fs3, fdb = ambiente(json.dumps(doc()).encode())
    r = handler.lambda_handler(evento(), None)["resultados"][0]
    assert r["status"] == "publicado" and r["total_pontos"] == 1
    item = fdb.itens[0]["Item"]
    assert item["pk"] == {"S": "mapa_atual"}
    assert json.loads(gzip.decompress(item["pontos_gz"]["B"]))[0]["id_bueiro"] == "BL-0001"
    assert fs3.copias[0] == ("databricks/processado/mapa_cidadao_2026-11-08.json", {"status": "publicado"})
    assert fs3.removidos == ["databricks/entrada/mapa_cidadao_2026-11-08.json"]
    assert fdb.itens[0]["TableName"] == "boca-lobo-dynamo-mapa"


@pytest.mark.parametrize("conteudo,trecho", [
    (b"{nao e json", "Expecting"),
    (json.dumps({"pontos": [ponto()]}).encode(), "gerado_em"),
    (json.dumps(doc(pontos=[])).encode(), "lista não vazia"),
    (json.dumps(doc(pontos=[ponto(nivel_alerta="PERIGO")])).encode(), "nivel_alerta"),
    (json.dumps(doc(pontos=[ponto(latitude=0.0, longitude=0.0)])).encode(), "fora da área"),
    (json.dumps(doc(pontos=[ponto(), ponto()])).encode(), "duplicado"),
    (json.dumps(doc(pontos=[ponto(latitude="-23.5")])).encode(), "latitude"),
])
def test_arquivo_invalido_vai_para_quarentena(ambiente, conteudo, trecho):
    fs3, fdb = ambiente(conteudo)
    r = handler.lambda_handler(evento(), None)["resultados"][0]
    assert r["status"] == "rejeitado" and trecho in r["motivo"]
    assert fdb.itens == []
    assert fs3.copias[0][0].startswith("databricks/quarentena/")


def test_arquivo_mais_antigo_nao_sobrescreve(ambiente):
    fs3, _ = ambiente(json.dumps(doc()).encode(), condicao_falha=True)
    r = handler.lambda_handler(evento(), None)["resultados"][0]
    assert r["status"] == "ignorado_mais_antigo"
    assert fs3.copias[0][0].startswith("databricks/processado/")
    assert fs3.copias[0][1] == {"status": "ignorado_mais_antigo"}


def test_chave_com_espaco_codificado(ambiente):
    fs3, _ = ambiente(json.dumps(doc()).encode())
    handler.lambda_handler(evento("databricks/entrada/mapa+cidadao.json"), None)
    assert fs3.removidos == ["databricks/entrada/mapa cidadao.json"]


@pytest.mark.parametrize("entrada,esperado", [
    ("2026-11-08 18:53:45.379444", "2026-11-08T21:53:45.379444Z"),  # export antigo: horário de SP, sem fuso
    ("2026-11-17T23:00:00+00:00", "2026-11-17T23:00:00.000000Z"),   # export novo: UTC com fuso
    ("2026-11-17T20:00:00-03:00", "2026-11-17T23:00:00.000000Z"),
])
def test_gerado_em_normalizado_para_utc(entrada, esperado):
    assert handler.gerado_em_utc(entrada) == esperado


def test_escrita_condicional_usa_utc(ambiente):
    _, fdb = ambiente(json.dumps(doc(gerado_em="2026-11-17T23:00:00+00:00")).encode())
    handler.lambda_handler(evento(), None)
    put = fdb.itens[0]
    assert put["ExpressionAttributeValues"][":v"] == {"S": "2026-11-17T23:00:00.000000Z"}
    assert "gerado_em_utc" in put["ConditionExpression"]


# ----------------------------------- fluxo rpa -----------------------------------
def ordem(**extra):
    base = {
        "ordem": 1, "id_bueiro": "BL-001", "endereco": "Rua das Garças, 664", "tipo": "boca de lobo",
        "area_id": "AR-01", "area": "Jardim Pantanal", "nivel_risco_area": 3, "lat": -23.4978, "lon": -46.4305,
        "nivel_obstrucao": 92.0, "tipo_residuo": "folhas", "data_inspecao": "2026-11-16T09:05", "score": 94.0,
        "prioridade": "CRITICA", "equipe_id": "EQ-01", "status": "ABERTA", "reinspecionar": False, "observacao": "",
    }
    base.update(extra)
    return base


def resultado_rpa(**extra):
    base = {
        "execucao": {
            "id": "EX20261118T142640", "inicio": "2026-11-18T14:26:40", "fim": "2026-11-18T14:26:40",
            "status": "CONCLUIDO_COM_PENDENCIAS", "nivel": "ALERTA", "fonte_previsao": "simulacao",
            "chuva_24h_mm": 75.0, "pico_mm_h": 22.5, "probabilidade_max_pct": 90.0, "linhas_lidas": 44,
            "inspecoes_validas": 23, "rejeitados": 7, "duplicados": 2, "ordens_geradas": 2, "alertas_enviados": 0,
            "mensagem": "texto livre do bot",
        },
        "ordens_servico": [ordem(), ordem(ordem=2, id_bueiro="BL-002", prioridade="ALTA", equipe_id=None,
                                          status="AGUARDANDO_REFORCO", reinspecionar=True)],
        "alertas": [{"destinatario_id": "MOR-001", "destino_mascarado": "(11) 9****-**72"}],
        "historico_execucoes": [],
    }
    base.update(extra)
    return base


def evento_rpa(nome="bocaLobo_20261118_142640.json"):
    return evento(f"rpa/entrada/{nome}")


def test_rpa_valido_publica_na_tabela_do_rpa_e_move_dentro_da_pasta(ambiente):
    fs3, fdb = ambiente(json.dumps(resultado_rpa()).encode())
    r = handler.lambda_handler(evento_rpa(), None)["resultados"][0]
    assert (r["status"], r["fluxo"], r["total_ordens"]) == ("publicado", "rpa", 2)
    put = fdb.itens[0]
    assert put["TableName"] == "boca-lobo-dynamo-rpa"
    assert put["ExpressionAttributeValues"][":v"] == {"S": "2026-11-18T17:26:40.000000Z"}  # SP -> UTC
    item = put["Item"]
    assert item["pk"] == {"S": "rpa_atual"} and item["nivel"] == {"S": "ALERTA"}
    dados = json.loads(gzip.decompress(item["dados_gz"]["B"]))
    assert [o["id_bueiro"] for o in dados["ordens_servico"]] == ["BL-001", "BL-002"]
    assert dados["ordens_servico"][1]["equipe_id"] is None
    assert fs3.copias[0] == ("rpa/processado/bocaLobo_20261118_142640.json", {"status": "publicado"})
    assert fs3.removidos == ["rpa/entrada/bocaLobo_20261118_142640.json"]


def test_rpa_publica_so_execucao_e_ordens_sem_alertas_nem_texto_livre(ambiente):
    _, fdb = ambiente(json.dumps(resultado_rpa()).encode())
    handler.lambda_handler(evento_rpa(), None)
    dados = json.loads(gzip.decompress(fdb.itens[0]["Item"]["dados_gz"]["B"]))
    assert set(dados) == {"execucao", "ordens_servico"}
    assert "mensagem" not in dados["execucao"]
    assert "MOR-001" not in json.dumps(dados)


def test_rpa_sem_acao_com_lista_vazia_e_valido(ambiente):
    ex = dict(resultado_rpa()["execucao"], status="SEM_ACAO", nivel="NORMAL", ordens_geradas=0)
    _, fdb = ambiente(json.dumps(resultado_rpa(execucao=ex, ordens_servico=[])).encode())
    assert handler.lambda_handler(evento_rpa(), None)["resultados"][0]["status"] == "publicado"
    assert fdb.itens[0]["Item"]["total_ordens"] == {"N": "0"}


def test_rpa_falha_de_previsao_sem_nivel_e_valida(ambiente):
    ex = {"id": "EX1", "inicio": "2026-11-18T06:00:00", "status": "FALHA_PREVISAO"}
    _, fdb = ambiente(json.dumps({"execucao": ex, "ordens_servico": []}).encode())
    assert handler.lambda_handler(evento_rpa(), None)["resultados"][0]["status"] == "publicado"
    assert "nivel" not in fdb.itens[0]["Item"]


def _ex(**extra):
    return dict(resultado_rpa()["execucao"], **extra)


@pytest.mark.parametrize("doc,trecho", [
    ({"ordens_servico": []}, "execucao"),
    ({"execucao": _ex()}, "ordens_servico"),
    (resultado_rpa(execucao=_ex(id="")), "execucao.id"),
    (resultado_rpa(execucao=_ex(inicio="ontem")), "execucao.inicio"),
    (resultado_rpa(execucao=_ex(nivel="TEMPESTADE")), "execucao.nivel"),
    (resultado_rpa(execucao=_ex(chuva_24h_mm=-1)), "chuva_24h_mm"),
    (resultado_rpa(ordens_servico={}), "deve ser uma lista"),
    (resultado_rpa(ordens_servico=[ordem(prioridade="BAIXA")]), "prioridade"),
    (resultado_rpa(ordens_servico=[ordem(status="FECHADA")]), "status"),
    (resultado_rpa(ordens_servico=[ordem(score=120)]), "score"),
    (resultado_rpa(ordens_servico=[ordem(nivel_obstrucao="90")]), "nivel_obstrucao"),
    (resultado_rpa(ordens_servico=[ordem(lat=0.0, lon=0.0)]), "fora da área"),
    (resultado_rpa(ordens_servico=[ordem(reinspecionar="sim")]), "reinspecionar"),
    (resultado_rpa(ordens_servico=[ordem(), ordem()]), "duplicado"),
])
def test_rpa_invalido_vai_para_quarentena_do_rpa(ambiente, doc, trecho):
    fs3, fdb = ambiente(json.dumps(doc).encode())
    r = handler.lambda_handler(evento_rpa(), None)["resultados"][0]
    assert r["status"] == "rejeitado" and trecho in r["motivo"], r
    assert fdb.itens == []
    assert fs3.copias[0][0] == "rpa/quarentena/bocaLobo_20261118_142640.json"


def test_rpa_mais_antigo_nao_sobrescreve(ambiente):
    fs3, _ = ambiente(json.dumps(resultado_rpa()).encode(), condicao_falha=True)
    r = handler.lambda_handler(evento_rpa(), None)["resultados"][0]
    assert r["status"] == "ignorado_mais_antigo"
    assert fs3.copias[0] == ("rpa/processado/bocaLobo_20261118_142640.json", {"status": "ignorado_mais_antigo"})


@pytest.mark.parametrize("chave", [
    "entrada/mapa.json",                       # pasta antiga, sem fluxo
    "rpa/bocaLobo.json",                       # raiz do fluxo, fora de entrada/
    "rpa/processado/x.json",                   # destino da própria Lambda: nunca reprocessa
    "outro/entrada/x.json",                    # fluxo desconhecido
])
def test_fora_de_fluxo_entrada_e_ignorado_sem_mover(ambiente, chave):
    fs3, fdb = ambiente(json.dumps(resultado_rpa()).encode())
    r = handler.lambda_handler(evento(chave), None)["resultados"][0]
    assert r["status"] == "ignorado_pasta_desconhecida"
    assert fs3.copias == [] and fs3.removidos == [] and fdb.itens == []
