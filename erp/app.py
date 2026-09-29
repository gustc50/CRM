import calendar
import csv
import gzip
import io
import os
import re
import sqlite3
import sys
import time
import zipfile
import threading
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta
from urllib.parse import quote

from flask import (
    Flask, Response, abort, flash, jsonify, redirect, render_template, request,
    send_from_directory, session, url_for,
)
from openpyxl import Workbook
from openpyxl.styles import Font
from werkzeug.utils import secure_filename

import asaas
import auth
import envio_email
import fiscal_certificado
import fiscal_manifestacao
import fiscal_nfe
import fiscal_nfse
import fiscal_sync
import ofx_parser
from models import (
    DIAS_POR_PAGAMENTO,
    PAPEL_ADMIN,
    PAPEL_CONTADOR,
    PAPEL_USUARIO,
    Categoria,
    Cliente,
    ContaBancaria,
    ContaMovimentacao,
    Empresa,
    Fornecedor,
    LancamentoRecorrente,
    NotaEletronica,
    NotaServico,
    Pagamento,
    Transacao,
    Usuario,
    db,
)


# ------------------------------------------------------------------ #
# Isolamento entre clientes
#
# Todo dado financeiro pertence a uma empresa. Estas três funções são o
# único caminho para chegar nesses dados: quem escrever `Model.query`
# direto numa tela de cliente abre a porta para um cliente enxergar o
# outro. O teste de isolamento confere isso rota a rota.
# ------------------------------------------------------------------ #

def da_empresa(model):
    """Consulta restrita à empresa da requisição."""
    return model.query.filter_by(empresa_id=auth.empresa_atual_id())


def buscar_ou_404(model, id):
    """Registro por id, mas só se for da empresa da requisição.

    Substitui o `query.get_or_404`, que ignora qualquer filtro: com ele,
    trocar o número na URL abriria o registro de outro cliente.
    """
    registro = da_empresa(model).filter_by(id=id).first()
    if registro is None:
        abort(404)
    return registro


def buscar(model, id):
    """Como `buscar_ou_404`, mas devolve None em vez de erro (validações)."""
    if id is None:
        return None
    return da_empresa(model).filter_by(id=id).first()


def novo_registro(model, **dados):
    """Cria um registro já carimbado com a empresa da requisição."""
    return model(empresa_id=auth.empresa_atual_id(), **dados)


TIPOS_VALIDOS = {'Receber', 'Pagar'}
STATUS_VALIDOS = {'Pendente', 'Concluído'}
EXTENSOES_ANEXO_PERMITIDAS = {'pdf', 'png', 'jpg', 'jpeg', 'gif', 'webp'}
EXTENSOES_OFX_PERMITIDAS = {'ofx', 'qfx'}

# Bancos brasileiros mais comuns (código Febraban de compensação). "000" é
# usado para contas sem banco (caixa/dinheiro em espécie).
BANCOS_BRASIL = [
    ('000', 'Caixa / Dinheiro (sem banco)'),
    ('001', 'Banco do Brasil'),
    ('033', 'Santander'),
    ('041', 'Banrisul'),
    ('070', 'BRB - Banco de Brasília'),
    ('077', 'Banco Inter'),
    ('104', 'Caixa Econômica Federal'),
    ('197', 'Stone'),
    ('208', 'BTG Pactual'),
    ('212', 'Banco Original'),
    ('218', 'Banco BS2'),
    ('237', 'Bradesco'),
    ('260', 'Nubank'),
    ('290', 'PagBank (PagSeguro)'),
    ('318', 'Banco BMG'),
    ('323', 'Mercado Pago'),
    ('336', 'C6 Bank'),
    ('341', 'Itaú Unibanco'),
    ('380', 'PicPay'),
    ('403', 'Cora'),
    ('422', 'Banco Safra'),
    ('623', 'Banco Pan'),
    ('637', 'Banco Sofisa'),
    ('735', 'Banco Neon'),
    ('748', 'Sicredi'),
    ('756', 'Sicoob'),
]
BANCOS_BRASIL_MAPA = dict(BANCOS_BRASIL)

HOST = '0.0.0.0'  # servidor hospedado: aceita acesso de fora da máquina
PORT = 5000


def resource_path(relative_path):
    """Resolve o caminho de recursos somente-leitura (templates/static).

    Quando empacotado com PyInstaller, esses arquivos ficam extraídos em
    uma pasta temporária apontada por sys._MEIPASS.
    """
    base_path = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


def data_path(filename):
    """Resolve o caminho do banco de dados (precisa ser gravável).

    Fica sempre ao lado do executável/script, nunca dentro da pasta
    temporária somente-leitura do PyInstaller.
    """
    if getattr(sys, 'frozen', False):
        base_path = os.path.dirname(sys.executable)
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, filename)


def anexos_dir():
    """Pasta onde os comprovantes/notas fiscais anexados aos lançamentos ficam gravados."""
    caminho = data_path('anexos')
    os.makedirs(caminho, exist_ok=True)
    return caminho


def _apagar_anexo(nome_armazenado):
    caminho = os.path.join(anexos_dir(), nome_armazenado)
    if os.path.exists(caminho):
        os.remove(caminho)


# Quantas cópias diárias do banco ficam guardadas antes de as mais velhas saírem
BACKUPS_MANTIDOS = 30


def backups_dir():
    """Pasta com as cópias de segurança do banco, ao lado do programa."""
    caminho = data_path('backups')
    os.makedirs(caminho, exist_ok=True)
    return caminho


def fazer_backup(forcar=False):
    """Grava uma cópia do banco em backups/erp-AAAA-MM-DD.db.

    Usa a API de backup do próprio SQLite em vez de copiar o arquivo: assim a
    cópia sai íntegra mesmo que algo esteja sendo gravado no momento. Roda uma
    vez por dia — se o backup de hoje já existe, não faz nada, a menos que seja
    pedido na mão pelo botão das Configurações.

    Retorna o caminho gravado, ou None quando não havia o que fazer.
    """
    origem = data_path('erp.db')
    if not os.path.exists(origem):
        return None

    destino = os.path.join(backups_dir(), f'erp-{date.today().isoformat()}.db')
    if os.path.exists(destino) and not forcar:
        return None

    conexao_origem = sqlite3.connect(origem)
    try:
        conexao_destino = sqlite3.connect(destino)
        try:
            conexao_origem.backup(conexao_destino)
        finally:
            conexao_destino.close()
    finally:
        conexao_origem.close()

    _limpar_backups_antigos()
    return destino


def _limpar_backups_antigos():
    arquivos = sorted(
        nome for nome in os.listdir(backups_dir())
        if nome.startswith('erp-') and nome.endswith('.db')
    )
    for antigo in arquivos[:-BACKUPS_MANTIDOS]:
        os.remove(os.path.join(backups_dir(), antigo))


def backups_existentes():
    """Backups já gravados, do mais recente para o mais antigo."""
    arquivos = []
    for nome in os.listdir(backups_dir()):
        if not (nome.startswith('erp-') and nome.endswith('.db')):
            continue
        caminho = os.path.join(backups_dir(), nome)
        arquivos.append({
            'nome': nome,
            'tamanho_kb': round(os.path.getsize(caminho) / 1024),
            'data': datetime.fromtimestamp(os.path.getmtime(caminho)),
        })
    return sorted(arquivos, key=lambda a: a['nome'], reverse=True)


app = Flask(
    __name__,
    template_folder=resource_path('templates'),
    static_folder=resource_path('static'),
)
app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{data_path('erp.db')}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # limite de 10 MB por anexo
db.init_app(app)

# Sessões duram uma semana sem atividade; depois disso pede login de novo.
app.permanent_session_lifetime = timedelta(days=7)

# Liga o login: define a chave de sessão (persistida em disco, para o reinício
# do servidor não deslogar todo mundo) e instala o porteiro das requisições.
auth.registrar(app, data_path('.chave_sessao'))
auth.bloquear_escrita_do_contador(app)


@app.errorhandler(413)
def arquivo_muito_grande(_erro):
    flash('Arquivo muito grande. O limite por anexo é 10 MB.', 'erro')
    return redirect(request.referrer or url_for('lancamentos'))


def migrar_schema():
    """Adiciona colunas novas em tabelas criadas por versões anteriores do app.

    Tabelas inteiramente novas (categoria, conta_bancaria, lancamento_recorrente)
    já são criadas automaticamente pelo db.create_all(); aqui só é preciso
    tratar colunas adicionadas a tabelas que já existiam antes.
    """
    inspector = db.inspect(db.engine)
    tabelas = inspector.get_table_names()

    if 'transacao' in tabelas:
        colunas = {c['name'] for c in inspector.get_columns('transacao')}
        comandos = {
            'data_pagamento': 'ALTER TABLE transacao ADD COLUMN data_pagamento DATE',
            'fornecedor_id': 'ALTER TABLE transacao ADD COLUMN fornecedor_id INTEGER',
            'cliente_id': 'ALTER TABLE transacao ADD COLUMN cliente_id INTEGER',
            'categoria_id': 'ALTER TABLE transacao ADD COLUMN categoria_id INTEGER',
            'conta_bancaria_id': 'ALTER TABLE transacao ADD COLUMN conta_bancaria_id INTEGER',
            'recorrente_id': 'ALTER TABLE transacao ADD COLUMN recorrente_id INTEGER',
            'anexo_arquivo': 'ALTER TABLE transacao ADD COLUMN anexo_arquivo VARCHAR(255)',
            'anexo_nome_original': 'ALTER TABLE transacao ADD COLUMN anexo_nome_original VARCHAR(255)',
        }
        pendentes = [sql for coluna, sql in comandos.items() if coluna not in colunas]
        if pendentes:
            with db.engine.begin() as conn:
                for sql in pendentes:
                    conn.execute(db.text(sql))

    for tabela in ('fornecedor', 'cliente'):
        if tabela not in tabelas:
            continue
        colunas_tabela = {c['name'] for c in inspector.get_columns(tabela)}
        pendentes_tabela = []
        if 'telefone' not in colunas_tabela:
            pendentes_tabela.append(f'ALTER TABLE {tabela} ADD COLUMN telefone VARCHAR(20)')
        if 'email' not in colunas_tabela:
            pendentes_tabela.append(f'ALTER TABLE {tabela} ADD COLUMN email VARCHAR(150)')
        if 'categoria_id' not in colunas_tabela:
            pendentes_tabela.append(f'ALTER TABLE {tabela} ADD COLUMN categoria_id INTEGER')
        if 'conta_bancaria_id' not in colunas_tabela:
            pendentes_tabela.append(f'ALTER TABLE {tabela} ADD COLUMN conta_bancaria_id INTEGER')
        if pendentes_tabela:
            with db.engine.begin() as conn:
                for sql in pendentes_tabela:
                    conn.execute(db.text(sql))

    # Dados de cobrança e de cadastro, que passaram a ser pedidos no
    # autocadastro. Bancos criados antes disso ficam com as colunas vazias.
    if 'empresa' in tabelas:
        colunas_empresa = {c['name'] for c in inspector.get_columns('empresa')}
        comandos_empresa = {
            'endereco': 'ALTER TABLE empresa ADD COLUMN endereco VARCHAR(200)',
            'cep': 'ALTER TABLE empresa ADD COLUMN cep VARCHAR(10)',
            'cidade': 'ALTER TABLE empresa ADD COLUMN cidade VARCHAR(80)',
            'uf': 'ALTER TABLE empresa ADD COLUMN uf VARCHAR(2)',
            'telefone': 'ALTER TABLE empresa ADD COLUMN telefone VARCHAR(20)',
            'email': 'ALTER TABLE empresa ADD COLUMN email VARCHAR(150)',
        }
        pendentes_empresa = [sql for coluna, sql in comandos_empresa.items()
                             if coluna not in colunas_empresa]
        if pendentes_empresa:
            with db.engine.begin() as conn:
                for sql in pendentes_empresa:
                    conn.execute(db.text(sql))

    if 'usuario' in tabelas:
        colunas_usuario = {c['name'] for c in inspector.get_columns('usuario')}
        comandos_usuario = {
            'cpf_cnpj': 'ALTER TABLE usuario ADD COLUMN cpf_cnpj VARCHAR(20)',
            'endereco': 'ALTER TABLE usuario ADD COLUMN endereco VARCHAR(200)',
            'cep': 'ALTER TABLE usuario ADD COLUMN cep VARCHAR(10)',
            'cidade': 'ALTER TABLE usuario ADD COLUMN cidade VARCHAR(80)',
            'uf': 'ALTER TABLE usuario ADD COLUMN uf VARCHAR(2)',
            'telefone': 'ALTER TABLE usuario ADD COLUMN telefone VARCHAR(20)',
        }
        pendentes_usuario = [sql for coluna, sql in comandos_usuario.items()
                             if coluna not in colunas_usuario]
        if pendentes_usuario:
            with db.engine.begin() as conn:
                for sql in pendentes_usuario:
                    conn.execute(db.text(sql))

    if 'categoria' in tabelas:
        colunas_categoria = {c['name'] for c in inspector.get_columns('categoria')}
        pendentes_categoria = []
        if 'tipo' not in colunas_categoria:
            # Categorias criadas antes desta coluna existir viram "Pagar" por
            # padrão; o usuário pode corrigir depois em Categorias > Editar.
            pendentes_categoria.append("ALTER TABLE categoria ADD COLUMN tipo VARCHAR(20) NOT NULL DEFAULT 'Pagar'")
        if 'centro_custo' not in colunas_categoria:
            pendentes_categoria.append('ALTER TABLE categoria ADD COLUMN centro_custo VARCHAR(80)')
        if pendentes_categoria:
            with db.engine.begin() as conn:
                for sql in pendentes_categoria:
                    conn.execute(db.text(sql))

    if 'conta_bancaria' in tabelas:
        colunas_conta = {c['name'] for c in inspector.get_columns('conta_bancaria')}
        comandos_conta = {
            'banco': 'ALTER TABLE conta_bancaria ADD COLUMN banco VARCHAR(10)',
            'agencia': 'ALTER TABLE conta_bancaria ADD COLUMN agencia VARCHAR(20)',
            'conta_numero': 'ALTER TABLE conta_bancaria ADD COLUMN conta_numero VARCHAR(30)',
            'saldo': 'ALTER TABLE conta_bancaria ADD COLUMN saldo FLOAT',
            'saldo_data': 'ALTER TABLE conta_bancaria ADD COLUMN saldo_data DATE',
        }
        pendentes_conta = [sql for coluna, sql in comandos_conta.items() if coluna not in colunas_conta]
        if pendentes_conta:
            with db.engine.begin() as conn:
                for sql in pendentes_conta:
                    conn.execute(db.text(sql))

    if 'nota_eletronica' in tabelas:
        colunas_nfe = {c['name'] for c in inspector.get_columns('nota_eletronica')}
        comandos_nfe = {
            'manifestacao_em': 'ALTER TABLE nota_eletronica ADD COLUMN manifestacao_em VARCHAR(30)',
            'manifestacao_protocolo': 'ALTER TABLE nota_eletronica ADD COLUMN manifestacao_protocolo VARCHAR(30)',
        }
        pendentes_nfe = [sql for coluna, sql in comandos_nfe.items() if coluna not in colunas_nfe]
        if pendentes_nfe:
            with db.engine.begin() as conn:
                for sql in pendentes_nfe:
                    conn.execute(db.text(sql))

    if 'conta_movimentacao' in tabelas:
        colunas_mov = {c['name'] for c in inspector.get_columns('conta_movimentacao')}
        if 'transacao_id' not in colunas_mov:
            with db.engine.begin() as conn:
                conn.execute(db.text('ALTER TABLE conta_movimentacao ADD COLUMN transacao_id INTEGER'))

    _normalizar_cnpj_cpf_existentes()


def _normalizar_cnpj_cpf_existentes():
    """Reescreve CNPJ/CPF já cadastrados para conter só dígitos.

    Cadastros feitos pela tela sempre gravaram o texto exatamente como
    digitado (podendo ter pontuação); os criados automaticamente a partir de
    notas fiscais sempre gravam só dígitos. Uniformiza os dois formatos para
    que a coluna fique consistente na listagem — idempotente, então rodar de
    novo em bancos já normalizados não faz nada.
    """
    for modelo in (Fornecedor, Cliente):
        alterados = False
        for registro in modelo.query.all():
            normalizado = re.sub(r'\D', '', registro.cnpj_cpf or '')
            if normalizado and normalizado != registro.cnpj_cpf:
                registro.cnpj_cpf = normalizado
                alterados = True
        if alterados:
            db.session.commit()


# Antes de mexer no schema: cópia do dia com o banco exatamente como estava.
# Se uma migração futura der errado, o estado anterior continua recuperável.
fazer_backup()

# A preparação do banco ficou no fim do arquivo (função `iniciar_sistema`):
# ela depende de funções definidas mais abaixo, como a criação das contas de
# teste e a geração de pendências de cada empresa.

# Sincronização de documentos fiscais (NFS-e / NF-e) via certificado digital
sync_fiscal = fiscal_sync.SyncFiscal(
    app,
    caminho_chave_fernet=data_path('.chave_secreta'),
    ca_extra_path=data_path('ca_extra.pem'),
)


def _validar_id_existente(valor, model):
    """Confirma que o id recebido do <select> corresponde a um registro real (campo obrigatório)."""
    if not valor or not valor.isdigit():
        return None
    registro = buscar(model, int(valor))
    return registro.id if registro else None


def _validar_id_opcional(valor, model):
    """Como `_validar_id_existente`, mas o campo pode ficar em branco.

    Retorna (id, valido). `valido` só é False quando um valor foi informado
    mas não corresponde a nenhum registro existente.
    """
    valor = (valor or '').strip()
    if not valor:
        return None, True
    if not valor.isdigit():
        return None, False
    registro = buscar(model, int(valor))
    if not registro:
        return None, False
    return registro.id, True


def _validar_categoria_opcional(valor, tipo_esperado):
    """Como `_validar_id_opcional`, mas também confere se o tipo da
    categoria (Pagar/Receber) bate com o tipo do lançamento."""
    categoria_id, ok = _validar_id_opcional(valor, Categoria)
    if not ok or categoria_id is None:
        return categoria_id, ok
    categoria = buscar(Categoria, categoria_id)
    if categoria.tipo != tipo_esperado:
        return None, False
    return categoria_id, True


def validar_transacao(form):
    """Valida os campos comuns a criação/edição de um lançamento."""
    tipo = form.get('tipo', '')
    descricao = form.get('descricao', '').strip()
    valor_str = form.get('valor', '')
    data_str = form.get('data_vencimento', '')

    if tipo not in TIPOS_VALIDOS:
        return None, 'Tipo de lançamento inválido.'

    if not descricao or len(descricao) > 100:
        return None, 'Descrição obrigatória (até 100 caracteres).'

    try:
        valor = float(valor_str)
    except (TypeError, ValueError):
        return None, 'Valor inválido.'

    if valor <= 0:
        return None, 'O valor deve ser maior que zero.'

    try:
        data_vencimento = datetime.strptime(data_str, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None, 'Data de vencimento inválida.'

    fornecedor_id = None
    cliente_id = None

    if tipo == 'Pagar':
        fornecedor_id = _validar_id_existente(form.get('fornecedor_id', ''), Fornecedor)
        if fornecedor_id is None:
            return None, 'Selecione um fornecedor.'
    else:
        cliente_id = _validar_id_existente(form.get('cliente_id', ''), Cliente)
        if cliente_id is None:
            return None, 'Selecione um cliente.'

    categoria_id, categoria_ok = _validar_categoria_opcional(form.get('categoria_id', ''), tipo)
    if not categoria_ok:
        return None, 'Categoria inválida (verifique se ela é do tipo certo: Pagar/Receber).'

    conta_bancaria_id, conta_ok = _validar_id_opcional(form.get('conta_bancaria_id', ''), ContaBancaria)
    if not conta_ok:
        return None, 'Conta bancária inválida.'

    dados = {
        'tipo': tipo,
        'descricao': descricao,
        'valor': valor,
        'data_vencimento': data_vencimento,
        'fornecedor_id': fornecedor_id,
        'cliente_id': cliente_id,
        'categoria_id': categoria_id,
        'conta_bancaria_id': conta_bancaria_id,
    }
    return dados, None


def nome_entidade(transacao):
    """Nome do fornecedor (contas a pagar) ou cliente (contas a receber) do lançamento."""
    if transacao.tipo == 'Pagar':
        return transacao.fornecedor.nome if transacao.fornecedor else ''
    return transacao.cliente.nome if transacao.cliente else ''


app.jinja_env.globals['nome_entidade'] = nome_entidade


def nome_banco(codigo):
    return BANCOS_BRASIL_MAPA.get(codigo or '', codigo or '—')


app.jinja_env.globals['nome_banco'] = nome_banco
app.jinja_env.globals['BANCOS_BRASIL'] = BANCOS_BRASIL


def formatar_cnpj_cpf(valor):
    """Formata um CNPJ/CPF (gravado só com dígitos) para exibição."""
    digitos = re.sub(r'\D', '', valor or '')
    if len(digitos) == 14:
        return f'{digitos[0:2]}.{digitos[2:5]}.{digitos[5:8]}/{digitos[8:12]}-{digitos[12:14]}'
    if len(digitos) == 11:
        return f'{digitos[0:3]}.{digitos[3:6]}.{digitos[6:9]}-{digitos[9:11]}'
    return valor or ''


app.jinja_env.globals['formatar_cnpj_cpf'] = formatar_cnpj_cpf


# Menu lateral. `ativo_em` lista os endpoints que acendem cada item (incluindo
# as telas de edição), e `filhos` são as sub-abas que aparecem quando a seção
# está aberta. Clicar no item pai leva para a primeira sub-aba.
MENU_LATERAL = [
    {'icone': '🏠', 'rotulo': 'Início', 'endpoint': 'inicio',
     'ativo_em': ('inicio',)},
    {'icone': '💰', 'rotulo': 'Lançamentos', 'endpoint': 'lancamentos',
     'ativo_em': ('lancamentos', 'editar')},
    {'icone': '📊', 'rotulo': 'Relatório', 'endpoint': 'relatorio',
     'ativo_em': ('relatorio',)},
    {'icone': '🔁', 'rotulo': 'Recorrentes', 'endpoint': 'recorrentes_listar',
     'ativo_em': ('recorrentes_listar', 'recorrentes_editar')},
    {'icone': '🧾', 'rotulo': 'Notas Fiscais', 'endpoint': 'notas_servico',
     'ativo_em': ('notas_servico', 'notas_eletronicas'),
     'filhos': [
         {'rotulo': 'NFS-e', 'endpoint': 'notas_servico', 'ativo_em': ('notas_servico',)},
         {'rotulo': 'NF-e', 'endpoint': 'notas_eletronicas', 'ativo_em': ('notas_eletronicas',)},
     ]},
    {'icone': '👥', 'rotulo': 'Cadastros', 'endpoint': 'fornecedores_listar',
     'ativo_em': ('fornecedores_listar', 'fornecedores_editar', 'clientes_listar', 'clientes_editar'),
     'filhos': [
         {'rotulo': 'Fornecedores', 'endpoint': 'fornecedores_listar',
          'ativo_em': ('fornecedores_listar', 'fornecedores_editar')},
         {'rotulo': 'Clientes', 'endpoint': 'clientes_listar',
          'ativo_em': ('clientes_listar', 'clientes_editar')},
     ]},
    {'icone': '🏷️', 'rotulo': 'Categorias', 'endpoint': 'categorias_listar',
     'ativo_em': ('categorias_listar', 'categorias_editar')},
    {'icone': '🏦', 'rotulo': 'Contas', 'endpoint': 'contas_listar',
     'ativo_em': ('contas_listar', 'contas_editar', 'contas_movimentacoes')},
    {'icone': '📨', 'rotulo': 'Contabilidade', 'endpoint': 'contabilidade',
     'ativo_em': ('contabilidade',)},
    {'icone': '⚙️', 'rotulo': 'Configurações', 'endpoint': 'configuracoes',
     'ativo_em': ('configuracoes',)},
]

MENU_ADMIN = [
    {'icone': '📊', 'rotulo': 'Painel', 'endpoint': 'admin_painel',
     'ativo_em': ('admin_painel',)},
    {'icone': '🏢', 'rotulo': 'Clientes', 'endpoint': 'admin_empresas',
     'ativo_em': ('admin_empresas', 'admin_empresa_nova')},
    {'icone': '👥', 'rotulo': 'Usuários', 'endpoint': 'admin_usuarios',
     'ativo_em': ('admin_usuarios', 'admin_usuario_novo')},
    {'icone': '💳', 'rotulo': 'Assinaturas', 'endpoint': 'admin_assinaturas',
     'ativo_em': ('admin_assinaturas',)},
]

MENU_CONTADOR = [
    {'icone': '🏢', 'rotulo': 'Meus clientes', 'endpoint': 'contador_clientes',
     'ativo_em': ('contador_clientes',)},
]


def menu_do_usuario():
    """Itens do menu conforme o papel de quem está logado.

    O contador só ganha as abas do cliente depois de escolher um: antes disso
    não há dado nenhum para mostrar.
    """
    usuario = auth.usuario_logado()
    if usuario is None:
        return []
    if usuario.eh_admin:
        return MENU_ADMIN
    if usuario.eh_contador:
        if auth.empresa_atual() is None:
            return MENU_CONTADOR
        # Dentro de um cliente: as telas de leitura, sem os cadastros
        somente_leitura = ('inicio', 'lancamentos', 'relatorio', 'notas_servico',
                           'notas_eletronicas', 'contabilidade')
        return MENU_CONTADOR + [i for i in MENU_LATERAL if i['endpoint'] in somente_leitura]
    return MENU_LATERAL


app.jinja_env.globals['MENU_LATERAL'] = MENU_LATERAL
app.jinja_env.globals['menu_do_usuario'] = menu_do_usuario
app.jinja_env.globals['usuario_logado'] = auth.usuario_logado
app.jinja_env.globals['empresa_atual'] = auth.empresa_atual
app.jinja_env.globals['somente_leitura'] = auth.somente_leitura


def validar_cadastro(form, tipo_categoria, model=None, id_atual=None):
    """Valida os campos comuns ao cadastro de fornecedor/cliente.

    `tipo_categoria` é 'Pagar' (fornecedor) ou 'Receber' (cliente): restringe
    quais categorias podem ser escolhidas como padrão para este cadastro.
    `model`/`id_atual` permitem recusar um CNPJ/CPF que já pertence a outro
    cadastro (o próprio registro é ignorado na edição).
    """
    cnpj_cpf = form.get('cnpj_cpf', '').strip()
    nome = form.get('nome', '').strip()
    endereco = form.get('endereco', '').strip()
    telefone = form.get('telefone', '').strip()
    email = form.get('email', '').strip()

    digitos = re.sub(r'\D', '', cnpj_cpf)
    if len(digitos) not in (11, 14):
        return None, 'CNPJ/CPF inválido (informe 11 dígitos para CPF ou 14 para CNPJ).'

    if model is not None:
        ja_existe = da_empresa(model).filter_by(cnpj_cpf=digitos).first()
        if ja_existe is not None and ja_existe.id != id_atual:
            return None, (
                f'O CNPJ/CPF {formatar_cnpj_cpf(digitos)} já está cadastrado '
                f'em "{ja_existe.nome}". Edite esse cadastro em vez de criar outro.'
            )

    if not nome or len(nome) > 150:
        return None, 'Nome obrigatório (até 150 caracteres).'

    if not endereco or len(endereco) > 200:
        return None, 'Endereço obrigatório (até 200 caracteres).'

    if telefone and len(telefone) > 20:
        return None, 'Telefone muito longo (máx. 20 caracteres).'

    if email and (len(email) > 150 or not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email)):
        return None, 'E-mail inválido.'

    categoria_id, categoria_ok = _validar_categoria_opcional(form.get('categoria_id', ''), tipo_categoria)
    if not categoria_ok:
        return None, 'Categoria padrão inválida.'

    conta_bancaria_id, conta_ok = _validar_id_opcional(form.get('conta_bancaria_id', ''), ContaBancaria)
    if not conta_ok:
        return None, 'Conta bancária padrão inválida.'

    dados = {
        'cnpj_cpf': digitos,
        'nome': nome,
        'endereco': endereco,
        'telefone': telefone or None,
        'email': email or None,
        'categoria_id': categoria_id,
        'conta_bancaria_id': conta_bancaria_id,
    }
    return dados, None


def _texto_delimitado(cabecalho, linhas):
    """Tabela como texto separado por ';' — serve tanto para o CSV baixado
    quanto para o .txt que vai anexado no e-mail do contador."""
    buffer = io.StringIO()
    buffer.write('﻿')  # BOM para o Excel abrir acentos corretamente
    writer = csv.writer(buffer, delimiter=';')
    writer.writerow(cabecalho)
    for linha in linhas:
        writer.writerow(linha)
    return buffer.getvalue()


def _exportar_csv_generico(cabecalho, linhas, nome_arquivo):
    return Response(
        _texto_delimitado(cabecalho, linhas),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{nome_arquivo}.csv"'},
    )


def _planilha_bytes(cabecalho, linhas, titulo_aba='Dados'):
    """Planilha XLSX como bytes (usada pelo download e pelo anexo do e-mail)."""
    wb = Workbook()
    ws = wb.active
    ws.title = titulo_aba[:31]  # Excel limita o nome da aba a 31 caracteres

    ws.append(cabecalho)
    for celula in ws[1]:
        celula.font = Font(bold=True)
    for linha in linhas:
        ws.append(linha)

    for indice, titulo in enumerate(cabecalho, start=1):
        letra = ws.cell(row=1, column=indice).column_letter
        ws.column_dimensions[letra].width = max(12, len(titulo) + 4)

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _exportar_xlsx_generico(cabecalho, linhas, nome_arquivo, titulo_aba='Dados'):
    return Response(
        _planilha_bytes(cabecalho, linhas, titulo_aba),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': f'attachment; filename="{nome_arquivo}.xlsx"'},
    )


def registrar_rotas_cadastro(model, nome_singular, nome_plural, prefixo, coluna_fk, tipo_categoria):
    """Registra as rotas de listar/adicionar/editar/excluir/exportar para um
    cadastro completo (CNPJ/CPF, nome, endereço, telefone, e-mail, categoria
    padrão). Fornecedores e clientes usam exatamente a mesma lógica, então
    as rotas são geradas uma única vez aqui e reaproveitadas para os dois.

    `coluna_fk` é a coluna de Transacao que referencia esse cadastro
    (Transacao.fornecedor_id ou Transacao.cliente_id), usada para impedir
    a exclusão de um registro que já está vinculado a algum lançamento.
    `tipo_categoria` é 'Pagar' ou 'Receber' — restringe a lista de
    categorias oferecidas como "padrão" para este cadastro.
    """

    def _categorias():
        return da_empresa(Categoria).filter_by(tipo=tipo_categoria).order_by(Categoria.nome).all()

    def _contas_bancarias():
        return da_empresa(ContaBancaria).order_by(ContaBancaria.nome).all()

    def listar():
        registros = da_empresa(model).order_by(model.nome).all()

        # Cadastros antigos podiam repetir o mesmo CNPJ/CPF escrito de formas
        # diferentes; depois da normalização eles ficam idênticos. Marcar os
        # repetidos deixa claro o que precisa ser unificado — sem isso, o
        # usuário só descobriria ao ser barrado tentando salvar uma edição.
        vistos = {}
        for registro in registros:
            vistos[registro.cnpj_cpf] = vistos.get(registro.cnpj_cpf, 0) + 1
        duplicados = {doc for doc, quantas in vistos.items() if quantas > 1}

        return render_template(
            'cadastro.html',
            registros=registros,
            duplicados=duplicados,
            titulo=nome_plural,
            titulo_singular=nome_singular,
            prefixo=prefixo,
            categorias=_categorias(),
            contas_bancarias=_contas_bancarias(),
        )

    def adicionar():
        dados, erro = validar_cadastro(request.form, tipo_categoria, model=model)
        if erro:
            flash(erro, 'erro')
        else:
            db.session.add(novo_registro(model, **dados))
            db.session.commit()
            flash(f'{nome_singular} cadastrado com sucesso.', 'sucesso')
        return redirect(url_for(f'{prefixo}_listar'))

    def editar(id):
        registro = buscar_ou_404(model, id)

        if request.method == 'POST':
            dados, erro = validar_cadastro(request.form, tipo_categoria, model=model, id_atual=id)
            if erro:
                flash(erro, 'erro')
                return redirect(url_for(f'{prefixo}_editar', id=id))

            registro.cnpj_cpf = dados['cnpj_cpf']
            registro.nome = dados['nome']
            registro.endereco = dados['endereco']
            registro.telefone = dados['telefone']
            registro.email = dados['email']
            registro.categoria_id = dados['categoria_id']
            registro.conta_bancaria_id = dados['conta_bancaria_id']
            db.session.commit()
            flash(f'{nome_singular} atualizado com sucesso.', 'sucesso')
            return redirect(url_for(f'{prefixo}_listar'))

        return render_template(
            'cadastro_editar.html',
            registro=registro,
            titulo_singular=nome_singular,
            prefixo=prefixo,
            categorias=_categorias(),
            contas_bancarias=_contas_bancarias(),
        )

    def excluir(id):
        registro = buscar_ou_404(model, id)

        em_uso = da_empresa(Transacao).filter(coluna_fk == id).first() is not None
        if em_uso:
            flash(
                f'Não é possível excluir: existem lançamentos vinculados a este {nome_singular.lower()}.',
                'erro',
            )
            return redirect(url_for(f'{prefixo}_listar'))

        db.session.delete(registro)
        db.session.commit()
        flash(f'{nome_singular} excluído.', 'sucesso')
        return redirect(url_for(f'{prefixo}_listar'))

    def exportar():
        registros = da_empresa(model).order_by(model.nome).all()
        cabecalho = ['CNPJ/CPF', 'Nome', 'Endereço', 'Telefone', 'E-mail', 'Categoria Padrão', 'Conta Padrão']
        linhas = [
            [
                r.cnpj_cpf, r.nome, r.endereco, r.telefone or '', r.email or '',
                r.categoria.nome if r.categoria else '',
                r.conta_bancaria.nome if r.conta_bancaria else '',
            ]
            for r in registros
        ]
        formato = request.args.get('formato', 'csv')
        if formato == 'xlsx':
            return _exportar_xlsx_generico(cabecalho, linhas, prefixo, nome_plural)
        return _exportar_csv_generico(cabecalho, linhas, prefixo)

    app.add_url_rule(f'/{prefixo}', f'{prefixo}_listar', listar, methods=['GET'])
    app.add_url_rule(f'/{prefixo}/adicionar', f'{prefixo}_adicionar', adicionar, methods=['POST'])
    app.add_url_rule(f'/{prefixo}/editar/<int:id>', f'{prefixo}_editar', editar, methods=['GET', 'POST'])
    app.add_url_rule(f'/{prefixo}/excluir/<int:id>', f'{prefixo}_excluir', excluir, methods=['POST'])
    app.add_url_rule(f'/{prefixo}/exportar', f'{prefixo}_exportar', exportar, methods=['GET'])


registrar_rotas_cadastro(Fornecedor, 'Fornecedor', 'Fornecedores', 'fornecedores', Transacao.fornecedor_id, tipo_categoria='Pagar')
registrar_rotas_cadastro(Cliente, 'Cliente', 'Clientes', 'clientes', Transacao.cliente_id, tipo_categoria='Receber')


def validar_categoria(form):
    nome = form.get('nome', '').strip()
    tipo = form.get('tipo', '')
    centro_custo = form.get('centro_custo', '').strip()

    if not nome or len(nome) > 50:
        return None, 'Nome obrigatório (até 50 caracteres).'
    if tipo not in TIPOS_VALIDOS:
        return None, 'Selecione se a categoria é para Contas a Pagar ou a Receber.'
    if len(centro_custo) > 80:
        return None, 'Centro de custo muito longo (máx. 80 caracteres).'

    return {'nome': nome, 'tipo': tipo, 'centro_custo': centro_custo or None}, None


@app.route('/categorias')
def categorias_listar():
    registros = da_empresa(Categoria).order_by(Categoria.tipo, Categoria.nome).all()
    return render_template('categorias.html', registros=registros)


@app.route('/categorias/adicionar', methods=['POST'])
def categorias_adicionar():
    dados, erro = validar_categoria(request.form)
    if erro:
        flash(erro, 'erro')
    else:
        db.session.add(novo_registro(Categoria, **dados))
        db.session.commit()
        flash('Categoria cadastrada com sucesso.', 'sucesso')
    return redirect(url_for('categorias_listar'))


@app.route('/categorias/editar/<int:id>', methods=['GET', 'POST'])
def categorias_editar(id):
    registro = buscar_ou_404(Categoria, id)
    if request.method == 'POST':
        dados, erro = validar_categoria(request.form)
        if erro:
            flash(erro, 'erro')
            return redirect(url_for('categorias_editar', id=id))
        registro.nome = dados['nome']
        registro.tipo = dados['tipo']
        registro.centro_custo = dados['centro_custo']
        db.session.commit()
        flash('Categoria atualizada com sucesso.', 'sucesso')
        return redirect(url_for('categorias_listar'))
    return render_template('categoria_editar.html', registro=registro)


@app.route('/categorias/excluir/<int:id>', methods=['POST'])
def categorias_excluir(id):
    registro = buscar_ou_404(Categoria, id)
    em_uso = (
        da_empresa(Transacao).filter_by(categoria_id=id).first() is not None
        or da_empresa(Fornecedor).filter_by(categoria_id=id).first() is not None
        or da_empresa(Cliente).filter_by(categoria_id=id).first() is not None
        or da_empresa(LancamentoRecorrente).filter_by(categoria_id=id).first() is not None
    )
    if em_uso:
        flash(
            'Não é possível excluir: existem lançamentos, fornecedores/clientes ou '
            'recorrências usando esta categoria.',
            'erro',
        )
        return redirect(url_for('categorias_listar'))
    db.session.delete(registro)
    db.session.commit()
    flash('Categoria excluída.', 'sucesso')
    return redirect(url_for('categorias_listar'))


def validar_conta_bancaria(form):
    nome = form.get('nome', '').strip()
    banco = form.get('banco', '').strip()
    agencia = form.get('agencia', '').strip()
    conta_numero = form.get('conta_numero', '').strip()

    if not nome or len(nome) > 80:
        return None, 'Nome obrigatório (até 80 caracteres).'
    if banco and banco not in BANCOS_BRASIL_MAPA:
        return None, 'Banco inválido.'
    if len(agencia) > 20:
        return None, 'Agência muito longa (máx. 20 caracteres).'
    if len(conta_numero) > 30:
        return None, 'Número da conta muito longo (máx. 30 caracteres).'

    return {
        'nome': nome,
        'banco': banco or None,
        'agencia': agencia or None,
        'conta_numero': conta_numero or None,
    }, None


@app.route('/contas')
def contas_listar():
    registros = da_empresa(ContaBancaria).order_by(ContaBancaria.nome).all()
    return render_template('contas.html', registros=registros)


@app.route('/contas/adicionar', methods=['POST'])
def contas_adicionar():
    dados, erro = validar_conta_bancaria(request.form)
    if erro:
        flash(erro, 'erro')
    else:
        db.session.add(novo_registro(ContaBancaria, **dados))
        db.session.commit()
        flash('Conta cadastrada com sucesso.', 'sucesso')
    return redirect(url_for('contas_listar'))


@app.route('/contas/editar/<int:id>', methods=['GET', 'POST'])
def contas_editar(id):
    registro = buscar_ou_404(ContaBancaria, id)
    if request.method == 'POST':
        dados, erro = validar_conta_bancaria(request.form)
        if erro:
            flash(erro, 'erro')
            return redirect(url_for('contas_editar', id=id))
        registro.nome = dados['nome']
        registro.banco = dados['banco']
        registro.agencia = dados['agencia']
        registro.conta_numero = dados['conta_numero']
        db.session.commit()
        flash('Conta atualizada com sucesso.', 'sucesso')
        return redirect(url_for('contas_listar'))
    return render_template('conta_editar.html', registro=registro)


@app.route('/contas/excluir/<int:id>', methods=['POST'])
def contas_excluir(id):
    registro = buscar_ou_404(ContaBancaria, id)
    em_uso = (
        da_empresa(Transacao).filter_by(conta_bancaria_id=id).first() is not None
        or da_empresa(LancamentoRecorrente).filter_by(conta_bancaria_id=id).first() is not None
    )
    if em_uso:
        flash('Não é possível excluir: existem lançamentos vinculados a esta conta.', 'erro')
        return redirect(url_for('contas_listar'))
    da_empresa(ContaMovimentacao).filter_by(conta_bancaria_id=id).delete()
    db.session.delete(registro)
    db.session.commit()
    flash('Conta excluída.', 'sucesso')
    return redirect(url_for('contas_listar'))


def _periodo_movimentacoes(args):
    hoje = datetime.now().date()
    inicio = parse_data(args.get('inicio', '')) or hoje.replace(day=1)
    fim = parse_data(args.get('fim', '')) or hoje
    if inicio > fim:
        inicio, fim = fim, inicio
    return inicio, fim


def _movimentacoes_do_periodo(conta_id, inicio, fim):
    return da_empresa(ContaMovimentacao).filter(
        ContaMovimentacao.conta_bancaria_id == conta_id,
        ContaMovimentacao.data >= inicio,
        ContaMovimentacao.data <= fim,
    ).order_by(ContaMovimentacao.data, ContaMovimentacao.id).all()


def _transacoes_conciliaveis(tipo):
    """Lançamentos Pendentes desse tipo ainda não vinculados a nenhuma
    movimentação bancária — candidatos a conciliação."""
    vinculadas = db.session.query(ContaMovimentacao.transacao_id).filter(
        ContaMovimentacao.transacao_id.isnot(None)
    )
    return da_empresa(Transacao).filter(
        Transacao.tipo == tipo,
        Transacao.status == 'Pendente',
        ~Transacao.id.in_(vinculadas),
    ).order_by(Transacao.data_vencimento).all()


def _sugerir_transacao(mov, candidatas):
    """Sugere o lançamento mais provável para um movimento importado: exige
    o mesmo valor (tolerância de 1 centavo, por causa de arredondamento) e,
    entre os que baterem, prefere o vencimento mais próximo da data do
    movimento. Sem valor batendo, não há sugestão — o usuário escolhe na mão.
    """
    valor_mov = abs(mov.valor)
    compativeis = [t for t in candidatas if abs(t.valor - valor_mov) < 0.01]
    if not compativeis:
        return None
    compativeis.sort(key=lambda t: abs((t.data_vencimento - mov.data).days))
    return compativeis[0]


@app.route('/contas/<int:id>/movimentacoes')
def contas_movimentacoes(id):
    conta = buscar_ou_404(ContaBancaria, id)
    inicio, fim = _periodo_movimentacoes(request.args)
    movimentos = _movimentacoes_do_periodo(id, inicio, fim)

    total_creditos = sum(m.valor for m in movimentos if m.tipo == 'CREDITO')
    total_debitos = sum(-m.valor for m in movimentos if m.tipo == 'DEBITO')

    candidatas_pagar = _transacoes_conciliaveis('Pagar')
    candidatas_receber = _transacoes_conciliaveis('Receber')
    conciliacao = {}
    for m in movimentos:
        if m.transacao_id:
            continue
        candidatas = candidatas_receber if m.tipo == 'CREDITO' else candidatas_pagar
        conciliacao[m.id] = {
            'candidatas': candidatas,
            'sugerida': _sugerir_transacao(m, candidatas),
        }

    return render_template(
        'conta_movimentacoes.html',
        conta=conta,
        movimentos=movimentos,
        inicio=inicio,
        fim=fim,
        total_creditos=total_creditos,
        total_debitos=total_debitos,
        saldo_periodo=total_creditos - total_debitos,
        conciliacao=conciliacao,
    )


@app.route('/contas/<int:id>/movimentacoes/<int:mov_id>/vincular', methods=['POST'])
def contas_movimentacoes_vincular(id, mov_id):
    mov = da_empresa(ContaMovimentacao).filter_by(id=mov_id, conta_bancaria_id=id).first_or_404()
    destino = url_for('contas_movimentacoes', id=id, inicio=request.args.get('inicio', ''), fim=request.args.get('fim', ''))

    transacao_id = request.form.get('transacao_id', '')
    if not transacao_id.isdigit():
        flash('Selecione um lançamento para vincular a esta movimentação.', 'erro')
        return redirect(destino)

    transacao = da_empresa(Transacao).get(int(transacao_id))
    if not transacao:
        flash('Lançamento não encontrado.', 'erro')
        return redirect(destino)

    tipo_esperado = 'Receber' if mov.tipo == 'CREDITO' else 'Pagar'
    if transacao.tipo != tipo_esperado:
        flash('Esse lançamento não é compatível com o tipo do movimento (crédito → Receber, débito → Pagar).', 'erro')
        return redirect(destino)

    outra_movimentacao = da_empresa(ContaMovimentacao).filter(
        ContaMovimentacao.transacao_id == transacao.id,
        ContaMovimentacao.id != mov.id,
    ).first()
    if outra_movimentacao:
        flash('Esse lançamento já está vinculado a outra movimentação do extrato.', 'erro')
        return redirect(destino)

    mov.transacao_id = transacao.id
    baixado_agora = transacao.status == 'Pendente'
    if baixado_agora:
        transacao.status = 'Concluído'
        transacao.data_pagamento = mov.data
    if not transacao.conta_bancaria_id:
        transacao.conta_bancaria_id = id
    db.session.commit()

    mensagem = f'Movimentação vinculada ao lançamento "{transacao.descricao}"'
    mensagem += ' — baixa dada automaticamente com a data do extrato.' if baixado_agora else '.'
    flash(mensagem, 'sucesso')
    return redirect(destino)


@app.route('/contas/<int:id>/movimentacoes/<int:mov_id>/desvincular', methods=['POST'])
def contas_movimentacoes_desvincular(id, mov_id):
    mov = da_empresa(ContaMovimentacao).filter_by(id=mov_id, conta_bancaria_id=id).first_or_404()
    mov.transacao_id = None
    db.session.commit()
    flash('Vínculo removido. O lançamento mantém o status atual — reabra-o manualmente se necessário.', 'sucesso')
    return redirect(url_for('contas_movimentacoes', id=id, inicio=request.args.get('inicio', ''), fim=request.args.get('fim', '')))


@app.route('/contas/<int:id>/importar-ofx', methods=['POST'])
def contas_importar_ofx(id):
    conta = buscar_ou_404(ContaBancaria, id)
    arquivo = request.files.get('ofx_arquivo')
    if not arquivo or not arquivo.filename:
        flash('Selecione um arquivo OFX para importar.', 'erro')
        return redirect(url_for('contas_movimentacoes', id=id))

    extensao = arquivo.filename.rsplit('.', 1)[-1].lower() if '.' in arquivo.filename else ''
    if extensao not in EXTENSOES_OFX_PERMITIDAS:
        flash('Formato não suportado. Envie um arquivo .ofx ou .qfx.', 'erro')
        return redirect(url_for('contas_movimentacoes', id=id))

    try:
        extrato = ofx_parser.parse_ofx(arquivo.read())
    except ofx_parser.OfxInvalido as exc:
        flash(f'Não foi possível ler o arquivo: {exc}', 'erro')
        return redirect(url_for('contas_movimentacoes', id=id))

    # Só preenche o que ainda estiver em branco — não sobrescreve dados que
    # o usuário já tenha corrigido manualmente no cadastro da conta.
    if extrato.conta.banco and not conta.banco:
        conta.banco = extrato.conta.banco
    if extrato.conta.agencia and not conta.agencia:
        conta.agencia = extrato.conta.agencia
    if extrato.conta.conta and not conta.conta_numero:
        conta.conta_numero = extrato.conta.conta
    if extrato.saldo is not None:
        conta.saldo = extrato.saldo
        conta.saldo_data = extrato.saldo_data

    novos = 0
    for mov in extrato.movimentos:
        existe = da_empresa(ContaMovimentacao).filter_by(conta_bancaria_id=id, fitid=mov.fitid).first()
        if existe:
            continue
        db.session.add(novo_registro(
            ContaMovimentacao,
            conta_bancaria_id=id,
            fitid=mov.fitid,
            data=mov.data,
            descricao=mov.descricao,
            valor=mov.valor,
            tipo=mov.tipo,
        ))
        novos += 1
    db.session.commit()

    if novos:
        flash(f'{novos} movimentação(ões) importada(s) com sucesso.', 'sucesso')
    else:
        flash('Nenhuma movimentação nova encontrada neste arquivo (já haviam sido importadas).', 'sucesso')

    if extrato.movimentos:
        datas = [m.data for m in extrato.movimentos]
        return redirect(url_for(
            'contas_movimentacoes', id=id,
            inicio=min(datas).isoformat(), fim=max(datas).isoformat(),
        ))
    return redirect(url_for('contas_movimentacoes', id=id))


@app.route('/contas/<int:id>/limpar-movimentacoes', methods=['POST'])
def contas_limpar_movimentacoes(id):
    buscar_ou_404(ContaBancaria, id)
    total = da_empresa(ContaMovimentacao).filter_by(conta_bancaria_id=id).delete()
    db.session.commit()
    flash(f'{total} movimentação(ões) removida(s). Você pode importar o OFX novamente.', 'sucesso')
    return redirect(url_for('contas_movimentacoes', id=id))


@app.route('/contas/<int:id>/movimentacoes/exportar')
def contas_movimentacoes_exportar(id):
    conta = buscar_ou_404(ContaBancaria, id)
    inicio, fim = _periodo_movimentacoes(request.args)
    movimentos = _movimentacoes_do_periodo(id, inicio, fim)

    cabecalho = ['Data', 'Descrição', 'Tipo', 'Valor']
    nome_arquivo = f'movimentacoes_{conta.nome}_{inicio.isoformat()}_a_{fim.isoformat()}'
    nome_arquivo = re.sub(r'[^A-Za-z0-9_-]+', '_', nome_arquivo)

    formato = request.args.get('formato', 'csv')
    if formato == 'xlsx':
        linhas = [[m.data.strftime('%d/%m/%Y'), m.descricao, m.tipo.capitalize(), m.valor] for m in movimentos]
        return _exportar_xlsx_generico(cabecalho, linhas, nome_arquivo, 'Movimentações')

    linhas = [
        [m.data.strftime('%d/%m/%Y'), m.descricao, m.tipo.capitalize(), f'{m.valor:.2f}'.replace('.', ',')]
        for m in movimentos
    ]
    return _exportar_csv_generico(cabecalho, linhas, nome_arquivo)


def parse_data(valor):
    try:
        return datetime.strptime(valor, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None


def competencia(data):
    """Converte uma data no formato AAAAMM (inteiro) usado para controlar a geração mensal."""
    return data.year * 100 + data.month


def proxima_competencia(comp):
    ano, mes = divmod(comp, 100)
    mes += 1
    if mes > 12:
        mes = 1
        ano += 1
    return ano * 100 + mes


def competencia_anterior(comp):
    ano, mes = divmod(comp, 100)
    mes -= 1
    if mes < 1:
        mes = 12
        ano -= 1
    return ano * 100 + mes


def data_da_competencia(comp, dia):
    ano, mes = divmod(comp, 100)
    ultimo_dia_do_mes = calendar.monthrange(ano, mes)[1]
    return date(ano, mes, min(dia, ultimo_dia_do_mes))


def gerar_lancamentos_recorrentes(empresa_id):
    """Cria os lançamentos de cada mês em que uma recorrência ativa da empresa
    ainda não gerou um lançamento, até o mês atual (coloca em dia quem ficou
    um tempo sem entrar). Idempotente: pode ser chamada quantas vezes for
    preciso sem duplicar lançamentos.
    """
    competencia_atual = competencia(datetime.now().date())
    total_gerado = 0

    for tpl in LancamentoRecorrente.query.filter_by(empresa_id=empresa_id, ativo=True).all():
        proxima = (
            proxima_competencia(tpl.ultima_geracao_mes)
            if tpl.ultima_geracao_mes
            else competencia_atual
        )
        while proxima <= competencia_atual:
            vencimento = data_da_competencia(proxima, tpl.dia_vencimento)
            ja_existe = Transacao.query.filter_by(
                empresa_id=empresa_id, recorrente_id=tpl.id, data_vencimento=vencimento
            ).first()
            if not ja_existe:
                db.session.add(Transacao(
                    empresa_id=empresa_id,
                    tipo=tpl.tipo,
                    descricao=tpl.descricao,
                    valor=tpl.valor,
                    data_vencimento=vencimento,
                    fornecedor_id=tpl.fornecedor_id,
                    cliente_id=tpl.cliente_id,
                    categoria_id=tpl.categoria_id,
                    conta_bancaria_id=tpl.conta_bancaria_id,
                    recorrente_id=tpl.id,
                ))
                total_gerado += 1
            tpl.ultima_geracao_mes = proxima
            proxima = proxima_competencia(proxima)

    if total_gerado:
        db.session.commit()
    return total_gerado


def validar_recorrente(form):
    """Valida os campos de um modelo de lançamento recorrente."""
    tipo = form.get('tipo', '')
    descricao = form.get('descricao', '').strip()
    valor_str = form.get('valor', '')
    dia_str = form.get('dia_vencimento', '')

    if tipo not in TIPOS_VALIDOS:
        return None, 'Tipo inválido.'

    if not descricao or len(descricao) > 100:
        return None, 'Descrição obrigatória (até 100 caracteres).'

    try:
        valor = float(valor_str)
    except (TypeError, ValueError):
        return None, 'Valor inválido.'

    if valor <= 0:
        return None, 'O valor deve ser maior que zero.'

    try:
        dia_vencimento = int(dia_str)
    except (TypeError, ValueError):
        return None, 'Dia de vencimento inválido.'

    if not 1 <= dia_vencimento <= 31:
        return None, 'Dia de vencimento deve estar entre 1 e 31.'

    fornecedor_id = None
    cliente_id = None

    if tipo == 'Pagar':
        fornecedor_id = _validar_id_existente(form.get('fornecedor_id', ''), Fornecedor)
        if fornecedor_id is None:
            return None, 'Selecione um fornecedor.'
    else:
        cliente_id = _validar_id_existente(form.get('cliente_id', ''), Cliente)
        if cliente_id is None:
            return None, 'Selecione um cliente.'

    categoria_id, categoria_ok = _validar_categoria_opcional(form.get('categoria_id', ''), tipo)
    if not categoria_ok:
        return None, 'Categoria inválida (verifique se ela é do tipo certo: Pagar/Receber).'

    conta_bancaria_id, conta_ok = _validar_id_opcional(form.get('conta_bancaria_id', ''), ContaBancaria)
    if not conta_ok:
        return None, 'Conta bancária inválida.'

    dados = {
        'tipo': tipo,
        'descricao': descricao,
        'valor': valor,
        'dia_vencimento': dia_vencimento,
        'fornecedor_id': fornecedor_id,
        'cliente_id': cliente_id,
        'categoria_id': categoria_id,
        'conta_bancaria_id': conta_bancaria_id,
        'ativo': form.get('ativo') == 'on',
    }
    return dados, None


# ------------------------------------------------------------------ #
# Início — visão geral do negócio
# ------------------------------------------------------------------ #

MESES_NO_GRAFICO = 6
MESES_ABREVIADOS = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez']


def _limites_do_mes(referencia):
    """Primeiro e último dia do mês da data informada."""
    primeiro = referencia.replace(day=1)
    ultimo = primeiro.replace(day=calendar.monthrange(primeiro.year, primeiro.month)[1])
    return primeiro, ultimo


def _mes_anterior(referencia):
    primeiro = referencia.replace(day=1)
    return (primeiro - timedelta(days=1)).replace(day=1)


def resultado_do_mes(referencia):
    """Entradas, saídas e resultado de um mês, por data de vencimento.

    Usa competência (vencimento) e não caixa (pagamento) para bater com o
    Relatório por Período e para não depender de o usuário ter dado baixa em
    tudo — senão um mês sem baixas apareceria como prejuízo que não existe.
    """
    inicio, fim = _limites_do_mes(referencia)
    transacoes = da_empresa(Transacao).filter(
        Transacao.data_vencimento >= inicio,
        Transacao.data_vencimento <= fim,
    ).all()

    entradas = sum(t.valor for t in transacoes if t.tipo == 'Receber')
    saidas = sum(t.valor for t in transacoes if t.tipo == 'Pagar')
    resultado = entradas - saidas

    return {
        'inicio': inicio,
        'fim': fim,
        'rotulo': f'{inicio.month:02d}/{inicio.year}',
        'rotulo_curto': MESES_ABREVIADOS[inicio.month - 1],
        'entradas': entradas,
        'saidas': saidas,
        'resultado': resultado,
        # Margem sobre o faturamento: sem entradas no mês não existe margem
        # (dividir por zero diria "prejuízo de infinito%").
        'margem': (resultado / entradas * 100) if entradas else None,
    }


def _variacao(atual, anterior):
    """Variação percentual entre dois valores; None quando não dá para comparar."""
    if not anterior:
        return None
    return (atual - anterior) / abs(anterior) * 100


# ------------------------------------------------------------------ #
# Entrada e saída do sistema
# ------------------------------------------------------------------ #

@app.route('/login', methods=['GET', 'POST'])
def login():
    if auth.usuario_logado() and request.method == 'GET':
        return redirect(url_for('inicio'))

    if request.method == 'POST':
        usuario, erro = auth.autenticar(request.form.get('email'), request.form.get('senha'))
        if erro:
            flash(erro, 'erro')
            return redirect(url_for('login'))

        motivo = auth.motivo_de_bloqueio(usuario)
        auth.entrar(usuario)
        if motivo:
            return redirect(url_for('sem_acesso', motivo=motivo))

        destino = request.form.get('proximo', '')
        # Só aceita caminho interno: um "próximo" apontando para fora viraria
        # um redirecionamento aberto, útil para golpe de phishing.
        if destino.startswith('/') and not destino.startswith('//'):
            return redirect(destino)
        return redirect(url_for('inicio'))

    return render_template('login.html', proximo=request.args.get('proximo', ''))


@app.route('/logout')
def logout():
    auth.sair()
    flash('Você saiu do sistema.', 'sucesso')
    return redirect(url_for('login'))


@app.route('/sem-acesso')
def sem_acesso():
    """Explica por que o acesso está barrado, em vez de só mandar para o login."""
    usuario = auth.usuario_logado()
    if usuario is None:
        return redirect(url_for('login'))

    motivo = auth.motivo_de_bloqueio(usuario) or request.args.get('motivo', '')
    if not motivo:
        return redirect(url_for('inicio'))

    return render_template('sem_acesso.html', usuario=usuario, motivo=motivo,
                           empresa=usuario.empresa,
                           # Só faz sentido oferecer pagamento a quem está
                           # barrado por assinatura: quem o admin bloqueou na
                           # mão não se desbloqueia pagando.
                           pode_pagar=(motivo == 'assinatura'),
                           link_suporte=link_do_suporte(usuario))


def link_do_suporte(usuario=None):
    """Conversa no WhatsApp com o suporte, já com a pessoa identificada.

    Devolve None quando o administrador ainda não cadastrou o número — é
    melhor não mostrar o botão do que mandar a pessoa para um número errado.
    """
    numero = re.sub(r'\D', '', fiscal_sync.sistema_get('suporte_whatsapp'))
    if not numero:
        return None

    # Número brasileiro digitado sem o código do país ganha o 55.
    if len(numero) in (10, 11):
        numero = f'55{numero}'

    if usuario is not None:
        empresa = usuario.empresa.nome if usuario.empresa else 'sem empresa'
        texto = (f'Olá! Sou {usuario.nome} ({usuario.email}), da {empresa}. '
                 f'Preciso de ajuda com o acesso ao sistema.')
    else:
        texto = 'Olá! Preciso de ajuda com o sistema.'

    return f'https://wa.me/{numero}?text={quote(texto)}'


# ------------------------------------------------------------------ #
# Autocadastro
# ------------------------------------------------------------------ #

UFS = ('AC', 'AL', 'AM', 'AP', 'BA', 'CE', 'DF', 'ES', 'GO', 'MA', 'MG', 'MS',
       'MT', 'PA', 'PB', 'PE', 'PI', 'PR', 'RJ', 'RN', 'RO', 'RR', 'RS', 'SC',
       'SE', 'SP', 'TO')

SENHA_MINIMA = 8

# Quantas contas podem sair do mesmo endereço por hora. O cadastro é aberto a
# qualquer um, então sem um teto um robô encheria o banco em minutos.
LIMITE_CADASTROS_POR_HORA = 5
_cadastros_recentes = defaultdict(list)
_trava_cadastros = threading.Lock()


def _pode_cadastrar(ip):
    """Se este endereço ainda pode criar conta nesta hora."""
    agora = time.time()
    with _trava_cadastros:
        recentes = [t for t in _cadastros_recentes[ip] if agora - t < 3600]
        _cadastros_recentes[ip] = recentes
        return len(recentes) < LIMITE_CADASTROS_POR_HORA


def _registrar_cadastro(ip):
    """Marca uma conta efetivamente criada.

    Só conta o que virou conta: quem erra o formulário algumas vezes não é
    o abuso de que o limite trata, e barrá-lo deixaria a pessoa travada por
    uma hora por ter digitado o CEP errado.
    """
    with _trava_cadastros:
        _cadastros_recentes[ip].append(time.time())


def validar_autocadastro(form):
    """Confere os dados do cadastro aberto. Retorna (dados, erro).

    O papel vem de um formulário público, então é escolhido de uma lista
    fechada: aceitar o que veio no POST deixaria qualquer visitante criar
    uma conta de administrador.
    """
    papel = form.get('papel', '')
    if papel not in (PAPEL_USUARIO, PAPEL_CONTADOR):
        return None, 'Escolha se a conta é de cliente ou de contador.'

    nome = form.get('nome', '').strip()
    if not nome or len(nome) > 150:
        return None, 'Informe o seu nome completo (até 150 caracteres).'

    email = form.get('email', '').strip().lower()
    if not email or len(email) > 150 or not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email):
        return None, 'E-mail inválido.'
    if not auth.email_disponivel(email):
        return None, 'Já existe uma conta com esse e-mail. Tente entrar ou use outro endereço.'

    senha = form.get('senha', '')
    if len(senha) < SENHA_MINIMA:
        return None, f'A senha precisa ter pelo menos {SENHA_MINIMA} caracteres.'
    if senha != form.get('senha_confirmacao', ''):
        return None, 'As duas senhas digitadas não são iguais.'

    documento = re.sub(r'\D', '', form.get('cpf_cnpj', ''))
    if len(documento) not in (11, 14):
        return None, 'CNPJ/CPF inválido (informe 11 dígitos para CPF ou 14 para CNPJ).'

    endereco = form.get('endereco', '').strip()
    if not endereco or len(endereco) > 200:
        return None, 'Endereço obrigatório (até 200 caracteres).'

    cep = re.sub(r'\D', '', form.get('cep', ''))
    if len(cep) != 8:
        return None, 'CEP inválido (informe os 8 dígitos).'

    cidade = form.get('cidade', '').strip()
    if not cidade or len(cidade) > 80:
        return None, 'Cidade obrigatória.'

    uf = form.get('uf', '').strip().upper()
    if uf not in UFS:
        return None, 'Escolha um estado (UF) válido.'

    telefone = form.get('telefone', '').strip()
    if len(telefone) > 20:
        return None, 'Telefone muito longo (máx. 20 caracteres).'

    # O contador não assina: quem paga é o cliente que o indicar. Por isso a
    # forma de pagamento só é lida (e cobrada) no cadastro de cliente.
    pagamento = form.get('pagamento', 'depois')
    if papel == PAPEL_USUARIO and pagamento not in ('agora', 'depois'):
        return None, 'Escolha se quer pagar agora ou depois.'

    empresa_nome = form.get('empresa_nome', '').strip()
    if papel == PAPEL_USUARIO:
        if not empresa_nome or len(empresa_nome) > 150:
            return None, 'Informe o nome da sua empresa (até 150 caracteres).'

    return {
        'papel': papel,
        'nome': nome,
        'email': email,
        'senha': senha,
        'cpf_cnpj': documento,
        'endereco': endereco,
        'cep': cep,
        'cidade': cidade,
        'uf': uf,
        'telefone': telefone or None,
        'pagamento': pagamento,
        'empresa_nome': empresa_nome,
    }, None


@app.route('/cadastro', methods=['GET', 'POST'])
def cadastro():
    """Criação de conta pelo próprio interessado, sem passar pelo admin."""
    if auth.usuario_logado():
        return redirect(url_for('inicio'))

    if request.method == 'GET':
        return render_template('criar_conta.html', ufs=UFS, form={},
                               valor=asaas.valor_mensalidade(),
                               cobranca_disponivel=asaas.configurado(),
                               dias=DIAS_POR_PAGAMENTO)

    if not _pode_cadastrar(request.remote_addr or 'desconhecido'):
        flash('Muitas contas criadas deste endereço. Tente novamente daqui a pouco.', 'erro')
        return redirect(url_for('cadastro'))

    dados, erro = validar_autocadastro(request.form)
    if erro:
        flash(erro, 'erro')
        # Devolve o que já foi digitado para a pessoa não recomeçar do zero.
        return render_template('criar_conta.html', ufs=UFS, form=request.form,
                               valor=asaas.valor_mensalidade(),
                               cobranca_disponivel=asaas.configurado(),
                               dias=DIAS_POR_PAGAMENTO), 400

    origem = request.remote_addr or 'desconhecido'

    if dados['papel'] == PAPEL_CONTADOR:
        contador = auth.criar_usuario(
            dados['email'], dados['senha'], dados['nome'], papel=PAPEL_CONTADOR,
            cpf_cnpj=dados['cpf_cnpj'], endereco=dados['endereco'], cep=dados['cep'],
            cidade=dados['cidade'], uf=dados['uf'], telefone=dados['telefone'],
        )
        db.session.commit()
        _registrar_cadastro(origem)
        auth.entrar(contador)
        flash('Conta criada! Peça ao seu cliente para informar o seu e-mail na aba '
              'Contabilidade do sistema dele — é isso que libera o acesso.', 'sucesso')
        return redirect(url_for('inicio'))

    # Cliente: nasce com a empresa, mas sem assinatura — o acesso às telas só
    # abre quando um pagamento for confirmado.
    empresa = Empresa(
        nome=dados['empresa_nome'],
        cnpj=dados['cpf_cnpj'],
        endereco=dados['endereco'],
        cep=dados['cep'],
        cidade=dados['cidade'],
        uf=dados['uf'],
        telefone=dados['telefone'],
        email=dados['email'],
    )
    db.session.add(empresa)
    db.session.flush()  # precisa do id para vincular o usuário

    usuario = auth.criar_usuario(
        dados['email'], dados['senha'], dados['nome'], papel=PAPEL_USUARIO,
        empresa=empresa, cpf_cnpj=dados['cpf_cnpj'], telefone=dados['telefone'],
    )
    db.session.commit()
    _registrar_cadastro(origem)

    # Entra já logado em todos os casos. Mandar de volta para o login deixava
    # a pessoa sem entender o que aconteceu com a conta que acabou de criar;
    # na tela de bloqueio ela vê a situação e os botões de pagar e de suporte.
    auth.entrar(usuario)

    if dados['pagamento'] == 'depois':
        flash('Conta criada! O acesso às telas abre quando o pagamento for '
              'confirmado — é só clicar em "Realizar pagamento" quando quiser.',
              'sucesso')
        return redirect(url_for('sem_acesso'))

    try:
        _, link = asaas.criar_cobranca(empresa, data_path('.chave_secreta'),
                                       url_base=os.environ.get('ERP_ASAAS_URL'))
    except asaas.ErroAsaas as exc:
        flash(f'Conta criada, mas não deu para gerar a cobrança agora: {exc}', 'erro')
        return redirect(url_for('sem_acesso'))

    return redirect(link)


# ------------------------------------------------------------------ #
# Assinatura (pagar depois, a partir da tela de bloqueio)
# ------------------------------------------------------------------ #

def _empresa_para_cobrar():
    """Empresa do usuário logado, quando ele é um cliente barrado por assinatura."""
    usuario = auth.usuario_logado()
    if usuario is None or usuario.papel != PAPEL_USUARIO or usuario.empresa is None:
        return None
    if not usuario.ativo:  # bloqueio do admin não se resolve pagando
        return None
    return usuario.empresa


@app.route('/assinatura')
def assinatura():
    return redirect(url_for('sem_acesso'))


@app.route('/assinatura/cobrar', methods=['POST'])
def assinatura_cobrar():
    """Gera a mensalidade e manda a pessoa para a página de pagamento."""
    empresa = _empresa_para_cobrar()
    if empresa is None:
        flash('Essa conta não tem assinatura para pagar.', 'erro')
        return redirect(url_for('sem_acesso'))

    try:
        _, link = asaas.criar_cobranca(empresa, data_path('.chave_secreta'),
                                       url_base=os.environ.get('ERP_ASAAS_URL'))
    except asaas.ErroAsaas as exc:
        flash(str(exc), 'erro')
        return redirect(url_for('sem_acesso'))

    return redirect(link)


@app.route('/assinatura/conferir', methods=['POST'])
def assinatura_conferir():
    """"Já paguei": confere na hora, sem esperar a rodada automática."""
    empresa = _empresa_para_cobrar()
    if empresa is None:
        return redirect(url_for('sem_acesso'))

    try:
        creditados = asaas.verificar_empresa(empresa, data_path('.chave_secreta'),
                                             url_base=os.environ.get('ERP_ASAAS_URL'))
    except asaas.ErroAsaas as exc:
        flash(str(exc), 'erro')
        return redirect(url_for('sem_acesso'))

    if creditados:
        flash(f'Pagamento confirmado! Acesso liberado até '
              f'{empresa.assinatura_ate.strftime("%d/%m/%Y")}.', 'sucesso')
        return redirect(url_for('inicio'))

    flash('Ainda não encontramos o pagamento. Se você acabou de pagar, o banco '
          'pode levar alguns minutos para avisar o Asaas.', 'erro')
    return redirect(url_for('sem_acesso'))


@app.route('/')
def inicio():
    usuario = auth.usuario_logado()
    # Admin e contador não têm dados financeiros próprios: cada um vai para
    # a sua área em vez de ver um painel vazio.
    if usuario.eh_admin:
        return redirect(url_for('admin_painel'))
    if usuario.eh_contador and auth.empresa_atual() is None:
        return redirect(url_for('contador_clientes'))

    hoje = datetime.now().date()
    mes_atual = resultado_do_mes(hoje)
    mes_passado = resultado_do_mes(_mes_anterior(hoje))

    # Série dos últimos meses, do mais antigo para o mais recente
    serie = []
    referencia = hoje
    for _ in range(MESES_NO_GRAFICO):
        serie.append(resultado_do_mes(referencia))
        referencia = _mes_anterior(referencia)
    serie.reverse()
    teto = max([max(m['entradas'], m['saidas']) for m in serie] or [0])

    # Caixa: o que existe em conta contra o que já está comprometido
    contas_com_saldo = da_empresa(ContaBancaria).filter(ContaBancaria.saldo.isnot(None)).all()
    saldo_contas = sum(c.saldo for c in contas_com_saldo) if contas_com_saldo else None
    saldo_data = max((c.saldo_data for c in contas_com_saldo if c.saldo_data), default=None)

    pendentes = da_empresa(Transacao).filter_by(status='Pendente').all()
    a_pagar = sum(t.valor for t in pendentes if t.tipo == 'Pagar')
    a_receber = sum(t.valor for t in pendentes if t.tipo == 'Receber')
    atrasados = [t for t in pendentes if t.tipo == 'Pagar' and t.data_vencimento < hoje]

    comprometido = None
    if saldo_contas and saldo_contas > 0:
        comprometido = min(a_pagar / saldo_contas * 100, 100)

    return render_template(
        'inicio.html',
        hoje=hoje,
        mes_atual=mes_atual,
        mes_passado=mes_passado,
        serie=serie,
        teto=teto,
        variacao_entradas=_variacao(mes_atual['entradas'], mes_passado['entradas']),
        variacao_saidas=_variacao(mes_atual['saidas'], mes_passado['saidas']),
        variacao_resultado=_variacao(mes_atual['resultado'], mes_passado['resultado']),
        saldo_contas=saldo_contas,
        saldo_data=saldo_data,
        a_pagar=a_pagar,
        a_receber=a_receber,
        sobra=(saldo_contas - a_pagar) if saldo_contas is not None else None,
        comprometido=comprometido,
        atrasados=len(atrasados),
        valor_atrasado=sum(t.valor for t in atrasados),
    )


@app.route('/lancamentos')
def lancamentos():
    todas_transacoes = da_empresa(Transacao).all()
    total_receber = sum(t.valor for t in todas_transacoes if t.tipo == 'Receber' and t.status == 'Pendente')
    total_pagar = sum(t.valor for t in todas_transacoes if t.tipo == 'Pagar' and t.status == 'Pendente')
    sem_categoria_count = sum(1 for t in todas_transacoes if t.categoria_id is None)

    contas_com_saldo = da_empresa(ContaBancaria).filter(ContaBancaria.saldo.isnot(None)).all()
    saldo_contas = sum(c.saldo for c in contas_com_saldo) if contas_com_saldo else None
    saldo_contas_data = max((c.saldo_data for c in contas_com_saldo if c.saldo_data), default=None)

    busca = request.args.get('busca', '').strip()
    filtro_tipo = request.args.get('filtro_tipo', 'Todos')
    filtro_status = request.args.get('filtro_status', 'Todos')
    filtro_categoria = request.args.get('filtro_categoria', '')
    filtro_conta = request.args.get('filtro_conta', '')

    # Período por vencimento. Os dois campos são opcionais e funcionam soltos:
    # só a data inicial mostra "daqui pra frente", só a final "até tal dia".
    filtro_inicio = parse_data(request.args.get('filtro_inicio', ''))
    filtro_fim = parse_data(request.args.get('filtro_fim', ''))
    if filtro_inicio and filtro_fim and filtro_inicio > filtro_fim:
        filtro_inicio, filtro_fim = filtro_fim, filtro_inicio

    query = da_empresa(Transacao)
    if busca:
        termo = f'%{busca}%'
        query = query.outerjoin(Transacao.fornecedor).outerjoin(Transacao.cliente).filter(db.or_(
            Transacao.descricao.ilike(termo),
            Fornecedor.nome.ilike(termo),
            Cliente.nome.ilike(termo),
        ))
    if filtro_tipo in TIPOS_VALIDOS:
        query = query.filter(Transacao.tipo == filtro_tipo)
    if filtro_status in STATUS_VALIDOS:
        query = query.filter(Transacao.status == filtro_status)
    if filtro_categoria == 'sem':
        query = query.filter(Transacao.categoria_id.is_(None))
    elif filtro_categoria.isdigit():
        query = query.filter(Transacao.categoria_id == int(filtro_categoria))
    if filtro_conta.isdigit():
        query = query.filter(Transacao.conta_bancaria_id == int(filtro_conta))
    if filtro_inicio:
        query = query.filter(Transacao.data_vencimento >= filtro_inicio)
    if filtro_fim:
        query = query.filter(Transacao.data_vencimento <= filtro_fim)

    transacoes = query.order_by(Transacao.data_vencimento).all()

    return render_template(
        'lancamentos.html',
        transacoes=transacoes,
        total_receber=total_receber,
        total_pagar=total_pagar,
        sem_categoria_count=sem_categoria_count,
        saldo_contas=saldo_contas,
        saldo_contas_data=saldo_contas_data,
        fornecedores=da_empresa(Fornecedor).order_by(Fornecedor.nome).all(),
        clientes=da_empresa(Cliente).order_by(Cliente.nome).all(),
        categorias=da_empresa(Categoria).order_by(Categoria.nome).all(),
        categorias_pagar=da_empresa(Categoria).filter_by(tipo='Pagar').order_by(Categoria.nome).all(),
        categorias_receber=da_empresa(Categoria).filter_by(tipo='Receber').order_by(Categoria.nome).all(),
        contas_bancarias=da_empresa(ContaBancaria).order_by(ContaBancaria.nome).all(),
        hoje=datetime.now().date(),
        busca=busca,
        filtro_tipo=filtro_tipo,
        filtro_status=filtro_status,
        filtro_categoria=filtro_categoria,
        filtro_conta=filtro_conta,
        filtro_inicio=filtro_inicio,
        filtro_fim=filtro_fim,
    )


@app.route('/adicionar', methods=['POST'])
def adicionar():
    dados, erro = validar_transacao(request.form)
    if erro:
        flash(erro, 'erro')
        return redirect(url_for('lancamentos'))

    db.session.add(novo_registro(Transacao, **dados))
    db.session.commit()
    flash('Lançamento adicionado com sucesso.', 'sucesso')
    return redirect(url_for('lancamentos'))


@app.route('/editar/<int:id>', methods=['GET', 'POST'])
def editar(id):
    transacao = buscar_ou_404(Transacao, id)

    if request.method == 'POST':
        dados, erro = validar_transacao(request.form)
        if erro:
            flash(erro, 'erro')
            return redirect(url_for('editar', id=id))

        status = request.form.get('status', '')
        if status not in STATUS_VALIDOS:
            flash('Status inválido.', 'erro')
            return redirect(url_for('editar', id=id))

        data_pagamento_str = request.form.get('data_pagamento', '').strip()
        data_pagamento = None
        if data_pagamento_str:
            data_pagamento = parse_data(data_pagamento_str)
            if not data_pagamento:
                flash('Data de pagamento inválida.', 'erro')
                return redirect(url_for('editar', id=id))

        if request.form.get('remover_anexo') == 'on' and transacao.anexo_arquivo:
            _apagar_anexo(transacao.anexo_arquivo)
            transacao.anexo_arquivo = None
            transacao.anexo_nome_original = None

        arquivo = request.files.get('anexo')
        if arquivo and arquivo.filename:
            extensao = arquivo.filename.rsplit('.', 1)[-1].lower() if '.' in arquivo.filename else ''
            if extensao not in EXTENSOES_ANEXO_PERMITIDAS:
                flash('Formato de anexo não suportado. Use PDF, PNG, JPG, GIF ou WEBP.', 'erro')
                return redirect(url_for('editar', id=id))

            if transacao.anexo_arquivo:
                _apagar_anexo(transacao.anexo_arquivo)

            nome_original = secure_filename(arquivo.filename)
            nome_armazenado = f'{transacao.id}_{uuid.uuid4().hex[:8]}_{nome_original}'
            arquivo.save(os.path.join(anexos_dir(), nome_armazenado))
            transacao.anexo_arquivo = nome_armazenado
            transacao.anexo_nome_original = nome_original

        transacao.tipo = dados['tipo']
        transacao.descricao = dados['descricao']
        transacao.valor = dados['valor']
        transacao.data_vencimento = dados['data_vencimento']
        transacao.status = status
        transacao.data_pagamento = data_pagamento
        transacao.fornecedor_id = dados['fornecedor_id']
        transacao.cliente_id = dados['cliente_id']
        transacao.categoria_id = dados['categoria_id']
        transacao.conta_bancaria_id = dados['conta_bancaria_id']
        db.session.commit()
        flash('Lançamento atualizado com sucesso.', 'sucesso')
        return redirect(url_for('lancamentos'))

    return render_template(
        'editar.html',
        t=transacao,
        fornecedores=da_empresa(Fornecedor).order_by(Fornecedor.nome).all(),
        clientes=da_empresa(Cliente).order_by(Cliente.nome).all(),
        categorias_pagar=da_empresa(Categoria).filter_by(tipo='Pagar').order_by(Categoria.nome).all(),
        categorias_receber=da_empresa(Categoria).filter_by(tipo='Receber').order_by(Categoria.nome).all(),
        contas_bancarias=da_empresa(ContaBancaria).order_by(ContaBancaria.nome).all(),
    )


@app.route('/excluir/<int:id>', methods=['POST'])
def excluir(id):
    transacao = buscar_ou_404(Transacao, id)
    if transacao.anexo_arquivo:
        _apagar_anexo(transacao.anexo_arquivo)
    # Uma nota fiscal vinculada a este lançamento volta a permitir "Gerar Lançamento"
    da_empresa(NotaServico).filter_by(transacao_id=id).update({'transacao_id': None})
    da_empresa(NotaEletronica).filter_by(transacao_id=id).update({'transacao_id': None})
    db.session.delete(transacao)
    db.session.commit()
    flash('Lançamento excluído.', 'sucesso')
    return redirect(url_for('lancamentos'))


def _destino_voltar():
    """Rota de retorno pós-ação (permite dar baixa a partir dos painéis de notas)."""
    voltar = request.form.get('voltar', '')
    if voltar.startswith('/') and not voltar.startswith('//'):
        return voltar
    return url_for('lancamentos')


@app.route('/concluir/<int:id>', methods=['POST'])
def concluir(id):
    transacao = buscar_ou_404(Transacao, id)

    data_pagamento = parse_data(request.form.get('data_pagamento', '').strip())
    if not data_pagamento:
        flash('Informe a data em que o pagamento foi realizado para dar baixa.', 'erro')
        return redirect(_destino_voltar())

    transacao.status = 'Concluído'
    transacao.data_pagamento = data_pagamento
    db.session.commit()
    return redirect(_destino_voltar())


@app.route('/reabrir/<int:id>', methods=['POST'])
def reabrir(id):
    transacao = buscar_ou_404(Transacao, id)
    transacao.status = 'Pendente'
    transacao.data_pagamento = None
    db.session.commit()
    return redirect(url_for('lancamentos'))


@app.route('/anexos/<int:id>')
def baixar_anexo(id):
    transacao = buscar_ou_404(Transacao, id)
    if not transacao.anexo_arquivo:
        flash('Este lançamento não possui anexo.', 'erro')
        return redirect(url_for('lancamentos'))
    return send_from_directory(
        anexos_dir(),
        transacao.anexo_arquivo,
        download_name=transacao.anexo_nome_original,
    )


def periodo_dos_parametros(args):
    """Lê inicio/fim/status/categoria/conta da querystring, com padrão = mês atual completo."""
    hoje = datetime.now().date()
    inicio = parse_data(args.get('inicio', '')) or hoje.replace(day=1)
    fim = parse_data(args.get('fim', '')) or hoje
    if inicio > fim:
        inicio, fim = fim, inicio

    status_filtro = args.get('status', 'Todos')
    if status_filtro not in STATUS_VALIDOS:
        status_filtro = 'Todos'

    categoria_filtro = args.get('categoria', '')
    if categoria_filtro != 'sem' and not categoria_filtro.isdigit():
        categoria_filtro = ''

    conta_filtro = args.get('conta', '')
    if not conta_filtro.isdigit():
        conta_filtro = ''

    return inicio, fim, status_filtro, categoria_filtro, conta_filtro


def transacoes_do_periodo(inicio, fim, status_filtro, categoria_filtro='', conta_filtro=''):
    query = da_empresa(Transacao).filter(
        Transacao.data_vencimento >= inicio,
        Transacao.data_vencimento <= fim,
    )
    if status_filtro in STATUS_VALIDOS:
        query = query.filter(Transacao.status == status_filtro)
    if categoria_filtro == 'sem':
        query = query.filter(Transacao.categoria_id.is_(None))
    elif categoria_filtro:
        query = query.filter(Transacao.categoria_id == int(categoria_filtro))
    if conta_filtro:
        query = query.filter(Transacao.conta_bancaria_id == int(conta_filtro))
    return query.order_by(Transacao.data_vencimento).all()


SEM_CATEGORIA = '(sem categoria)'
SEM_CENTRO_CUSTO = '(sem centro de custo)'


def _rotulo_categoria(t):
    return t.categoria.nome if t.categoria else SEM_CATEGORIA


def _rotulo_centro_custo(t):
    if t.categoria and t.categoria.centro_custo:
        return t.categoria.centro_custo
    return SEM_CENTRO_CUSTO


def agrupar_transacoes(transacoes, rotulo):
    """Totaliza entradas, saídas e saldo por grupo (categoria ou centro de custo).

    `rotulo` é a função que diz a qual grupo cada lançamento pertence. Os grupos
    saem ordenados por volume movimentado, com os "(sem ...)" sempre no fim.
    """
    grupos = {}
    for t in transacoes:
        nome = rotulo(t)
        grupo = grupos.setdefault(nome, {'nome': nome, 'qtde': 0, 'entradas': 0.0, 'saidas': 0.0})
        grupo['qtde'] += 1
        if t.tipo == 'Receber':
            grupo['entradas'] += t.valor
        else:
            grupo['saidas'] += t.valor

    for grupo in grupos.values():
        grupo['saldo'] = grupo['entradas'] - grupo['saidas']

    return sorted(
        grupos.values(),
        key=lambda g: (g['nome'].startswith('('), -(g['entradas'] + g['saidas'])),
    )


@app.route('/relatorio')
def relatorio():
    inicio, fim, status_filtro, categoria_filtro, conta_filtro = periodo_dos_parametros(request.args)
    transacoes = transacoes_do_periodo(inicio, fim, status_filtro, categoria_filtro, conta_filtro)

    total_entradas = sum(t.valor for t in transacoes if t.tipo == 'Receber')
    total_saidas = sum(t.valor for t in transacoes if t.tipo == 'Pagar')
    saldo = total_entradas - total_saidas

    hoje = datetime.now().date()
    atalhos = [
        {'label': 'Próximos 7 dias', 'inicio': hoje, 'fim': hoje + timedelta(days=7)},
        {'label': 'Próximos 15 dias', 'inicio': hoje, 'fim': hoje + timedelta(days=15)},
        {'label': 'Próximos 30 dias', 'inicio': hoje, 'fim': hoje + timedelta(days=30)},
    ]

    return render_template(
        'relatorio.html',
        transacoes=transacoes,
        inicio=inicio,
        fim=fim,
        status_filtro=status_filtro,
        categoria_filtro=categoria_filtro,
        conta_filtro=conta_filtro,
        total_entradas=total_entradas,
        total_saidas=total_saidas,
        saldo=saldo,
        por_categoria=agrupar_transacoes(transacoes, _rotulo_categoria),
        por_centro_custo=agrupar_transacoes(transacoes, _rotulo_centro_custo),
        categorias=da_empresa(Categoria).order_by(Categoria.tipo, Categoria.nome).all(),
        contas_bancarias=da_empresa(ContaBancaria).order_by(ContaBancaria.nome).all(),
        atalhos=atalhos,
        hoje=hoje,
    )


def _nome_arquivo(inicio, fim):
    return f'lancamentos_{inicio.isoformat()}_a_{fim.isoformat()}'


CABECALHO_LANCAMENTOS = [
    'Descrição', 'Tipo', 'Fornecedor/Cliente', 'Categoria', 'Centro de Custo',
    'Conta', 'Vencimento', 'Pagamento', 'Valor', 'Status',
]


def linhas_lancamentos(transacoes, valor_numerico=False):
    """Linhas dos lançamentos para planilha/texto.

    `valor_numerico` decide entre o número puro (planilha, que soma) e o texto
    com vírgula decimal (arquivos de texto abertos no Excel em português).
    """
    return [
        [
            t.descricao,
            t.tipo,
            nome_entidade(t),
            t.categoria.nome if t.categoria else '',
            (t.categoria.centro_custo or '') if t.categoria else '',
            t.conta_bancaria.nome if t.conta_bancaria else '',
            t.data_vencimento.strftime('%d/%m/%Y'),
            t.data_pagamento.strftime('%d/%m/%Y') if t.data_pagamento else '',
            t.valor if valor_numerico else f'{t.valor:.2f}'.replace('.', ','),
            t.status,
        ]
        for t in transacoes
    ]


def _exportar_csv(transacoes, nome_arquivo):
    return _exportar_csv_generico(CABECALHO_LANCAMENTOS, linhas_lancamentos(transacoes), nome_arquivo)


def _exportar_xlsx(transacoes, nome_arquivo):
    cabecalho = list(CABECALHO_LANCAMENTOS)
    cabecalho[8] = 'Valor (R$)'
    linhas = linhas_lancamentos(transacoes, valor_numerico=True)
    return _exportar_xlsx_generico(cabecalho, linhas, nome_arquivo, 'Lançamentos')


# ------------------------------------------------------------------ #
# Contabilidade — envio dos documentos do período ao contador
# ------------------------------------------------------------------ #

def _notas_do_periodo(inicio, fim):
    """NFS-e e NF-e emitidas dentro do período, com XML guardado."""
    servico = da_empresa(NotaServico).filter(
        NotaServico.data_emissao >= inicio,
        NotaServico.data_emissao <= fim,
        NotaServico.xml_gzip.isnot(None),
    ).order_by(NotaServico.data_emissao).all()

    eletronica = da_empresa(NotaEletronica).filter(
        NotaEletronica.data_emissao >= inicio,
        NotaEletronica.data_emissao <= fim,
        NotaEletronica.xml_gzip.isnot(None),
    ).order_by(NotaEletronica.data_emissao).all()

    return servico, eletronica


def _zip_das_notas(notas_servico, notas_eletronicas):
    """ZIP com os XMLs separados em pastas por tipo e por entrada/saída.

    Retorna (bytes, quantidade) — ou (None, 0) quando não há nenhum XML.
    """
    if not notas_servico and not notas_eletronicas:
        return None, 0

    buffer = io.BytesIO()
    total = 0
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as pacote:
        for nota in notas_servico:
            pasta = 'nfse/emitidas' if nota.papel == 'emitida' else 'nfse/recebidas'
            pacote.writestr(f'{pasta}/NFSe_{nota.numero or nota.chave_acesso}.xml',
                            gzip.decompress(nota.xml_gzip))
            total += 1
        for nota in notas_eletronicas:
            pasta = 'nfe/emitidas' if nota.papel == 'emitida' else 'nfe/recebidas'
            pacote.writestr(f'{pasta}/NFe_{nota.chave}.xml', gzip.decompress(nota.xml_gzip))
            total += 1

    return buffer.getvalue(), total


def _anexos_contabilidade(inicio, fim):
    """Monta os anexos do período: planilha, texto e o pacote de XMLs."""
    transacoes = da_empresa(Transacao).filter(
        Transacao.data_vencimento >= inicio,
        Transacao.data_vencimento <= fim,
    ).order_by(Transacao.data_vencimento).all()

    periodo = f'{inicio.isoformat()}_a_{fim.isoformat()}'
    anexos = []

    cabecalho_planilha = list(CABECALHO_LANCAMENTOS)
    cabecalho_planilha[8] = 'Valor (R$)'
    anexos.append((
        f'lancamentos_{periodo}.xlsx',
        _planilha_bytes(cabecalho_planilha, linhas_lancamentos(transacoes, valor_numerico=True), 'Lançamentos'),
    ))
    anexos.append((
        f'lancamentos_{periodo}.txt',
        _texto_delimitado(CABECALHO_LANCAMENTOS, linhas_lancamentos(transacoes)).encode('utf-8'),
    ))

    notas_servico, notas_eletronicas = _notas_do_periodo(inicio, fim)
    pacote, total_notas = _zip_das_notas(notas_servico, notas_eletronicas)
    if pacote:
        anexos.append((f'notas_fiscais_{periodo}.zip', pacote))

    return anexos, len(transacoes), total_notas


def _identificacao_empresa():
    documento = fiscal_sync.config_get('cert_documento')
    nome = fiscal_sync.config_get('cert_titular')
    if documento:
        return f'{nome} ({formatar_cnpj_cpf(documento)})' if nome else formatar_cnpj_cpf(documento)
    return nome or 'empresa sem CNPJ configurado'


@app.route('/contabilidade')
def contabilidade():
    hoje = datetime.now().date()
    inicio = parse_data(request.args.get('inicio', '')) or hoje.replace(day=1)
    fim = parse_data(request.args.get('fim', '')) or hoje
    if inicio > fim:
        inicio, fim = fim, inicio

    anexos, total_lancamentos, total_notas = _anexos_contabilidade(inicio, fim)
    tamanho_mb = sum(len(conteudo) for _, conteudo in anexos) / (1024 * 1024)
    email_contador = fiscal_sync.config_get('email_contador')

    return render_template(
        'contabilidade.html',
        inicio=inicio,
        fim=fim,
        total_lancamentos=total_lancamentos,
        total_notas=total_notas,
        anexos=[{'nome': nome, 'kb': round(len(conteudo) / 1024)} for nome, conteudo in anexos],
        tamanho_mb=round(tamanho_mb, 2),
        limite_mb=envio_email.LIMITE_ANEXOS_MB,
        smtp_ok=_smtp_configurado(),
        empresa=_identificacao_empresa(),
        email_contador=email_contador,
        contador_com_acesso=_contador_com_acesso(email_contador),
    )


def _contador_com_acesso(email_contador):
    """Conta de contador que hoje enxerga esta empresa, se houver.

    Serve para o cliente ver, na própria tela, quem está com acesso aos dados
    dele — o vínculo é criado pelo simples fato de ele informar o e-mail.
    """
    email = (email_contador or '').strip().lower()
    if not email:
        return None
    return Usuario.query.filter_by(email=email, papel=PAPEL_CONTADOR).first()


@app.route('/contabilidade/contador', methods=['POST'])
def contabilidade_contador():
    """Grava o e-mail do contador — é o que libera o acesso dele aos dados.

    Fica separado do envio porque o cliente pode querer só dar o acesso, sem
    mandar nenhum arquivo: com a conta ligada, o contador busca sozinho.
    """
    email = request.form.get('email_contador', '').strip().lower()
    if email and not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email):
        flash('Informe um e-mail válido para o contador.', 'erro')
        return redirect(url_for('contabilidade'))

    fiscal_sync.config_set('email_contador', email)

    if not email:
        flash('Contador removido: ninguém mais tem acesso aos seus dados.', 'sucesso')
    elif _contador_com_acesso(email):
        flash(
            f'Contador {email} salvo. Ele já pode entrar e ver seus dados '
            '(somente leitura), sem você precisar enviar nada.',
            'sucesso',
        )
    else:
        flash(
            f'Contador {email} salvo. Ainda não existe conta de contador com esse '
            'e-mail — assim que ela for criada, o acesso é liberado sozinho. '
            'Enquanto isso, dá para enviar os documentos por e-mail aqui embaixo.',
            'sucesso',
        )
    return redirect(url_for('contabilidade'))


@app.route('/contabilidade/enviar', methods=['POST'])
def contabilidade_enviar():
    inicio = parse_data(request.form.get('inicio', ''))
    fim = parse_data(request.form.get('fim', ''))
    if not inicio or not fim:
        flash('Informe a data inicial e a data final do período.', 'erro')
        return redirect(url_for('contabilidade'))
    if inicio > fim:
        inicio, fim = fim, inicio

    destinatario = request.form.get('email_contador', '').strip()
    if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', destinatario):
        flash('Informe um e-mail válido para o contador.', 'erro')
        return redirect(url_for('contabilidade', inicio=inicio.isoformat(), fim=fim.isoformat()))

    if not _smtp_configurado():
        flash('Configure o envio de e-mail na aba Configurações antes de enviar.', 'erro')
        return redirect(url_for('configuracoes'))

    anexos, total_lancamentos, total_notas = _anexos_contabilidade(inicio, fim)
    tamanho_mb = sum(len(conteudo) for _, conteudo in anexos) / (1024 * 1024)
    if tamanho_mb > envio_email.LIMITE_ANEXOS_MB:
        flash(
            f'Os anexos somam {tamanho_mb:.1f} MB e passam do limite de '
            f'{envio_email.LIMITE_ANEXOS_MB} MB aceito pela maioria dos provedores. '
            'Envie em períodos menores (por exemplo, mês a mês).',
            'erro',
        )
        return redirect(url_for('contabilidade', inicio=inicio.isoformat(), fim=fim.isoformat()))

    empresa = _identificacao_empresa()
    periodo_texto = f'{inicio.strftime("%d/%m/%Y")} a {fim.strftime("%d/%m/%Y")}'
    observacao = request.form.get('mensagem', '').strip()

    corpo = (
        f'Seu cliente {empresa} enviou uma mensagem.\n\n'
        f'Seguem os documentos do período de {periodo_texto}:\n\n'
        f'- {total_lancamentos} lançamento(s), em planilha (.xlsx) e em texto (.txt)\n'
        f'- {total_notas} nota(s) fiscal(is) em XML, no arquivo .zip separadas por tipo\n'
    )
    if observacao:
        corpo += f'\nObservação do cliente:\n{observacao}\n'
    corpo += '\n—\nEnviado automaticamente pelo sistema de Gestão Financeira.\n'

    try:
        _enviar_email(
            destinatario,
            f'Documentos contábeis {periodo_texto} — {empresa}',
            corpo,
            anexos,
        )
    except envio_email.ErroEnvio as exc:
        flash(str(exc), 'erro')
        return redirect(url_for('contabilidade', inicio=inicio.isoformat(), fim=fim.isoformat()))

    fiscal_sync.config_set('email_contador', destinatario)
    fiscal_sync.config_set('contabilidade_ultimo_envio', f'{datetime.now():%d/%m/%Y %H:%M} para {destinatario} ({periodo_texto})')
    flash(
        f'Enviado para {destinatario}: {total_lancamentos} lançamento(s) e {total_notas} nota(s) fiscal(is).',
        'sucesso',
    )
    return redirect(url_for('contabilidade', inicio=inicio.isoformat(), fim=fim.isoformat()))


@app.route('/relatorio/exportar')
def exportar_relatorio():
    inicio, fim, status_filtro, categoria_filtro, conta_filtro = periodo_dos_parametros(request.args)
    transacoes = transacoes_do_periodo(inicio, fim, status_filtro, categoria_filtro, conta_filtro)
    nome_arquivo = _nome_arquivo(inicio, fim)

    formato = request.args.get('formato', 'csv')
    if formato == 'xlsx':
        return _exportar_xlsx(transacoes, nome_arquivo)
    return _exportar_csv(transacoes, nome_arquivo)


@app.route('/relatorio/exportar-agrupado/<por>')
def exportar_relatorio_agrupado(por):
    if por not in ('categoria', 'centro-custo'):
        flash('Agrupamento inválido.', 'erro')
        return redirect(url_for('relatorio'))

    inicio, fim, status_filtro, categoria_filtro, conta_filtro = periodo_dos_parametros(request.args)
    transacoes = transacoes_do_periodo(inicio, fim, status_filtro, categoria_filtro, conta_filtro)

    if por == 'categoria':
        grupos = agrupar_transacoes(transacoes, _rotulo_categoria)
        titulo, coluna = 'Categoria', 'Categoria'
    else:
        grupos = agrupar_transacoes(transacoes, _rotulo_centro_custo)
        titulo, coluna = 'Centro de Custo', 'Centro de Custo'

    cabecalho = [coluna, 'Lançamentos', 'Entradas', 'Saídas', 'Saldo']
    nome_arquivo = f'{por}_{inicio.isoformat()}_a_{fim.isoformat()}'

    if request.args.get('formato') == 'xlsx':
        linhas = [[g['nome'], g['qtde'], g['entradas'], g['saidas'], g['saldo']] for g in grupos]
        return _exportar_xlsx_generico(cabecalho, linhas, nome_arquivo, titulo)

    linhas = [
        [
            g['nome'],
            g['qtde'],
            f"{g['entradas']:.2f}".replace('.', ','),
            f"{g['saidas']:.2f}".replace('.', ','),
            f"{g['saldo']:.2f}".replace('.', ','),
        ]
        for g in grupos
    ]
    return _exportar_csv_generico(cabecalho, linhas, nome_arquivo)


@app.route('/recorrentes')
def recorrentes_listar():
    registros = da_empresa(LancamentoRecorrente).order_by(LancamentoRecorrente.descricao).all()
    return render_template(
        'recorrentes.html',
        registros=registros,
        fornecedores=da_empresa(Fornecedor).order_by(Fornecedor.nome).all(),
        clientes=da_empresa(Cliente).order_by(Cliente.nome).all(),
        categorias_pagar=da_empresa(Categoria).filter_by(tipo='Pagar').order_by(Categoria.nome).all(),
        categorias_receber=da_empresa(Categoria).filter_by(tipo='Receber').order_by(Categoria.nome).all(),
        contas_bancarias=da_empresa(ContaBancaria).order_by(ContaBancaria.nome).all(),
    )


@app.route('/recorrentes/adicionar', methods=['POST'])
def recorrentes_adicionar():
    dados, erro = validar_recorrente(request.form)
    if erro:
        flash(erro, 'erro')
        return redirect(url_for('recorrentes_listar'))

    novo = novo_registro(LancamentoRecorrente, **dados)
    novo.ultima_geracao_mes = competencia_anterior(competencia(datetime.now().date()))
    db.session.add(novo)
    db.session.commit()
    flash('Lançamento recorrente cadastrado com sucesso.', 'sucesso')
    return redirect(url_for('recorrentes_listar'))


@app.route('/recorrentes/editar/<int:id>', methods=['GET', 'POST'])
def recorrentes_editar(id):
    tpl = buscar_ou_404(LancamentoRecorrente, id)

    if request.method == 'POST':
        dados, erro = validar_recorrente(request.form)
        if erro:
            flash(erro, 'erro')
            return redirect(url_for('recorrentes_editar', id=id))

        for campo, valor in dados.items():
            setattr(tpl, campo, valor)
        db.session.commit()
        flash('Lançamento recorrente atualizado com sucesso.', 'sucesso')
        return redirect(url_for('recorrentes_listar'))

    return render_template(
        'recorrentes_editar.html',
        t=tpl,
        fornecedores=da_empresa(Fornecedor).order_by(Fornecedor.nome).all(),
        clientes=da_empresa(Cliente).order_by(Cliente.nome).all(),
        categorias_pagar=da_empresa(Categoria).filter_by(tipo='Pagar').order_by(Categoria.nome).all(),
        categorias_receber=da_empresa(Categoria).filter_by(tipo='Receber').order_by(Categoria.nome).all(),
        contas_bancarias=da_empresa(ContaBancaria).order_by(ContaBancaria.nome).all(),
    )


@app.route('/recorrentes/excluir/<int:id>', methods=['POST'])
def recorrentes_excluir(id):
    tpl = buscar_ou_404(LancamentoRecorrente, id)
    db.session.delete(tpl)
    db.session.commit()
    flash('Lançamento recorrente excluído.', 'sucesso')
    return redirect(url_for('recorrentes_listar'))


@app.route('/recorrentes/gerar', methods=['POST'])
def recorrentes_gerar():
    total = gerar_lancamentos_recorrentes(auth.empresa_atual_id())
    if total:
        flash(f'{total} lançamento(s) gerado(s) com sucesso.', 'sucesso')
    else:
        flash('Nenhum lançamento novo para gerar no momento.', 'sucesso')
    return redirect(url_for('recorrentes_listar'))


# ------------------------------------------------------------------ #
# Notas fiscais (NFS-e / NF-e) e Configurações
# ------------------------------------------------------------------ #

SITUACOES_NFE = {
    '1': 'Autorizada',
    '2': 'Denegada',
    '3': 'Cancelada',
    'completa': 'Autorizada',
}


def situacao_nfe_texto(situacao):
    return SITUACOES_NFE.get(str(situacao or ''), situacao or '—')


def nfe_sem_efeito(situacao):
    """True para NF-e cancelada (3) ou denegada (2): nenhuma das duas gera
    obrigação de pagamento, então não viram lançamento."""
    return str(situacao or '') in ('2', '3')


app.jinja_env.globals['situacao_nfe_texto'] = situacao_nfe_texto
app.jinja_env.globals['nfe_sem_efeito'] = nfe_sem_efeito


def _periodo_simples(args):
    """Filtro de período (inicio/fim) dos painéis de notas.

    O padrão são os últimos 90 dias, e não o mês atual: é essa a janela que os
    servidores do governo guardam, então uma nota recém-baixada de um mês
    anterior ficaria invisível se o padrão fosse só o mês corrente.
    """
    hoje = datetime.now().date()
    inicio = parse_data(args.get('inicio', '')) or (hoje - timedelta(days=90))
    fim = parse_data(args.get('fim', '')) or hoje
    if inicio > fim:
        inicio, fim = fim, inicio
    return inicio, fim


def _contexto_fiscal_comum():
    return {
        'cert_ok': fiscal_sync.certificado_configurado(),
        'hoje': datetime.now().date(),
    }


@app.route('/notas-servico')
def notas_servico():
    inicio, fim = _periodo_simples(request.args)

    query = da_empresa(NotaServico).filter(
        NotaServico.data_emissao >= inicio,
        NotaServico.data_emissao <= fim,
    ).order_by(NotaServico.data_emissao.desc(), NotaServico.id.desc())
    notas = query.all()

    emitidas = [n for n in notas if n.papel == 'emitida']
    recebidas = [n for n in notas if n.papel != 'emitida']

    def _totais(lista):
        ativas = [n for n in lista if n.situacao == 'ATIVA']
        return {
            'qtde': len(ativas),
            'valor': sum(n.valor_servico or 0 for n in ativas),
            'iss': sum(n.valor_iss or 0 for n in ativas),
        }

    return render_template(
        'notas_servico.html',
        inicio=inicio,
        fim=fim,
        emitidas=emitidas,
        recebidas=recebidas,
        fora_do_periodo=da_empresa(NotaServico).count() - len(notas),
        totais_emitidas=_totais(emitidas),
        totais_recebidas=_totais(recebidas),
        sync=sync_fiscal.status('nfse', auth.empresa_atual_id()),
        ultima_sync=fiscal_sync.config_get('nfse_ultima_sync'),
        ultimo_status=fiscal_sync.config_get('nfse_ultimo_status'),
        **_contexto_fiscal_comum(),
    )


@app.route('/notas-eletronicas')
def notas_eletronicas():
    inicio, fim = _periodo_simples(request.args)

    # Notas sem data de emissão sempre aparecem (não dá para saber o período)
    query = da_empresa(NotaEletronica).filter(
        db.or_(
            NotaEletronica.data_emissao.is_(None),
            db.and_(
                NotaEletronica.data_emissao >= inicio,
                NotaEletronica.data_emissao <= fim,
            ),
        )
    ).order_by(NotaEletronica.data_emissao.desc(), NotaEletronica.id.desc())
    notas = query.all()

    emitidas = [n for n in notas if n.papel == 'emitida']
    recebidas = [n for n in notas if n.papel != 'emitida']

    def _totais(lista):
        validas = [n for n in lista if str(n.situacao or '') != '3']
        return {
            'qtde': len(validas),
            'valor': sum(n.valor_total or 0 for n in validas),
        }

    return render_template(
        'notas_eletronicas.html',
        inicio=inicio,
        fim=fim,
        emitidas=emitidas,
        recebidas=recebidas,
        fora_do_periodo=da_empresa(NotaEletronica).count() - len(notas),
        totais_emitidas=_totais(emitidas),
        totais_recebidas=_totais(recebidas),
        sync=sync_fiscal.status('nfe', auth.empresa_atual_id()),
        ultima_sync=fiscal_sync.config_get('nfe_ultima_sync'),
        ultimo_status=fiscal_sync.config_get('nfe_ultimo_status'),
        minutos_espera=sync_fiscal.nfe_minutos_de_espera(auth.empresa_atual_id()),
        **_contexto_fiscal_comum(),
    )


@app.route('/notas-servico/sincronizar', methods=['POST'])
def nfse_sincronizar():
    if not fiscal_sync.certificado_configurado():
        flash('Configure o certificado digital na aba Configurações antes de sincronizar.', 'erro')
        return redirect(url_for('configuracoes'))
    if sync_fiscal.iniciar('nfse', auth.empresa_atual_id()):
        flash('Sincronização das NFS-e iniciada.', 'sucesso')
    else:
        flash('Já existe uma sincronização de NFS-e em andamento.', 'erro')
    return redirect(url_for('notas_servico'))


@app.route('/notas-eletronicas/sincronizar', methods=['POST'])
def nfe_sincronizar():
    if not fiscal_sync.certificado_configurado():
        flash('Configure o certificado digital na aba Configurações antes de sincronizar.', 'erro')
        return redirect(url_for('configuracoes'))

    forcar = request.form.get('forcar') == '1'
    espera = sync_fiscal.nfe_minutos_de_espera(auth.empresa_atual_id())
    if espera > 0 and not forcar:
        flash(
            f'A SEFAZ exige intervalo de 1 hora entre consultas sem novidade. '
            f'Aguarde ~{espera} min — as notas já baixadas continuam disponíveis abaixo.',
            'erro',
        )
        return redirect(url_for('notas_eletronicas'))

    if sync_fiscal.iniciar('nfe', auth.empresa_atual_id()):
        flash('Sincronização das NF-e iniciada.', 'sucesso')
    else:
        flash('Já existe uma sincronização de NF-e em andamento.', 'erro')
    return redirect(url_for('notas_eletronicas'))


@app.route('/notas-servico/sincronizacao')
def nfse_status_sincronizacao():
    return jsonify(sync_fiscal.status('nfse', auth.empresa_atual_id()))


@app.route('/notas-eletronicas/sincronizacao')
def nfe_status_sincronizacao():
    return jsonify(sync_fiscal.status('nfe', auth.empresa_atual_id()))


@app.route('/notas-servico/<int:id>/xml')
def nfse_baixar_xml(id):
    nota = buscar_ou_404(NotaServico, id)
    if not nota.xml_gzip:
        flash('XML desta nota não está disponível.', 'erro')
        return redirect(url_for('notas_servico'))
    return Response(
        gzip.decompress(nota.xml_gzip),
        mimetype='application/xml',
        headers={'Content-Disposition': f'attachment; filename="NFSe_{nota.chave_acesso}.xml"'},
    )


@app.route('/notas-eletronicas/<int:id>/xml')
def nfe_baixar_xml(id):
    nota = buscar_ou_404(NotaEletronica, id)
    if not nota.xml_gzip:
        flash('XML desta nota não está disponível.', 'erro')
        return redirect(url_for('notas_eletronicas'))
    return Response(
        gzip.decompress(nota.xml_gzip),
        mimetype='application/xml',
        headers={'Content-Disposition': f'attachment; filename="NFe_{nota.chave}.xml"'},
    )


@app.route('/notas-eletronicas/<int:id>/dar-ciencia', methods=['POST'])
def nfe_dar_ciencia(id):
    """Registra a Ciência da Operação de uma NF-e de compra na SEFAZ.

    É um evento fiscal de verdade, gravado no seu CNPJ e sem desfazer — por
    isso só acontece por ação explícita do usuário, nunca junto da
    sincronização automática.
    """
    nota = buscar_ou_404(NotaEletronica, id)
    destino = _destino_voltar()

    if not fiscal_sync.certificado_configurado():
        flash('Configure o certificado digital na aba Configurações antes de manifestar.', 'erro')
        return redirect(url_for('configuracoes'))
    if nota.papel == 'emitida':
        flash('A manifestação vale só para notas emitidas contra a empresa (compras).', 'erro')
        return redirect(destino)
    if nota.manifestacao_em:
        flash('Esta nota já teve a ciência registrada.', 'erro')
        return redirect(destino)

    try:
        caminho, senha = sync_fiscal.credenciais(auth.empresa_atual_id())
        with open(caminho, 'rb') as arquivo_pfx:
            chave_privada, certificado, _ = fiscal_certificado.carregar_pfx(arquivo_pfx.read(), senha)

        with fiscal_certificado.CertificadoContext(caminho, senha) as ctx:
            resultado = fiscal_manifestacao.dar_ciencia(
                chave=nota.chave,
                documento=fiscal_sync.config_get('cert_documento'),
                ambiente=fiscal_sync.config_get('nfe_ambiente', 'producao'),
                dh_evento=datetime.now().astimezone().replace(microsecond=0).isoformat(),
                chave_privada=chave_privada,
                certificado=certificado,
                cert_pair=ctx.cert_pair,
                url=os.environ.get('ERP_NFE_EVENTO_URL'),
                verify=sync_fiscal.verify(),
            )
    except fiscal_manifestacao.ErroManifestacao as exc:
        flash(f'A SEFAZ recusou a manifestação: {exc}', 'erro')
        return redirect(destino)
    except fiscal_certificado.CertificadoInvalido as exc:
        flash(str(exc), 'erro')
        return redirect(destino)
    except Exception as exc:  # falha de rede/timeout: superfície única para a UI
        flash(f'Não foi possível registrar a manifestação: {exc}', 'erro')
        return redirect(destino)

    nota.manifestacao_em = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    nota.manifestacao_protocolo = resultado.get('protocolo')
    db.session.commit()

    flash(
        f'Ciência registrada na SEFAZ ({resultado["cstat"]} {resultado["xmotivo"]}). '
        'Sincronize novamente em alguns minutos para baixar o XML completo desta nota.',
        'sucesso',
    )
    return redirect(destino)


@app.route('/notas-servico/<int:id>/danfse')
def nfse_baixar_danfse(id):
    nota = buscar_ou_404(NotaServico, id)
    if not fiscal_sync.certificado_configurado():
        flash('Configure o certificado digital na aba Configurações.', 'erro')
        return redirect(url_for('notas_servico'))
    try:
        caminho = fiscal_sync.config_get('cert_caminho')
        senha = fiscal_certificado.descriptografar_senha(
            fiscal_sync.config_get('cert_senha_cripto'), data_path('.chave_secreta')
        )
        ambiente = fiscal_sync.config_get('nfse_ambiente', 'producao')
        with fiscal_certificado.CertificadoContext(caminho, senha) as ctx:
            cliente = fiscal_nfse.AdnClient(
                ambiente,
                ctx.cert_pair,
                base_adn=os.environ.get('ERP_ADN_BASE'),
                base_sefin=os.environ.get('ERP_SEFIN_BASE'),
                verify=data_path('ca_extra.pem') if os.path.exists(data_path('ca_extra.pem')) else True,
            )
            with cliente:
                pdf = cliente.baixar_danfse(nota.chave_acesso)
    except (fiscal_nfse.AdnErro, fiscal_certificado.CertificadoInvalido) as exc:
        flash(f'Não foi possível baixar o DANFSe: {exc}', 'erro')
        return redirect(url_for('notas_servico'))
    return Response(
        pdf,
        mimetype='application/pdf',
        headers={'Content-Disposition': f'attachment; filename="DANFSe_{nota.chave_acesso}.pdf"'},
    )


@app.route('/notas-servico/<int:id>/gerar-lancamento', methods=['POST'])
def nfse_gerar_lancamento(id):
    """Fallback manual: normalmente o lançamento já foi gerado sozinho na
    sincronização. Serve para notas puladas por falta de valor, ou cujo
    lançamento vinculado foi excluído depois."""
    nota = buscar_ou_404(NotaServico, id)
    if nota.situacao != 'ATIVA':
        flash('Não é possível gerar lançamento de uma nota cancelada/substituída.', 'erro')
        return redirect(_destino_voltar())

    tipo = 'Receber' if nota.papel == 'emitida' else 'Pagar'
    contraparte_doc = nota.tomador_doc if tipo == 'Receber' else nota.prestador_doc
    contraparte_nome = nota.tomador_nome if tipo == 'Receber' else nota.prestador_nome
    descricao = f"NFS-e {nota.numero or nota.chave_acesso[-8:]} - {contraparte_nome or 'sem identificação'}"

    _, erro = fiscal_sync.gerar_lancamento_para_nota(
        nota, tipo, contraparte_doc, contraparte_nome,
        nota.valor_liquido or nota.valor_servico, descricao, nota.municipio,
    )
    if erro:
        flash(erro, 'erro')
    else:
        db.session.commit()
        flash(f'Lançamento ({tipo}) gerado com sucesso a partir da NFS-e.', 'sucesso')
    return redirect(_destino_voltar())


@app.route('/notas-eletronicas/<int:id>/gerar-lancamento', methods=['POST'])
def nfe_gerar_lancamento(id):
    """Fallback manual (ver nfse_gerar_lancamento)."""
    nota = buscar_ou_404(NotaEletronica, id)
    if str(nota.situacao or '') in ('2', '3'):
        flash('Não é possível gerar lançamento de uma nota cancelada ou denegada.', 'erro')
        return redirect(_destino_voltar())

    tipo = 'Receber' if nota.papel == 'emitida' else 'Pagar'
    if tipo == 'Receber':
        contraparte_doc, contraparte_nome = nota.dest_doc, nota.dest_nome
    else:
        contraparte_doc, contraparte_nome = nota.emitente_doc, nota.emitente_nome
    descricao = f"NF-e {nota.chave[25:34].lstrip('0') or nota.chave[-8:]} - {contraparte_nome or nota.emitente_nome or 'sem identificação'}"

    _, erro = fiscal_sync.gerar_lancamento_para_nota(
        nota, tipo, contraparte_doc, contraparte_nome,
        nota.valor_total, descricao,
    )
    if erro:
        flash(erro, 'erro')
    else:
        db.session.commit()
        flash(f'Lançamento ({tipo}) gerado com sucesso a partir da NF-e.', 'sucesso')
    return redirect(_destino_voltar())


@app.route('/configuracoes', methods=['GET', 'POST'])
def configuracoes():
    caminho_chave = data_path('.chave_secreta')

    if request.method == 'POST':
        caminho = request.form.get('cert_caminho', '').strip()
        senha = request.form.get('cert_senha', '')

        # Enviar o .pfx pelo navegador evita ter que digitar o caminho completo:
        # o arquivo é copiado para a pasta "certificados", ao lado do programa.
        arquivo = request.files.get('cert_arquivo')
        if arquivo and arquivo.filename:
            nome = secure_filename(arquivo.filename)
            if not nome.lower().endswith(('.pfx', '.p12')):
                flash('O certificado deve ser um arquivo .pfx ou .p12.', 'erro')
                return redirect(url_for('configuracoes'))
            # Uma pasta por empresa: com todos os certificados no mesmo lugar,
            # dois clientes com arquivos de mesmo nome sobrescreveriam um ao outro.
            pasta = os.path.join(data_path('certificados'), str(auth.empresa_atual_id()))
            os.makedirs(pasta, exist_ok=True)
            caminho = os.path.join(pasta, nome)
            arquivo.save(caminho)
            try:
                os.chmod(caminho, 0o600)
            except OSError:
                pass  # Windows não suporta chmod POSIX

        nfse_ambiente = request.form.get('nfse_ambiente', 'producao')
        nfe_ambiente = request.form.get('nfe_ambiente', 'producao')
        uf = request.form.get('uf', '').strip().upper()

        if nfse_ambiente not in fiscal_nfse.AMBIENTES_NFSE:
            nfse_ambiente = 'producao'
        if nfe_ambiente not in fiscal_nfe.URLS_NFE:
            nfe_ambiente = 'homologacao' if nfe_ambiente == 'homologacao' else 'producao'

        fiscal_sync.config_set('nfse_ambiente', nfse_ambiente)
        fiscal_sync.config_set('nfe_ambiente', nfe_ambiente)
        if uf in fiscal_certificado.UF_PARA_CODIGO:
            fiscal_sync.config_set('cert_uf', uf)
            fiscal_sync.config_set('nfe_uf_autor', fiscal_certificado.UF_PARA_CODIGO[uf])

        if caminho:
            fiscal_sync.config_set('cert_caminho', caminho)
            senha_valida = senha or (
                fiscal_certificado.descriptografar_senha(
                    fiscal_sync.config_get('cert_senha_cripto'), caminho_chave
                ) if fiscal_sync.config_get('cert_senha_cripto') else ''
            )
            if senha_valida:
                try:
                    info = fiscal_certificado.inspecionar_pfx(caminho, senha_valida)
                except fiscal_certificado.CertificadoInvalido as exc:
                    flash(f'Certificado não validado: {exc}', 'erro')
                    return redirect(url_for('configuracoes'))

                fiscal_sync.config_set(
                    'cert_senha_cripto',
                    fiscal_certificado.criptografar_senha(senha_valida, caminho_chave),
                )
                fiscal_sync.config_set('cert_documento', info.documento)
                fiscal_sync.config_set('cert_titular', info.titular)
                fiscal_sync.config_set('cert_validade', info.valido_ate)
                if info.uf and not uf:
                    fiscal_sync.config_set('cert_uf', info.uf)
                    fiscal_sync.config_set(
                        'nfe_uf_autor', fiscal_certificado.UF_PARA_CODIGO.get(info.uf, '35')
                    )
                aviso = ''
                if info.expirado:
                    aviso = ' ATENÇÃO: o certificado está VENCIDO.'
                elif info.dias_para_vencer <= 30:
                    aviso = f' Atenção: o certificado vence em {info.dias_para_vencer} dia(s).'
                flash(
                    f'Certificado validado: {info.titular} '
                    f'({info.tipo_documento} {info.documento}), válido até {info.valido_ate}.{aviso}',
                    'sucesso',
                )
            else:
                flash('Caminho salvo. Informe a senha do certificado para validá-lo.', 'erro')
        else:
            flash('Configurações salvas.', 'sucesso')
        return redirect(url_for('configuracoes'))

    return render_template(
        'configuracoes.html',
        cert_caminho=fiscal_sync.config_get('cert_caminho'),
        cert_documento=fiscal_sync.config_get('cert_documento'),
        cert_titular=fiscal_sync.config_get('cert_titular'),
        cert_validade=fiscal_sync.config_get('cert_validade'),
        cert_uf=fiscal_sync.config_get('cert_uf'),
        senha_salva=bool(fiscal_sync.config_get('cert_senha_cripto')),
        nfse_ambiente=fiscal_sync.config_get('nfse_ambiente', 'producao'),
        nfe_ambiente=fiscal_sync.config_get('nfe_ambiente', 'producao'),
        nfse_ultimo_nsu=fiscal_sync.config_get('nfse_ultimo_nsu', '0'),
        nfe_ultimo_nsu=fiscal_sync.config_get('nfe_ultimo_nsu', '000000000000000'),
        ufs=sorted(fiscal_certificado.UF_PARA_CODIGO.keys()),
        ambientes_nfse=fiscal_nfse.AMBIENTES_NFSE,
        backups=backups_existentes(),
        backups_pasta=backups_dir(),
        smtp=_config_smtp(),
        provedores_smtp=envio_email.PROVEDORES_SMTP,
        segurancas_smtp=envio_email.SEGURANCAS,
    )


def _config_smtp():
    """Configuração de e-mail atual (a senha nunca sai daqui em claro)."""
    return {
        'provedor': fiscal_sync.config_get('smtp_provedor', 'gmail'),
        'servidor': fiscal_sync.config_get('smtp_servidor'),
        'porta': fiscal_sync.config_get('smtp_porta', '587'),
        'seguranca': fiscal_sync.config_get('smtp_seguranca', 'tls'),
        'usuario': fiscal_sync.config_get('smtp_usuario'),
        'remetente': fiscal_sync.config_get('smtp_remetente'),
        'remetente_nome': fiscal_sync.config_get('smtp_remetente_nome'),
        'senha_salva': bool(fiscal_sync.config_get('smtp_senha_cripto')),
    }


def _smtp_configurado():
    return bool(fiscal_sync.config_get('smtp_servidor') and fiscal_sync.config_get('smtp_remetente'))


def _enviar_email(destinatario, assunto, corpo, anexos=()):
    """Envia usando o SMTP configurado, decifrando a senha guardada."""
    senha_cripto = fiscal_sync.config_get('smtp_senha_cripto')
    senha = fiscal_certificado.descriptografar_senha(senha_cripto, data_path('.chave_secreta')) if senha_cripto else ''
    return envio_email.enviar(
        servidor=fiscal_sync.config_get('smtp_servidor'),
        porta=fiscal_sync.config_get('smtp_porta', '587'),
        seguranca=fiscal_sync.config_get('smtp_seguranca', 'tls'),
        usuario=fiscal_sync.config_get('smtp_usuario'),
        senha=senha,
        remetente=fiscal_sync.config_get('smtp_remetente'),
        remetente_nome=fiscal_sync.config_get('smtp_remetente_nome'),
        destinatario=destinatario,
        assunto=assunto,
        corpo=corpo,
        anexos=anexos,
    )


@app.route('/configuracoes/email', methods=['POST'])
def configuracoes_email():
    provedor = request.form.get('smtp_provedor', 'outro')
    if provedor not in envio_email.PROVEDORES_SMTP:
        provedor = 'outro'
    preset = envio_email.PROVEDORES_SMTP[provedor]

    # Escolhendo um provedor da lista, servidor/porta/segurança vêm prontos;
    # em "Outro servidor" é o usuário quem informa.
    if provedor == 'outro':
        servidor = request.form.get('smtp_servidor', '').strip()
        porta = request.form.get('smtp_porta', '587').strip()
        seguranca = request.form.get('smtp_seguranca', 'tls')
    else:
        servidor, porta, seguranca = preset['servidor'], str(preset['porta']), preset['seguranca']

    if seguranca not in dict(envio_email.SEGURANCAS):
        seguranca = 'tls'
    if not porta.isdigit():
        flash('Porta do servidor inválida.', 'erro')
        return redirect(url_for('configuracoes'))

    remetente = request.form.get('smtp_remetente', '').strip()
    if remetente and not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', remetente):
        flash('E-mail remetente inválido.', 'erro')
        return redirect(url_for('configuracoes'))

    fiscal_sync.config_set('smtp_provedor', provedor)
    fiscal_sync.config_set('smtp_servidor', servidor)
    fiscal_sync.config_set('smtp_porta', porta)
    fiscal_sync.config_set('smtp_seguranca', seguranca)
    fiscal_sync.config_set('smtp_usuario', request.form.get('smtp_usuario', '').strip())
    fiscal_sync.config_set('smtp_remetente', remetente)
    fiscal_sync.config_set('smtp_remetente_nome', request.form.get('smtp_remetente_nome', '').strip())

    senha = request.form.get('smtp_senha', '')
    if senha:
        fiscal_sync.config_set(
            'smtp_senha_cripto',
            fiscal_certificado.criptografar_senha(senha, data_path('.chave_secreta')),
        )

    flash('Configurações de e-mail salvas.', 'sucesso')
    return redirect(url_for('configuracoes'))


@app.route('/configuracoes/email/testar', methods=['POST'])
def configuracoes_email_testar():
    destino = request.form.get('destino', '').strip() or fiscal_sync.config_get('smtp_remetente')
    if not destino:
        flash('Informe um e-mail para receber o teste.', 'erro')
        return redirect(url_for('configuracoes'))

    try:
        _enviar_email(
            destino,
            'Teste de envio — Gestão Financeira',
            'Se você recebeu esta mensagem, o envio de e-mail do sistema está funcionando.\n\n'
            'Pode responder ignorando: é só um teste de configuração.',
        )
    except envio_email.ErroEnvio as exc:
        flash(str(exc), 'erro')
        return redirect(url_for('configuracoes'))

    flash(f'E-mail de teste enviado para {destino}. Confira a caixa de entrada (e o spam).', 'sucesso')
    return redirect(url_for('configuracoes'))


@app.route('/configuracoes/backup', methods=['POST'])
def configuracoes_backup():
    caminho = fazer_backup(forcar=True)
    if caminho:
        flash(f'Backup gravado em {caminho}', 'sucesso')
    else:
        flash('Não há banco de dados para copiar ainda.', 'erro')
    return redirect(url_for('configuracoes'))


@app.route('/configuracoes/resetar-nsu/<qual>', methods=['POST'])
def configuracoes_resetar_nsu(qual):
    if qual == 'nfse':
        fiscal_sync.config_set('nfse_ultimo_nsu', '0')
        flash('Cursor de NFS-e zerado: a próxima sincronização baixa tudo de novo (sem duplicar).', 'sucesso')
    elif qual == 'nfe':
        fiscal_sync.config_set('nfe_ultimo_nsu', '000000000000000')
        fiscal_sync.config_set('nfe_max_nsu', '0')
        fiscal_sync.config_set('nfe_ultimo_cstat', '')
        flash('Cursor de NF-e zerado: a próxima sincronização re-baixa o que a SEFAZ ainda guarda (~90 dias).', 'sucesso')
    else:
        flash('Opção inválida.', 'erro')
    return redirect(url_for('configuracoes'))


# ------------------------------------------------------------------ #
# Painel do administrador
# ------------------------------------------------------------------ #

def _resumo_assinaturas():
    empresas = Empresa.query.order_by(Empresa.nome).all()
    em_dia = [e for e in empresas if e.assinatura_em_dia]
    return {
        'empresas': empresas,
        'total': len(empresas),
        'pagas': len(em_dia),
        'vencidas': len(empresas) - len(em_dia),
        'usuarios_ativos': Usuario.query.filter_by(ativo=True).count(),
        'usuarios_bloqueados': Usuario.query.filter_by(ativo=False).count(),
    }


@app.route('/admin')
@auth.exigir_papel(PAPEL_ADMIN)
def admin_painel():
    resumo = _resumo_assinaturas()
    return render_template(
        'admin_painel.html',
        **resumo,
        vencendo=[e for e in resumo['empresas']
                  if e.dias_restantes is not None and 0 <= e.dias_restantes <= 7],
        ultima_verificacao=fiscal_sync.sistema_get('asaas_ultima_verificacao') or '',
        ultimos_pagamentos=Pagamento.query.order_by(Pagamento.registrado_em.desc()).limit(10).all(),
    )


@app.route('/admin/clientes')
@auth.exigir_papel(PAPEL_ADMIN)
def admin_empresas():
    return render_template(
        'admin_empresas.html',
        empresas=Empresa.query.order_by(Empresa.nome).all(),
    )


@app.route('/admin/clientes/novo', methods=['POST'])
@auth.exigir_papel(PAPEL_ADMIN)
def admin_empresa_nova():
    nome = request.form.get('nome', '').strip()
    cnpj = re.sub(r'\D', '', request.form.get('cnpj', ''))
    email = request.form.get('email', '').strip().lower()
    senha = request.form.get('senha', '')
    nome_usuario = request.form.get('nome_usuario', '').strip() or nome
    dias = request.form.get('dias_cortesia', '')

    if not nome:
        flash('Informe o nome da empresa.', 'erro')
        return redirect(url_for('admin_empresas'))
    if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email):
        flash('Informe um e-mail válido para o usuário da empresa.', 'erro')
        return redirect(url_for('admin_empresas'))
    if len(senha) < 8:
        flash('A senha precisa ter pelo menos 8 caracteres.', 'erro')
        return redirect(url_for('admin_empresas'))
    if Usuario.query.filter_by(email=email).first():
        flash(f'Já existe um usuário com o e-mail {email}.', 'erro')
        return redirect(url_for('admin_empresas'))

    empresa = Empresa(nome=nome, cnpj=cnpj or None)
    if dias.isdigit() and int(dias) > 0:
        empresa.creditar_dias(int(dias))
    db.session.add(empresa)
    db.session.flush()

    auth.criar_usuario(email, senha, nome_usuario, papel=PAPEL_USUARIO, empresa=empresa)
    db.session.commit()

    flash(f'Empresa "{nome}" criada com o usuário {email}.', 'sucesso')
    return redirect(url_for('admin_empresas'))


@app.route('/admin/clientes/<int:id>/assinatura', methods=['POST'])
@auth.exigir_papel(PAPEL_ADMIN)
def admin_empresa_assinatura(id):
    empresa = Empresa.query.get_or_404(id)
    acao = request.form.get('acao', '')

    if acao == 'liberar':
        dias = request.form.get('dias', '30')
        empresa.creditar_dias(int(dias) if dias.isdigit() else 30)
        flash(f'Assinatura de "{empresa.nome}" liberada até {empresa.assinatura_ate.strftime("%d/%m/%Y")}.', 'sucesso')
    elif acao == 'encerrar':
        empresa.assinatura_ate = None
        flash(f'Assinatura de "{empresa.nome}" encerrada: o acesso já está bloqueado.', 'sucesso')
    else:
        flash('Ação inválida.', 'erro')
        return redirect(url_for('admin_empresas'))

    db.session.commit()
    return redirect(url_for('admin_empresas'))


@app.route('/admin/usuarios')
@auth.exigir_papel(PAPEL_ADMIN)
def admin_usuarios():
    return render_template(
        'admin_usuarios.html',
        usuarios=Usuario.query.order_by(Usuario.papel, Usuario.nome).all(),
        empresas=Empresa.query.order_by(Empresa.nome).all(),
        papeis=(PAPEL_ADMIN, PAPEL_USUARIO, PAPEL_CONTADOR),
    )


@app.route('/admin/usuarios/novo', methods=['POST'])
@auth.exigir_papel(PAPEL_ADMIN)
def admin_usuario_novo():
    email = request.form.get('email', '').strip().lower()
    senha = request.form.get('senha', '')
    nome = request.form.get('nome', '').strip()
    papel = request.form.get('papel', PAPEL_USUARIO)
    empresa_id = request.form.get('empresa_id', '')

    if papel not in (PAPEL_ADMIN, PAPEL_USUARIO, PAPEL_CONTADOR):
        flash('Papel inválido.', 'erro')
        return redirect(url_for('admin_usuarios'))
    if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email):
        flash('E-mail inválido.', 'erro')
        return redirect(url_for('admin_usuarios'))
    if len(senha) < 8:
        flash('A senha precisa ter pelo menos 8 caracteres.', 'erro')
        return redirect(url_for('admin_usuarios'))
    if Usuario.query.filter_by(email=email).first():
        flash(f'Já existe um usuário com o e-mail {email}.', 'erro')
        return redirect(url_for('admin_usuarios'))

    empresa = None
    if papel == PAPEL_USUARIO:
        empresa = Empresa.query.get(int(empresa_id)) if empresa_id.isdigit() else None
        if empresa is None:
            flash('Escolha a empresa deste usuário.', 'erro')
            return redirect(url_for('admin_usuarios'))

    auth.criar_usuario(email, senha, nome or email, papel=papel, empresa=empresa)
    db.session.commit()

    if papel == PAPEL_CONTADOR:
        flash(
            f'Contador {email} criado. Ele passa a ver um cliente assim que o próprio '
            'cliente informar esse e-mail na aba Contabilidade.',
            'sucesso',
        )
    else:
        flash(f'Usuário {email} criado.', 'sucesso')
    return redirect(url_for('admin_usuarios'))


@app.route('/admin/usuarios/<int:id>/bloqueio', methods=['POST'])
@auth.exigir_papel(PAPEL_ADMIN)
def admin_usuario_bloqueio(id):
    usuario = Usuario.query.get_or_404(id)
    if usuario.id == auth.usuario_logado().id:
        flash('Você não pode bloquear a si mesmo.', 'erro')
        return redirect(url_for('admin_usuarios'))

    usuario.ativo = not usuario.ativo
    db.session.commit()
    flash(
        f'Usuário {usuario.email} {"desbloqueado" if usuario.ativo else "bloqueado"}.',
        'sucesso',
    )
    return redirect(url_for('admin_usuarios'))


@app.route('/admin/assinaturas')
@auth.exigir_papel(PAPEL_ADMIN)
def admin_assinaturas():
    return render_template(
        'admin_assinaturas.html',
        **_resumo_assinaturas(),
        pagamentos=Pagamento.query.order_by(Pagamento.registrado_em.desc()).limit(50).all(),
        asaas=asaas.configuracao(),
        ultima_verificacao=fiscal_sync.sistema_get('asaas_ultima_verificacao') or '',
        ultimo_resultado=fiscal_sync.sistema_get('asaas_ultimo_resultado') or '',
        suporte_whatsapp=fiscal_sync.sistema_get('suporte_whatsapp'),
    )


@app.route('/admin/asaas', methods=['POST'])
@auth.exigir_papel(PAPEL_ADMIN)
def admin_asaas_configurar():
    ambiente = request.form.get('ambiente', 'sandbox')
    if ambiente not in asaas.URLS:
        ambiente = 'sandbox'
    fiscal_sync.sistema_set('asaas_ambiente', ambiente)

    token = request.form.get('token', '').strip()
    if token:
        fiscal_sync.sistema_set(
            'asaas_token_cripto',
            fiscal_certificado.criptografar_senha(token, data_path('.chave_secreta')),
        )

    # Número do suporte: guardado só com dígitos, para montar o link do
    # WhatsApp sem depender de como o admin digitou.
    whatsapp = re.sub(r'\D', '', request.form.get('suporte_whatsapp', ''))
    if whatsapp and len(whatsapp) < 10:
        flash('Número de WhatsApp muito curto — informe com DDD.', 'erro')
        return redirect(url_for('admin_assinaturas'))
    fiscal_sync.sistema_set('suporte_whatsapp', whatsapp)

    valor = request.form.get('valor', '').strip().replace(',', '.')
    if valor:
        try:
            if float(valor) < 0:
                raise ValueError
            fiscal_sync.sistema_set('asaas_valor', f'{float(valor):.2f}')
        except ValueError:
            flash('Valor da mensalidade inválido — o resto foi salvo.', 'erro')
            return redirect(url_for('admin_assinaturas'))

    flash('Configuração do Asaas salva.', 'sucesso')
    return redirect(url_for('admin_assinaturas'))


@app.route('/admin/asaas/verificar', methods=['POST'])
@auth.exigir_papel(PAPEL_ADMIN)
def admin_asaas_verificar():
    try:
        resumo = asaas.verificar(
            data_path('.chave_secreta'),
            desde=datetime.now().date() - timedelta(days=90),
            url_base=os.environ.get('ERP_ASAAS_URL'),
        )
    except asaas.ErroAsaas as exc:
        fiscal_sync.sistema_set('asaas_ultimo_resultado', f'Erro: {exc}')
        flash(str(exc), 'erro')
        return redirect(url_for('admin_assinaturas'))

    fiscal_sync.sistema_set('asaas_ultima_verificacao', f'{datetime.now():%d/%m/%Y %H:%M}')
    fiscal_sync.sistema_set('asaas_ultimo_resultado', resumo)
    flash(resumo, 'sucesso')
    return redirect(url_for('admin_assinaturas'))


@app.route('/admin/clientes/<int:id>/asaas', methods=['POST'])
@auth.exigir_papel(PAPEL_ADMIN)
def admin_empresa_asaas(id):
    """Liga a empresa ao cliente correspondente no Asaas.

    É esse identificador que permite saber de quem é cada cobrança paga.
    """
    empresa = Empresa.query.get_or_404(id)
    empresa.asaas_cliente_id = request.form.get('asaas_cliente_id', '').strip() or None
    db.session.commit()
    flash(f'ID do cliente Asaas atualizado para "{empresa.nome}".', 'sucesso')
    return redirect(url_for('admin_empresas'))


def verificar_pagamentos_em_segundo_plano():
    """Confere o Asaas de tempos em tempos, sem ninguém precisar clicar.

    Falha de rede aqui não pode derrubar o servidor nem parar o laço: o
    resultado fica registrado e a próxima rodada tenta de novo.
    """
    def rodar():
        while True:
            time.sleep(asaas.INTERVALO_HORAS * 3600)
            with app.app_context():
                if not fiscal_sync.sistema_get('asaas_token_cripto'):
                    continue
                try:
                    resumo = asaas.verificar(
                        data_path('.chave_secreta'),
                        desde=datetime.now().date() - timedelta(days=90),
                        url_base=os.environ.get('ERP_ASAAS_URL'),
                    )
                    fiscal_sync.sistema_set('asaas_ultimo_resultado', resumo)
                except Exception as exc:
                    fiscal_sync.sistema_set('asaas_ultimo_resultado', f'Erro: {exc}')
                fiscal_sync.sistema_set(
                    'asaas_ultima_verificacao', f'{datetime.now():%d/%m/%Y %H:%M}'
                )

    threading.Thread(target=rodar, daemon=True).start()


# ------------------------------------------------------------------ #
# Área do contador
# ------------------------------------------------------------------ #

@app.route('/contador')
@auth.exigir_papel(PAPEL_CONTADOR)
def contador_clientes():
    usuario = auth.usuario_logado()
    session.pop('contador_empresa_id', None)
    return render_template(
        'contador_clientes.html',
        clientes=auth.empresas_do_contador(usuario),
        email=usuario.email,
    )


@app.route('/contador/abrir/<int:id>')
@auth.exigir_papel(PAPEL_CONTADOR)
def contador_abrir(id):
    usuario = auth.usuario_logado()
    empresa = Empresa.query.get_or_404(id)
    if not auth.contador_atende(usuario, empresa):
        flash('Esse cliente não indicou o seu e-mail na aba Contabilidade.', 'erro')
        return redirect(url_for('contador_clientes'))

    session['contador_empresa_id'] = empresa.id
    flash(f'Você está vendo os dados de {empresa.nome} (somente leitura).', 'sucesso')
    return redirect(url_for('inicio'))


def gerar_pendencias_de_todos():
    """Coloca em dia as recorrências e os lançamentos de notas de cada empresa.

    Roda na inicialização do servidor: como agora são vários clientes no mesmo
    processo, percorre empresa por empresa em vez de olhar um banco só.
    """
    for empresa in Empresa.query.all():
        gerar_lancamentos_recorrentes(empresa.id)
        fiscal_sync.gerar_lancamentos_pendentes(empresa.id)


# ------------------------------------------------------------------ #
# Primeira execução
# ------------------------------------------------------------------ #

# Contas criadas automaticamente quando o banco está vazio, para dar para
# entrar e testar sem precisar cadastrar nada na mão. As senhas são
# provisórias e estão no README — troque todas antes de colocar no ar.
CONTAS_DE_TESTE = [
    # (e-mail, senha, nome, papel, empresa, dias de assinatura)
    ('admin@teste.com.br', 'admin-teste-123', 'Administrador do Sistema', PAPEL_ADMIN, None, None),
    ('cliente1@teste.com.br', 'cliente-teste-123', 'Maria — Padaria Pão Quente',
     PAPEL_USUARIO, 'Padaria Pão Quente Ltda', 30),
    ('cliente2@teste.com.br', 'cliente-teste-123', 'João — Oficina Roda Livre',
     PAPEL_USUARIO, 'Oficina Roda Livre ME', 0),  # 0 = já vencido, para testar o bloqueio
    ('contador@teste.com.br', 'contador-teste-123', 'Carlos — Escritório Contábil',
     PAPEL_CONTADOR, None, None),
]


def criar_contas_de_teste():
    """Semeia as contas de exemplo — só quando ainda não existe usuário nenhum.

    Nunca mexe num banco que já tem gente: se houver qualquer usuário, sai sem
    fazer nada, para não recriar conta apagada de propósito nem trocar senha.
    """
    if Usuario.query.first() is not None:
        return False

    for email, senha, nome, papel, nome_empresa, dias in CONTAS_DE_TESTE:
        empresa = None
        if nome_empresa:
            empresa = Empresa(nome=nome_empresa)
            if dias:
                empresa.creditar_dias(dias)
            elif dias == 0:
                # Vencido ontem: serve para ver a tela de bloqueio
                empresa.assinatura_ate = date.today() - timedelta(days=1)
            db.session.add(empresa)
            db.session.flush()

        auth.criar_usuario(email, senha, nome, papel=papel, empresa=empresa)

    db.session.commit()

    # O cliente 1 já aponta para o contador de teste, para o vínculo existir
    # assim que alguém entrar — é o mesmo que o cliente faria na aba
    # Contabilidade, digitando o e-mail dele.
    primeiro = Usuario.query.filter_by(email='cliente1@teste.com.br').first()
    if primeiro and primeiro.empresa:
        fiscal_sync.config_set('email_contador', 'contador@teste.com.br',
                               empresa_id=primeiro.empresa_id)
    return True


def conferir_banco_compativel():
    """Recusa subir sobre um banco da versão antiga (sem multiusuário).

    Um erp.db do tempo do programa de mesa não tem a coluna empresa_id: se o
    servidor subisse assim, as telas quebrariam aos poucos em vez de avisar.
    """
    inspector = db.inspect(db.engine)
    tabelas = inspector.get_table_names()
    if 'transacao' not in tabelas:
        return  # banco novo, será criado do zero

    colunas = {c['name'] for c in inspector.get_columns('transacao')}
    if 'empresa_id' not in colunas:
        raise SystemExit(
            '\nEste banco de dados é da versão antiga (programa de mesa, sem login).\n'
            'O SaaS começa com base limpa: mova o erp.db antigo para outro lugar\n'
            'e suba o servidor de novo — as contas de teste serão criadas.\n'
        )


def iniciar_sistema():
    """Prepara o banco e deixa o servidor pronto para receber acesso."""
    with app.app_context():
        conferir_banco_compativel()
        db.create_all()
        migrar_schema()

        if criar_contas_de_teste():
            print('\n' + '=' * 62)
            print(' Banco vazio: contas de teste criadas.')
            for email, senha, _, papel, _, _ in CONTAS_DE_TESTE:
                print(f'   {papel:<9} {email:<26} senha: {senha}')
            print(' TROQUE ESSAS SENHAS ANTES DE COLOCAR NO AR.')
            print('=' * 62 + '\n')

        gerar_pendencias_de_todos()

    verificar_pagamentos_em_segundo_plano()


iniciar_sistema()


if __name__ == '__main__':
    # Servidor hospedado: sem abrir navegador e escutando em todas as
    # interfaces, para o serviço ficar acessível fora da máquina local.
    # Em produção, rode atrás de um servidor WSGI (gunicorn/waitress) e de um
    # proxy com HTTPS — o servidor embutido do Flask não é feito para isso.
    modo_debug = os.environ.get('ERP_DEBUG') == '1'
    app.run(host=os.environ.get('ERP_HOST', HOST), port=int(os.environ.get('ERP_PORT', PORT)),
            debug=modo_debug)
