"""Envio do aviso de encomenda ao morador."""
import logging
import re

import httpx

from app import config

log = logging.getLogger("notificador")


class ErroNotificacao(Exception):
    pass


def normalizar_telefone(telefone: str) -> str:
    """Deixa só dígitos e acrescenta o DDI 55 em números brasileiros."""
    digitos = re.sub(r"\D", "", telefone or "")
    if len(digitos) in (10, 11):  # DDD + número
        digitos = "55" + digitos
    return digitos


def texto_aviso(nome: str, unidade: str, codigo_retirada: str) -> str:
    primeiro_nome = nome.split()[0] if nome else ""
    return (
        f"Olá {primeiro_nome}! Chegou uma encomenda para {unidade} "
        f"na portaria do {config.NOME_CONDOMINIO}. "
        f"Código de retirada: {codigo_retirada}"
    )


def enviar_aviso(nome: str, telefone: str, unidade: str, codigo_retirada: str) -> None:
    """Envia o aviso. Lança ErroNotificacao se falhar."""
    numero = normalizar_telefone(telefone)
    if len(numero) < 12:
        raise ErroNotificacao(f"Telefone inválido: {telefone!r}")

    if config.NOTIFICADOR == "console":
        log.warning("[AVISO -> %s] %s", numero, texto_aviso(nome, unidade, codigo_retirada))
        return
    if config.NOTIFICADOR == "whatsapp":
        _enviar_whatsapp(numero, nome, unidade, codigo_retirada)
        return
    raise ErroNotificacao(f"NOTIFICADOR desconhecido: {config.NOTIFICADOR!r}")


def _enviar_whatsapp(numero: str, nome: str, unidade: str, codigo_retirada: str) -> None:
    """WhatsApp Cloud API. Mensagens iniciadas pela empresa precisam de um
    template aprovado pela Meta, com 3 variáveis: {{1}} nome, {{2}} unidade,
    {{3}} código de retirada."""
    if not (config.WHATSAPP_TOKEN and config.WHATSAPP_PHONE_NUMBER_ID):
        raise ErroNotificacao("WHATSAPP_TOKEN/WHATSAPP_PHONE_NUMBER_ID não configurados")

    url = (
        f"https://graph.facebook.com/{config.WHATSAPP_API_VERSION}/"
        f"{config.WHATSAPP_PHONE_NUMBER_ID}/messages"
    )
    parametros = [nome.split()[0] if nome else "morador(a)", unidade, codigo_retirada]
    corpo = {
        "messaging_product": "whatsapp",
        "to": numero,
        "type": "template",
        "template": {
            "name": config.WHATSAPP_TEMPLATE,
            "language": {"code": config.WHATSAPP_TEMPLATE_IDIOMA},
            "components": [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": p} for p in parametros],
                }
            ],
        },
    }
    try:
        resp = httpx.post(
            url,
            json=corpo,
            headers={"Authorization": f"Bearer {config.WHATSAPP_TOKEN}"},
            timeout=15,
        )
    except httpx.HTTPError as e:
        raise ErroNotificacao(f"Falha de conexão com o WhatsApp: {e}") from e
    if resp.status_code >= 400:
        raise ErroNotificacao(f"WhatsApp respondeu {resp.status_code}: {resp.text[:300]}")
