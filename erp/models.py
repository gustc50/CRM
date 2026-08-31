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


class Configuracao(db.Model):
    """Par chave/valor para as configurações do sistema (certificado digital etc.)."""
    id = db.Column(db.Integer, primary_key=True)
    chave = db.Column(db.String(60), unique=True, nullable=False)
    valor = db.Column(db.Text, nullable=True)


class NotaServico(db.Model):
    """NFS-e baixada do Ambiente de Dados Nacional (Portal Nacional da NFS-e)."""
    id = db.Column(db.Integer, primary_key=True)
    chave_acesso = db.Column(db.String(60), unique=True, nullable=False)
    nsu = db.Column(db.Integer)
    papel = db.Column(db.String(15), nullable=False)  # 'emitida' (a receber) | 'recebida' (a pagar)
    numero = db.Column(db.String(20))
    serie = db.Column(db.String(10))
    data_emissao = db.Column(db.Date)
    competencia = db.Column(db.String(10))
    prestador_doc = db.Column(db.String(20))
    prestador_nome = db.Column(db.String(200))
    tomador_doc = db.Column(db.String(20))
    tomador_nome = db.Column(db.String(200))
    municipio = db.Column(db.String(120))
    descricao_servico = db.Column(db.Text)
    valor_servico = db.Column(db.Float)
    valor_liquido = db.Column(db.Float)
    valor_iss = db.Column(db.Float)
    situacao = db.Column(db.String(15), nullable=False, default='ATIVA')  # ATIVA | CANCELADA | SUBSTITUIDA
    xml_gzip = db.Column(db.LargeBinary)

    transacao_id = db.Column(db.Integer, db.ForeignKey('transacao.id'), nullable=True)
    transacao = db.relationship('Transacao')


class NotaServicoEvento(db.Model):
    """Eventos da NFS-e (cancelamentos/substituições) recebidos na distribuição."""
    id = db.Column(db.Integer, primary_key=True)
    chave_acesso = db.Column(db.String(60), nullable=False)
    nsu = db.Column(db.Integer)
    tipo_evento = db.Column(db.String(10))
    descricao = db.Column(db.String(300))
    dh_evento = db.Column(db.String(40))
    __table_args__ = (db.UniqueConstraint('chave_acesso', 'tipo_evento', 'nsu'),)


class NotaEletronica(db.Model):
    """NF-e distribuída pelo Ambiente Nacional (webservice NFeDistribuicaoDFe)."""
    id = db.Column(db.Integer, primary_key=True)
    chave = db.Column(db.String(60), unique=True, nullable=False)
    nsu = db.Column(db.String(20))
    tipo_doc = db.Column(db.String(10), nullable=False)  # 'resNFe' (resumo) | 'nfeProc' (completa)
    papel = db.Column(db.String(15), nullable=False)  # 'emitida' (a receber) | 'recebida' (a pagar)
    emitente_doc = db.Column(db.String(20))
    emitente_nome = db.Column(db.String(200))
    dest_doc = db.Column(db.String(20))
    dest_nome = db.Column(db.String(200))
    valor_total = db.Column(db.Float)
    data_emissao = db.Column(db.Date)
    situacao = db.Column(db.String(20))
    xml_gzip = db.Column(db.LargeBinary)
    baixado_em = db.Column(db.String(30))

    transacao_id = db.Column(db.Integer, db.ForeignKey('transacao.id'), nullable=True)
    transacao = db.relationship('Transacao')
