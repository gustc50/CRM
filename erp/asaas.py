"""Integração com o Asaas: confere quem pagou e libera mais 30 dias.

Funciona por **consulta periódica**: de tempos em tempos o servidor pergunta
ao Asaas quais cobranças foram recebidas e credita o prazo de quem pagou.
Não depende de webhook, então roda antes de existir domínio e HTTPS
configurados — em troca, o pagamento pode demorar alguns minutos para
refletir.

O token fica guardado criptografado (mesma chave do certificado digital) e
nunca aparece em tela depois de salvo.

Cada cobrança só credita uma vez: o `asaas_id` é único na tabela Pagamento,
então reencontrá-la numa consulta seguinte não soma 30 dias de novo.
"""
from __future__ import annotations

import datetime as dt

import requests

from models import DIAS_POR_PAGAMENTO, Empresa, Pagamento, db

URLS = {
    'producao': 'https://api.asaas.com/v3',
    'sandbox': 'https://api-sandbox.asaas.com/v3',
}

# Situações em que o dinheiro efetivamente entrou
SITUACOES_PAGAS = ('RECEIVED', 'CONFIRMED', 'RECEIVED_IN_CASH')

# De quanto em quanto tempo a verificação automática roda
INTERVALO_HORAS = 6


class ErroAsaas(Exception):
    pass


def configuracao():
    """Configuração atual do Asaas (sem expor o token)."""
    from fiscal_sync import sistema_get
    return {
        'ambiente': sistema_get('asaas_ambiente', 'sandbox'),
        'token_salvo': bool(sistema_get('asaas_token_cripto')),
        'url': URLS.get(sistema_get('asaas_ambiente', 'sandbox'), URLS['sandbox']),
    }


def _token(caminho_chave):
    from fiscal_certificado import descriptografar_senha
    from fiscal_sync import sistema_get

    cripto = sistema_get('asaas_token_cripto')
    if not cripto:
        raise ErroAsaas('Token do Asaas não configurado. Cadastre-o no painel do administrador.')
    return descriptografar_senha(cripto, caminho_chave)


def _base_url():
    from fiscal_sync import sistema_get
    ambiente = sistema_get('asaas_ambiente', 'sandbox')
    return URLS.get(ambiente, URLS['sandbox'])


def listar_cobrancas_pagas(caminho_chave, desde=None, url_base=None, limite=100):
    """Cobranças recebidas/confirmadas no Asaas, da mais recente para trás."""
    token = _token(caminho_chave)
    base = url_base or _base_url()

    parametros = {'limit': min(limite, 100), 'offset': 0}
    if desde:
        parametros['paymentDate[ge]'] = desde.isoformat()

    cobrancas = []
    while True:
        try:
            resposta = requests.get(
                f'{base}/payments',
                params={**parametros, 'status': 'RECEIVED'},
                headers={'access_token': token, 'User-Agent': 'GestaoFinanceira'},
                timeout=30,
            )
        except requests.RequestException as exc:
            raise ErroAsaas(f'Não foi possível falar com o Asaas: {exc}') from exc

        if resposta.status_code == 401:
            raise ErroAsaas('O Asaas recusou o token. Confira se ele é do ambiente certo (produção x sandbox).')
        if resposta.status_code >= 400:
            raise ErroAsaas(f'Asaas respondeu {resposta.status_code}: {resposta.text[:200]}')

        dados = resposta.json()
        cobrancas.extend(dados.get('data', []))

        if not dados.get('hasMore') or len(cobrancas) >= limite:
            break
        parametros['offset'] += parametros['limit']

    return cobrancas


def _data(valor):
    try:
        return dt.datetime.strptime(valor[:10], '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None


def aplicar_pagamentos(cobrancas):
    """Credita os dias de quem pagou. Retorna (creditados, ignorados, sem_dono).

    Uma cobrança só entra se der para saber de qual empresa ela é — o vínculo
    é o `asaas_cliente_id` gravado na empresa. Cobrança de cliente que não está
    cadastrado aqui é contada como "sem dono" e aparece para o administrador
    resolver, em vez de ser descartada em silêncio.
    """
    creditados = ignorados = sem_dono = 0

    for cobranca in cobrancas:
        asaas_id = cobranca.get('id')
        if not asaas_id:
            continue
        if cobranca.get('status') not in SITUACOES_PAGAS:
            continue

        if Pagamento.query.filter_by(asaas_id=asaas_id).first():
            ignorados += 1  # já creditado numa verificação anterior
            continue

        cliente = cobranca.get('customer')
        empresa = Empresa.query.filter_by(asaas_cliente_id=cliente).first() if cliente else None
        if empresa is None:
            sem_dono += 1
            continue

        pago_em = _data(cobranca.get('paymentDate') or cobranca.get('confirmedDate'))
        empresa.creditar_dias(DIAS_POR_PAGAMENTO, a_partir_de=pago_em)

        db.session.add(Pagamento(
            empresa_id=empresa.id,
            asaas_id=asaas_id,
            valor=cobranca.get('value'),
            pago_em=pago_em,
            situacao=cobranca.get('status'),
            liberado_ate=empresa.assinatura_ate,
        ))
        creditados += 1

    if creditados:
        db.session.commit()
    return creditados, ignorados, sem_dono


def verificar(caminho_chave, desde=None, url_base=None):
    """Consulta o Asaas e credita os pagamentos novos. Retorna o resumo em texto."""
    cobrancas = listar_cobrancas_pagas(caminho_chave, desde=desde, url_base=url_base)
    creditados, ignorados, sem_dono = aplicar_pagamentos(cobrancas)

    partes = [f'{len(cobrancas)} cobrança(s) paga(s) encontrada(s)']
    partes.append(f'{creditados} creditada(s)')
    if ignorados:
        partes.append(f'{ignorados} já creditada(s) antes')
    if sem_dono:
        partes.append(f'{sem_dono} sem empresa vinculada (confira o ID do cliente Asaas no cadastro)')
    return ', '.join(partes) + '.'
