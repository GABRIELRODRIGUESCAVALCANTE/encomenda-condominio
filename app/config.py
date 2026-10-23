"""Configuração lida de variáveis de ambiente (ou do arquivo .env)."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _carregar_env(caminho: Path) -> None:
    if not caminho.exists():
        return
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, valor = linha.split("=", 1)
        os.environ.setdefault(chave.strip(), valor.strip().strip('"').strip("'"))


_carregar_env(BASE_DIR / ".env")

DATABASE_PATH = os.environ.get("DATABASE_PATH", str(BASE_DIR / "encomendas.db"))
NOME_CONDOMINIO = os.environ.get("NOME_CONDOMINIO", "Condomínio")

# Login da portaria (HTTP Basic). Troque em produção!
PORTARIA_USUARIO = os.environ.get("PORTARIA_USUARIO", "portaria")
PORTARIA_SENHA = os.environ.get("PORTARIA_SENHA", "trocar123")

# "console" só imprime a mensagem no terminal (bom para testes).
# "whatsapp" usa a WhatsApp Cloud API oficial da Meta.
NOTIFICADOR = os.environ.get("NOTIFICADOR", "console")

WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_TEMPLATE = os.environ.get("WHATSAPP_TEMPLATE", "encomenda_chegou")
WHATSAPP_TEMPLATE_IDIOMA = os.environ.get("WHATSAPP_TEMPLATE_IDIOMA", "pt_BR")
WHATSAPP_API_VERSION = os.environ.get("WHATSAPP_API_VERSION", "v21.0")
