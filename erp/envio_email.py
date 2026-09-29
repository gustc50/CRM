"""Envio de e-mail por SMTP — usado para mandar os documentos ao contador.

Não existe SMTP que envie de graça sem nenhuma conta: o que existe são
provedores com plano gratuito. Por isso o sistema não traz nenhuma credencial
embutida (seria uma conta compartilhada por todo mundo, e cairia em spam ou
seria bloqueada rapidinho). O usuário escolhe um provedor da lista abaixo, que
já preenche servidor/porta/segurança, e informa o e-mail e a senha dele.
"""
from __future__ import annotations

import mimetypes
import smtplib
import ssl
from email.message import EmailMessage

# Provedores com plano gratuito e o que cada um exige. O "aviso" aparece na
# tela de Configurações para o usuário não tropeçar na senha de aplicativo.
PROVEDORES_SMTP = {
    'gmail': {
        'nome': 'Gmail',
        'servidor': 'smtp.gmail.com',
        'porta': 587,
        'seguranca': 'tls',
        'aviso': 'O Gmail não aceita a senha normal: ative a verificação em duas etapas '
                 'e gere uma "Senha de app" em myaccount.google.com/apppasswords.',
    },
    'outlook': {
        'nome': 'Outlook / Hotmail',
        'servidor': 'smtp-mail.outlook.com',
        'porta': 587,
        'seguranca': 'tls',
        'aviso': 'Contas Microsoft com verificação em duas etapas precisam de uma senha de aplicativo.',
    },
    'brevo': {
        'nome': 'Brevo (ex-Sendinblue)',
        'servidor': 'smtp-relay.brevo.com',
        'porta': 587,
        'seguranca': 'tls',
        'aviso': 'Plano gratuito com 300 e-mails por dia. O usuário e a senha do SMTP '
                 'ficam no painel do Brevo, em SMTP & API (não é a senha do site).',
    },
    'zoho': {
        'nome': 'Zoho Mail',
        'servidor': 'smtp.zoho.com',
        'porta': 587,
        'seguranca': 'tls',
        'aviso': 'Use uma senha de aplicativo gerada no painel do Zoho.',
    },
    'outro': {
        'nome': 'Outro servidor',
        'servidor': '',
        'porta': 587,
        'seguranca': 'tls',
        'aviso': 'Peça ao provedor de e-mail o servidor SMTP, a porta e o tipo de segurança.',
    },
}

SEGURANCAS = (('tls', 'STARTTLS (porta 587)'), ('ssl', 'SSL/TLS (porta 465)'), ('nenhuma', 'Sem criptografia'))

# Limite prático de anexo aceito pela maioria dos provedores
LIMITE_ANEXOS_MB = 20


class ErroEnvio(Exception):
    pass


def montar_mensagem(remetente, remetente_nome, destinatario, assunto, corpo, anexos=()):
    """Monta a mensagem. `anexos` são tuplas (nome_do_arquivo, conteudo_bytes)."""
    msg = EmailMessage()
    msg['From'] = f'{remetente_nome} <{remetente}>' if remetente_nome else remetente
    msg['To'] = destinatario
    msg['Subject'] = assunto
    msg.set_content(corpo)

    for nome, conteudo in anexos:
        tipo, _ = mimetypes.guess_type(nome)
        principal, _, subtipo = (tipo or 'application/octet-stream').partition('/')
        msg.add_attachment(conteudo, maintype=principal, subtype=subtipo, filename=nome)

    return msg


def enviar(servidor, porta, seguranca, usuario, senha, remetente, remetente_nome,
           destinatario, assunto, corpo, anexos=()):
    """Envia a mensagem, traduzindo as falhas mais comuns para português."""
    if not servidor or not remetente:
        raise ErroEnvio('Configure o servidor SMTP e o e-mail remetente na aba Configurações.')

    mensagem = montar_mensagem(remetente, remetente_nome, destinatario, assunto, corpo, anexos)

    try:
        if seguranca == 'ssl':
            conexao = smtplib.SMTP_SSL(servidor, int(porta), timeout=60, context=ssl.create_default_context())
        else:
            conexao = smtplib.SMTP(servidor, int(porta), timeout=60)

        with conexao:
            if seguranca == 'tls':
                conexao.starttls(context=ssl.create_default_context())
            if usuario:
                conexao.login(usuario, senha or '')
            conexao.send_message(mensagem)
    except smtplib.SMTPAuthenticationError as exc:
        raise ErroEnvio(
            'O servidor recusou o usuário/senha. Em Gmail, Outlook e Zoho normalmente é '
            'preciso usar uma "senha de aplicativo", e não a senha da conta. '
            f'(resposta do servidor: {exc.smtp_code})'
        ) from exc
    except smtplib.SMTPRecipientsRefused as exc:
        raise ErroEnvio(f'O servidor recusou o destinatário {destinatario}.') from exc
    except smtplib.SMTPSenderRefused as exc:
        raise ErroEnvio(
            f'O servidor recusou o remetente {remetente}. Ele costuma exigir que o remetente '
            'seja a mesma conta usada para autenticar.'
        ) from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise ErroEnvio(f'Não foi possível falar com o servidor de e-mail: {exc}') from exc

    return True
