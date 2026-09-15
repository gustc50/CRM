"""
Gerenciamento do certificado digital A1 (.pfx / .p12).

Regra de ouro: o certificado só existe na máquina do cliente. Este app
- NUNCA envia o .pfx pra nenhum servidor seu;
- NUNCA grava a senha em disco;
- extrai temporariamente a chave/certificado em arquivos temporários só
  durante a chamada HTTPS (mTLS) com a SEFAZ, e apaga logo em seguida.
"""
import tempfile
import os
import stat
from cryptography.hazmat.primitives.serialization import pkcs12, Encoding, PrivateFormat, NoEncryption
from cryptography.hazmat.primitives.serialization import BestAvailableEncryption
from cryptography import x509


class CertificadoInvalido(Exception):
    pass


class CertificadoContext:
    """
    Context manager: ao entrar, decodifica o .pfx e escreve PEMs temporários
    (permissão 600, só o dono lê) para uso com `requests` (cert=(certfile, keyfile)).
    Ao sair, apaga os arquivos temporários imediatamente.
    """

    def __init__(self, pfx_path: str, senha: str):
        self.pfx_path = pfx_path
        self.senha = senha
        self._tmpdir = None
        self.cert_file = None
        self.key_file = None
        self.cnpj = None
        self.titular = None
        self.validade_fim = None
        self.uf = None

    def __enter__(self):
        if not os.path.isfile(self.pfx_path):
            raise CertificadoInvalido(f"Arquivo de certificado não encontrado: {self.pfx_path}")

        with open(self.pfx_path, "rb") as f:
            pfx_bytes = f.read()

        try:
            private_key, certificate, additional_certs = pkcs12.load_key_and_certificates(
                pfx_bytes, self.senha.encode("utf-8")
            )
        except Exception as e:
            raise CertificadoInvalido(
                "Não foi possível abrir o certificado. Verifique o arquivo e a senha."
            ) from e

        if private_key is None or certificate is None:
            raise CertificadoInvalido("Certificado .pfx não contém chave privada e/ou certificado válido.")

        # Extrai CNPJ, nome do titular e UF do certificado (campo Subject, padrão ICP-Brasil)
        self.titular = certificate.subject.rfc4514_string()
        self.cnpj = _extrair_cnpj_do_certificado(certificate)
        self.validade_fim = certificate.not_valid_after_utc.isoformat()
        self.uf = _extrair_uf_do_certificado(certificate)

        # Escreve arquivos temporários (só nesta sessão de `with`)
        self._tmpdir = tempfile.mkdtemp(prefix="nfeapp_cert_")
        os.chmod(self._tmpdir, stat.S_IRWXU)  # só o dono acessa a pasta

        self.key_file = os.path.join(self._tmpdir, "key.pem")
        self.cert_file = os.path.join(self._tmpdir, "cert.pem")

        with open(self.key_file, "wb") as f:
            f.write(
                private_key.private_bytes(
                    Encoding.PEM, PrivateFormat.TraditionalOpenSSL, NoEncryption()
                )
            )
        os.chmod(self.key_file, stat.S_IRUSR | stat.S_IWUSR)

        with open(self.cert_file, "wb") as f:
            f.write(certificate.public_bytes(Encoding.PEM))
            # inclui a cadeia intermediária, se vier no .pfx (ajuda a validação da SEFAZ)
            if additional_certs:
                for c in additional_certs:
                    f.write(c.public_bytes(Encoding.PEM))
        os.chmod(self.cert_file, stat.S_IRUSR | stat.S_IWUSR)

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Limpeza imediata — não deixamos rastro do material sensível em disco
        try:
            if self.key_file and os.path.exists(self.key_file):
                _apagar_seguro(self.key_file)
            if self.cert_file and os.path.exists(self.cert_file):
                os.remove(self.cert_file)
            if self._tmpdir and os.path.isdir(self._tmpdir):
                os.rmdir(self._tmpdir)
        except OSError:
            pass
        return False  # não suprime exceções

    @property
    def cert_pair(self):
        """Tupla (cert_file, key_file) pronta pro parâmetro `cert=` do requests."""
        return (self.cert_file, self.key_file)


def _apagar_seguro(path):
    """Sobrescreve o arquivo com zeros antes de apagar (higiene básica)."""
    try:
        tamanho = os.path.getsize(path)
        with open(path, "r+b") as f:
            f.write(b"\x00" * tamanho)
            f.flush()
            os.fsync(f.fileno())
    finally:
        os.remove(path)


def _extrair_cnpj_do_certificado(certificate: x509.Certificate) -> str | None:
    """
    Certificados e-CNPJ ICP-Brasil trazem o CNPJ dentro da extensão
    SubjectAlternativeName (otherName) ou embutido no CN, no formato:
    "RAZAO SOCIAL:CNPJ14DIGITOS". Tentamos os dois jeitos.
    """
    try:
        cn = certificate.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
        if cn:
            valor = cn[0].value
            if ":" in valor:
                possivel_cnpj = valor.split(":")[-1].strip()
                digitos = "".join(ch for ch in possivel_cnpj if ch.isdigit())
                if len(digitos) == 14:
                    return digitos
    except Exception:
        pass
    return None


_UFS_VALIDAS = {
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS",
    "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC",
    "SP", "SE", "TO",
}

_NOME_PARA_UF = {
    "ACRE": "AC", "ALAGOAS": "AL", "AMAPA": "AP", "AMAZONAS": "AM",
    "BAHIA": "BA", "CEARA": "CE", "DISTRITO FEDERAL": "DF",
    "ESPIRITO SANTO": "ES", "GOIAS": "GO", "MARANHAO": "MA",
    "MATO GROSSO": "MT", "MATO GROSSO DO SUL": "MS", "MINAS GERAIS": "MG",
    "PARA": "PA", "PARAIBA": "PB", "PARANA": "PR", "PERNAMBUCO": "PE",
    "PIAUI": "PI", "RIO DE JANEIRO": "RJ", "RIO GRANDE DO NORTE": "RN",
    "RIO GRANDE DO SUL": "RS", "RONDONIA": "RO", "RORAIMA": "RR",
    "SANTA CATARINA": "SC", "SAO PAULO": "SP", "SERGIPE": "SE",
    "TOCANTINS": "TO",
}


def _normalizar_uf(valor: str) -> str | None:
    """Aceita tanto sigla ('SP') quanto nome por extenso ('São Paulo')."""
    import unicodedata

    v = valor.strip().upper()
    v = "".join(c for c in unicodedata.normalize("NFD", v) if unicodedata.category(c) != "Mn")
    if len(v) == 2 and v in _UFS_VALIDAS:
        return v
    return _NOME_PARA_UF.get(v)


def _extrair_uf_do_certificado(certificate: x509.Certificate) -> str | None:
    """Certificados ICP-Brasil geralmente trazem o estado do titular no
    atributo ST (stateOrProvinceName) do Subject. Usamos pra pré-selecionar
    a UF no cadastro sem o usuário precisar saber código de nada."""
    try:
        st = certificate.subject.get_attributes_for_oid(x509.NameOID.STATE_OR_PROVINCE_NAME)
        if st and st[0].value:
            return _normalizar_uf(st[0].value)
    except Exception:
        pass
    return None


def validar_pfx_rapido(pfx_path: str, senha: str) -> dict:
    """Usado pela tela de cadastro pra checar se o certificado/senha estão OK
    antes de salvar a empresa, sem manter nada em disco depois."""
    with CertificadoContext(pfx_path, senha) as ctx:
        return {
            "valido": True,
            "cnpj": ctx.cnpj,
            "titular": ctx.titular,
            "validade_fim": ctx.validade_fim,
            "uf": ctx.uf,
        }
