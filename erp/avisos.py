"""E-mails que o sistema manda.

Saem todos de uma conta só, a do **sistema**, configurada pelo administrador:
boas-vindas, pagamento confirmado, assinatura vencendo e vencida, o acesso
do contador e os documentos que o cliente manda ao contador. Antes cada
cliente configurava o próprio SMTP para falar com o contador; agora ninguém
além do admin mexe nisso.

Nada aqui pode derrubar o que está acontecendo em volta: se o SMTP do sistema
não estiver configurado, ou o envio falhar, a função devolve False e a vida
segue. Cobrar e liberar acesso não pode depender de o e-mail ter saído.
"""
from __future__ import annotations

import datetime as dt

import envio_email

# Quantos dias antes do vencimento o cliente é avisado
DIAS_DE_AVISO = 3

NOME_DO_SERVICO = 'Gestão Financeira'


def configuracao():
    """SMTP do sistema (sem expor a senha)."""
    from fiscal_sync import sistema_get
    return {
        'provedor': sistema_get('sistema_smtp_provedor', 'gmail'),
        'servidor': sistema_get('sistema_smtp_servidor'),
        'porta': sistema_get('sistema_smtp_porta', '587'),
        'seguranca': sistema_get('sistema_smtp_seguranca', 'tls'),
        'usuario': sistema_get('sistema_smtp_usuario'),
        'remetente': sistema_get('sistema_smtp_remetente'),
        'remetente_nome': sistema_get('sistema_smtp_nome', NOME_DO_SERVICO),
        'senha_salva': bool(sistema_get('sistema_smtp_senha_cripto')),
    }


def configurado():
    from fiscal_sync import sistema_get
    return bool(sistema_get('sistema_smtp_servidor') and sistema_get('sistema_smtp_remetente'))


def enviar(destinatario, assunto, corpo, caminho_chave, anexos=(), responder_para=None,
           remetente_nome=None):
    """Manda um e-mail pela conta do sistema. Devolve (ok, erro) — nunca
    levanta exceção. `remetente_nome` troca o nome que aparece no "De"
    (o endereço continua sendo o do sistema)."""
    from fiscal_certificado import descriptografar_senha
    from fiscal_sync import sistema_get

    if not configurado():
        return False, 'SMTP do sistema não configurado.'

    senha = ''
    cripto = sistema_get('sistema_smtp_senha_cripto')
    if cripto:
        try:
            senha = descriptografar_senha(cripto, caminho_chave)
        except Exception as exc:
            return False, f'Não foi possível ler a senha do SMTP: {exc}'

    try:
        envio_email.enviar(
            servidor=sistema_get('sistema_smtp_servidor'),
            porta=sistema_get('sistema_smtp_porta', '587'),
            seguranca=sistema_get('sistema_smtp_seguranca', 'tls'),
            usuario=sistema_get('sistema_smtp_usuario'),
            senha=senha,
            remetente=sistema_get('sistema_smtp_remetente'),
            remetente_nome=remetente_nome or sistema_get('sistema_smtp_nome', NOME_DO_SERVICO),
            destinatario=destinatario,
            assunto=assunto,
            corpo=corpo,
            anexos=anexos,
            responder_para=responder_para,
        )
        return True, None
    except Exception as exc:
        return False, str(exc)


# ------------------------------------------------------------------ #
# Os textos
# ------------------------------------------------------------------ #

def _assinatura(endereco_do_sistema):
    if endereco_do_sistema:
        return f'\n\nAcesse em: {endereco_do_sistema}\n\n— {NOME_DO_SERVICO}'
    return f'\n\n— {NOME_DO_SERVICO}'


def boas_vindas(usuario, endereco=''):
    corpo = (
        f'Olá, {usuario.nome}!\n\n'
        f'Sua conta no {NOME_DO_SERVICO} foi criada com o e-mail {usuario.email}.\n'
    )
    if usuario.empresa is not None and not usuario.empresa.assinatura_em_dia:
        corpo += (
            '\nO acesso às telas abre assim que o pagamento for confirmado. '
            'Entrando no sistema você encontra o botão para pagar e o contato '
            'do suporte.\n'
        )
    return f'Bem-vindo ao {NOME_DO_SERVICO}', corpo + _assinatura(endereco)


def pagamento_confirmado(empresa, usuario, endereco=''):
    corpo = (
        f'Olá, {usuario.nome}!\n\n'
        f'Recebemos o pagamento da {empresa.nome}. O acesso está liberado até '
        f'{empresa.assinatura_ate.strftime("%d/%m/%Y")}.\n'
    )
    return 'Pagamento confirmado — acesso liberado', corpo + _assinatura(endereco)


def vencendo(empresa, usuario, dias, endereco=''):
    quando = 'hoje' if dias == 0 else ('amanhã' if dias == 1 else f'em {dias} dias')
    corpo = (
        f'Olá, {usuario.nome}!\n\n'
        f'A assinatura da {empresa.nome} vence {quando} '
        f'({empresa.assinatura_ate.strftime("%d/%m/%Y")}).\n\n'
        'Para não perder o acesso, entre no sistema e use o botão '
        '"Realizar pagamento".\n'
    )
    return f'Sua assinatura vence {quando}', corpo + _assinatura(endereco)


def venceu(empresa, usuario, endereco=''):
    corpo = (
        f'Olá, {usuario.nome}!\n\n'
        f'A assinatura da {empresa.nome} venceu em '
        f'{empresa.assinatura_ate.strftime("%d/%m/%Y")} e o acesso às telas '
        'está suspenso.\n\n'
        'Seus dados continuam guardados. Assim que o pagamento for confirmado, '
        'tudo volta como estava.\n'
    )
    return 'Assinatura vencida — acesso suspenso', corpo + _assinatura(endereco)


def resumo_do_admin(resumo, vencendo_em_breve, vencidas, endereco=''):
    linhas = [
        'Resumo diário do sistema.',
        '',
        f'Rotina: {resumo}',
        '',
        f'Assinaturas vencendo nos próximos {DIAS_DE_AVISO} dias: {len(vencendo_em_breve)}',
    ]
    for empresa in vencendo_em_breve:
        linhas.append(f'  - {empresa.nome} (até {empresa.assinatura_ate:%d/%m/%Y})')
    linhas.append('')
    linhas.append(f'Assinaturas vencidas: {len(vencidas)}')
    for empresa in vencidas:
        linhas.append(f'  - {empresa.nome}')
    return 'Resumo diário — Gestão Financeira', '\n'.join(linhas) + _assinatura(endereco)


def dias_para_vencer(empresa, hoje=None):
    if empresa.assinatura_ate is None:
        return None
    return (empresa.assinatura_ate - (hoje or dt.date.today())).days


def acesso_contador(empresa, cliente, email_contador, senha_provisoria=None, endereco=''):
    """Avisa o contador de que um cliente liberou os dados para ele.

    Com `senha_provisoria`, a conta dele acabou de ser criada e a mensagem
    traz o login; sem ela, ele já tinha conta e só ganhou mais um cliente.
    """
    dados = [f'Empresa: {empresa.nome}']
    if empresa.cnpj:
        dados.append(f'CNPJ: {_cnpj(empresa.cnpj)}')
    dados.append(f'Responsável: {cliente.nome} ({cliente.email})')
    if getattr(cliente, 'telefone', None):
        dados.append(f'Telefone: {cliente.telefone}')

    corpo = (
        'Olá!\n\n'
        f'{cliente.nome}, da {empresa.nome}, informou este e-mail como o do contador '
        f'da empresa no {NOME_DO_SERVICO}. Com isso você passa a ver os lançamentos, '
        'as notas fiscais e os relatórios dela direto no sistema (somente leitura), '
        'e pode baixar o pacote do mês na aba Contabilidade.\n\n'
        + '\n'.join(dados) + '\n'
    )
    if senha_provisoria:
        corpo += (
            '\nCriamos uma conta de contador para você:\n\n'
            f'  E-mail: {email_contador}\n'
            f'  Senha provisória: {senha_provisoria}\n\n'
            'No primeiro acesso, troque a senha em "Minha conta". Se outros clientes '
            'seus usarem o sistema, eles aparecem todos nesta mesma conta.\n'
        )
        assunto = f'{empresa.nome} liberou os dados para você — seu acesso ao {NOME_DO_SERVICO}'
    else:
        corpo += '\nEntre com a sua conta de sempre: a empresa já aparece em "Meus clientes".\n'
        assunto = f'{empresa.nome} liberou os dados para você no {NOME_DO_SERVICO}'
    return assunto, corpo + _assinatura(endereco)


def _cnpj(digitos):
    d = ''.join(ch for ch in str(digitos) if ch.isdigit())
    if len(d) == 14:
        return f'{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}'
    return digitos
