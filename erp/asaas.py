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
import re

import requests

from models import (DIAS_POR_PAGAMENTO, ORIGEM_ASAAS, ORIGEM_MANUAL, PAPEL_USUARIO,
                    Empresa, Pagamento, Usuario, db)

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

# Quanto custa cada acesso além dos inclusos (models.USUARIOS_INCLUSOS)
VALOR_USUARIO_PADRAO = 19.90

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
        'valor_usuario': valor_usuario_extra(),
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


def valor_usuario_extra():
    """Quanto custa cada acesso além dos inclusos na mensalidade."""
    from fiscal_sync import sistema_get
    try:
        return round(float(sistema_get('asaas_valor_usuario', '') or VALOR_USUARIO_PADRAO), 2)
    except (TypeError, ValueError):
        return VALOR_USUARIO_PADRAO


def mensalidade_da_empresa(empresa):
    """Mensalidade + os acessos que passam do que está incluso.

    O cálculo é feito na hora de cobrar, com a equipe que a empresa tem
    naquele momento: quem tirou gente da equipe paga menos no mês seguinte,
    sem precisar avisar ninguém.
    """
    return round(valor_mensalidade() + empresa.usuarios_extras * valor_usuario_extra(), 2)


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
        'value': valor if valor is not None else mensalidade_da_empresa(empresa),
        'dueDate': vencimento.isoformat(),
        'description': _descricao_cobranca(empresa),
        'externalReference': str(empresa.id),
    })

    link = resposta.get('invoiceUrl') or resposta.get('bankSlipUrl')
    if not link:
        raise ErroAsaas('O Asaas não devolveu o link de pagamento.')
    return resposta.get('id'), link


def _descricao_cobranca(empresa):
    """Texto que a pessoa lê no boleto/pix, já explicando os acessos extras."""
    base = f'Gestão Financeira — assinatura mensal ({DIAS_POR_PAGAMENTO} dias)'
    extras = empresa.usuarios_extras
    if extras:
        return f'{base} + {extras} acesso(s) adicional(is)'
    return base


def verificar_empresa(empresa, caminho_chave, url_base=None):
    """Confere as cobranças de UMA empresa, para o "já paguei" não esperar.

    A verificação automática roda de 6 em 6 horas; quem acabou de pagar não
    tem por que esperar tudo isso para o acesso voltar. Se a empresa ainda
    não tem o id do Asaas, procura lá o cliente pelo CPF/CNPJ cadastrado.
    """
    clientes = [empresa.asaas_cliente_id] if empresa.asaas_cliente_id else []
    if not clientes:
        documento = _so_digitos(empresa.cnpj)
        if len(documento) in (11, 14):
            resposta = _chamar('GET', '/customers', caminho_chave, url_base=url_base,
                               params={'cpfCnpj': documento})
            clientes = [c['id'] for c in resposta.get('data', []) if c.get('id')]

    cobrancas = []
    for cliente_id in clientes:
        cobrancas.extend(listar_cobrancas_pagas(caminho_chave, url_base=url_base,
                                                cliente=cliente_id))
    if not cobrancas:
        return 0
    r = aplicar_pagamentos(cobrancas, caminho_chave=caminho_chave, url_base=url_base)
    return r['creditados'] + r['absorvidos']


def _data(valor):
    try:
        return dt.datetime.strptime(valor[:10], '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None


def _so_digitos(valor):
    return re.sub(r'\D', '', valor or '')


def documento_do_cliente(cliente_id, caminho_chave, url_base=None, cache=None):
    """CPF/CNPJ do cliente no Asaas (só dígitos), com cache por rodada.

    A cobrança paga traz só o id do cliente; o documento vem do cadastro dele.
    O cache evita perguntar de novo pelo mesmo cliente quando ele pagou várias
    cobranças na mesma consulta.
    """
    if cache is not None and cliente_id in cache:
        return cache[cliente_id]
    try:
        dados = _chamar('GET', f'/customers/{cliente_id}', caminho_chave, url_base=url_base)
        documento = _so_digitos(dados.get('cpfCnpj'))
    except ErroAsaas:
        documento = ''
    if cache is not None:
        cache[cliente_id] = documento
    return documento


def empresas_pelo_documento(documento):
    """Empresas cujo CPF/CNPJ cadastrado é este — pela empresa ou pelo usuário.

    Olha os dois lugares porque o cadastro pode ter o CNPJ na empresa e o
    pagamento ter saído no CPF do sócio, ou o contrário.
    """
    if len(documento) not in (11, 14):
        return []
    achadas = {}
    for empresa in Empresa.query.filter(Empresa.cnpj.isnot(None)).all():
        if _so_digitos(empresa.cnpj) == documento:
            achadas[empresa.id] = empresa
    for usuario in Usuario.query.filter(Usuario.cpf_cnpj.isnot(None),
                                        Usuario.papel == PAPEL_USUARIO).all():
        if _so_digitos(usuario.cpf_cnpj) == documento and usuario.empresa is not None:
            achadas[usuario.empresa.id] = usuario.empresa
    return list(achadas.values())


def _identificar_empresa(cliente_id, caminho_chave, url_base, cache):
    """De quem é a cobrança. Retorna (empresa, como, detalhe).

    `como` é 'vinculo' (id do Asaas gravado na empresa), 'documento' (casou
    pelo CPF/CNPJ), 'ambiguo' (o documento bate com mais de uma empresa — não
    libera ninguém, para não liberar a errada) ou 'sem_dono'.
    """
    empresa = Empresa.query.filter_by(asaas_cliente_id=cliente_id).first() if cliente_id else None
    if empresa is not None:
        return empresa, 'vinculo', None

    if not cliente_id or caminho_chave is None:
        return None, 'sem_dono', None

    documento = documento_do_cliente(cliente_id, caminho_chave, url_base, cache)
    candidatas = empresas_pelo_documento(documento)
    if len(candidatas) == 1:
        empresa = candidatas[0]
        if not empresa.asaas_cliente_id:
            # Grava o vínculo: das próximas vezes nem precisa consultar o documento
            empresa.asaas_cliente_id = cliente_id
        return empresa, 'documento', documento
    if len(candidatas) > 1:
        return None, 'ambiguo', documento
    return None, 'sem_dono', documento


def _liberacao_manual_que_cobre(empresa, pago_em):
    """Liberação manual feita logo depois deste pagamento, ainda sem cobrança.

    É o caso de o admin ver o pagamento no Asaas e clicar em Liberar antes de
    a consulta automática passar. Sem esta checagem, o mesmo pagamento
    renderia duas vezes: o clique e a consulta.
    """
    if pago_em is None:
        return None
    limite = pago_em + dt.timedelta(days=DIAS_POR_PAGAMENTO)
    candidatas = [
        l for l in empresa.liberacoes
        if l.origem == ORIGEM_MANUAL and not l.asaas_id
        and pago_em <= l.liberado_em.date() <= limite
    ]
    return min(candidatas, key=lambda l: l.liberado_em) if candidatas else None


def aplicar_pagamentos(cobrancas, caminho_chave=None, url_base=None):
    """Libera quem pagou, pela mesma regra do clique do admin.

    Retorna um dicionário com o que aconteceu, para o resumo:
    creditados, por_documento, absorvidos, ignorados, ambiguos e sem_dono.

    - A cobrança é casada primeiro pelo id do cliente no Asaas e, se não
      houver vínculo, pelo **CPF/CNPJ** dele — e o vínculo fica gravado.
    - Documento que bate com mais de uma empresa não libera ninguém.
    - Cada cobrança rende uma vez só (`Pagamento.asaas_id` é único).
    - As cobranças são aplicadas em ordem de data de pagamento, para o
      resultado não depender da ordem em que o Asaas as devolve.
    """
    resultado = {'creditados': 0, 'por_documento': 0, 'absorvidos': 0,
                 'ignorados': 0, 'ambiguos': 0, 'sem_dono': 0, 'liberadas': []}
    cache_documentos = {}

    pagas = [c for c in cobrancas if c.get('id') and c.get('status') in SITUACOES_PAGAS]
    pagas.sort(key=lambda c: (_data(c.get('paymentDate') or c.get('confirmedDate'))
                              or dt.date.max, c.get('id')))

    for cobranca in pagas:
        asaas_id = cobranca['id']
        if Pagamento.query.filter_by(asaas_id=asaas_id).first():
            resultado['ignorados'] += 1  # já creditado numa verificação anterior
            continue

        empresa, como, documento = _identificar_empresa(
            cobranca.get('customer'), caminho_chave, url_base, cache_documentos)
        if empresa is None:
            resultado['ambiguos' if como == 'ambiguo' else 'sem_dono'] += 1
            continue

        pago_em = _data(cobranca.get('paymentDate') or cobranca.get('confirmedDate'))

        manual = _liberacao_manual_que_cobre(empresa, pago_em)
        if manual is not None:
            # O admin já liberou por este pagamento: só amarra um ao outro.
            manual.asaas_id = asaas_id
            manual.observacao = (f'pagamento de {pago_em:%d/%m/%Y} já liberado na mão; '
                                 f'não somou dias de novo')
            resultado['absorvidos'] += 1
        else:
            if como == 'documento':
                observacao = f'casado pelo CPF/CNPJ {documento}'
                resultado['por_documento'] += 1
            else:
                observacao = None
            empresa.liberar(ORIGEM_ASAAS, data_referencia=pago_em,
                            asaas_id=asaas_id, observacao=observacao)
            resultado['creditados'] += 1
            if empresa not in resultado['liberadas']:
                resultado['liberadas'].append(empresa)

        db.session.add(Pagamento(
            empresa_id=empresa.id,
            asaas_id=asaas_id,
            valor=cobranca.get('value'),
            pago_em=pago_em,
            situacao=cobranca.get('status'),
            liberado_ate=empresa.assinatura_ate,
        ))
        db.session.flush()  # o próximo pagamento da mesma empresa já enxerga este

    db.session.commit()
    return resultado


def verificar(caminho_chave, desde=None, url_base=None, ao_liberar=None):
    """Consulta o Asaas e credita os pagamentos novos. Retorna o resumo em texto.

    `ao_liberar(empresa)` é chamado para cada empresa que ganhou acesso — é
    por ali que o app manda o e-mail de pagamento confirmado. Falha no aviso
    não desfaz a liberação.
    """
    cobrancas = listar_cobrancas_pagas(caminho_chave, desde=desde, url_base=url_base)
    r = aplicar_pagamentos(cobrancas, caminho_chave=caminho_chave, url_base=url_base)

    if ao_liberar is not None:
        for empresa in r['liberadas']:
            try:
                ao_liberar(empresa)
            except Exception:
                pass

    partes = [f'{len(cobrancas)} cobrança(s) paga(s) encontrada(s)']
    partes.append(f"{r['creditados']} liberada(s)")
    if r['por_documento']:
        partes.append(f"{r['por_documento']} delas reconhecida(s) pelo CPF/CNPJ")
    if r['absorvidos']:
        partes.append(f"{r['absorvidos']} já tinha(m) sido liberada(s) na mão")
    if r['ignorados']:
        partes.append(f"{r['ignorados']} já creditada(s) antes")
    if r['ambiguos']:
        partes.append(f"{r['ambiguos']} com CPF/CNPJ de mais de uma empresa (não liberadas)")
    if r['sem_dono']:
        partes.append(f"{r['sem_dono']} sem empresa com esse CPF/CNPJ")
    return ', '.join(partes) + '.'
