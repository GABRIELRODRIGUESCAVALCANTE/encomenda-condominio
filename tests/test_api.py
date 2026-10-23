import base64

import pytest
from fastapi.testclient import TestClient

from app import config, notificador


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_PATH", str(tmp_path / "teste.db"))
    monkeypatch.setattr(config, "FOTOS_DIR", str(tmp_path / "fotos"))
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


JPEG_MINIMO = b"\xff\xd8\xff\xe0" + b"\x00" * 100
PNG_MINIMO = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100


def test_foto_da_encomenda(cliente, tmp_path):
    m = criar_morador(cliente)
    enc = cliente.post("/api/encomendas", json={"morador_id": m["id"]}).json()
    url = f"/api/encomendas/{enc['id']}/foto"

    assert cliente.get(url).status_code == 404
    assert cliente.get("/api/encomendas").json()[0]["tem_foto"] is False

    r = cliente.put(url, files={"foto": ("f.jpg", JPEG_MINIMO, "image/jpeg")})
    assert r.status_code == 200
    primeira = r.json()["foto"]
    assert primeira.endswith(".jpg")
    pend = cliente.get("/api/encomendas").json()[0]
    assert pend["tem_foto"] is True and pend["foto"] == primeira

    baixada = cliente.get(url)
    assert baixada.status_code == 200 and baixada.content == JPEG_MINIMO
    assert baixada.headers["content-type"] == "image/jpeg"

    # Trocar a foto apaga o arquivo antigo
    segunda = cliente.put(url, files={"foto": ("f.png", PNG_MINIMO, "image/png")}).json()["foto"]
    assert segunda.endswith(".png") and segunda != primeira
    assert sorted(p.name for p in (tmp_path / "fotos").iterdir()) == [segunda]


def test_foto_exige_login(cliente):
    m = criar_morador(cliente)
    enc = cliente.post("/api/encomendas", json={"morador_id": m["id"]}).json()
    cliente.put(f"/api/encomendas/{enc['id']}/foto", files={"foto": ("f.jpg", JPEG_MINIMO, "image/jpeg")})
    assert cliente.get(f"/api/encomendas/{enc['id']}/foto", headers=basic("x", "y")).status_code == 401


def test_foto_recusa_arquivo_invalido_ou_grande(cliente, monkeypatch):
    m = criar_morador(cliente)
    enc = cliente.post("/api/encomendas", json={"morador_id": m["id"]}).json()
    url = f"/api/encomendas/{enc['id']}/foto"
    # Nome e tipo dizem imagem, mas o conteúdo não é
    assert cliente.put(url, files={"foto": ("x.jpg", b"<html>oi</html>", "image/jpeg")}).status_code == 400
    monkeypatch.setattr(config, "FOTO_MAX_BYTES", 50)
    assert cliente.put(url, files={"foto": ("f.jpg", JPEG_MINIMO, "image/jpeg")}).status_code == 413
    assert cliente.put("/api/encomendas/999/foto", files={"foto": ("f.jpg", b"\xff\xd8\xff", "image/jpeg")}).status_code == 404


def test_banco_antigo_ganha_coluna_foto(tmp_path, monkeypatch):
    import sqlite3
    from app import db

    caminho = tmp_path / "antigo.db"
    antigo = sqlite3.connect(caminho)
    antigo.execute("""CREATE TABLE encomendas (id INTEGER PRIMARY KEY, morador_id INTEGER NOT NULL,
        codigo TEXT NOT NULL DEFAULT '', observacao TEXT NOT NULL DEFAULT '', codigo_retirada TEXT NOT NULL,
        recebida_em TEXT, retirada_em TEXT, retirada_por TEXT, notificado INTEGER NOT NULL DEFAULT 0,
        erro_notificacao TEXT)""")
    antigo.commit()
    antigo.close()
    monkeypatch.setattr(config, "DATABASE_PATH", str(caminho))
    db.iniciar_banco()
    db.iniciar_banco()  # rodar de novo não pode dar erro
    with db.transacao() as conn:
        assert "foto" in {c["name"] for c in conn.execute("PRAGMA table_info(encomendas)")}
