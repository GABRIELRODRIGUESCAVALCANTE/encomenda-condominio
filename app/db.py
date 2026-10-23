"""Banco de dados SQLite: criação das tabelas e conexão."""
import sqlite3
from contextlib import contextmanager

from app import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS moradores (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    nome        TEXT NOT NULL,
    telefone    TEXT NOT NULL,
    bloco       TEXT NOT NULL DEFAULT '',
    apto        TEXT NOT NULL,
    ativo       INTEGER NOT NULL DEFAULT 1,
    criado_em   TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_moradores_apto ON moradores (bloco, apto);

-- Códigos de rastreio que o morador avisa antes da entrega.
CREATE TABLE IF NOT EXISTS rastreios (
    codigo      TEXT PRIMARY KEY,
    morador_id  INTEGER NOT NULL REFERENCES moradores (id),
    descricao   TEXT NOT NULL DEFAULT '',
    criado_em   TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS encomendas (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    morador_id         INTEGER NOT NULL REFERENCES moradores (id),
    codigo             TEXT NOT NULL DEFAULT '',
    observacao         TEXT NOT NULL DEFAULT '',
    codigo_retirada    TEXT NOT NULL,
    recebida_em        TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    retirada_em        TEXT,
    retirada_por       TEXT,
    notificado         INTEGER NOT NULL DEFAULT 0,
    erro_notificacao   TEXT
);
CREATE INDEX IF NOT EXISTS idx_encomendas_pendentes ON encomendas (retirada_em);
"""


def conectar() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def iniciar_banco() -> None:
    with conectar() as conn:
        conn.executescript(SCHEMA)


@contextmanager
def transacao():
    conn = conectar()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
