"""NF-e — cliente do Web Service NFeDistribuicaoDFe (Ambiente Nacional/SEFAZ).

Portado do sistema "Consulta de NFe". Baseado na Nota Técnica 2014.002:
usa o método ``nfeDistDFeInteresse`` com ``<distNSU>`` para buscar, em lotes,
todos os documentos fiscais (resumos de NFe e NFes completas) de interesse do
CNPJ informado — autenticado via certificado digital do próprio CNPJ (mTLS).

Regras da SEFAZ respeitadas:
- Sempre envia o ultNSU retornado na consulta anterior (nunca "do zero");
- Se cStat=137 (nenhum documento novo), não insiste antes de 1h — senão o
  CNPJ é bloqueado temporariamente (656 - Consumo Indevido);
- No máximo ~50 documentos por lote (a própria SEFAZ pagina via NSU).
"""
from __future__ import annotations

import base64
import gzip
from xml.etree import ElementTree as ET

import requests

URLS_NFE = {
    'producao': 'https://www1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx',
    'homologacao': 'https://hom1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx',
}

SOAP_ACTION = 'http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe/nfeDistDFeInteresse'

_NS = '{http://www.portalfiscal.inf.br/nfe}'


class ErroSefaz(Exception):
    def __init__(self, cstat, xmotivo):
        self.cstat = cstat
        self.xmotivo = xmotivo
        super().__init__(f'[{cstat}] {xmotivo}')


def _montar_xml_distnsu(cnpj: str, uf_autor: str, ambiente: str, ult_nsu: str) -> str:
    tp_amb = '1' if ambiente == 'producao' else '2'
    # IMPORTANTE: sem declaração <?xml ...?> aqui. Este XML vai embutido dentro
    # do <nfeDadosMsg> do envelope SOAP, e uma declaração no meio do documento
    # deixa a mensagem malformada — a SEFAZ responde 400 Bad Request.
    return (
        f'<distDFeInt xmlns="http://www.portalfiscal.inf.br/nfe" versao="1.01">'
        f'<tpAmb>{tp_amb}</tpAmb>'
        f'<cUFAutor>{uf_autor}</cUFAutor>'
        f'<CNPJ>{cnpj}</CNPJ>'
        f'<distNSU><ultNSU>{ult_nsu}</ultNSU></distNSU>'
        f'</distDFeInt>'
    )


def _montar_envelope_soap(xml_dados: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap12:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
        'xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">'
        '<soap12:Body>'
        '<nfeDistDFeInteresse xmlns="http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe">'
        f'<nfeDadosMsg>{xml_dados}</nfeDadosMsg>'
        '</nfeDistDFeInteresse>'
        '</soap12:Body>'
        '</soap12:Envelope>'
    )


def consultar_distnsu(cnpj: str, uf_autor: str, ambiente: str, ult_nsu: str,
                      cert_pair, url: str | None = None, verify=True) -> dict:
    """Faz UMA chamada ao webservice (um lote de até ~50 documentos).

    Retorna dict: { cstat, xmotivo, ult_nsu, max_nsu, documentos: [...] }
    documentos: lista de dicts com tipo ('resNFe'/'nfeProc'/'resEvento'/outro),
    xml (str) e nsu.
    """
    xml_dados = _montar_xml_distnsu(cnpj, uf_autor, ambiente, ult_nsu)
    envelope = _montar_envelope_soap(xml_dados)

    destino = url or URLS_NFE[ambiente]
    headers = {
        'Content-Type': 'application/soap+xml; charset=utf-8; action=' + SOAP_ACTION,
    }

    resp = requests.post(
        destino,
        data=envelope.encode('utf-8'),
        headers=headers,
        cert=cert_pair,   # autenticação mTLS com o certificado do cliente
        verify=verify,
        timeout=60,
    )

    if resp.status_code >= 400:
        # A SEFAZ costuma mandar o motivo real no corpo (SOAP Fault). Mostra
        # ele em vez de só "400 Bad Request", que não ajuda a diagnosticar.
        corpo = (resp.text or '').strip()
        detalhe = _extrair_soap_fault(corpo) or corpo[:400]
        raise ErroSefaz(
            str(resp.status_code),
            f'HTTP {resp.status_code} do webservice. {detalhe}'.strip(),
        )

    return _parsear_resposta(resp.text)


def _extrair_soap_fault(xml_resposta: str) -> str | None:
    """Tenta extrair a mensagem de um SOAP Fault (<faultstring>/<Reason>/<Text>)."""
    if not xml_resposta or '<' not in xml_resposta:
        return None
    try:
        root = ET.fromstring(xml_resposta)
    except ET.ParseError:
        return None
    for tag in ('faultstring', 'Text', 'Reason', 'faultcode'):
        for el in root.iter():
            if el.tag.endswith(tag) and el.text and el.text.strip():
                return el.text.strip()
    return None


def _parsear_resposta(xml_resposta: str) -> dict:
    root = ET.fromstring(xml_resposta)

    ret = root.find(f'.//{_NS}retDistDFeInt')
    if ret is None:
        raise ErroSefaz('000', 'Resposta da SEFAZ em formato inesperado (sem retDistDFeInt).')

    def texto(tag, default=None):
        el = ret.find(f'{_NS}{tag}')
        return el.text if el is not None else default

    cstat = texto('cStat')
    xmotivo = texto('xMotivo')
    ult_nsu = texto('ultNSU', '000000000000000')
    max_nsu = texto('maxNSU', '000000000000000')

    if cstat not in ('137', '138'):
        # 137 = nenhum documento novo (não é erro, é "está tudo em dia")
        # 138 = documento(s) encontrado(s)
        raise ErroSefaz(cstat, xmotivo)

    documentos = []
    lote = ret.find(f'{_NS}loteDistDFeInt')
    if lote is not None:
        for doc_zip in lote.findall(f'{_NS}docZip'):
            nsu_doc = doc_zip.get('NSU')
            schema = doc_zip.get('schema', '')
            conteudo_b64 = doc_zip.text
            xml_descompactado = gzip.decompress(base64.b64decode(conteudo_b64)).decode('utf-8')
            tipo = _identificar_tipo_doc(schema, xml_descompactado)
            documentos.append({'nsu': nsu_doc, 'schema': schema, 'tipo': tipo, 'xml': xml_descompactado})

    return {'cstat': cstat, 'xmotivo': xmotivo, 'ult_nsu': ult_nsu, 'max_nsu': max_nsu, 'documentos': documentos}


def _identificar_tipo_doc(schema: str, xml_str: str) -> str:
    if 'resNFe' in schema or '<resNFe' in xml_str:
        return 'resNFe'
    if 'resEvento' in schema or '<resEvento' in xml_str:
        return 'resEvento'
    if 'procNFe' in schema or 'nfeProc' in xml_str:
        return 'nfeProc'
    return 'outro'


def extrair_dados_nfeproc(xml_str: str) -> dict:
    """Extrai campos úteis de uma NFe completa (<nfeProc>): chave, emitente,
    destinatário, data de emissão e valor."""
    root = ET.fromstring(xml_str)

    def achar(caminho, default=None):
        el = root.find(caminho)
        return el.text if el is not None and el.text else default

    chave = achar(f'.//{_NS}protNFe/{_NS}infProt/{_NS}chNFe')
    if not chave:
        inf = root.find(f'.//{_NS}infNFe')
        if inf is not None:
            chave = (inf.get('Id') or '').replace('NFe', '') or None

    return {
        'chave_nfe': chave,
        'emitente_cnpj': achar(f'.//{_NS}emit/{_NS}CNPJ') or achar(f'.//{_NS}emit/{_NS}CPF'),
        'emitente_nome': achar(f'.//{_NS}emit/{_NS}xNome'),
        'dest_cnpj': achar(f'.//{_NS}dest/{_NS}CNPJ') or achar(f'.//{_NS}dest/{_NS}CPF'),
        'dest_nome': achar(f'.//{_NS}dest/{_NS}xNome'),
        'data_emissao': achar(f'.//{_NS}ide/{_NS}dhEmi') or achar(f'.//{_NS}ide/{_NS}dEmi'),
        'valor_total': float(achar(f'.//{_NS}ICMSTot/{_NS}vNF', '0') or 0),
        'situacao': 'completa',
    }


def extrair_dados_resumo(xml_str: str) -> dict:
    """Extrai campos úteis de um <resNFe> (resumo) para exibir na lista sem
    precisar baixar a NFe completa."""
    root = ET.fromstring(xml_str)

    def get(tag, default=None):
        el = root.find(f'{_NS}{tag}')
        return el.text if el is not None else default

    return {
        'chave_nfe': get('chNFe'),
        'emitente_cnpj': get('CNPJ') or get('CPF'),
        'emitente_nome': get('xNome'),
        'dest_cnpj': None,
        'dest_nome': None,
        'data_emissao': get('dhEmi'),
        'valor_total': float(get('vNF', '0') or 0),
        'situacao': get('cSitNFe'),
    }
