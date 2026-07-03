import csv
import io
import os
import sys
import threading
import webbrowser
from datetime import datetime

from flask import Flask, Response, flash, redirect, render_template, request, url_for
from openpyxl import Workbook
from openpyxl.styles import Font

from models import db, Transacao

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

with app.app_context():
    db.create_all()


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

    dados = {
        'tipo': tipo,
        'descricao': descricao,
        'valor': valor,
        'data_vencimento': data_vencimento,
    }
    return dados, None


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

    total_receber = sum(t.valor for t in transacoes if t.tipo == 'Receber' and t.status == 'Pendente')
    total_pagar = sum(t.valor for t in transacoes if t.tipo == 'Pagar' and t.status == 'Pendente')

    return render_template('index.html', transacoes=transacoes, total_receber=total_receber, total_pagar=total_pagar)


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

        transacao.tipo = dados['tipo']
        transacao.descricao = dados['descricao']
        transacao.valor = dados['valor']
        transacao.data_vencimento = dados['data_vencimento']
        transacao.status = status
        db.session.commit()
        flash('Lançamento atualizado com sucesso.', 'sucesso')
        return redirect(url_for('index'))

    return render_template('editar.html', t=transacao)


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
    db.session.commit()
    return redirect(url_for('index'))


@app.route('/reabrir/<int:id>', methods=['POST'])
def reabrir(id):
    transacao = Transacao.query.get_or_404(id)
    transacao.status = 'Pendente'
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
    writer.writerow(['Descrição', 'Tipo', 'Vencimento', 'Valor', 'Status'])
    for t in transacoes:
        writer.writerow([
            t.descricao,
            t.tipo,
            t.data_vencimento.strftime('%d/%m/%Y'),
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

    ws.append(['Descrição', 'Tipo', 'Vencimento', 'Valor (R$)', 'Status'])
    for celula in ws[1]:
        celula.font = Font(bold=True)

    for t in transacoes:
        ws.append([
            t.descricao,
            t.tipo,
            t.data_vencimento.strftime('%d/%m/%Y'),
            t.valor,
            t.status,
        ])

    larguras = [30, 12, 14, 14, 14]
    for coluna, largura in zip('ABCDE', larguras):
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
