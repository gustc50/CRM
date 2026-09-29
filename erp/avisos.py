"""E-mails que o sistema manda por conta própria para os clientes.

São diferentes dos e-mails da aba Contabilidade: aqueles saem da conta SMTP
de *cada cliente* para o contador dele. Estes saem de uma conta do **sistema**,
configurada pelo administrador, e falam com os clientes em nome do serviço:
boas-vindas, pagamento confirmado, assinatura vencendo e assinatura vencida.

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


def enviar(destinatario, assunto, corpo, caminho_chave):
    """Manda um aviso. Devolve (ok, erro) — nunca levanta exceção."""
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
            remetente_nome=sistema_get('sistema_smtp_nome', NOME_DO_SERVICO),
            destinatario=destinatario,
            assunto=assunto,
            corpo=corpo,
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
