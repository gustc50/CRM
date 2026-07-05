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

from flask import Flask, Response, flash, redirect, render_template, request, send_from_directory, url_for
from openpyxl import Workbook
from openpyxl.styles import Font
from werkzeug.utils import secure_filename

from models import Categoria, Cliente, ContaBancaria, Fornecedor, LancamentoRecorrente, Transacao, db

TIPOS_VALIDOS = {'Receber', 'Pagar'}
STATUS_VALIDOS = {'Pendente', 'Concluído'}
EXTENSOES_ANEXO_PERMITIDAS = {'pdf', 'png', 'jpg', 'jpeg', 'gif', 'webp'}
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
        if pendentes_tabela:
            with db.engine.begin() as conn:
                for sql in pendentes_tabela:
                    conn.execute(db.text(sql))


with app.app_context():
    db.create_all()
    migrar_schema()


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

    categoria_id, categoria_ok = _validar_id_opcional(form.get('categoria_id', ''), Categoria)
    if not categoria_ok:
        return None, 'Categoria inválida.'

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


def validar_cadastro(form):
    """Valida os campos comuns ao cadastro de fornecedor/cliente."""
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

    dados = {
        'cnpj_cpf': cnpj_cpf,
        'nome': nome,
        'endereco': endereco,
        'telefone': telefone or None,
        'email': email or None,
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


def registrar_rotas_cadastro(model, nome_singular, nome_plural, prefixo, coluna_fk):
    """Registra as rotas de listar/adicionar/editar/excluir/exportar para um
    cadastro completo (CNPJ/CPF, nome, endereço, telefone, e-mail).
    Fornecedores e clientes usam exatamente a mesma lógica, então as rotas
    são geradas uma única vez aqui e reaproveitadas para os dois.

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
            registro.telefone = dados['telefone']
            registro.email = dados['email']
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

    def exportar():
        registros = model.query.order_by(model.nome).all()
        cabecalho = ['CNPJ/CPF', 'Nome', 'Endereço', 'Telefone', 'E-mail']
        linhas = [
            [r.cnpj_cpf, r.nome, r.endereco, r.telefone or '', r.email or '']
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


registrar_rotas_cadastro(Fornecedor, 'Fornecedor', 'Fornecedores', 'fornecedores', Transacao.fornecedor_id)
registrar_rotas_cadastro(Cliente, 'Cliente', 'Clientes', 'clientes', Transacao.cliente_id)


def validar_nome_simples(form, campo_maxlen):
    nome = form.get('nome', '').strip()
    if not nome or len(nome) > campo_maxlen:
        return None, f'Nome obrigatório (até {campo_maxlen} caracteres).'
    return {'nome': nome}, None


def registrar_rotas_simples(model, nome_singular, nome_plural, prefixo, coluna_fk, campo_maxlen):
    """Registra o CRUD de um cadastro com um único campo (nome): Categoria e
    Conta Bancária. `coluna_fk` impede excluir um registro já em uso.
    """

    def listar():
        registros = model.query.order_by(model.nome).all()
        return render_template(
            'simples.html',
            registros=registros,
            titulo=nome_plural,
            titulo_singular=nome_singular,
            prefixo=prefixo,
        )

    def adicionar():
        dados, erro = validar_nome_simples(request.form, campo_maxlen)
        if erro:
            flash(erro, 'erro')
        else:
            db.session.add(model(**dados))
            db.session.commit()
            flash(f'{nome_singular} cadastrada com sucesso.', 'sucesso')
        return redirect(url_for(f'{prefixo}_listar'))

    def editar(id):
        registro = model.query.get_or_404(id)
        if request.method == 'POST':
            dados, erro = validar_nome_simples(request.form, campo_maxlen)
            if erro:
                flash(erro, 'erro')
                return redirect(url_for(f'{prefixo}_editar', id=id))
            registro.nome = dados['nome']
            db.session.commit()
            flash(f'{nome_singular} atualizada com sucesso.', 'sucesso')
            return redirect(url_for(f'{prefixo}_listar'))
        return render_template(
            'simples_editar.html',
            registro=registro,
            titulo_singular=nome_singular,
            prefixo=prefixo,
        )

    def excluir(id):
        registro = model.query.get_or_404(id)
        em_uso = Transacao.query.filter(coluna_fk == id).first() is not None
        if em_uso:
            flash(
                f'Não é possível excluir: existem lançamentos vinculados a esta {nome_singular.lower()}.',
                'erro',
            )
            return redirect(url_for(f'{prefixo}_listar'))
        db.session.delete(registro)
        db.session.commit()
        flash(f'{nome_singular} excluída.', 'sucesso')
        return redirect(url_for(f'{prefixo}_listar'))

    app.add_url_rule(f'/{prefixo}', f'{prefixo}_listar', listar, methods=['GET'])
    app.add_url_rule(f'/{prefixo}/adicionar', f'{prefixo}_adicionar', adicionar, methods=['POST'])
    app.add_url_rule(f'/{prefixo}/editar/<int:id>', f'{prefixo}_editar', editar, methods=['GET', 'POST'])
    app.add_url_rule(f'/{prefixo}/excluir/<int:id>', f'{prefixo}_excluir', excluir, methods=['POST'])


registrar_rotas_simples(Categoria, 'Categoria', 'Categorias', 'categorias', Transacao.categoria_id, campo_maxlen=50)
registrar_rotas_simples(ContaBancaria, 'Conta', 'Contas Bancárias', 'contas', Transacao.conta_bancaria_id, campo_maxlen=80)


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

    categoria_id, categoria_ok = _validar_id_opcional(form.get('categoria_id', ''), Categoria)
    if not categoria_ok:
        return None, 'Categoria inválida.'

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

    busca = request.args.get('busca', '').strip()
    filtro_tipo = request.args.get('filtro_tipo', 'Todos')
    filtro_status = request.args.get('filtro_status', 'Todos')
    filtro_categoria = request.args.get('filtro_categoria', '')

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
    if filtro_categoria.isdigit():
        query = query.filter(Transacao.categoria_id == int(filtro_categoria))

    transacoes = query.order_by(Transacao.data_vencimento).all()

    return render_template(
        'index.html',
        transacoes=transacoes,
        total_receber=total_receber,
        total_pagar=total_pagar,
        fornecedores=Fornecedor.query.order_by(Fornecedor.nome).all(),
        clientes=Cliente.query.order_by(Cliente.nome).all(),
        categorias=Categoria.query.order_by(Categoria.nome).all(),
        contas_bancarias=ContaBancaria.query.order_by(ContaBancaria.nome).all(),
        hoje=datetime.now().date(),
        busca=busca,
        filtro_tipo=filtro_tipo,
        filtro_status=filtro_status,
        filtro_categoria=filtro_categoria,
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
        categorias=Categoria.query.order_by(Categoria.nome).all(),
        contas_bancarias=ContaBancaria.query.order_by(ContaBancaria.nome).all(),
    )


@app.route('/excluir/<int:id>', methods=['POST'])
def excluir(id):
    transacao = Transacao.query.get_or_404(id)
    if transacao.anexo_arquivo:
        _apagar_anexo(transacao.anexo_arquivo)
    db.session.delete(transacao)
    db.session.commit()
    flash('Lançamento excluído.', 'sucesso')
    return redirect(url_for('index'))


@app.route('/concluir/<int:id>', methods=['POST'])
def concluir(id):
    transacao = Transacao.query.get_or_404(id)

    data_pagamento = parse_data(request.form.get('data_pagamento', '').strip())
    if not data_pagamento:
        flash('Informe a data em que o pagamento foi realizado para dar baixa.', 'erro')
        return redirect(url_for('index'))

    transacao.status = 'Concluído'
    transacao.data_pagamento = data_pagamento
    db.session.commit()
    return redirect(url_for('index'))


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
    if not categoria_filtro.isdigit():
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
    if categoria_filtro:
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
        categorias=Categoria.query.order_by(Categoria.nome).all(),
        contas_bancarias=ContaBancaria.query.order_by(ContaBancaria.nome).all(),
        atalhos=atalhos,
        hoje=hoje,
    )


def _nome_arquivo(inicio, fim):
    return f'lancamentos_{inicio.isoformat()}_a_{fim.isoformat()}'


def _exportar_csv(transacoes, nome_arquivo):
    cabecalho = ['Descrição', 'Tipo', 'Fornecedor/Cliente', 'Categoria', 'Conta', 'Vencimento', 'Pagamento', 'Valor', 'Status']
    linhas = [
        [
            t.descricao,
            t.tipo,
            nome_entidade(t),
            t.categoria.nome if t.categoria else '',
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
    cabecalho = ['Descrição', 'Tipo', 'Fornecedor/Cliente', 'Categoria', 'Conta', 'Vencimento', 'Pagamento', 'Valor (R$)', 'Status']
    linhas = [
        [
            t.descricao,
            t.tipo,
            nome_entidade(t),
            t.categoria.nome if t.categoria else '',
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
        categorias=Categoria.query.order_by(Categoria.nome).all(),
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
        categorias=Categoria.query.order_by(Categoria.nome).all(),
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


with app.app_context():
    gerar_lancamentos_recorrentes()


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
