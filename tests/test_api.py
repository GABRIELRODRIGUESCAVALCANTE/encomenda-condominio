import base64

import pytest
from fastapi.testclient import TestClient

from app import config, notificador


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_PATH", str(tmp_path / "teste.db"))
    monkeypatch.setattr(config, "NOTIFICADOR", "console")
    from app.main import app

    with TestClient(app, headers=basic(config.PORTARIA_USUARIO, config.PORTARIA_SENHA)) as c:
        yield c


def basic(usuario, senha):
    return {"Authorization": "Basic " + base64.b64encode(f"{usuario}:{senha}".encode()).decode()}


def criar_morador(cliente, **extra):
    dados = {"nome": "Maria Souza", "telefone": "(11) 98765-4321", "bloco": "a", "apto": "302", **extra}
    resp = cliente.post("/api/moradores", json=dados)
    assert resp.status_code == 201
    return resp.json()


def test_exige_login(cliente):
    assert cliente.get("/api/moradores", headers=basic("x", "y")).status_code == 401
    assert cliente.get("/", headers=basic("x", "y")).status_code == 401


def test_telefone_normalizado():
    assert notificador.normalizar_telefone("(11) 98765-4321") == "5511987654321"
    assert notificador.normalizar_telefone("+55 11 98765-4321") == "5511987654321"


def test_cadastra_e_busca_morador(cliente):
    m = criar_morador(cliente)
    assert m["telefone"] == "5511987654321"
    assert m["unidade"] == "Bloco A - Apto 302"
    assert [x["id"] for x in cliente.get("/api/moradores", params={"q": "302"}).json()] == [m["id"]]
    assert [x["id"] for x in cliente.get("/api/moradores", params={"q": "A302"}).json()] == [m["id"]]
    assert [x["id"] for x in cliente.get("/api/moradores", params={"q": "maria"}).json()] == [m["id"]]
    assert cliente.get("/api/moradores", params={"q": "999"}).json() == []


def test_fluxo_completo_com_rastreio_pre_cadastrado(cliente, caplog):
    m = criar_morador(cliente)
    cliente.post("/api/rastreios", json={"codigo": "BR123456789BR", "morador_id": m["id"]})

    # Porteiro lê o QR: sistema já sabe de quem é
    r = cliente.get("/api/buscar-codigo", params={"codigo": "BR123456789BR"}).json()
    assert r["morador"]["id"] == m["id"]
    assert r["ja_registrada"] is False

    with caplog.at_level("WARNING", logger="notificador"):
        enc = cliente.post("/api/encomendas", json={"morador_id": m["id"], "codigo": "BR123456789BR"}).json()
    assert enc["notificado"] is True
    assert len(enc["codigo_retirada"]) == 4
    assert enc["codigo_retirada"] in caplog.text and "5511987654321" in caplog.text

    # Ler de novo o mesmo QR avisa que já está registrada
    r = cliente.get("/api/buscar-codigo", params={"codigo": "BR123456789BR"}).json()
    assert r["ja_registrada"] is True and r["morador"] is None

    pend = cliente.get("/api/encomendas").json()
    assert [p["id"] for p in pend] == [enc["id"]]

    # Código errado é recusado; certo dá baixa
    errado = "0000" if enc["codigo_retirada"] != "0000" else "1111"
    assert cliente.post(f"/api/encomendas/{enc['id']}/retirar", json={"codigo_retirada": errado}).status_code == 400
    ok = cliente.post(f"/api/encomendas/{enc['id']}/retirar", json={"codigo_retirada": enc["codigo_retirada"]})
    assert ok.status_code == 200
    assert cliente.get("/api/encomendas").json() == []
    assert len(cliente.get("/api/encomendas", params={"status": "retiradas"}).json()) == 1


def test_retirada_sem_codigo_exige_nome(cliente):
    m = criar_morador(cliente)
    enc = cliente.post("/api/encomendas", json={"morador_id": m["id"]}).json()
    url = f"/api/encomendas/{enc['id']}/retirar"
    assert cliente.post(url, json={"sem_codigo": True}).status_code == 400
    assert cliente.post(url, json={"sem_codigo": True, "retirada_por": "Maria"}).status_code == 200


def test_falha_no_aviso_nao_perde_encomenda(cliente, monkeypatch):
    m = criar_morador(cliente)

    def falhar(*_args, **_kw):
        raise notificador.ErroNotificacao("WhatsApp fora do ar")

    monkeypatch.setattr("app.main.enviar_aviso", falhar)
    enc = cliente.post("/api/encomendas", json={"morador_id": m["id"]}).json()
    assert enc["notificado"] is False and "fora do ar" in enc["erro_notificacao"]
    pend = cliente.get("/api/encomendas").json()
    assert pend[0]["erro_notificacao"] == "WhatsApp fora do ar"

    monkeypatch.setattr("app.main.enviar_aviso", notificador.enviar_aviso)
    assert cliente.post(f"/api/encomendas/{enc['id']}/reenviar").json()["notificado"] is True


def test_importa_csv_com_ponto_e_virgula(cliente):
    csv = "Nome;Telefone;Bloco;Apto\nAna;21987654321;B;204\n;;;\nJoão;11912345678;;101\n"
    r = cliente.post("/api/moradores/importar", files={"arquivo": ("m.csv", csv.encode(), "text/csv")}).json()
    assert r["importados"] == 2 and len(r["erros"]) == 1
    nomes = {m["nome"] for m in cliente.get("/api/moradores").json()}
    assert nomes == {"Ana", "João"}


def test_whatsapp_monta_requisicao(monkeypatch):
    monkeypatch.setattr(config, "NOTIFICADOR", "whatsapp")
    monkeypatch.setattr(config, "WHATSAPP_TOKEN", "tok")
    monkeypatch.setattr(config, "WHATSAPP_PHONE_NUMBER_ID", "123")
    enviado = {}

    class Resp:
        status_code = 200
        text = "{}"

    def falso_post(url, json, headers, timeout):
        enviado.update(url=url, json=json, headers=headers)
        return Resp()

    monkeypatch.setattr(notificador.httpx, "post", falso_post)
    notificador.enviar_aviso("Maria Souza", "11987654321", "Apto 302", "4821")
    assert enviado["url"].endswith("/123/messages")
    assert enviado["json"]["to"] == "5511987654321"
    params = [p["text"] for p in enviado["json"]["template"]["components"][0]["parameters"]]
    assert params == ["Maria", "Apto 302", "4821"]
    assert enviado["headers"]["Authorization"] == "Bearer tok"
