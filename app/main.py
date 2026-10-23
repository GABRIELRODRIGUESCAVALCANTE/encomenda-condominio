"""API e telas do sistema de encomendas da portaria."""
import csv
import io
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import config, db
from app.notificador import ErroNotificacao, enviar_aviso, normalizar_telefone

logging.basicConfig(level=logging.INFO)
STATIC = config.BASE_DIR / "app" / "static"

seguranca = HTTPBasic()


def autenticar(cred: HTTPBasicCredentials = Depends(seguranca)) -> str:
    usuario_ok = secrets.compare_digest(cred.username.encode(), config.PORTARIA_USUARIO.encode())
    senha_ok = secrets.compare_digest(cred.password.encode(), config.PORTARIA_SENHA.encode())
    if not (usuario_ok and senha_ok):
        raise HTTPException(401, "Usuário ou senha inválidos", headers={"WWW-Authenticate": "Basic"})
    return cred.username


@asynccontextmanager
async def ciclo_de_vida(_app: FastAPI):
    db.iniciar_banco()
    yield


app = FastAPI(title="Encomendas do Condomínio", lifespan=ciclo_de_vida, dependencies=[Depends(autenticar)])
# CSS/JS públicos; as telas e a API exigem login.
app.mount("/static", StaticFiles(directory=STATIC), name="static")


# ---------- Modelos ----------

class MoradorIn(BaseModel):
    nome: str = Field(min_length=1)
    telefone: str = Field(min_length=8)
    bloco: str = ""
    apto: str = Field(min_length=1)


class RastreioIn(BaseModel):
    codigo: str = Field(min_length=3)
    morador_id: int
    descricao: str = ""


class EncomendaIn(BaseModel):
    morador_id: int
    codigo: str = ""
    observacao: str = ""


class RetiradaIn(BaseModel):
    codigo_retirada: str = ""
    retirada_por: str = ""
    sem_codigo: bool = False  # morador perdeu o código: exige nome de quem retirou


# ---------- Utilidades ----------

def unidade(m) -> str:
    return f"Bloco {m['bloco']} - Apto {m['apto']}" if m["bloco"] else f"Apto {m['apto']}"


def buscar_morador(conn, morador_id: int):
    m = conn.execute("SELECT * FROM moradores WHERE id = ? AND ativo = 1", (morador_id,)).fetchone()
    if not m:
        raise HTTPException(404, "Morador não encontrado")
    return m


def morador_dict(m) -> dict:
    d = dict(m)
    d["unidade"] = unidade(m)
    return d


def notificar(conn, encomenda_id: int) -> dict:
    """Envia o aviso e grava o resultado na encomenda."""
    e = conn.execute(
        """SELECT e.codigo_retirada, m.nome, m.telefone, m.bloco, m.apto
           FROM encomendas e JOIN moradores m ON m.id = e.morador_id WHERE e.id = ?""",
        (encomenda_id,),
    ).fetchone()
    try:
        enviar_aviso(e["nome"], e["telefone"], unidade(e), e["codigo_retirada"])
        conn.execute("UPDATE encomendas SET notificado = 1, erro_notificacao = NULL WHERE id = ?", (encomenda_id,))
        return {"notificado": True, "erro_notificacao": None}
    except ErroNotificacao as erro:
        logging.error("Falha ao notificar encomenda %s: %s", encomenda_id, erro)
        conn.execute("UPDATE encomendas SET notificado = 0, erro_notificacao = ? WHERE id = ?", (str(erro), encomenda_id))
        return {"notificado": False, "erro_notificacao": str(erro)}


def tipo_imagem(cabecalho: bytes) -> str | None:
    """Extensão pela assinatura do arquivo (não confia no nome nem no content-type)."""
    if cabecalho.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if cabecalho.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if cabecalho[:4] == b"RIFF" and cabecalho[8:12] == b"WEBP":
        return ".webp"
    return None


# ---------- Telas ----------

@app.get("/", include_in_schema=False)
def tela_portaria():
    return FileResponse(STATIC / "index.html")


@app.get("/moradores", include_in_schema=False)
def tela_moradores():
    return FileResponse(STATIC / "moradores.html")


@app.get("/api/config")
def ver_config():
    return {"nome_condominio": config.NOME_CONDOMINIO, "notificador": config.NOTIFICADOR}


# ---------- Moradores ----------

@app.get("/api/moradores")
def listar_moradores(q: str = ""):
    termo = q.strip()
    with db.transacao() as conn:
        if termo:
            linhas = conn.execute(
                """SELECT * FROM moradores WHERE ativo = 1 AND (
                       nome LIKE ? OR apto = ? OR (bloco || apto) LIKE ? OR (bloco || ' ' || apto) LIKE ?)
                   ORDER BY bloco, apto, nome LIMIT 50""",
                (f"%{termo}%", termo, f"%{termo.replace(' ', '')}%", f"%{termo}%"),
            ).fetchall()
        else:
            linhas = conn.execute("SELECT * FROM moradores WHERE ativo = 1 ORDER BY bloco, apto, nome").fetchall()
    return [morador_dict(m) for m in linhas]


@app.post("/api/moradores", status_code=201)
def criar_morador(dados: MoradorIn):
    with db.transacao() as conn:
        cur = conn.execute(
            "INSERT INTO moradores (nome, telefone, bloco, apto) VALUES (?, ?, ?, ?)",
            (dados.nome.strip(), normalizar_telefone(dados.telefone), dados.bloco.strip().upper(), dados.apto.strip()),
        )
        return morador_dict(buscar_morador(conn, cur.lastrowid))


@app.put("/api/moradores/{morador_id}")
def atualizar_morador(morador_id: int, dados: MoradorIn):
    with db.transacao() as conn:
        buscar_morador(conn, morador_id)
        conn.execute(
            "UPDATE moradores SET nome = ?, telefone = ?, bloco = ?, apto = ? WHERE id = ?",
            (dados.nome.strip(), normalizar_telefone(dados.telefone), dados.bloco.strip().upper(), dados.apto.strip(), morador_id),
        )
        return morador_dict(buscar_morador(conn, morador_id))


@app.delete("/api/moradores/{morador_id}", status_code=204)
def remover_morador(morador_id: int):
    # Desativa em vez de apagar, para manter o histórico de encomendas.
    with db.transacao() as conn:
        buscar_morador(conn, morador_id)
        conn.execute("UPDATE moradores SET ativo = 0 WHERE id = ?", (morador_id,))


@app.post("/api/moradores/importar")
async def importar_moradores(arquivo: UploadFile = File(...)):
    """Importa CSV com colunas: nome, telefone, bloco, apto (separador , ou ;)."""
    bruto = await arquivo.read()
    try:
        texto = bruto.decode("utf-8-sig")
    except UnicodeDecodeError:
        texto = bruto.decode("latin-1")  # CSV salvo pelo Excel em português
    primeira_linha = texto.splitlines()[0] if texto else ""
    separador = ";" if primeira_linha.count(";") > primeira_linha.count(",") else ","
    leitor = csv.DictReader(io.StringIO(texto), delimiter=separador)
    leitor.fieldnames = [c.strip().lower() for c in (leitor.fieldnames or [])]
    faltando = {"nome", "telefone", "apto"} - set(leitor.fieldnames)
    if faltando:
        raise HTTPException(400, f"Colunas obrigatórias ausentes: {', '.join(sorted(faltando))}")

    importados, erros = 0, []
    with db.transacao() as conn:
        for n, linha in enumerate(leitor, start=2):
            nome, tel, apto = ((linha.get(c) or "").strip() for c in ("nome", "telefone", "apto"))
            if not (nome and tel and apto):
                erros.append(f"Linha {n}: nome, telefone e apto são obrigatórios")
                continue
            conn.execute(
                "INSERT INTO moradores (nome, telefone, bloco, apto) VALUES (?, ?, ?, ?)",
                (nome, normalizar_telefone(tel), (linha.get("bloco") or "").strip().upper(), apto),
            )
            importados += 1
    return {"importados": importados, "erros": erros}


# ---------- Rastreios pré-cadastrados ----------

@app.post("/api/rastreios", status_code=201)
def pre_cadastrar_rastreio(dados: RastreioIn):
    with db.transacao() as conn:
        buscar_morador(conn, dados.morador_id)
        conn.execute(
            "INSERT OR REPLACE INTO rastreios (codigo, morador_id, descricao) VALUES (?, ?, ?)",
            (dados.codigo.strip(), dados.morador_id, dados.descricao.strip()),
        )
    return {"ok": True}


@app.get("/api/buscar-codigo")
def buscar_codigo(codigo: str):
    """Chamado logo após ler o QR: diz de quem é a encomenda (se pré-cadastrada)
    e se esse código já foi registrado e ainda não retirado."""
    codigo = codigo.strip()
    with db.transacao() as conn:
        r = conn.execute(
            """SELECT m.* FROM rastreios r JOIN moradores m ON m.id = r.morador_id
               WHERE r.codigo = ? AND m.ativo = 1""",
            (codigo,),
        ).fetchone()
        duplicada = conn.execute(
            "SELECT id FROM encomendas WHERE codigo = ? AND codigo != '' AND retirada_em IS NULL",
            (codigo,),
        ).fetchone()
    return {
        "codigo": codigo,
        "morador": morador_dict(r) if r else None,
        "ja_registrada": bool(duplicada),
    }


# ---------- Encomendas ----------

@app.get("/api/encomendas")
def listar_encomendas(status: str = "pendentes", q: str = ""):
    filtro = "e.retirada_em IS NULL" if status == "pendentes" else "e.retirada_em IS NOT NULL"
    params: list = []
    if q.strip():
        filtro += " AND (m.nome LIKE ? OR m.apto = ? OR e.codigo LIKE ?)"
        params += [f"%{q.strip()}%", q.strip(), f"%{q.strip()}%"]
    with db.transacao() as conn:
        linhas = conn.execute(
            f"""SELECT e.*, m.nome, m.telefone, m.bloco, m.apto
                FROM encomendas e JOIN moradores m ON m.id = e.morador_id
                WHERE {filtro} ORDER BY e.recebida_em DESC LIMIT 200""",
            params,
        ).fetchall()
    return [{**dict(e), "unidade": unidade(e), "tem_foto": bool(e["foto"])} for e in linhas]


@app.post("/api/encomendas", status_code=201)
def registrar_encomenda(dados: EncomendaIn):
    with db.transacao() as conn:
        buscar_morador(conn, dados.morador_id)
        codigo_retirada = f"{secrets.randbelow(10000):04d}"
        cur = conn.execute(
            "INSERT INTO encomendas (morador_id, codigo, observacao, codigo_retirada) VALUES (?, ?, ?, ?)",
            (dados.morador_id, dados.codigo.strip(), dados.observacao.strip(), codigo_retirada),
        )
        encomenda_id = cur.lastrowid
        if dados.codigo.strip():
            conn.execute("DELETE FROM rastreios WHERE codigo = ?", (dados.codigo.strip(),))
        conn.commit()  # a encomenda fica salva mesmo se o envio do aviso falhar
        resultado = notificar(conn, encomenda_id)
    return {"id": encomenda_id, "codigo_retirada": codigo_retirada, **resultado}


@app.post("/api/encomendas/{encomenda_id}/reenviar")
def reenviar_aviso(encomenda_id: int):
    with db.transacao() as conn:
        e = conn.execute("SELECT retirada_em FROM encomendas WHERE id = ?", (encomenda_id,)).fetchone()
        if not e:
            raise HTTPException(404, "Encomenda não encontrada")
        if e["retirada_em"]:
            raise HTTPException(400, "Encomenda já foi retirada")
        return notificar(conn, encomenda_id)


@app.put("/api/encomendas/{encomenda_id}/foto")
async def salvar_foto(encomenda_id: int, foto: UploadFile = File(...)):
    conteudo = await foto.read(config.FOTO_MAX_BYTES + 1)
    if len(conteudo) > config.FOTO_MAX_BYTES:
        raise HTTPException(413, f"Foto maior que {config.FOTO_MAX_BYTES // (1024 * 1024)} MB")
    extensao = tipo_imagem(conteudo[:16])
    if not extensao:
        raise HTTPException(400, "Envie uma imagem JPG, PNG ou WEBP")

    with db.transacao() as conn:
        e = conn.execute("SELECT foto FROM encomendas WHERE id = ?", (encomenda_id,)).fetchone()
        if not e:
            raise HTTPException(404, "Encomenda não encontrada")
        pasta = Path(config.FOTOS_DIR)
        pasta.mkdir(parents=True, exist_ok=True)
        # Nome novo a cada envio: troca a URL e o navegador não mostra a foto antiga do cache.
        nome = f"{encomenda_id}-{secrets.token_hex(4)}{extensao}"
        (pasta / nome).write_bytes(conteudo)
        conn.execute("UPDATE encomendas SET foto = ? WHERE id = ?", (nome, encomenda_id))
    if e["foto"]:
        (pasta / e["foto"]).unlink(missing_ok=True)
    return {"foto": nome}


@app.get("/api/encomendas/{encomenda_id}/foto")
def ver_foto(encomenda_id: int):
    with db.transacao() as conn:
        e = conn.execute("SELECT foto FROM encomendas WHERE id = ?", (encomenda_id,)).fetchone()
    if not e or not e["foto"]:
        raise HTTPException(404, "Encomenda sem foto")
    caminho = Path(config.FOTOS_DIR) / e["foto"]
    if not caminho.is_file():
        raise HTTPException(404, "Arquivo da foto não encontrado")
    return FileResponse(caminho, headers={"Cache-Control": "private, max-age=31536000"})


@app.post("/api/encomendas/{encomenda_id}/retirar")
def registrar_retirada(encomenda_id: int, dados: RetiradaIn):
    with db.transacao() as conn:
        e = conn.execute("SELECT * FROM encomendas WHERE id = ?", (encomenda_id,)).fetchone()
        if not e:
            raise HTTPException(404, "Encomenda não encontrada")
        if e["retirada_em"]:
            raise HTTPException(400, "Encomenda já foi retirada")
        if dados.sem_codigo:
            if not dados.retirada_por.strip():
                raise HTTPException(400, "Informe o nome de quem retirou")
        elif dados.codigo_retirada.strip() != e["codigo_retirada"]:
            raise HTTPException(400, "Código de retirada incorreto")
        conn.execute(
            "UPDATE encomendas SET retirada_em = datetime('now', 'localtime'), retirada_por = ? WHERE id = ?",
            (dados.retirada_por.strip() or "morador", encomenda_id),
        )
    return {"ok": True}
