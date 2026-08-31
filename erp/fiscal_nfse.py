"""NFS-e (Sistema Nacional / Portal Nacional da NFS-e).

Portado do sistema "NFS-e Monitor":

* Interpretação dos XMLs do leiaute nacional (NFS-e + eventos), tolerante a
  variações de namespace/versão (busca por *local name*);
* Cliente HTTP (mTLS via ``requests``) das APIs do Ambiente de Dados
  Nacional (ADN): distribuição de DF-e por NSU, eventos por chave e
  DANFSe (PDF).
"""
from __future__ import annotations

import base64
import gzip
import json
import re
import zlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import requests

AMBIENTES_NFSE = {
    'producao': {
        'nome': 'Produção',
        'adn': 'https://adn.nfse.gov.br/contribuintes',
        'sefin': 'https://sefin.nfse.gov.br/sefinnacional',
    },
    'producao_restrita': {
        'nome': 'Produção Restrita (testes)',
        'adn': 'https://adn.producaorestrita.nfse.gov.br/contribuintes',
        'sefin': 'https://sefin.producaorestrita.nfse.gov.br/sefinnacional',
    },
}

REQUEST_TIMEOUT = 90


class AdnErro(Exception):
    """Erro de comunicação ou de negócio devolvido pelas APIs nacionais."""


# ------------------------------------------------------------------ #
# Interpretação dos XMLs
# ------------------------------------------------------------------ #

def _local(tag: str) -> str:
    return tag.rsplit('}', 1)[-1]


def _primeiro(raiz: ET.Element, nome: str) -> ET.Element | None:
    for el in raiz.iter():
        if _local(el.tag) == nome:
            return el
    return None


def _texto(raiz: ET.Element | None, nome: str) -> str:
    if raiz is None:
        return ''
    el = _primeiro(raiz, nome)
    return (el.text or '').strip() if el is not None else ''


def _numero(raiz: ET.Element | None, nome: str) -> float | None:
    t = _texto(raiz, nome)
    if not t:
        return None
    try:
        return float(t.replace(',', '.'))
    except ValueError:
        return None


def _documento_pessoa(el: ET.Element | None) -> str:
    """CNPJ, CPF ou NIF dentro de um bloco prest/toma/interm/emit."""
    if el is None:
        return ''
    for filho in el.iter():
        if _local(filho.tag) in ('CNPJ', 'CPF', 'NIF') and (filho.text or '').strip():
            return re.sub(r'\D', '', filho.text)
    return ''


def decodificar_arquivo_xml(conteudo: str | bytes) -> bytes:
    """Decodifica o campo ArquivoXml da distribuição (Base64 de XML gzipado).

    Tolera três formatos observados nas APIs nacionais: gzip+base64,
    zlib/deflate+base64 e XML puro em base64 (ou já em texto).
    """
    if isinstance(conteudo, str):
        conteudo = conteudo.strip().encode('ascii', 'ignore')
    if conteudo.lstrip().startswith(b'<'):
        return conteudo
    bruto = base64.b64decode(conteudo, validate=False)
    if bruto[:2] == b'\x1f\x8b':
        return gzip.decompress(bruto)
    if bruto.lstrip().startswith(b'<'):
        return bruto
    for wbits in (zlib.MAX_WBITS, -zlib.MAX_WBITS, zlib.MAX_WBITS | 16):
        try:
            return zlib.decompress(bruto, wbits)
        except zlib.error:
            continue
    return bruto


@dataclass
class NotaExtraida:
    chave_acesso: str = ''
    numero: str = ''
    serie: str = ''
    dh_emissao: str = ''
    data_emissao: str = ''
    dh_processamento: str = ''
    competencia: str = ''
    prestador_doc: str = ''
    prestador_nome: str = ''
    tomador_doc: str = ''
    tomador_nome: str = ''
    intermediario_doc: str = ''
    municipio: str = ''
    descricao_servico: str = ''
    valor_servico: float | None = None
    valor_liquido: float | None = None
    valor_iss: float | None = None


@dataclass
class EventoExtraido:
    chave_acesso: str = ''
    tipo_evento: str = ''
    descricao: str = ''
    dh_evento: str = ''
    cancela_nota: bool = False
    substitui_nota: bool = False


def tipo_documento(xml_bytes: bytes) -> str:
    """Identifica se o XML é uma NFS-e ou um evento."""
    raiz = ET.fromstring(xml_bytes)
    nome = _local(raiz.tag).lower()
    if 'evento' in nome:
        return 'EVENTO'
    if 'nfse' in nome or _primeiro(raiz, 'infNFSe') is not None:
        return 'NFSE'
    return 'OUTRO'


def parse_nfse(xml_bytes: bytes) -> NotaExtraida:
    raiz = ET.fromstring(xml_bytes)
    nota = NotaExtraida()

    inf = _primeiro(raiz, 'infNFSe')
    if inf is not None:
        id_attr = inf.get('Id', '')
        nota.chave_acesso = re.sub(r'^NFS', '', id_attr)
    nota.numero = _texto(raiz, 'nNFSe')
    nota.dh_processamento = _texto(raiz, 'dhProc')

    emit = _primeiro(raiz, 'emit')
    emit_doc = _documento_pessoa(emit)
    emit_nome = _texto(emit, 'xNome')

    dps = _primeiro(raiz, 'infDPS')
    if dps is not None:
        nota.serie = _texto(dps, 'serie')
        nota.dh_emissao = _texto(dps, 'dhEmi')
        nota.competencia = _texto(dps, 'dCompet')
        prest = _primeiro(dps, 'prest')
        toma = _primeiro(dps, 'toma')
        interm = _primeiro(dps, 'interm')
        nota.prestador_doc = _documento_pessoa(prest)
        nota.prestador_nome = _texto(prest, 'xNome')
        nota.tomador_doc = _documento_pessoa(toma)
        nota.tomador_nome = _texto(toma, 'xNome')
        nota.intermediario_doc = _documento_pessoa(interm)
        nota.descricao_servico = _texto(dps, 'xDescServ')
        tp_emit = _texto(dps, 'tpEmit')
        # tpEmit: 1=prestador emite, 2=tomador, 3=intermediário. O bloco emit
        # da NFS-e traz a razão social de quem emitiu — usa como fallback.
        if emit_doc:
            if not nota.prestador_doc and tp_emit in ('', '1'):
                nota.prestador_doc = emit_doc
            if emit_doc == nota.prestador_doc and not nota.prestador_nome:
                nota.prestador_nome = emit_nome
            if emit_doc == nota.tomador_doc and not nota.tomador_nome:
                nota.tomador_nome = emit_nome
    if not nota.prestador_nome and emit_nome:
        nota.prestador_nome = emit_nome

    nota.data_emissao = (nota.dh_emissao or '')[:10]
    nota.municipio = (
        _texto(raiz, 'xLocPrestacao') or _texto(raiz, 'xLocEmi') or _texto(raiz, 'xLocIncid')
    )

    valores_nfse = _primeiro(inf if inf is not None else raiz, 'valores')
    nota.valor_liquido = _numero(valores_nfse, 'vLiq')
    nota.valor_iss = _numero(valores_nfse, 'vISSQN')
    nota.valor_servico = _numero(dps, 'vServ') if dps is not None else None
    if nota.valor_servico is None:
        nota.valor_servico = _numero(raiz, 'vServ')
    if nota.valor_servico is None:
        nota.valor_servico = nota.valor_liquido
    return nota


_RE_TIPO_EVENTO = re.compile(r'^e(\d{6})$')

# Palavras que descaracterizam um cancelamento efetivo
_NAO_CANCELA = ('indeferido', 'bloqueio', 'desbloqueio', 'anula')


def parse_evento(xml_bytes: bytes) -> EventoExtraido:
    raiz = ET.fromstring(xml_bytes)
    ev = EventoExtraido()
    ev.chave_acesso = re.sub(r'^NFS', '', _texto(raiz, 'chNFSe'))
    ev.dh_evento = _texto(raiz, 'dhEvento') or _texto(raiz, 'dhProc')

    descricao = ''
    for el in raiz.iter():
        m = _RE_TIPO_EVENTO.match(_local(el.tag))
        if m:
            ev.tipo_evento = m.group(1)
            descricao = _texto(el, 'xDesc') or _texto(el, 'xDescEv')
            break
    if not descricao:
        descricao = _texto(raiz, 'xDesc') or _texto(raiz, 'xDescEv')
    ev.descricao = descricao

    desc_lower = descricao.lower()
    eh_cancelamento = 'cancelamento' in desc_lower or ev.tipo_evento in (
        '101101',  # Cancelamento de NFS-e
        '105102',  # Cancelamento por substituição
        '305102',  # Cancelamento por ofício
    )
    if eh_cancelamento and not any(p in desc_lower for p in _NAO_CANCELA):
        ev.cancela_nota = True
        if 'substitui' in desc_lower or ev.tipo_evento == '105102':
            ev.substitui_nota = True
    return ev


def evento_cancela(tipo_evento: str, descricao: str) -> str | None:
    """Retorna a situação resultante ('CANCELADA'/'SUBSTITUIDA') ou None."""
    desc = (descricao or '').lower()
    tipo = tipo_evento or ''
    cancela = 'cancelamento' in desc or tipo in ('101101', '105102', '305102')
    if cancela and not any(p in desc for p in _NAO_CANCELA):
        return 'SUBSTITUIDA' if ('substitui' in desc or tipo == '105102') else 'CANCELADA'
    return None


# ------------------------------------------------------------------ #
# Cliente do ADN (mTLS via requests)
# ------------------------------------------------------------------ #

def _get_ci(dicionario: dict, *nomes: str):
    """Busca tolerante (case-insensitive) de uma chave no JSON."""
    if not isinstance(dicionario, dict):
        return None
    baixo = {k.lower(): v for k, v in dicionario.items()}
    for nome in nomes:
        if nome.lower() in baixo:
            return baixo[nome.lower()]
    return None


@dataclass
class DocumentoDistribuido:
    nsu: int
    chave_acesso: str
    tipo_documento: str
    xml_bytes: bytes


@dataclass
class RespostaDistribuicao:
    documentos: list[DocumentoDistribuido] = field(default_factory=list)
    ult_nsu: int | None = None
    max_nsu: int | None = None
    status: str = ''

    @property
    def tem_mais(self) -> bool:
        if not self.documentos:
            return False
        if self.max_nsu is not None:
            maior = max(d.nsu for d in self.documentos if d.nsu is not None)
            return maior < self.max_nsu
        # Sem maxNSU informado: lote cheio sugere que há mais documentos.
        return len(self.documentos) >= 50


class AdnClient:
    def __init__(self, ambiente: str, cert_pair, base_adn: str | None = None,
                 base_sefin: str | None = None, verify=True):
        cfg = AMBIENTES_NFSE.get(ambiente, AMBIENTES_NFSE['producao'])
        self.base_adn = (base_adn or cfg['adn']).rstrip('/')
        self.base_sefin = (base_sefin or cfg['sefin']).rstrip('/')
        # O verify é repassado em CADA requisição: parâmetros por requisição
        # têm precedência sobre variáveis de ambiente (REQUESTS_CA_BUNDLE),
        # que sobrescreveriam um verify definido só na sessão.
        self._verify = verify
        self._sessao = requests.Session()
        self._sessao.cert = cert_pair
        self._sessao.headers.update({
            'Accept': 'application/json',
            'User-Agent': 'ERP-Financeiro/1.0 (consulta de contribuinte)',
        })

    def fechar(self) -> None:
        self._sessao.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.fechar()

    # ------------------------------------------------------------------ #

    def _erro_de_resposta(self, resp: requests.Response) -> AdnErro:
        detalhe = ''
        try:
            corpo = resp.json()
            detalhe = (
                _get_ci(corpo, 'mensagem', 'message', 'erro', 'descricao', 'motivo')
                or json.dumps(corpo, ensure_ascii=False)[:400]
            )
            if isinstance(detalhe, (list, dict)):
                detalhe = json.dumps(detalhe, ensure_ascii=False)[:400]
        except Exception:
            detalhe = resp.text[:400]
        if resp.status_code in (401, 403, 496):
            detalhe = (
                'Acesso negado pelo servidor nacional. Verifique se o certificado '
                f'digital é válido e corresponde ao CNPJ consultado. ({detalhe})'
            )
        return AdnErro(f'HTTP {resp.status_code} — {detalhe}')

    def distribuir_dfe(self, ultimo_nsu: int) -> RespostaDistribuicao:
        """Busca o próximo lote de documentos a partir do último NSU conhecido."""
        url = f'{self.base_adn}/DFe/{int(ultimo_nsu)}'
        try:
            resp = self._sessao.get(url, timeout=REQUEST_TIMEOUT, verify=self._verify)
        except requests.RequestException as exc:
            raise AdnErro(f'Falha de conexão com o ADN: {exc}') from exc

        if resp.status_code in (204, 404):
            return RespostaDistribuicao(status='sem_documentos')
        if resp.status_code != 200:
            raise self._erro_de_resposta(resp)

        try:
            corpo = resp.json()
        except json.JSONDecodeError as exc:
            raise AdnErro(f'Resposta inesperada do ADN (não é JSON): {resp.text[:200]}') from exc

        resultado = RespostaDistribuicao()
        resultado.status = str(_get_ci(corpo, 'StatusProcessamento', 'status') or '')
        for campo, attr in (('ultNSU', 'ult_nsu'), ('maxNSU', 'max_nsu')):
            valor = _get_ci(corpo, campo, campo.lower(), 'maiorNSU' if campo == 'maxNSU' else campo)
            if valor is not None:
                try:
                    setattr(resultado, attr, int(valor))
                except (TypeError, ValueError):
                    pass

        lote = _get_ci(corpo, 'LoteDFe', 'loteDfe', 'lote', 'documentos') or []
        for item in lote:
            arquivo = _get_ci(item, 'ArquivoXml', 'arquivoXml', 'XmlDocumento', 'xml')
            if not arquivo:
                continue
            try:
                xml_bytes = decodificar_arquivo_xml(arquivo)
            except Exception:
                continue
            nsu_raw = _get_ci(item, 'NSU', 'nsu')
            try:
                nsu = int(nsu_raw)
            except (TypeError, ValueError):
                nsu = 0
            resultado.documentos.append(
                DocumentoDistribuido(
                    nsu=nsu,
                    chave_acesso=str(_get_ci(item, 'ChaveAcesso', 'chaveAcesso', 'chave') or ''),
                    tipo_documento=str(_get_ci(item, 'TipoDocumento', 'tipoDocumento', 'tipoDoc') or '').upper(),
                    xml_bytes=xml_bytes,
                )
            )
        if resultado.documentos and resultado.ult_nsu is None:
            resultado.ult_nsu = max(d.nsu for d in resultado.documentos)
        return resultado

    def baixar_danfse(self, chave_acesso: str) -> bytes:
        """Baixa o PDF (DANFSe) da nota; tenta SEFIN Nacional e depois o ADN."""
        erros = []
        for url in (
            f'{self.base_sefin}/danfse/{chave_acesso}',
            f"{self.base_adn.rsplit('/contribuintes', 1)[0]}/danfse/{chave_acesso}",
        ):
            try:
                resp = self._sessao.get(
                    url,
                    headers={'Accept': 'application/pdf, application/json'},
                    timeout=REQUEST_TIMEOUT,
                    verify=self._verify,
                )
            except requests.RequestException as exc:
                erros.append(f'{url}: {exc}')
                continue
            if resp.status_code == 200:
                conteudo = resp.content
                if conteudo[:4] == b'%PDF':
                    return conteudo
                # Alguns retornos embrulham o PDF em JSON base64
                try:
                    corpo = resp.json()
                    b64 = _get_ci(corpo, 'pdf', 'arquivoPdf', 'danfse', 'documento')
                    if b64:
                        pdf = base64.b64decode(b64)
                        if pdf[:4] == b'%PDF':
                            return pdf
                except Exception:
                    pass
                erros.append(f'{url}: resposta não é PDF')
            else:
                erros.append(f'{url}: HTTP {resp.status_code}')
        raise AdnErro('Não foi possível obter o DANFSe. ' + ' | '.join(erros))
