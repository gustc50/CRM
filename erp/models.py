from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Transacao(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.String(20), nullable=False)  # 'Receber' ou 'Pagar'
    descricao = db.Column(db.String(100), nullable=False)
    valor = db.Column(db.Float, nullable=False)
    data_vencimento = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), default='Pendente')  # 'Pendente' ou 'Concluído'
