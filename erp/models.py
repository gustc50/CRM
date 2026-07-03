from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Fornecedor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cnpj_cpf = db.Column(db.String(20), nullable=False)
    nome = db.Column(db.String(150), nullable=False)
    endereco = db.Column(db.String(200), nullable=False)


class Cliente(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cnpj_cpf = db.Column(db.String(20), nullable=False)
    nome = db.Column(db.String(150), nullable=False)
    endereco = db.Column(db.String(200), nullable=False)


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
