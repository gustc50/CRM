"""Prepara os módulos do app para rodarem fora da janela do pywebview.

Substitui `webview` e `certificado` por dublês: o primeiro só existe pra abrir
a GUI, e o segundo depende do `cryptography` + um .pfx real, que não cabe num
teste. Precisa rodar ANTES de `import main`, que importa os dois no topo.
"""
import os
import sys
import tempfile
import types

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

CNPJ_EMPRESA = "11222333000181"
SENHA_CERTA = "senha-certa"


class CertificadoInvalido(Exception):
    pass


class _CertificadoContext:
    """Dublê do certificado: aceita SENHA_CERTA e diz pertencer a CNPJ_EMPRESA."""

    def __init__(self, pfx_path, senha):
        if senha != SENHA_CERTA:
            raise CertificadoInvalido("Senha do certificado incorreta.")
        self.cnpj = CNPJ_EMPRESA
        self.cert_pair = ("/fake/cert.pem", "/fake/key.pem")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def banco_limpo(db):
    """Aponta o db pra um SQLite novo em pasta temporária.

    Os testes nunca tocam no data/app.db real, que guarda as NFes dos clientes.
    """
    db.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="nfe-teste-"), "app.db")
    db.init_db()


def preparar():
    """Instala os dublês e devolve (db, main, sefaz_client) com banco temporário."""
    sys.modules.setdefault("webview", types.ModuleType("webview"))

    cert = types.ModuleType("certificado")
    cert.CertificadoInvalido = CertificadoInvalido
    cert.CertificadoContext = _CertificadoContext
    sys.modules["certificado"] = cert

    import db

    banco_limpo(db)

    import main
    import sefaz_client

    return db, main, sefaz_client
