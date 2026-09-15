"""
Camada de dados local (SQLite).
Guarda: empresas cadastradas, controle de NSU (paginação SEFAZ) e as NFes baixadas.

IMPORTANTE: a senha do certificado NUNCA é gravada aqui. Só o caminho do
arquivo .pfx é guardado; a senha é digitada pelo usuário a cada consulta
(ou fica em memória durante a sessão do app, nunca em disco).
"""
import sqlite3
import os
import json
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "app.db")


def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS empresas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            razao_social TEXT NOT NULL,
            cnpj TEXT NOT NULL UNIQUE,          -- 14 dígitos, só números
            certificado_path TEXT,               -- caminho do .pfx no disco do cliente
            ambiente TEXT NOT NULL DEFAULT 'producao',  -- 'producao' ou 'homologacao'
            uf_autor TEXT NOT NULL DEFAULT '35', -- código UF (35=SP, ver tabela IBGE)
            ultimo_nsu TEXT NOT NULL DEFAULT '000000000000000',
            criado_em TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS notas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
            chave_nfe TEXT NOT NULL,
            nsu TEXT NOT NULL,
            tipo_doc TEXT NOT NULL,              -- 'resNFe' (resumo) ou 'nfeProc' (completa)
            emitente_cnpj TEXT,
            emitente_nome TEXT,
            valor_total REAL,
            data_emissao TEXT,
            situacao TEXT,
            xml_completo TEXT,                   -- XML bruto (descompactado) recebido
            baixado_em TEXT NOT NULL,
            UNIQUE(empresa_id, chave_nfe, tipo_doc)
        );

        CREATE TABLE IF NOT EXISTS log_consultas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
            data_hora TEXT NOT NULL,
            cstat TEXT,
            xmotivo TEXT,
            qtd_docs INTEGER DEFAULT 0
        );
        """
    )
    # Migração: bancos antigos podem ter a mesma chave duplicada (resumo +
    # completa). Remove o resumo quando a nota completa já existe.
    conn.execute(
        """DELETE FROM notas WHERE tipo_doc = 'resNFe' AND EXISTS (
               SELECT 1 FROM notas n2
               WHERE n2.empresa_id = notas.empresa_id
                 AND n2.chave_nfe = notas.chave_nfe
                 AND n2.tipo_doc = 'nfeProc'
           )"""
    )
    conn.commit()
    conn.close()


# ---------- Empresas ----------

def criar_empresa(razao_social, cnpj, certificado_path, ambiente="producao", uf_autor="35"):
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO empresas (razao_social, cnpj, certificado_path, ambiente, uf_autor, criado_em) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (razao_social, cnpj, certificado_path, ambiente, uf_autor, datetime.now().isoformat()),
    )
    conn.commit()
    empresa_id = cur.lastrowid
    conn.close()
    return empresa_id


def listar_empresas():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM empresas ORDER BY razao_social").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def obter_empresa(empresa_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM empresas WHERE id = ?", (empresa_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def atualizar_ultimo_nsu(empresa_id, nsu):
    """O cursor só anda pra frente. A SEFAZ às vezes responde 137 com ultNSU
    zerado (ou sem a tag), e gravar isso jogava o cursor de volta pro início:
    a consulta seguinte re-baixava ~90 dias em vários lotes seguidos, o que
    queima a cota de consumo e acaba gerando 656. Retroceder de propósito é
    só via resetar_nsu() (o botão de re-sincronizar)."""
    conn = get_conn()
    try:
        atual = conn.execute(
            "SELECT ultimo_nsu FROM empresas WHERE id = ?", (empresa_id,)
        ).fetchone()
        try:
            if atual is not None and int(nsu) < int(atual["ultimo_nsu"]):
                return
        except (TypeError, ValueError):
            pass  # valor não numérico: deixa o UPDATE seguir
        conn.execute("UPDATE empresas SET ultimo_nsu = ? WHERE id = ?", (nsu, empresa_id))
        conn.commit()
    finally:
        conn.close()


def resetar_nsu(empresa_id):
    """Zera o cursor de paginação: a próxima consulta re-pede à SEFAZ tudo
    que ela ainda retém (~90 dias). Recupera documentos que o Ambiente
    Nacional tenha 'pulado' em respostas 137 instáveis."""
    conn = get_conn()
    conn.execute("UPDATE empresas SET ultimo_nsu = '000000000000000' WHERE id = ?", (empresa_id,))
    conn.commit()
    conn.close()


def excluir_empresa(empresa_id):
    conn = get_conn()
    conn.execute("DELETE FROM empresas WHERE id = ?", (empresa_id,))
    conn.commit()
    conn.close()


# ---------- Notas ----------

def salvar_nota(empresa_id, chave_nfe, nsu, tipo_doc, emitente_cnpj, emitente_nome,
                 valor_total, data_emissao, situacao, xml_completo):
    conn = get_conn()
    try:
        # Uma NFe pode chegar duas vezes: primeiro como resumo (resNFe) e
        # depois completa (nfeProc). Na lista deve existir só UMA linha por
        # chave: a completa vence o resumo.
        if chave_nfe:
            if tipo_doc == "nfeProc":
                conn.execute(
                    "DELETE FROM notas WHERE empresa_id = ? AND chave_nfe = ? AND tipo_doc = 'resNFe'",
                    (empresa_id, chave_nfe),
                )
            elif tipo_doc == "resNFe":
                ja_tem_completa = conn.execute(
                    "SELECT 1 FROM notas WHERE empresa_id = ? AND chave_nfe = ? AND tipo_doc = 'nfeProc'",
                    (empresa_id, chave_nfe),
                ).fetchone()
                if ja_tem_completa:
                    return  # resumo não acrescenta nada se a completa já está salva

        conn.execute(
            """INSERT OR REPLACE INTO notas
               (empresa_id, chave_nfe, nsu, tipo_doc, emitente_cnpj, emitente_nome,
                valor_total, data_emissao, situacao, xml_completo, baixado_em)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (empresa_id, chave_nfe, nsu, tipo_doc, emitente_cnpj, emitente_nome,
             valor_total, data_emissao, situacao, xml_completo, datetime.now().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def listar_notas(empresa_id, data_inicio=None, data_fim=None):
    """data_inicio/data_fim: strings 'AAAA-MM-DD' (opcionais) pra filtrar por
    data de emissão. Notas sem data (ex: nfeProc sem resumo prévio) sempre
    aparecem, já que não dá pra saber se estão dentro do período ou não."""
    condicoes = ["empresa_id = ?"]
    params = [empresa_id]

    if data_inicio:
        condicoes.append("(data_emissao IS NULL OR date(data_emissao) >= date(?))")
        params.append(data_inicio)
    if data_fim:
        condicoes.append("(data_emissao IS NULL OR date(data_emissao) <= date(?))")
        params.append(data_fim)

    conn = get_conn()
    rows = conn.execute(
        "SELECT id, chave_nfe, nsu, tipo_doc, emitente_cnpj, emitente_nome, valor_total, "
        "data_emissao, situacao, baixado_em FROM notas WHERE "
        + " AND ".join(condicoes)
        + " ORDER BY data_emissao DESC",
        params,
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def obter_nota_xml(nota_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM notas WHERE id = ?", (nota_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


# ---------- Log ----------

def registrar_log(empresa_id, cstat, xmotivo, qtd_docs):
    conn = get_conn()
    conn.execute(
        "INSERT INTO log_consultas (empresa_id, data_hora, cstat, xmotivo, qtd_docs) VALUES (?, ?, ?, ?, ?)",
        (empresa_id, datetime.now().isoformat(), cstat, xmotivo, qtd_docs),
    )
    conn.commit()
    conn.close()


def ultima_consulta(empresa_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM log_consultas WHERE empresa_id = ? ORDER BY data_hora DESC LIMIT 1",
        (empresa_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None
