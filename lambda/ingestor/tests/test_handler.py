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


def evento(chave="entrada/mapa_cidadao_2026-11-08.json"):
    return {"Records": [{"s3": {"bucket": {"name": "b"}, "object": {"key": chave}}}]}


def test_arquivo_valido_publica_e_move_para_processado(ambiente):
    fs3, fdb = ambiente(json.dumps(doc()).encode())
    r = handler.lambda_handler(evento(), None)["resultados"][0]
    assert r["status"] == "publicado" and r["total_pontos"] == 1
    item = fdb.itens[0]["Item"]
    assert item["pk"] == {"S": "mapa_atual"}
    assert json.loads(gzip.decompress(item["pontos_gz"]["B"]))[0]["id_bueiro"] == "BL-0001"
    assert fs3.copias[0][0] == "processado/mapa_cidadao_2026-11-08.json"
    assert fs3.removidos == ["entrada/mapa_cidadao_2026-11-08.json"]


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
    assert fs3.copias[0][0].startswith("quarentena/")


def test_arquivo_mais_antigo_nao_sobrescreve(ambiente):
    fs3, _ = ambiente(json.dumps(doc()).encode(), condicao_falha=True)
    r = handler.lambda_handler(evento(), None)["resultados"][0]
    assert r["status"] == "ignorado_mais_antigo"
    assert fs3.copias[0][0].startswith("processado/")


def test_chave_com_espaco_codificado(ambiente):
    fs3, _ = ambiente(json.dumps(doc()).encode())
    handler.lambda_handler(evento("entrada/mapa+cidadao.json"), None)
    assert fs3.removidos == ["entrada/mapa cidadao.json"]
