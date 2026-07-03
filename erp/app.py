import os
import sys
import threading
import webbrowser
from datetime import datetime

from flask import Flask, render_template, request, redirect, url_for, flash

from models import db, Transacao

TIPOS_VALIDOS = {'Receber', 'Pagar'}
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


@app.route('/')
def index():
    transacoes = Transacao.query.order_by(Transacao.data_vencimento).all()

    total_receber = sum(t.valor for t in transacoes if t.tipo == 'Receber' and t.status == 'Pendente')
    total_pagar = sum(t.valor for t in transacoes if t.tipo == 'Pagar' and t.status == 'Pendente')

    return render_template('index.html', transacoes=transacoes, total_receber=total_receber, total_pagar=total_pagar)


@app.route('/adicionar', methods=['POST'])
def adicionar():
    tipo = request.form.get('tipo', '')
    descricao = request.form.get('descricao', '').strip()
    valor_str = request.form.get('valor', '')
    data_str = request.form.get('data_vencimento', '')

    if tipo not in TIPOS_VALIDOS:
        flash('Tipo de lançamento inválido.', 'erro')
        return redirect(url_for('index'))

    if not descricao or len(descricao) > 100:
        flash('Descrição obrigatória (até 100 caracteres).', 'erro')
        return redirect(url_for('index'))

    try:
        valor = float(valor_str)
    except (TypeError, ValueError):
        flash('Valor inválido.', 'erro')
        return redirect(url_for('index'))

    if valor <= 0:
        flash('O valor deve ser maior que zero.', 'erro')
        return redirect(url_for('index'))

    try:
        data_vencimento = datetime.strptime(data_str, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        flash('Data de vencimento inválida.', 'erro')
        return redirect(url_for('index'))

    nova_transacao = Transacao(
        tipo=tipo,
        descricao=descricao,
        valor=valor,
        data_vencimento=data_vencimento,
    )
    db.session.add(nova_transacao)
    db.session.commit()
    return redirect(url_for('index'))


@app.route('/concluir/<int:id>', methods=['POST'])
def concluir(id):
    transacao = Transacao.query.get_or_404(id)
    transacao.status = 'Concluído'
    db.session.commit()
    return redirect(url_for('index'))


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
