"""Certificado digital A1 (.pfx/.p12) — carregamento, validação e mTLS.

Funcionalidades portadas dos sistemas NFS-e Monitor e Consulta de NFe:

* Inspeção do certificado: extrai CNPJ/CPF (extensões ICP-Brasil), titular,
  validade e UF do titular;
* ``CertificadoContext``: converte o .pfx em um par de PEMs temporários
  (permissão restrita) para autenticação mútua (mTLS) com ``requests``,
  apagando os arquivos com sobrescrita logo após o uso;
* Criptografia local (Fernet) da senha do certificado — a chave fica em um
  arquivo ao lado do banco de dados (``.chave_secreta``).
"""
from __future__ import annotations

import datetime as dt
import os
import re
import stat
import tempfile
import unicodedata
from dataclasses import dataclass

from cryptography import x509
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    pkcs12,
)
from cryptography.x509.oid import NameOID

# Extensões ICP-Brasil (subjectAltName / otherName)
OID_ICP_CNPJ = x509.ObjectIdentifier('2.16.76.1.3.3')  # e-CNPJ: CNPJ da PJ
OID_ICP_PF = x509.ObjectIdentifier('2.16.76.1.3.1')    # e-CPF: dados da PF


class CertificadoInvalido(Exception):
    pass


# ------------------------------------------------------------------ #
# Criptografia local da senha (Fernet)
# ------------------------------------------------------------------ #

def _obter_chave_fernet(caminho_chave: str) -> bytes:
    if os.path.exists(caminho_chave):
        with open(caminho_chave, 'rb') as f:
            return f.read().strip()
    chave = Fernet.generate_key()
    with open(caminho_chave, 'wb') as f:
        f.write(chave)
    try:
        os.chmod(caminho_chave, 0o600)
    except OSError:
        pass  # Windows não suporta chmod POSIX
    return chave


def criptografar_senha(texto: str, caminho_chave: str) -> str:
    return Fernet(_obter_chave_fernet(caminho_chave)).encrypt(texto.encode('utf-8')).decode('ascii')


def descriptografar_senha(token: str, caminho_chave: str) -> str:
    return Fernet(_obter_chave_fernet(caminho_chave)).decrypt(token.encode('ascii')).decode('utf-8')


# ------------------------------------------------------------------ #
# Inspeção do certificado
# ------------------------------------------------------------------ #

@dataclass
class InfoCertificado:
    titular: str
    documento: str          # CNPJ (14 dígitos) ou CPF (11 dígitos)
    tipo_documento: str     # 'CNPJ' | 'CPF'
    valido_de: str
    valido_ate: str
    expirado: bool
    dias_para_vencer: int
    uf: str | None


def _extrair_documento(cert: x509.Certificate) -> tuple[str, str]:
    """Retorna (documento, tipo) a partir das extensões ICP-Brasil ou do CN."""
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        for other in san.value.get_values_for_type(x509.OtherName):
            if other.type_id == OID_ICP_CNPJ:
                digitos = re.findall(rb'\d{14}', other.value)
                if digitos:
                    return digitos[0].decode(), 'CNPJ'
            if other.type_id == OID_ICP_PF:
                # No e-CPF o campo contém data de nascimento (8) + CPF (11) + ...
                m = re.search(rb'\d{8}(\d{11})', other.value)
                if m:
                    return m.group(1).decode(), 'CPF'
    except x509.ExtensionNotFound:
        pass

    # CN no padrão "RAZAO SOCIAL:documento"
    cns = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    if cns:
        m = re.search(r':(\d{14})$', cns[0].value)
        if m:
            return m.group(1), 'CNPJ'
        m = re.search(r':(\d{11})$', cns[0].value)
        if m:
            return m.group(1), 'CPF'
    raise CertificadoInvalido(
        'Não foi possível extrair o CNPJ/CPF do certificado. '
        'Verifique se é um certificado ICP-Brasil (e-CNPJ/e-CPF).'
    )


def _titular(cert: x509.Certificate) -> str:
    cns = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    if not cns:
        return ''
    return cns[0].value.split(':')[0]


_UFS_VALIDAS = {
    'AC', 'AL', 'AP', 'AM', 'BA', 'CE', 'DF', 'ES', 'GO', 'MA', 'MT', 'MS',
    'MG', 'PA', 'PB', 'PR', 'PE', 'PI', 'RJ', 'RN', 'RS', 'RO', 'RR', 'SC',
    'SP', 'SE', 'TO',
}

_NOME_PARA_UF = {
    'ACRE': 'AC', 'ALAGOAS': 'AL', 'AMAPA': 'AP', 'AMAZONAS': 'AM',
    'BAHIA': 'BA', 'CEARA': 'CE', 'DISTRITO FEDERAL': 'DF',
    'ESPIRITO SANTO': 'ES', 'GOIAS': 'GO', 'MARANHAO': 'MA',
    'MATO GROSSO': 'MT', 'MATO GROSSO DO SUL': 'MS', 'MINAS GERAIS': 'MG',
    'PARA': 'PA', 'PARAIBA': 'PB', 'PARANA': 'PR', 'PERNAMBUCO': 'PE',
    'PIAUI': 'PI', 'RIO DE JANEIRO': 'RJ', 'RIO GRANDE DO NORTE': 'RN',
    'RIO GRANDE DO SUL': 'RS', 'RONDONIA': 'RO', 'RORAIMA': 'RR',
    'SANTA CATARINA': 'SC', 'SAO PAULO': 'SP', 'SERGIPE': 'SE',
    'TOCANTINS': 'TO',
}

# Código IBGE da UF (cUFAutor do webservice da NF-e)
UF_PARA_CODIGO = {
    'RO': '11', 'AC': '12', 'AM': '13', 'RR': '14', 'PA': '15', 'AP': '16',
    'TO': '17', 'MA': '21', 'PI': '22', 'CE': '23', 'RN': '24', 'PB': '25',
    'PE': '26', 'AL': '27', 'SE': '28', 'BA': '29', 'MG': '31', 'ES': '32',
    'RJ': '33', 'SP': '35', 'PR': '41', 'SC': '42', 'RS': '43', 'MS': '50',
    'MT': '51', 'GO': '52', 'DF': '53',
}


def _normalizar_uf(valor: str) -> str | None:
    """Aceita tanto sigla ('SP') quanto nome por extenso ('São Paulo')."""
    v = valor.strip().upper()
    v = ''.join(c for c in unicodedata.normalize('NFD', v) if unicodedata.category(c) != 'Mn')
    if len(v) == 2 and v in _UFS_VALIDAS:
        return v
    return _NOME_PARA_UF.get(v)


def _extrair_uf(cert: x509.Certificate) -> str | None:
    """UF do titular no atributo ST (stateOrProvinceName) do Subject."""
    try:
        st = cert.subject.get_attributes_for_oid(NameOID.STATE_OR_PROVINCE_NAME)
        if st and st[0].value:
            return _normalizar_uf(st[0].value)
    except Exception:
        pass
    return None


def carregar_pfx(pfx_bytes: bytes, senha: str):
    """Carrega o PKCS#12 e devolve (chave_privada, certificado, cadeia)."""
    try:
        chave, cert, cadeia = pkcs12.load_key_and_certificates(
            pfx_bytes, senha.encode('utf-8') if senha else None
        )
    except Exception as exc:  # senha errada, arquivo corrompido etc.
        raise CertificadoInvalido(
            'Não foi possível abrir o certificado: senha incorreta ou arquivo inválido.'
        ) from exc
    if cert is None or chave is None:
        raise CertificadoInvalido('O arquivo não contém certificado e chave privada.')
    return chave, cert, cadeia or []


def inspecionar_pfx(caminho_pfx: str, senha: str) -> InfoCertificado:
    if not os.path.isfile(caminho_pfx):
        raise CertificadoInvalido(f'Arquivo de certificado não encontrado: {caminho_pfx}')
    with open(caminho_pfx, 'rb') as f:
        pfx_bytes = f.read()

    _, cert, _ = carregar_pfx(pfx_bytes, senha)
    documento, tipo = _extrair_documento(cert)
    agora = dt.datetime.now(dt.timezone.utc)
    nao_depois = cert.not_valid_after_utc if hasattr(cert, 'not_valid_after_utc') else cert.not_valid_after.replace(tzinfo=dt.timezone.utc)
    nao_antes = cert.not_valid_before_utc if hasattr(cert, 'not_valid_before_utc') else cert.not_valid_before.replace(tzinfo=dt.timezone.utc)
    return InfoCertificado(
        titular=_titular(cert),
        documento=documento,
        tipo_documento=tipo,
        valido_de=nao_antes.date().isoformat(),
        valido_ate=nao_depois.date().isoformat(),
        expirado=agora > nao_depois,
        dias_para_vencer=(nao_depois - agora).days,
        uf=_extrair_uf(cert),
    )


# ------------------------------------------------------------------ #
# Contexto mTLS para requests
# ------------------------------------------------------------------ #

def _apagar_seguro(caminho: str) -> None:
    """Sobrescreve o arquivo com zeros antes de apagar (higiene básica)."""
    try:
        tamanho = os.path.getsize(caminho)
        with open(caminho, 'r+b') as f:
            f.write(b'\x00' * tamanho)
            f.flush()
            os.fsync(f.fileno())
    finally:
        os.remove(caminho)


class CertificadoContext:
    """Context manager: ao entrar, decodifica o .pfx e escreve PEMs temporários
    (permissão restrita) para uso com ``requests`` (``cert=(cert, key)``).
    Ao sair, apaga os arquivos temporários imediatamente — a chave privada
    nunca fica em disco fora da janela de uso.
    """

    def __init__(self, caminho_pfx: str, senha: str):
        self.caminho_pfx = caminho_pfx
        self.senha = senha
        self._tmpdir = None
        self.cert_file = None
        self.key_file = None
        self.documento = None

    def __enter__(self):
        if not os.path.isfile(self.caminho_pfx):
            raise CertificadoInvalido(f'Arquivo de certificado não encontrado: {self.caminho_pfx}')

        with open(self.caminho_pfx, 'rb') as f:
            pfx_bytes = f.read()
        chave, cert, cadeia = carregar_pfx(pfx_bytes, self.senha)

        try:
            self.documento = _extrair_documento(cert)[0]
        except CertificadoInvalido:
            self.documento = None

        self._tmpdir = tempfile.mkdtemp(prefix='erp_cert_')
        os.chmod(self._tmpdir, stat.S_IRWXU)  # só o dono acessa a pasta

        self.key_file = os.path.join(self._tmpdir, 'key.pem')
        self.cert_file = os.path.join(self._tmpdir, 'cert.pem')

        with open(self.key_file, 'wb') as f:
            f.write(chave.private_bytes(Encoding.PEM, PrivateFormat.TraditionalOpenSSL, NoEncryption()))
        os.chmod(self.key_file, stat.S_IRUSR | stat.S_IWUSR)

        with open(self.cert_file, 'wb') as f:
            f.write(cert.public_bytes(Encoding.PEM))
            # inclui a cadeia intermediária, se vier no .pfx (ajuda na validação)
            for extra in cadeia:
                f.write(extra.public_bytes(Encoding.PEM))
        os.chmod(self.cert_file, stat.S_IRUSR | stat.S_IWUSR)

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
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
        """Tupla (cert_file, key_file) pronta para o parâmetro ``cert=`` do requests."""
        return (self.cert_file, self.key_file)
