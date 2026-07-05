from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Fornecedor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cnpj_cpf = db.Column(db.String(20), nullable=False)
    nome = db.Column(db.String(150), nullable=False)
    endereco = db.Column(db.String(200), nullable=False)
    telefone = db.Column(db.String(20), nullable=True)
    email = db.Column(db.String(150), nullable=True)


class Cliente(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cnpj_cpf = db.Column(db.String(20), nullable=False)
    nome = db.Column(db.String(150), nullable=False)
    endereco = db.Column(db.String(200), nullable=False)
    telefone = db.Column(db.String(20), nullable=True)
    email = db.Column(db.String(150), nullable=True)


class Categoria(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(50), nullable=False)


class ContaBancaria(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(80), nullable=False)


class Transacao(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.String(20), nullable=False)  # 'Receber' ou 'Pagar'
    descricao = db.Column(db.String(100), nullable=False)
    valor = db.Column(db.Float, nullable=False)
    data_vencimento = db.Column(db.Date, nullable=False)
    data_pagamento = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(20), default='Pendente')  # 'Pendente' ou 'Concluído'

    # Preenchido apenas quando tipo == 'Pagar'
    fornecedor_id = db.Column(db.Integer, db.ForeignKey('fornecedor.id'), nullable=True)
    fornecedor = db.relationship('Fornecedor')

    # Preenchido apenas quando tipo == 'Receber'
    cliente_id = db.Column(db.Integer, db.ForeignKey('cliente.id'), nullable=True)
    cliente = db.relationship('Cliente')

    categoria_id = db.Column(db.Integer, db.ForeignKey('categoria.id'), nullable=True)
    categoria = db.relationship('Categoria')

    conta_bancaria_id = db.Column(db.Integer, db.ForeignKey('conta_bancaria.id'), nullable=True)
    conta_bancaria = db.relationship('ContaBancaria')

    # Preenchido quando o lançamento foi gerado a partir de um LancamentoRecorrente
    recorrente_id = db.Column(db.Integer, nullable=True)

    # Comprovante/nota fiscal anexado (arquivo gravado na pasta "anexos")
    anexo_arquivo = db.Column(db.String(255), nullable=True)
    anexo_nome_original = db.Column(db.String(255), nullable=True)


class LancamentoRecorrente(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.String(20), nullable=False)
    descricao = db.Column(db.String(100), nullable=False)
    valor = db.Column(db.Float, nullable=False)
    dia_vencimento = db.Column(db.Integer, nullable=False)  # 1 a 31
    ativo = db.Column(db.Boolean, nullable=False, default=True)

    # Mês/ano (formato AAAAMM) da última competência já gerada como lançamento
    ultima_geracao_mes = db.Column(db.Integer, nullable=True)

    fornecedor_id = db.Column(db.Integer, db.ForeignKey('fornecedor.id'), nullable=True)
    fornecedor = db.relationship('Fornecedor')

    cliente_id = db.Column(db.Integer, db.ForeignKey('cliente.id'), nullable=True)
    cliente = db.relationship('Cliente')

    categoria_id = db.Column(db.Integer, db.ForeignKey('categoria.id'), nullable=True)
    categoria = db.relationship('Categoria')

    conta_bancaria_id = db.Column(db.Integer, db.ForeignKey('conta_bancaria.id'), nullable=True)
    conta_bancaria = db.relationship('ContaBancaria')
