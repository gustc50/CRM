import calendar
import csv
import io
import os
import re
import sys
import threading
import uuid
import webbrowser
from datetime import date, datetime, timedelta

from flask import Flask, Response, flash, jsonify, redirect, render_template, request, send_from_directory, url_for
from openpyxl import Workbook
from openpyxl.styles import Font
from werkzeug.utils import secure_filename

import fiscal_certificado
import fiscal_nfe
import fiscal_nfse
import fiscal_sync
import ofx_parser
from models import (
    Categoria,
    Cliente,
    ContaBancaria,
    ContaMovimentacao,
    Fornecedor,
    LancamentoRecorrente,
    NotaEletronica,
    NotaServico,
    Transacao,
    db,
)

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

HOST = '127.0.0.1'
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


app = Flask(
    __name__,
    template_folder=resource_path('templates'),
    static_folder=resource_path('static'),
)
app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{data_path('erp.db')}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # limite de 10 MB por anexo
app.secret_key = os.urandom(24)  # suficiente para assinar as mensagens flash desta sessão
db.init_app(app)


@app.errorhandler(413)
def arquivo_muito_grande(_erro):
    flash('Arquivo muito grande. O limite por anexo é 10 MB.', 'erro')
    return redirect(request.referrer or url_for('index'))


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


with app.app_context():
    db.create_all()
    migrar_schema()

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
    registro = model.query.get(int(valor))
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
    registro = model.query.get(int(valor))
    if not registro:
        return None, False
    return registro.id, True


def _validar_categoria_opcional(valor, tipo_esperado):
    """Como `_validar_id_opcional`, mas também confere se o tipo da
    categoria (Pagar/Receber) bate com o tipo do lançamento."""
    categoria_id, ok = _validar_id_opcional(valor, Categoria)
    if not ok or categoria_id is None:
        return categoria_id, ok
    categoria = Categoria.query.get(categoria_id)
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


def validar_cadastro(form, tipo_categoria):
    """Valida os campos comuns ao cadastro de fornecedor/cliente.

    `tipo_categoria` é 'Pagar' (fornecedor) ou 'Receber' (cliente): restringe
    quais categorias podem ser escolhidas como padrão para este cadastro.
    """
    cnpj_cpf = form.get('cnpj_cpf', '').strip()
    nome = form.get('nome', '').strip()
    endereco = form.get('endereco', '').strip()
    telefone = form.get('telefone', '').strip()
    email = form.get('email', '').strip()

    digitos = re.sub(r'\D', '', cnpj_cpf)
    if len(digitos) not in (11, 14):
        return None, 'CNPJ/CPF inválido (informe 11 dígitos para CPF ou 14 para CNPJ).'

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


def _exportar_csv_generico(cabecalho, linhas, nome_arquivo):
    buffer = io.StringIO()
    buffer.write('﻿')  # BOM para o Excel abrir acentos corretamente
    writer = csv.writer(buffer, delimiter=';')
    writer.writerow(cabecalho)
    for linha in linhas:
        writer.writerow(linha)

    return Response(
        buffer.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{nome_arquivo}.csv"'},
    )


def _exportar_xlsx_generico(cabecalho, linhas, nome_arquivo, titulo_aba='Dados'):
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
    buffer.seek(0)

    return Response(
        buffer.getvalue(),
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
        return Categoria.query.filter_by(tipo=tipo_categoria).order_by(Categoria.nome).all()

    def _contas_bancarias():
        return ContaBancaria.query.order_by(ContaBancaria.nome).all()

    def listar():
        registros = model.query.order_by(model.nome).all()
        return render_template(
            'cadastro.html',
            registros=registros,
            titulo=nome_plural,
            titulo_singular=nome_singular,
            prefixo=prefixo,
            categorias=_categorias(),
            contas_bancarias=_contas_bancarias(),
        )

    def adicionar():
        dados, erro = validar_cadastro(request.form, tipo_categoria)
        if erro:
            flash(erro, 'erro')
        else:
            db.session.add(model(**dados))
            db.session.commit()
            flash(f'{nome_singular} cadastrado com sucesso.', 'sucesso')
        return redirect(url_for(f'{prefixo}_listar'))

    def editar(id):
        registro = model.query.get_or_404(id)

        if request.method == 'POST':
            dados, erro = validar_cadastro(request.form, tipo_categoria)
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
        registro = model.query.get_or_404(id)

        em_uso = Transacao.query.filter(coluna_fk == id).first() is not None
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
        registros = model.query.order_by(model.nome).all()
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
    registros = Categoria.query.order_by(Categoria.tipo, Categoria.nome).all()
    return render_template('categorias.html', registros=registros)


@app.route('/categorias/adicionar', methods=['POST'])
def categorias_adicionar():
    dados, erro = validar_categoria(request.form)
    if erro:
        flash(erro, 'erro')
    else:
        db.session.add(Categoria(**dados))
        db.session.commit()
        flash('Categoria cadastrada com sucesso.', 'sucesso')
    return redirect(url_for('categorias_listar'))


@app.route('/categorias/editar/<int:id>', methods=['GET', 'POST'])
def categorias_editar(id):
    registro = Categoria.query.get_or_404(id)
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
    registro = Categoria.query.get_or_404(id)
    em_uso = (
        Transacao.query.filter_by(categoria_id=id).first() is not None
        or Fornecedor.query.filter_by(categoria_id=id).first() is not None
        or Cliente.query.filter_by(categoria_id=id).first() is not None
        or LancamentoRecorrente.query.filter_by(categoria_id=id).first() is not None
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
    registros = ContaBancaria.query.order_by(ContaBancaria.nome).all()
    return render_template('contas.html', registros=registros)


@app.route('/contas/adicionar', methods=['POST'])
def contas_adicionar():
    dados, erro = validar_conta_bancaria(request.form)
    if erro:
        flash(erro, 'erro')
    else:
        db.session.add(ContaBancaria(**dados))
        db.session.commit()
        flash('Conta cadastrada com sucesso.', 'sucesso')
    return redirect(url_for('contas_listar'))


@app.route('/contas/editar/<int:id>', methods=['GET', 'POST'])
def contas_editar(id):
    registro = ContaBancaria.query.get_or_404(id)
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
    registro = ContaBancaria.query.get_or_404(id)
    em_uso = (
        Transacao.query.filter_by(conta_bancaria_id=id).first() is not None
        or LancamentoRecorrente.query.filter_by(conta_bancaria_id=id).first() is not None
    )
    if em_uso:
        flash('Não é possível excluir: existem lançamentos vinculados a esta conta.', 'erro')
        return redirect(url_for('contas_listar'))
    ContaMovimentacao.query.filter_by(conta_bancaria_id=id).delete()
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
    return ContaMovimentacao.query.filter(
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
    return Transacao.query.filter(
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
    conta = ContaBancaria.query.get_or_404(id)
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
    mov = ContaMovimentacao.query.filter_by(id=mov_id, conta_bancaria_id=id).first_or_404()
    destino = url_for('contas_movimentacoes', id=id, inicio=request.args.get('inicio', ''), fim=request.args.get('fim', ''))

    transacao_id = request.form.get('transacao_id', '')
    if not transacao_id.isdigit():
        flash('Selecione um lançamento para vincular a esta movimentação.', 'erro')
        return redirect(destino)

    transacao = Transacao.query.get(int(transacao_id))
    if not transacao:
        flash('Lançamento não encontrado.', 'erro')
        return redirect(destino)

    tipo_esperado = 'Receber' if mov.tipo == 'CREDITO' else 'Pagar'
    if transacao.tipo != tipo_esperado:
        flash('Esse lançamento não é compatível com o tipo do movimento (crédito → Receber, débito → Pagar).', 'erro')
        return redirect(destino)

    outra_movimentacao = ContaMovimentacao.query.filter(
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
    mov = ContaMovimentacao.query.filter_by(id=mov_id, conta_bancaria_id=id).first_or_404()
    mov.transacao_id = None
    db.session.commit()
    flash('Vínculo removido. O lançamento mantém o status atual — reabra-o manualmente se necessário.', 'sucesso')
    return redirect(url_for('contas_movimentacoes', id=id, inicio=request.args.get('inicio', ''), fim=request.args.get('fim', '')))


@app.route('/contas/<int:id>/importar-ofx', methods=['POST'])
def contas_importar_ofx(id):
    conta = ContaBancaria.query.get_or_404(id)
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
        existe = ContaMovimentacao.query.filter_by(conta_bancaria_id=id, fitid=mov.fitid).first()
        if existe:
            continue
        db.session.add(ContaMovimentacao(
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
    ContaBancaria.query.get_or_404(id)
    total = ContaMovimentacao.query.filter_by(conta_bancaria_id=id).delete()
    db.session.commit()
    flash(f'{total} movimentação(ões) removida(s). Você pode importar o OFX novamente.', 'sucesso')
    return redirect(url_for('contas_movimentacoes', id=id))


@app.route('/contas/<int:id>/movimentacoes/exportar')
def contas_movimentacoes_exportar(id):
    conta = ContaBancaria.query.get_or_404(id)
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


def gerar_lancamentos_recorrentes():
    """Cria os lançamentos de cada mês em que uma recorrência ativa ainda não
    gerou um lançamento, até o mês atual (permite "colocar em dia" caso o
    programa fique um tempo sem ser aberto). Idempotente: pode ser chamada
    quantas vezes for preciso sem duplicar lançamentos.
    """
    competencia_atual = competencia(datetime.now().date())
    total_gerado = 0

    for tpl in LancamentoRecorrente.query.filter_by(ativo=True).all():
        proxima = (
            proxima_competencia(tpl.ultima_geracao_mes)
            if tpl.ultima_geracao_mes
            else competencia_atual
        )
        while proxima <= competencia_atual:
            vencimento = data_da_competencia(proxima, tpl.dia_vencimento)
            ja_existe = Transacao.query.filter_by(recorrente_id=tpl.id, data_vencimento=vencimento).first()
            if not ja_existe:
                db.session.add(Transacao(
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


@app.route('/')
def index():
    todas_transacoes = Transacao.query.all()
    total_receber = sum(t.valor for t in todas_transacoes if t.tipo == 'Receber' and t.status == 'Pendente')
    total_pagar = sum(t.valor for t in todas_transacoes if t.tipo == 'Pagar' and t.status == 'Pendente')
    sem_categoria_count = sum(1 for t in todas_transacoes if t.categoria_id is None)

    contas_com_saldo = ContaBancaria.query.filter(ContaBancaria.saldo.isnot(None)).all()
    saldo_contas = sum(c.saldo for c in contas_com_saldo) if contas_com_saldo else None
    saldo_contas_data = max((c.saldo_data for c in contas_com_saldo if c.saldo_data), default=None)

    busca = request.args.get('busca', '').strip()
    filtro_tipo = request.args.get('filtro_tipo', 'Todos')
    filtro_status = request.args.get('filtro_status', 'Todos')
    filtro_categoria = request.args.get('filtro_categoria', '')
    filtro_conta = request.args.get('filtro_conta', '')

    query = Transacao.query
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

    transacoes = query.order_by(Transacao.data_vencimento).all()

    return render_template(
        'index.html',
        transacoes=transacoes,
        total_receber=total_receber,
        total_pagar=total_pagar,
        sem_categoria_count=sem_categoria_count,
        saldo_contas=saldo_contas,
        saldo_contas_data=saldo_contas_data,
        fornecedores=Fornecedor.query.order_by(Fornecedor.nome).all(),
        clientes=Cliente.query.order_by(Cliente.nome).all(),
        categorias=Categoria.query.order_by(Categoria.nome).all(),
        categorias_pagar=Categoria.query.filter_by(tipo='Pagar').order_by(Categoria.nome).all(),
        categorias_receber=Categoria.query.filter_by(tipo='Receber').order_by(Categoria.nome).all(),
        contas_bancarias=ContaBancaria.query.order_by(ContaBancaria.nome).all(),
        hoje=datetime.now().date(),
        busca=busca,
        filtro_tipo=filtro_tipo,
        filtro_status=filtro_status,
        filtro_categoria=filtro_categoria,
        filtro_conta=filtro_conta,
    )


@app.route('/adicionar', methods=['POST'])
def adicionar():
    dados, erro = validar_transacao(request.form)
    if erro:
        flash(erro, 'erro')
        return redirect(url_for('index'))

    db.session.add(Transacao(**dados))
    db.session.commit()
    flash('Lançamento adicionado com sucesso.', 'sucesso')
    return redirect(url_for('index'))


@app.route('/editar/<int:id>', methods=['GET', 'POST'])
def editar(id):
    transacao = Transacao.query.get_or_404(id)

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
        return redirect(url_for('index'))

    return render_template(
        'editar.html',
        t=transacao,
        fornecedores=Fornecedor.query.order_by(Fornecedor.nome).all(),
        clientes=Cliente.query.order_by(Cliente.nome).all(),
        categorias_pagar=Categoria.query.filter_by(tipo='Pagar').order_by(Categoria.nome).all(),
        categorias_receber=Categoria.query.filter_by(tipo='Receber').order_by(Categoria.nome).all(),
        contas_bancarias=ContaBancaria.query.order_by(ContaBancaria.nome).all(),
    )


@app.route('/excluir/<int:id>', methods=['POST'])
def excluir(id):
    transacao = Transacao.query.get_or_404(id)
    if transacao.anexo_arquivo:
        _apagar_anexo(transacao.anexo_arquivo)
    # Uma nota fiscal vinculada a este lançamento volta a permitir "Gerar Lançamento"
    NotaServico.query.filter_by(transacao_id=id).update({'transacao_id': None})
    NotaEletronica.query.filter_by(transacao_id=id).update({'transacao_id': None})
    db.session.delete(transacao)
    db.session.commit()
    flash('Lançamento excluído.', 'sucesso')
    return redirect(url_for('index'))


def _destino_voltar():
    """Rota de retorno pós-ação (permite dar baixa a partir dos painéis de notas)."""
    voltar = request.form.get('voltar', '')
    if voltar.startswith('/') and not voltar.startswith('//'):
        return voltar
    return url_for('index')


@app.route('/concluir/<int:id>', methods=['POST'])
def concluir(id):
    transacao = Transacao.query.get_or_404(id)

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
    transacao = Transacao.query.get_or_404(id)
    transacao.status = 'Pendente'
    transacao.data_pagamento = None
    db.session.commit()
    return redirect(url_for('index'))


@app.route('/anexos/<int:id>')
def baixar_anexo(id):
    transacao = Transacao.query.get_or_404(id)
    if not transacao.anexo_arquivo:
        flash('Este lançamento não possui anexo.', 'erro')
        return redirect(url_for('index'))
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
    query = Transacao.query.filter(
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
        categorias=Categoria.query.order_by(Categoria.tipo, Categoria.nome).all(),
        contas_bancarias=ContaBancaria.query.order_by(ContaBancaria.nome).all(),
        atalhos=atalhos,
        hoje=hoje,
    )


def _nome_arquivo(inicio, fim):
    return f'lancamentos_{inicio.isoformat()}_a_{fim.isoformat()}'


def _exportar_csv(transacoes, nome_arquivo):
    cabecalho = ['Descrição', 'Tipo', 'Fornecedor/Cliente', 'Categoria', 'Centro de Custo', 'Conta', 'Vencimento', 'Pagamento', 'Valor', 'Status']
    linhas = [
        [
            t.descricao,
            t.tipo,
            nome_entidade(t),
            t.categoria.nome if t.categoria else '',
            (t.categoria.centro_custo or '') if t.categoria else '',
            t.conta_bancaria.nome if t.conta_bancaria else '',
            t.data_vencimento.strftime('%d/%m/%Y'),
            t.data_pagamento.strftime('%d/%m/%Y') if t.data_pagamento else '',
            f'{t.valor:.2f}'.replace('.', ','),
            t.status,
        ]
        for t in transacoes
    ]
    return _exportar_csv_generico(cabecalho, linhas, nome_arquivo)


def _exportar_xlsx(transacoes, nome_arquivo):
    cabecalho = ['Descrição', 'Tipo', 'Fornecedor/Cliente', 'Categoria', 'Centro de Custo', 'Conta', 'Vencimento', 'Pagamento', 'Valor (R$)', 'Status']
    linhas = [
        [
            t.descricao,
            t.tipo,
            nome_entidade(t),
            t.categoria.nome if t.categoria else '',
            (t.categoria.centro_custo or '') if t.categoria else '',
            t.conta_bancaria.nome if t.conta_bancaria else '',
            t.data_vencimento.strftime('%d/%m/%Y'),
            t.data_pagamento.strftime('%d/%m/%Y') if t.data_pagamento else '',
            t.valor,
            t.status,
        ]
        for t in transacoes
    ]
    return _exportar_xlsx_generico(cabecalho, linhas, nome_arquivo, 'Lançamentos')


@app.route('/relatorio/exportar')
def exportar_relatorio():
    inicio, fim, status_filtro, categoria_filtro, conta_filtro = periodo_dos_parametros(request.args)
    transacoes = transacoes_do_periodo(inicio, fim, status_filtro, categoria_filtro, conta_filtro)
    nome_arquivo = _nome_arquivo(inicio, fim)

    formato = request.args.get('formato', 'csv')
    if formato == 'xlsx':
        return _exportar_xlsx(transacoes, nome_arquivo)
    return _exportar_csv(transacoes, nome_arquivo)


@app.route('/recorrentes')
def recorrentes_listar():
    registros = LancamentoRecorrente.query.order_by(LancamentoRecorrente.descricao).all()
    return render_template(
        'recorrentes.html',
        registros=registros,
        fornecedores=Fornecedor.query.order_by(Fornecedor.nome).all(),
        clientes=Cliente.query.order_by(Cliente.nome).all(),
        categorias_pagar=Categoria.query.filter_by(tipo='Pagar').order_by(Categoria.nome).all(),
        categorias_receber=Categoria.query.filter_by(tipo='Receber').order_by(Categoria.nome).all(),
        contas_bancarias=ContaBancaria.query.order_by(ContaBancaria.nome).all(),
    )


@app.route('/recorrentes/adicionar', methods=['POST'])
def recorrentes_adicionar():
    dados, erro = validar_recorrente(request.form)
    if erro:
        flash(erro, 'erro')
        return redirect(url_for('recorrentes_listar'))

    novo = LancamentoRecorrente(**dados)
    novo.ultima_geracao_mes = competencia_anterior(competencia(datetime.now().date()))
    db.session.add(novo)
    db.session.commit()
    flash('Lançamento recorrente cadastrado com sucesso.', 'sucesso')
    return redirect(url_for('recorrentes_listar'))


@app.route('/recorrentes/editar/<int:id>', methods=['GET', 'POST'])
def recorrentes_editar(id):
    tpl = LancamentoRecorrente.query.get_or_404(id)

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
        fornecedores=Fornecedor.query.order_by(Fornecedor.nome).all(),
        clientes=Cliente.query.order_by(Cliente.nome).all(),
        categorias_pagar=Categoria.query.filter_by(tipo='Pagar').order_by(Categoria.nome).all(),
        categorias_receber=Categoria.query.filter_by(tipo='Receber').order_by(Categoria.nome).all(),
        contas_bancarias=ContaBancaria.query.order_by(ContaBancaria.nome).all(),
    )


@app.route('/recorrentes/excluir/<int:id>', methods=['POST'])
def recorrentes_excluir(id):
    tpl = LancamentoRecorrente.query.get_or_404(id)
    db.session.delete(tpl)
    db.session.commit()
    flash('Lançamento recorrente excluído.', 'sucesso')
    return redirect(url_for('recorrentes_listar'))


@app.route('/recorrentes/gerar', methods=['POST'])
def recorrentes_gerar():
    total = gerar_lancamentos_recorrentes()
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


app.jinja_env.globals['situacao_nfe_texto'] = situacao_nfe_texto


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

    query = NotaServico.query.filter(
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
        fora_do_periodo=NotaServico.query.count() - len(notas),
        totais_emitidas=_totais(emitidas),
        totais_recebidas=_totais(recebidas),
        sync=sync_fiscal.status('nfse'),
        ultima_sync=fiscal_sync.config_get('nfse_ultima_sync'),
        ultimo_status=fiscal_sync.config_get('nfse_ultimo_status'),
        **_contexto_fiscal_comum(),
    )


@app.route('/notas-eletronicas')
def notas_eletronicas():
    inicio, fim = _periodo_simples(request.args)

    # Notas sem data de emissão sempre aparecem (não dá para saber o período)
    query = NotaEletronica.query.filter(
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
        fora_do_periodo=NotaEletronica.query.count() - len(notas),
        totais_emitidas=_totais(emitidas),
        totais_recebidas=_totais(recebidas),
        sync=sync_fiscal.status('nfe'),
        ultima_sync=fiscal_sync.config_get('nfe_ultima_sync'),
        ultimo_status=fiscal_sync.config_get('nfe_ultimo_status'),
        minutos_espera=sync_fiscal.nfe_minutos_de_espera(),
        **_contexto_fiscal_comum(),
    )


@app.route('/notas-servico/sincronizar', methods=['POST'])
def nfse_sincronizar():
    if not fiscal_sync.certificado_configurado():
        flash('Configure o certificado digital na aba Configurações antes de sincronizar.', 'erro')
        return redirect(url_for('configuracoes'))
    if sync_fiscal.iniciar('nfse'):
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
    espera = sync_fiscal.nfe_minutos_de_espera()
    if espera > 0 and not forcar:
        flash(
            f'A SEFAZ exige intervalo de 1 hora entre consultas sem novidade. '
            f'Aguarde ~{espera} min — as notas já baixadas continuam disponíveis abaixo.',
            'erro',
        )
        return redirect(url_for('notas_eletronicas'))

    if sync_fiscal.iniciar('nfe'):
        flash('Sincronização das NF-e iniciada.', 'sucesso')
    else:
        flash('Já existe uma sincronização de NF-e em andamento.', 'erro')
    return redirect(url_for('notas_eletronicas'))


@app.route('/notas-servico/sincronizacao')
def nfse_status_sincronizacao():
    return jsonify(sync_fiscal.status('nfse'))


@app.route('/notas-eletronicas/sincronizacao')
def nfe_status_sincronizacao():
    return jsonify(sync_fiscal.status('nfe'))


@app.route('/notas-servico/<int:id>/xml')
def nfse_baixar_xml(id):
    nota = NotaServico.query.get_or_404(id)
    if not nota.xml_gzip:
        flash('XML desta nota não está disponível.', 'erro')
        return redirect(url_for('notas_servico'))
    import gzip as _gzip
    return Response(
        _gzip.decompress(nota.xml_gzip),
        mimetype='application/xml',
        headers={'Content-Disposition': f'attachment; filename="NFSe_{nota.chave_acesso}.xml"'},
    )


@app.route('/notas-eletronicas/<int:id>/xml')
def nfe_baixar_xml(id):
    nota = NotaEletronica.query.get_or_404(id)
    if not nota.xml_gzip:
        flash('XML desta nota não está disponível.', 'erro')
        return redirect(url_for('notas_eletronicas'))
    import gzip as _gzip
    return Response(
        _gzip.decompress(nota.xml_gzip),
        mimetype='application/xml',
        headers={'Content-Disposition': f'attachment; filename="NFe_{nota.chave}.xml"'},
    )


@app.route('/notas-servico/<int:id>/danfse')
def nfse_baixar_danfse(id):
    nota = NotaServico.query.get_or_404(id)
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
    nota = NotaServico.query.get_or_404(id)
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
    nota = NotaEletronica.query.get_or_404(id)
    if str(nota.situacao or '') == '3':
        flash('Não é possível gerar lançamento de uma nota cancelada.', 'erro')
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
            pasta = data_path('certificados')
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
    )


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


with app.app_context():
    gerar_lancamentos_recorrentes()
    # Cobre notas fiscais sincronizadas antes de a geração automática existir
    fiscal_sync.gerar_lancamentos_pendentes()


def _abrir_navegador():
    webbrowser.open(f'http://{HOST}:{PORT}')


if __name__ == '__main__':
    if getattr(sys, 'frozen', False):
        # Executável empacotado: sem reloader (ele tentaria reabrir o próprio
        # .exe e entraria em loop) e com o navegador abrindo sozinho.
        threading.Timer(1.0, _abrir_navegador).start()
        app.run(host=HOST, port=PORT, debug=False, use_reloader=False)
    else:
        app.run(host=HOST, port=PORT, debug=True)
