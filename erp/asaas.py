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

# Mensalidade cobrada de cada cliente, quando o admin não configurou outra
VALOR_PADRAO = 99.90

# Prazo do boleto/pix gerado no autocadastro
DIAS_PARA_VENCER = 3


class ErroAsaas(Exception):
    pass


def configuracao():
    """Configuração atual do Asaas (sem expor o token)."""
    from fiscal_sync import sistema_get
    return {
        'ambiente': sistema_get('asaas_ambiente', 'sandbox'),
        'token_salvo': bool(sistema_get('asaas_token_cripto')),
        'url': URLS.get(sistema_get('asaas_ambiente', 'sandbox'), URLS['sandbox']),
        'valor': valor_mensalidade(),
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


def configurado():
    """True quando o admin já cadastrou o token. Sem ele não dá para cobrar."""
    from fiscal_sync import sistema_get
    return bool(sistema_get('asaas_token_cripto'))


def valor_mensalidade():
    """Quanto é cobrado por mês. O admin pode mudar em Assinaturas."""
    from fiscal_sync import sistema_get
    try:
        return round(float(sistema_get('asaas_valor', '') or VALOR_PADRAO), 2)
    except (TypeError, ValueError):
        return VALOR_PADRAO


def _chamar(metodo, caminho, caminho_chave, url_base=None, **kwargs):
    """Uma chamada à API do Asaas, com os erros já traduzidos."""
    token = _token(caminho_chave)
    base = url_base or _base_url()

    try:
        resposta = requests.request(
            metodo,
            f'{base}{caminho}',
            headers={'access_token': token, 'User-Agent': 'GestaoFinanceira'},
            timeout=30,
            **kwargs,
        )
    except requests.RequestException as exc:
        raise ErroAsaas(f'Não foi possível falar com o Asaas: {exc}') from exc

    if resposta.status_code == 401:
        raise ErroAsaas('O Asaas recusou o token. Confira se ele é do ambiente certo (produção x sandbox).')
    if resposta.status_code >= 400:
        # O Asaas explica o que faltou no corpo; repassar ajuda mais do que
        # um "deu erro" genérico.
        detalhe = resposta.text[:300]
        try:
            erros = resposta.json().get('errors') or []
            if erros:
                detalhe = '; '.join(e.get('description', '') for e in erros)
        except ValueError:
            pass
        raise ErroAsaas(f'Asaas recusou a operação: {detalhe}')

    try:
        return resposta.json()
    except ValueError as exc:
        raise ErroAsaas('O Asaas respondeu algo que não é JSON.') from exc


def listar_cobrancas_pagas(caminho_chave, desde=None, url_base=None, limite=100,
                           cliente=None):
    """Cobranças já pagas no Asaas.

    Percorre uma situação de cada vez porque o filtro `status` da API aceita
    um valor só: pedir apenas RECEIVED deixaria de fora as confirmadas
    (cartão aprovado e ainda não repassado) e as recebidas em dinheiro, que
    aqui também valem como pagas.
    """
    cobrancas = []

    for situacao in SITUACOES_PAGAS:
        parametros = {'limit': min(limite, 100), 'offset': 0, 'status': situacao}
        if desde:
            parametros['paymentDate[ge]'] = desde.isoformat()
        if cliente:
            parametros['customer'] = cliente

        desta_situacao = 0
        while True:
            dados = _chamar('GET', '/payments', caminho_chave, url_base=url_base,
                            params=parametros)
            pagina = dados.get('data', [])
            cobrancas.extend(pagina)
            desta_situacao += len(pagina)

            if not dados.get('hasMore') or desta_situacao >= limite:
                break
            parametros['offset'] += parametros['limit']

    return cobrancas


def criar_cliente(empresa, caminho_chave, url_base=None):
    """Cadastra a empresa como cliente no Asaas e guarda o id do vínculo.

    É esse id que, mais tarde, diz de quem é cada cobrança paga. Se a empresa
    já tem um, não cria outro.
    """
    if empresa.asaas_cliente_id:
        return empresa.asaas_cliente_id

    dados = {
        'name': empresa.nome,
        'cpfCnpj': empresa.cnpj or '',
        'externalReference': str(empresa.id),
    }
    if empresa.email:
        dados['email'] = empresa.email
    if empresa.telefone:
        dados['phone'] = empresa.telefone
    if empresa.endereco:
        dados['address'] = empresa.endereco
    if empresa.cep:
        dados['postalCode'] = empresa.cep

    resposta = _chamar('POST', '/customers', caminho_chave, url_base=url_base, json=dados)
    cliente_id = resposta.get('id')
    if not cliente_id:
        raise ErroAsaas('O Asaas não devolveu o identificador do cliente.')

    empresa.asaas_cliente_id = cliente_id
    db.session.commit()
    return cliente_id


def criar_cobranca(empresa, caminho_chave, valor=None, url_base=None):
    """Gera a mensalidade da empresa e devolve (id, link de pagamento).

    `billingType` fica em UNDEFINED de propósito: assim o Asaas monta uma
    página em que a pessoa escolhe entre pix, boleto e cartão, em vez de
    obrigá-la a um meio só.
    """
    cliente_id = criar_cliente(empresa, caminho_chave, url_base=url_base)
    vencimento = dt.date.today() + dt.timedelta(days=DIAS_PARA_VENCER)

    resposta = _chamar('POST', '/payments', caminho_chave, url_base=url_base, json={
        'customer': cliente_id,
        'billingType': 'UNDEFINED',
        'value': valor if valor is not None else valor_mensalidade(),
        'dueDate': vencimento.isoformat(),
        'description': f'Gestão Financeira — assinatura mensal ({DIAS_POR_PAGAMENTO} dias)',
        'externalReference': str(empresa.id),
    })

    link = resposta.get('invoiceUrl') or resposta.get('bankSlipUrl')
    if not link:
        raise ErroAsaas('O Asaas não devolveu o link de pagamento.')
    return resposta.get('id'), link


def verificar_empresa(empresa, caminho_chave, url_base=None):
    """Confere as cobranças de UMA empresa, para o "já paguei" não esperar.

    A verificação automática roda de 6 em 6 horas; quem acabou de pagar não
    tem por que esperar tudo isso para o acesso voltar.
    """
    if not empresa.asaas_cliente_id:
        return 0
    cobrancas = listar_cobrancas_pagas(caminho_chave, url_base=url_base,
                                       cliente=empresa.asaas_cliente_id)
    creditados, _, _ = aplicar_pagamentos(cobrancas)
    return creditados


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
