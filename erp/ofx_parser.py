"""Leitor de extratos bancários no formato OFX (Open Financial Exchange).

A maioria dos bancos brasileiros exporta OFX 1.x, que é SGML (tags sem
fechamento, ex.: ``<DTPOSTED>20260615120000`` sem ``</DTPOSTED>``) — não é
XML válido. Este módulo converte esse SGML para um XML equivalente antes de
interpretar; arquivos já em OFX 2.x (XML de verdade) passam direto.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime


class OfxInvalido(Exception):
    pass


def _sgml_para_xml(texto: str) -> str:
    inicio = texto.upper().find('<OFX>')
    if inicio == -1:
        raise OfxInvalido('Arquivo não parece ser um OFX válido (tag <OFX> não encontrada).')
    corpo = texto[inicio:]

    # Fecha tags "folha" (<TAG>valor, sem fechamento, seguidas de quebra de
    # linha): troca "<TAG>valor\n" por "<TAG>valor</TAG>\n". Tags de bloco
    # (ex.: <STMTTRN>, sem valor antes da quebra de linha) NÃO batem aqui —
    # ficam como estão, fechadas explicitamente mais adiante no próprio
    # arquivo pela sua tag de fechamento real (</STMTTRN>).
    def _fechar_folha(m):
        tag, conteudo = m.group(1), m.group(2).strip()
        if not conteudo:
            return m.group(0)  # tag de bloco (sem valor) — não fechar aqui
        return f'<{tag}>{_escapar_xml(conteudo)}</{tag}>\n'

    corpo = re.sub(r'<([A-Za-z0-9.]+)>([^<\r\n]*)[ \t]*\r?\n', _fechar_folha, corpo)
    return corpo


def _escapar_xml(texto: str) -> str:
    return (
        texto.replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
    )


def _decodificar(conteudo: bytes) -> str:
    for enc in ('utf-8', 'latin-1', 'cp1252'):
        try:
            return conteudo.decode(enc)
        except UnicodeDecodeError:
            continue
    return conteudo.decode('utf-8', errors='replace')


def _para_arvore(conteudo: bytes) -> ET.Element:
    texto = _decodificar(conteudo)
    texto_limpo = texto.lstrip()
    if texto_limpo.startswith('<?xml') or texto_limpo.startswith('<OFX xmlns'):
        xml_str = texto
    else:
        xml_str = _sgml_para_xml(texto)

    try:
        return ET.fromstring(xml_str)
    except ET.ParseError as exc:
        raise OfxInvalido(f'Não foi possível interpretar o arquivo OFX: {exc}') from exc


def _local(tag: str) -> str:
    return tag.rsplit('}', 1)[-1].upper()


def _achar(raiz, nome: str):
    if raiz is None:
        return None
    for el in raiz.iter():
        if _local(el.tag) == nome.upper():
            return el
    return None


def _texto_de(raiz, nome: str, default: str = '') -> str:
    el = _achar(raiz, nome)
    return (el.text or '').strip() if el is not None and el.text else default


def _parse_data_ofx(valor: str):
    """AAAAMMDD[HHMMSS[.mmm]][fuso] -> date; None se não reconhecido."""
    if not valor:
        return None
    m = re.match(r'(\d{8})', valor.strip())
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), '%Y%m%d').date()
    except ValueError:
        return None


def _parse_valor_ofx(valor: str):
    if not valor:
        return None
    try:
        return float(valor.strip().replace(',', '.'))
    except ValueError:
        return None


@dataclass
class ContaOfx:
    banco: str = ''
    agencia: str = ''
    conta: str = ''
    tipo_conta: str = ''


@dataclass
class MovimentoOfx:
    fitid: str
    data: date
    valor: float
    descricao: str
    tipo: str  # 'CREDITO' | 'DEBITO'


@dataclass
class ExtratoOfx:
    conta: ContaOfx
    movimentos: list = field(default_factory=list)
    saldo: float | None = None
    saldo_data: date | None = None


def parse_ofx(conteudo: bytes) -> ExtratoOfx:
    raiz = _para_arvore(conteudo)

    bloco_conta = _achar(raiz, 'BANKACCTFROM') or _achar(raiz, 'CCACCTFROM')
    conta = ContaOfx(
        banco=_texto_de(bloco_conta, 'BANKID'),
        agencia=_texto_de(bloco_conta, 'BRANCHID'),
        conta=_texto_de(bloco_conta, 'ACCTID'),
        tipo_conta=_texto_de(bloco_conta, 'ACCTTYPE'),
    )

    movimentos = []
    fitids_vistos = set()
    for trn in raiz.iter():
        if _local(trn.tag) != 'STMTTRN':
            continue
        fitid = _texto_de(trn, 'FITID')
        data_mov = _parse_data_ofx(_texto_de(trn, 'DTPOSTED'))
        valor = _parse_valor_ofx(_texto_de(trn, 'TRNAMT'))
        if not fitid or data_mov is None or valor is None:
            continue
        if fitid in fitids_vistos:
            continue  # o próprio arquivo às vezes repete o mesmo FITID
        fitids_vistos.add(fitid)
        descricao = (
            _texto_de(trn, 'MEMO') or _texto_de(trn, 'NAME')
            or _texto_de(trn, 'TRNTYPE') or 'Movimentação'
        )
        movimentos.append(MovimentoOfx(
            fitid=fitid,
            data=data_mov,
            valor=valor,
            descricao=descricao[:200],
            tipo='CREDITO' if valor >= 0 else 'DEBITO',
        ))

    bloco_saldo = _achar(raiz, 'LEDGERBAL')
    saldo = _parse_valor_ofx(_texto_de(bloco_saldo, 'BALAMT')) if bloco_saldo is not None else None
    saldo_data = _parse_data_ofx(_texto_de(bloco_saldo, 'DTASOF')) if bloco_saldo is not None else None

    if not movimentos and bloco_conta is None:
        raise OfxInvalido('Nenhuma movimentação nem dado de conta encontrado neste arquivo OFX.')

    return ExtratoOfx(conta=conta, movimentos=movimentos, saldo=saldo, saldo_data=saldo_data)
