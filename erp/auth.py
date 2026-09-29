"""Login, papéis e controle de acesso.

Três papéis:

- **admin**: dono do sistema. Gerencia empresas, usuários e assinaturas.
  Não tem dados financeiros próprios.
- **user**: o cliente. Enxerga exclusivamente os dados da própria empresa.
- **contador**: enxerga, só de leitura, as empresas que colocaram o e-mail
  dele na aba Contabilidade. Quem concede o acesso é o próprio cliente,
  digitando o e-mail; nada é liberado sem essa indicação.

O bloqueio tem duas causas independentes: `Usuario.ativo = False` (o admin
bloqueou na mão) e assinatura vencida (ninguém pagou nos últimos 30 dias).
As duas levam para a mesma tela, com a explicação certa.
"""
from __future__ import annotations

import datetime as dt
import os
import secrets
from functools import wraps

from flask import flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from models import PAPEL_ADMIN, PAPEL_CONTADOR, PAPEL_USUARIO, Empresa, Usuario, db

CHAVE_SESSAO = 'usuario_id'

# Tamanho mínimo da senha, em qualquer lugar que ela seja definida
SENHA_MINIMA = 8

# Rotas que podem ser abertas sem estar logado
ROTAS_LIVRES = {'login', 'logout', 'static', 'sem_acesso', 'cadastro'}

# Rotas que quem está barrado ainda alcança. São as da própria assinatura:
# barrar quem quer pagar deixaria a conta presa sem saída.
ROTAS_DO_BLOQUEIO = {'assinatura', 'assinatura_cobrar', 'assinatura_conferir'}


def carregar_chave_secreta(caminho):
    """Chave de assinatura dos cookies de sessão, persistida em disco.

    Gerar a chave a cada boot (os.urandom) derrubaria a sessão de todo mundo
    sempre que o servidor reiniciasse ou subisse um segundo processo.
    """
    if os.path.exists(caminho):
        with open(caminho, 'rb') as arquivo:
            chave = arquivo.read().strip()
            if chave:
                return chave

    chave = secrets.token_bytes(32)
    with open(caminho, 'wb') as arquivo:
        arquivo.write(chave)
    try:
        os.chmod(caminho, 0o600)  # só o dono lê
    except OSError:
        pass  # sistemas de arquivo sem permissão POSIX (ex.: Windows)
    return chave


def hash_senha(senha):
    return generate_password_hash(senha)


def senha_confere(usuario, senha):
    return check_password_hash(usuario.senha_hash, senha)


def usuario_logado():
    """Usuário da sessão atual, ou None. Fica em `g` para não repetir consulta."""
    if 'usuario' not in g:
        usuario_id = session.get(CHAVE_SESSAO)
        g.usuario = Usuario.query.get(usuario_id) if usuario_id else None
    return g.usuario


def empresa_atual():
    """Empresa cujos dados a requisição pode tocar.

    Para o cliente é a empresa dele. Para o contador é o cliente que ele
    abriu (guardado na sessão e sempre reconferido). Admin não navega nas
    telas financeiras, então não tem empresa.
    """
    if 'empresa' in g:
        return g.empresa

    usuario = usuario_logado()
    g.empresa = None

    if usuario is None:
        return None
    if usuario.papel == PAPEL_USUARIO:
        g.empresa = usuario.empresa
    elif usuario.papel == PAPEL_CONTADOR:
        empresa_id = session.get('contador_empresa_id')
        if empresa_id:
            # Reconfere a permissão a cada requisição: se o cliente trocar o
            # e-mail do contador, o acesso cai na hora, sem depender da sessão.
            empresa = Empresa.query.get(empresa_id)
            if empresa is not None and contador_atende(usuario, empresa):
                g.empresa = empresa

    return g.empresa


def empresa_atual_id():
    empresa = empresa_atual()
    return empresa.id if empresa else None


def contador_atende(usuario, empresa):
    """O contador só alcança a empresa que informou o e-mail dele.

    A comparação é feita sobre o e-mail gravado na aba Contabilidade da
    empresa — é o cliente quem concede, digitando o endereço.
    """
    if usuario is None or usuario.papel != PAPEL_CONTADOR or empresa is None:
        return False
    from fiscal_sync import config_get  # importado aqui para evitar ciclo
    email_indicado = (config_get('email_contador', empresa_id=empresa.id) or '').strip().lower()
    return bool(email_indicado) and email_indicado == usuario.email.strip().lower()


def empresas_do_contador(usuario):
    """Clientes que indicaram este contador."""
    if usuario is None or usuario.papel != PAPEL_CONTADOR:
        return []
    return [e for e in Empresa.query.order_by(Empresa.nome).all() if contador_atende(usuario, e)]


def motivo_de_bloqueio(usuario):
    """Por que este usuário não pode entrar; None quando pode."""
    if usuario is None:
        return None
    if not usuario.ativo:
        return 'bloqueado'
    if usuario.papel == PAPEL_USUARIO:
        if usuario.empresa is None:
            return 'sem_empresa'
        if not usuario.empresa.assinatura_em_dia:
            return 'assinatura'
    return None


def registrar(app, caminho_chave):
    """Liga a autenticação no app: chave de sessão e porteiro das requisições."""
    app.secret_key = carregar_chave_secreta(caminho_chave)

    @app.before_request
    def exigir_login():
        if request.endpoint in ROTAS_LIVRES or request.endpoint is None:
            return None

        usuario = usuario_logado()
        if usuario is None:
            if request.method != 'GET':
                # Sessão expirou no meio de um envio: não tenta voltar depois
                return redirect(url_for('login'))
            return redirect(url_for('login', proximo=request.full_path))

        motivo = motivo_de_bloqueio(usuario)
        if motivo and request.endpoint not in ROTAS_DO_BLOQUEIO:
            return redirect(url_for('sem_acesso', motivo=motivo))

        return None


def exigir_papel(*papeis):
    """Restringe uma rota a determinados papéis."""
    def decorador(funcao):
        @wraps(funcao)
        def envolvida(*args, **kwargs):
            usuario = usuario_logado()
            if usuario is None or usuario.papel not in papeis:
                flash('Você não tem acesso a essa área.', 'erro')
                return redirect(url_for('inicio'))
            return funcao(*args, **kwargs)
        return envolvida
    return decorador


def somente_leitura():
    """True quando quem está olhando não pode alterar nada (contador)."""
    usuario = usuario_logado()
    return usuario is not None and usuario.papel == PAPEL_CONTADOR


def bloquear_escrita_do_contador(app):
    """O contador enxerga, mas não mexe: barra qualquer envio de formulário.

    Vale como rede de segurança — os botões de alteração já não aparecem para
    ele — para que nenhuma rota nova nasça gravando dado de cliente por engano.
    """
    @app.before_request
    def barrar():
        if request.method in ('GET', 'HEAD', 'OPTIONS'):
            return None
        if request.endpoint in ROTAS_LIVRES or request.endpoint is None:
            return None
        if somente_leitura() and not (request.endpoint or '').startswith('contador_'):
            flash('Contador tem acesso somente de leitura aos dados do cliente.', 'erro')
            return redirect(request.referrer or url_for('inicio'))
        return None


def autenticar(email, senha):
    """Confere as credenciais. Retorna (usuario, erro).

    Depois de alguns erros seguidos a conta fica trancada por um tempo — é o
    que impede alguém de ficar testando senha atrás de senha numa tela que
    está aberta na internet.
    """
    email = (email or '').strip().lower()
    usuario = Usuario.query.filter_by(email=email).first()

    if usuario is not None and usuario.em_castigo:
        return None, (
            f'Conta temporariamente bloqueada por tentativas de senha erradas. '
            f'Tente de novo em {usuario.minutos_de_castigo} minuto(s).'
        )

    # Mensagem igual para e-mail inexistente e senha errada: dizer qual dos dois
    # falhou entrega para um estranho quais e-mails existem no sistema.
    if usuario is None or not senha_confere(usuario, senha or ''):
        if usuario is not None:
            trancou = usuario.errou_a_senha()
            db.session.commit()
            if trancou:
                return None, (
                    f'Senha errada demais vezes. A conta ficou bloqueada por '
                    f'{usuario.minutos_de_castigo} minuto(s).'
                )
        return None, 'E-mail ou senha incorretos.'

    if usuario.tentativas_erradas or usuario.bloqueado_ate:
        usuario.acertou_a_senha()
        db.session.commit()

    return usuario, None


def trocar_senha(usuario, senha_atual, senha_nova, senha_confirmacao):
    """Troca a senha conferindo a atual. Retorna (ok, erro)."""
    if not senha_confere(usuario, senha_atual or ''):
        return False, 'A senha atual está errada.'
    ok, erro = senha_aceitavel(senha_nova, senha_confirmacao)
    if not ok:
        return False, erro
    usuario.senha_hash = hash_senha(senha_nova)
    usuario.acertou_a_senha()
    db.session.commit()
    return True, None


def senha_aceitavel(senha, confirmacao=None):
    """Regras mínimas da senha, num lugar só. Retorna (ok, erro)."""
    if len(senha or '') < SENHA_MINIMA:
        return False, f'A senha precisa ter pelo menos {SENHA_MINIMA} caracteres.'
    if confirmacao is not None and senha != confirmacao:
        return False, 'As duas senhas digitadas não são iguais.'
    return True, None


def entrar(usuario):
    session.clear()
    session[CHAVE_SESSAO] = usuario.id
    session.permanent = True
    usuario.ultimo_acesso = dt.datetime.now()
    db.session.commit()


def sair():
    session.clear()


def criar_usuario(email, senha, nome, papel=PAPEL_USUARIO, empresa=None, cargo=None,
                  **dados_pessoais):
    """Cria uma conta já com a senha em hash (a senha em claro nunca é gravada).

    `dados_pessoais` aceita cpf_cnpj, endereco, cep, cidade, uf e telefone —
    preenchidos pelo autocadastro, sobretudo no do contador, que não tem
    empresa onde guardá-los.
    """
    permitidos = ('cpf_cnpj', 'endereco', 'cep', 'cidade', 'uf', 'telefone', 'cargo')
    usuario = Usuario(
        email=email.strip().lower(),
        senha_hash=hash_senha(senha),
        nome=nome,
        papel=papel,
        empresa_id=empresa.id if empresa else None,
        cargo=cargo,
        **{c: v for c, v in dados_pessoais.items() if c in permitidos and c != 'cargo'},
    )
    db.session.add(usuario)
    return usuario


def email_disponivel(email):
    return Usuario.query.filter_by(email=(email or '').strip().lower()).first() is None
