"""NF-e — manifestação do destinatário (evento de Ciência da Operação).

Antes da manifestação, a SEFAZ só libera o *resumo* (resNFe) das notas de
compra: dá para ver o emitente e o valor, mas não o XML completo com os itens.
Registrando o evento "Ciência da Operação" (tpEvento 210210) a nota completa
passa a ser distribuída nas próximas consultas ao NFeDistribuicaoDFe.

O evento precisa ir assinado em XML-DSig (assinatura *enveloped*). Como o XML
assinado é gerado aqui — nada vem de fora — ele já é montado diretamente na
forma canônica C14N 1.0 exigida pela SEFAZ (namespace herdado explicitado no
elemento assinado, atributos em ordem, sem espaços entre as tags). Isso evita
depender de uma biblioteca de canonicalização só para este caso.

Referências: NT 2014.002 (manifestação do destinatário) e o schema
envEvento_v1.00 / procEventoNFe_v1.00.
"""
from __future__ import annotations

import base64
import re
from xml.etree import ElementTree as ET

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

URLS_RECEPCAO_EVENTO = {
    'producao': 'https://www1.nfe.fazenda.gov.br/NFeRecepcaoEvento4/NFeRecepcaoEvento4.asmx',
    'homologacao': 'https://hom1.nfe.fazenda.gov.br/NFeRecepcaoEvento4/NFeRecepcaoEvento4.asmx',
}

SOAP_ACTION = 'http://www.portalfiscal.inf.br/nfe/wsdl/NFeRecepcaoEvento4/nfeRecepcaoEvento'

_NS = '{http://www.portalfiscal.inf.br/nfe}'

# Ciência da Operação: a manifestação mais branda — apenas registra que a
# empresa tomou conhecimento da nota, sem confirmar nem recusar a operação.
TP_EVENTO_CIENCIA = '210210'
DESC_EVENTO_CIENCIA = 'Ciencia da Operacao'

# Órgão 91 = Ambiente Nacional (é ele quem recebe a manifestação)
C_ORGAO_AMBIENTE_NACIONAL = '91'

# cStat de sucesso do evento: registrado e vinculado à NF-e / registrado fora
# de prazo mas aceito / já registrado antes (duplicidade — o efeito é o mesmo).
CSTAT_EVENTO_OK = ('135', '136', '573')


class ErroManifestacao(Exception):
    def __init__(self, cstat, xmotivo):
        self.cstat = cstat
        self.xmotivo = xmotivo
        super().__init__(f'[{cstat}] {xmotivo}')


def _digitos(valor) -> str:
    return re.sub(r'\D', '', valor or '')


def _escapar(texto: str) -> str:
    return (
        str(texto)
        .replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
    )


def montar_inf_evento(chave: str, documento: str, ambiente: str, dh_evento: str,
                      sequencia: int = 1) -> tuple[str, str]:
    """Monta o <infEvento> como ele fica dentro do <evento> e devolve (xml, Id).

    Aqui o namespace não é declarado porque é herdado do <evento> pai; quem
    acrescenta essa declaração é `forma_canonica`, usada só para o digest.
    """
    doc = _digitos(documento)
    tag_doc = 'CPF' if len(doc) == 11 else 'CNPJ'
    identificador = f'ID{TP_EVENTO_CIENCIA}{chave}{sequencia:02d}'
    tp_amb = '1' if ambiente == 'producao' else '2'

    xml = (
        f'<infEvento Id="{identificador}">'
        f'<cOrgao>{C_ORGAO_AMBIENTE_NACIONAL}</cOrgao>'
        f'<tpAmb>{tp_amb}</tpAmb>'
        f'<{tag_doc}>{doc}</{tag_doc}>'
        f'<chNFe>{chave}</chNFe>'
        f'<dhEvento>{dh_evento}</dhEvento>'
        f'<tpEvento>{TP_EVENTO_CIENCIA}</tpEvento>'
        f'<nSeqEvento>{sequencia}</nSeqEvento>'
        f'<verEvento>1.00</verEvento>'
        f'<detEvento versao="1.00">'
        f'<descEvento>{DESC_EVENTO_CIENCIA}</descEvento>'
        f'</detEvento>'
        f'</infEvento>'
    )
    return xml, identificador


def forma_canonica(inf_evento_xml: str) -> bytes:
    """Forma canônica (C14N 1.0) do <infEvento>, que é o que entra no digest.

    A canonicalização de um trecho traz para o elemento assinado os namespaces
    herdados dos ancestrais — no documento final o xmlns está lá no <evento>,
    aqui ele precisa aparecer explícito no próprio <infEvento>.
    """
    return inf_evento_xml.replace(
        '<infEvento ', '<infEvento xmlns="http://www.portalfiscal.inf.br/nfe" ', 1
    ).encode('utf-8')


def assinar_evento(inf_evento_xml: str, identificador: str, chave_privada, certificado) -> str:
    """Devolve o <evento> completo, com <infEvento> e a <Signature> ao lado.

    Assinatura conforme o padrão da NF-e: digest SHA-1 do elemento referenciado,
    transformações "enveloped-signature" + C14N, e a própria SignedInfo assinada
    com RSA-SHA1.
    """
    digest = base64.b64encode(_sha1(forma_canonica(inf_evento_xml))).decode('ascii')

    signed_info = (
        '<SignedInfo xmlns="http://www.w3.org/2000/09/xmldsig#">'
        '<CanonicalizationMethod Algorithm="http://www.w3.org/TR/2001/REC-xml-c14n-20010315"></CanonicalizationMethod>'
        '<SignatureMethod Algorithm="http://www.w3.org/2000/09/xmldsig#rsa-sha1"></SignatureMethod>'
        f'<Reference URI="#{identificador}">'
        '<Transforms>'
        '<Transform Algorithm="http://www.w3.org/2000/09/xmldsig#enveloped-signature"></Transform>'
        '<Transform Algorithm="http://www.w3.org/TR/2001/REC-xml-c14n-20010315"></Transform>'
        '</Transforms>'
        '<DigestMethod Algorithm="http://www.w3.org/2000/09/xmldsig#sha1"></DigestMethod>'
        f'<DigestValue>{digest}</DigestValue>'
        '</Reference>'
        '</SignedInfo>'
    )

    assinatura = base64.b64encode(
        chave_privada.sign(signed_info.encode('utf-8'), padding.PKCS1v15(), hashes.SHA1())
    ).decode('ascii')

    cert_b64 = base64.b64encode(
        certificado.public_bytes(serialization.Encoding.DER)
    ).decode('ascii')

    return (
        '<evento xmlns="http://www.portalfiscal.inf.br/nfe" versao="1.00">'
        + inf_evento_xml
        + '<Signature xmlns="http://www.w3.org/2000/09/xmldsig#">'
        + signed_info
        + f'<SignatureValue>{assinatura}</SignatureValue>'
        + f'<KeyInfo><X509Data><X509Certificate>{cert_b64}</X509Certificate></X509Data></KeyInfo>'
        + '</Signature>'
        + '</evento>'
    )


def _sha1(dados: bytes) -> bytes:
    digest = hashes.Hash(hashes.SHA1())
    digest.update(dados)
    return digest.finalize()


def _montar_envelope_soap(env_evento: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap12:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
        'xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">'
        '<soap12:Body>'
        '<nfeRecepcaoEvento xmlns="http://www.portalfiscal.inf.br/nfe/wsdl/NFeRecepcaoEvento4">'
        f'<nfeDadosMsg>{env_evento}</nfeDadosMsg>'
        '</nfeRecepcaoEvento>'
        '</soap12:Body>'
        '</soap12:Envelope>'
    )


def dar_ciencia(chave: str, documento: str, ambiente: str, dh_evento: str,
                chave_privada, certificado, cert_pair, url: str | None = None,
                verify=True, sequencia: int = 1) -> dict:
    """Registra a Ciência da Operação de uma NF-e na SEFAZ.

    Retorna {'cstat', 'xmotivo', 'protocolo'} em caso de sucesso e levanta
    ErroManifestacao quando a SEFAZ recusa o evento.
    """
    inf_evento, identificador = montar_inf_evento(chave, documento, ambiente, dh_evento, sequencia)
    evento_assinado = assinar_evento(inf_evento, identificador, chave_privada, certificado)

    env_evento = (
        '<envEvento xmlns="http://www.portalfiscal.inf.br/nfe" versao="1.00">'
        '<idLote>1</idLote>'
        f'{evento_assinado}'
        '</envEvento>'
    )

    destino = url or URLS_RECEPCAO_EVENTO[ambiente]
    resposta = requests.post(
        destino,
        data=_montar_envelope_soap(env_evento).encode('utf-8'),
        headers={'Content-Type': 'application/soap+xml; charset=utf-8; action=' + SOAP_ACTION},
        cert=cert_pair,
        verify=verify,
        timeout=60,
    )

    if resposta.status_code >= 400:
        corpo = (resposta.text or '').strip()
        raise ErroManifestacao(str(resposta.status_code), f'HTTP {resposta.status_code} do webservice. {corpo[:300]}')

    return _parsear_retorno(resposta.text)


def _parsear_retorno(xml_resposta: str) -> dict:
    try:
        raiz = ET.fromstring(xml_resposta)
    except ET.ParseError as exc:
        raise ErroManifestacao('000', f'Resposta da SEFAZ ilegível: {exc}') from exc

    # O cStat que importa é o do evento em si (dentro de retEvento); o do lote
    # só diz se o lote foi recebido.
    inf_retorno = None
    for elemento in raiz.iter():
        if elemento.tag == f'{_NS}infEvento' and elemento.find(f'{_NS}cStat') is not None:
            inf_retorno = elemento
            break

    if inf_retorno is None:
        cstat_lote = _texto(raiz, 'cStat')
        raise ErroManifestacao(cstat_lote or '000', _texto(raiz, 'xMotivo') or 'Retorno sem evento processado.')

    cstat = _texto(inf_retorno, 'cStat')
    xmotivo = _texto(inf_retorno, 'xMotivo')
    if cstat not in CSTAT_EVENTO_OK:
        raise ErroManifestacao(cstat, xmotivo)

    return {'cstat': cstat, 'xmotivo': xmotivo, 'protocolo': _texto(inf_retorno, 'nProt')}


def _texto(raiz, tag: str) -> str:
    for elemento in raiz.iter():
        if elemento.tag == f'{_NS}{tag}':
            return (elemento.text or '').strip()
    return ''
