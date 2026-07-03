import csv
import io
import os
import re
import sys
import threading
import webbrowser
from datetime import datetime

from flask import Flask, Response, flash, redirect, render_template, request, url_for
from openpyxl import Workbook
from openpyxl.styles import Font

from models import Cliente, Fornecedor, Transacao, db

TIPOS_VALIDOS = {'Receber', 'Pagar'}
STATUS_VALIDOS = {'Pendente', 'Concluído'}
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


app = Flask(
    __name__,
    template_folder=resource_path('templates'),
    static_folder=resource_path('static'),
)
app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{data_path('erp.db')}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = os.urandom(24)  # suficiente para assinar as mensagens flash desta sessão
db.init_app(app)


def migrar_schema():
    """Adiciona colunas novas em bancos criados por versões anteriores do app."""
    inspector = db.inspect(db.engine)
    if 'transacao' not in inspector.get_table_names():
        return
    colunas = {c['name'] for c in inspector.get_columns('transacao')}
    comandos = {
        'data_pagamento': 'ALTER TABLE transacao ADD COLUMN data_pagamento DATE',
        'fornecedor_id': 'ALTER TABLE transacao ADD COLUMN fornecedor_id INTEGER',
        'cliente_id': 'ALTER TABLE transacao ADD COLUMN cliente_id INTEGER',
    }
    pendentes = [sql for coluna, sql in comandos.items() if coluna not in colunas]
    if pendentes:
        with db.engine.begin() as conn:
            for sql in pendentes:
                conn.execute(db.text(sql))


with app.app_context():
    db.create_all()
    migrar_schema()


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

    dados = {
        'tipo': tipo,
        'descricao': descricao,
        'valor': valor,
        'data_vencimento': data_vencimento,
        'fornecedor_id': fornecedor_id,
        'cliente_id': cliente_id,
    }
    return dados, None


def _validar_id_existente(valor, model):
    """Confirma que o id recebido do <select> corresponde a um registro real."""
    if not valor or not valor.isdigit():
        return None
    registro = model.query.get(int(valor))
    return registro.id if registro else None


def nome_entidade(transacao):
    """Nome do fornecedor (contas a pagar) ou cliente (contas a receber) do lançamento."""
    if transacao.tipo == 'Pagar':
        return transacao.fornecedor.nome if transacao.fornecedor else ''
    return transacao.cliente.nome if transacao.cliente else ''


app.jinja_env.globals['nome_entidade'] = nome_entidade


def validar_cadastro(form):
    """Valida os campos comuns ao cadastro de fornecedor/cliente."""
    cnpj_cpf = form.get('cnpj_cpf', '').strip()
    nome = form.get('nome', '').strip()
    endereco = form.get('endereco', '').strip()

    digitos = re.sub(r'\D', '', cnpj_cpf)
    if len(digitos) not in (11, 14):
        return None, 'CNPJ/CPF inválido (informe 11 dígitos para CPF ou 14 para CNPJ).'

    if not nome or len(nome) > 150:
        return None, 'Nome obrigatório (até 150 caracteres).'

    if not endereco or len(endereco) > 200:
        return None, 'Endereço obrigatório (até 200 caracteres).'

    dados = {'cnpj_cpf': cnpj_cpf, 'nome': nome, 'endereco': endereco}
    return dados, None


def registrar_rotas_cadastro(model, nome_singular, nome_plural, prefixo, coluna_fk):
    """Registra as rotas de listar/adicionar/editar/excluir para um cadastro
    simples (CNPJ/CPF, nome, endereço). Fornecedores e clientes usam
    exatamente a mesma lógica, então as rotas são geradas uma única vez
    aqui e reaproveitadas para os dois.

    `coluna_fk` é a coluna de Transacao que referencia esse cadastro
    (Transacao.fornecedor_id ou Transacao.cliente_id), usada para impedir
    a exclusão de um registro que já está vinculado a algum lançamento.
    """

    def listar():
        registros = model.query.order_by(model.nome).all()
        return render_template(
            'cadastro.html',
            registros=registros,
            titulo=nome_plural,
            titulo_singular=nome_singular,
            prefixo=prefixo,
        )

    def adicionar():
        dados, erro = validar_cadastro(request.form)
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
            dados, erro = validar_cadastro(request.form)
            if erro:
                flash(erro, 'erro')
                return redirect(url_for(f'{prefixo}_editar', id=id))

            registro.cnpj_cpf = dados['cnpj_cpf']
            registro.nome = dados['nome']
            registro.endereco = dados['endereco']
            db.session.commit()
            flash(f'{nome_singular} atualizado com sucesso.', 'sucesso')
            return redirect(url_for(f'{prefixo}_listar'))

        return render_template(
            'cadastro_editar.html',
            registro=registro,
            titulo_singular=nome_singular,
            prefixo=prefixo,
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

    app.add_url_rule(f'/{prefixo}', f'{prefixo}_listar', listar, methods=['GET'])
    app.add_url_rule(f'/{prefixo}/adicionar', f'{prefixo}_adicionar', adicionar, methods=['POST'])
    app.add_url_rule(f'/{prefixo}/editar/<int:id>', f'{prefixo}_editar', editar, methods=['GET', 'POST'])
    app.add_url_rule(f'/{prefixo}/excluir/<int:id>', f'{prefixo}_excluir', excluir, methods=['POST'])


registrar_rotas_cadastro(Fornecedor, 'Fornecedor', 'Fornecedores', 'fornecedores', Transacao.fornecedor_id)
registrar_rotas_cadastro(Cliente, 'Cliente', 'Clientes', 'clientes', Transacao.cliente_id)


def parse_data(valor):
    try:
        return datetime.strptime(valor, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None


def periodo_dos_parametros(args):
    """Lê inicio/fim/status da querystring, com padrão = mês atual completo."""
    hoje = datetime.now().date()
    inicio = parse_data(args.get('inicio', '')) or hoje.replace(day=1)
    fim = parse_data(args.get('fim', '')) or hoje
    if inicio > fim:
        inicio, fim = fim, inicio

    status_filtro = args.get('status', 'Todos')
    if status_filtro not in STATUS_VALIDOS:
        status_filtro = 'Todos'

    return inicio, fim, status_filtro


def transacoes_do_periodo(inicio, fim, status_filtro):
    query = Transacao.query.filter(
        Transacao.data_vencimento >= inicio,
        Transacao.data_vencimento <= fim,
    )
    if status_filtro in STATUS_VALIDOS:
        query = query.filter(Transacao.status == status_filtro)
    return query.order_by(Transacao.data_vencimento).all()


@app.route('/')
def index():
    transacoes = Transacao.query.order_by(Transacao.data_vencimento).all()
    fornecedores = Fornecedor.query.order_by(Fornecedor.nome).all()
    clientes = Cliente.query.order_by(Cliente.nome).all()

    total_receber = sum(t.valor for t in transacoes if t.tipo == 'Receber' and t.status == 'Pendente')
    total_pagar = sum(t.valor for t in transacoes if t.tipo == 'Pagar' and t.status == 'Pendente')

    return render_template(
        'index.html',
        transacoes=transacoes,
        total_receber=total_receber,
        total_pagar=total_pagar,
        fornecedores=fornecedores,
        clientes=clientes,
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

        transacao.tipo = dados['tipo']
        transacao.descricao = dados['descricao']
        transacao.valor = dados['valor']
        transacao.data_vencimento = dados['data_vencimento']
        transacao.status = status
        transacao.data_pagamento = data_pagamento
        transacao.fornecedor_id = dados['fornecedor_id']
        transacao.cliente_id = dados['cliente_id']
        db.session.commit()
        flash('Lançamento atualizado com sucesso.', 'sucesso')
        return redirect(url_for('index'))

    fornecedores = Fornecedor.query.order_by(Fornecedor.nome).all()
    clientes = Cliente.query.order_by(Cliente.nome).all()
    return render_template('editar.html', t=transacao, fornecedores=fornecedores, clientes=clientes)


@app.route('/excluir/<int:id>', methods=['POST'])
def excluir(id):
    transacao = Transacao.query.get_or_404(id)
    db.session.delete(transacao)
    db.session.commit()
    flash('Lançamento excluído.', 'sucesso')
    return redirect(url_for('index'))


@app.route('/concluir/<int:id>', methods=['POST'])
def concluir(id):
    transacao = Transacao.query.get_or_404(id)
    transacao.status = 'Concluído'
    transacao.data_pagamento = datetime.now().date()
    db.session.commit()
    return redirect(url_for('index'))


@app.route('/reabrir/<int:id>', methods=['POST'])
def reabrir(id):
    transacao = Transacao.query.get_or_404(id)
    transacao.status = 'Pendente'
    transacao.data_pagamento = None
    db.session.commit()
    return redirect(url_for('index'))


@app.route('/relatorio')
def relatorio():
    inicio, fim, status_filtro = periodo_dos_parametros(request.args)
    transacoes = transacoes_do_periodo(inicio, fim, status_filtro)

    total_entradas = sum(t.valor for t in transacoes if t.tipo == 'Receber')
    total_saidas = sum(t.valor for t in transacoes if t.tipo == 'Pagar')
    saldo = total_entradas - total_saidas

    return render_template(
        'relatorio.html',
        transacoes=transacoes,
        inicio=inicio,
        fim=fim,
        status_filtro=status_filtro,
        total_entradas=total_entradas,
        total_saidas=total_saidas,
        saldo=saldo,
    )


def _nome_arquivo(inicio, fim):
    return f'lancamentos_{inicio.isoformat()}_a_{fim.isoformat()}'


def _exportar_csv(transacoes, nome_arquivo):
    buffer = io.StringIO()
    buffer.write('﻿')  # BOM para o Excel abrir acentos corretamente
    writer = csv.writer(buffer, delimiter=';')
    writer.writerow(['Descrição', 'Tipo', 'Fornecedor/Cliente', 'Vencimento', 'Pagamento', 'Valor', 'Status'])
    for t in transacoes:
        writer.writerow([
            t.descricao,
            t.tipo,
            nome_entidade(t),
            t.data_vencimento.strftime('%d/%m/%Y'),
            t.data_pagamento.strftime('%d/%m/%Y') if t.data_pagamento else '',
            f'{t.valor:.2f}'.replace('.', ','),
            t.status,
        ])

    return Response(
        buffer.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{nome_arquivo}.csv"'},
    )


def _exportar_xlsx(transacoes, nome_arquivo):
    wb = Workbook()
    ws = wb.active
    ws.title = 'Lançamentos'

    ws.append(['Descrição', 'Tipo', 'Fornecedor/Cliente', 'Vencimento', 'Pagamento', 'Valor (R$)', 'Status'])
    for celula in ws[1]:
        celula.font = Font(bold=True)

    for t in transacoes:
        ws.append([
            t.descricao,
            t.tipo,
            nome_entidade(t),
            t.data_vencimento.strftime('%d/%m/%Y'),
            t.data_pagamento.strftime('%d/%m/%Y') if t.data_pagamento else '',
            t.valor,
            t.status,
        ])

    larguras = [30, 12, 24, 14, 14, 14, 14]
    for coluna, largura in zip('ABCDEFG', larguras):
        ws.column_dimensions[coluna].width = largura

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    return Response(
        buffer.getvalue(),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': f'attachment; filename="{nome_arquivo}.xlsx"'},
    )


@app.route('/relatorio/exportar')
def exportar_relatorio():
    inicio, fim, status_filtro = periodo_dos_parametros(request.args)
    transacoes = transacoes_do_periodo(inicio, fim, status_filtro)
    nome_arquivo = _nome_arquivo(inicio, fim)

    formato = request.args.get('formato', 'csv')
    if formato == 'xlsx':
        return _exportar_xlsx(transacoes, nome_arquivo)
    return _exportar_csv(transacoes, nome_arquivo)


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
