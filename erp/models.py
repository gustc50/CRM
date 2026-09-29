import datetime as dt

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

# Papéis de usuário
PAPEL_ADMIN = 'admin'        # dono do sistema: gerencia clientes e assinaturas
PAPEL_USUARIO = 'user'       # cliente final, dono de uma empresa
PAPEL_CONTADOR = 'contador'  # vê, só de leitura, os clientes que o indicaram
PAPEIS = (PAPEL_ADMIN, PAPEL_USUARIO, PAPEL_CONTADOR)

# Cargo de quem trabalha na empresa cliente. Contador não entra aqui de
# propósito: ele tem cadastro próprio, é liberado pelo e-mail informado na aba
# Contabilidade e enxerga os dados somente de leitura.
CARGO_SOCIO = 'socio'
CARGO_FUNCIONARIO = 'funcionario'
CARGOS = (
    (CARGO_SOCIO, 'Sócio'),
    (CARGO_FUNCIONARIO, 'Funcionário'),
)

# Quantos usuários cabem na mensalidade antes de começar a cobrar por cabeça
USUARIOS_INCLUSOS = 3

# Cada pagamento confirmado no Asaas libera este tanto de acesso
DIAS_POR_PAGAMENTO = 30

# Login: quantos erros seguidos trancam a conta, e por quanto tempo
TENTATIVAS_ATE_BLOQUEIO = 4
MINUTOS_DE_BLOQUEIO = 30


class Empresa(db.Model):
    """O cliente do SaaS: é a ele que pertence todo dado financeiro.

    Tudo no sistema (lançamentos, notas, contas, configurações) carrega o
    `empresa_id` — é o que impede um cliente de enxergar o outro.
    """
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(150), nullable=False)
    cnpj = db.Column(db.String(20), nullable=True)
    criada_em = db.Column(db.DateTime, nullable=False, default=dt.datetime.now)

    # Endereço de cobrança, preenchido no autocadastro. O CEP fica separado
    # porque o Asaas exige o dele para emitir boleto.
    endereco = db.Column(db.String(200), nullable=True)
    cep = db.Column(db.String(10), nullable=True)
    cidade = db.Column(db.String(80), nullable=True)
    uf = db.Column(db.String(2), nullable=True)
    telefone = db.Column(db.String(20), nullable=True)
    email = db.Column(db.String(150), nullable=True)

    # Assinatura: o acesso vale enquanto hoje <= assinatura_ate
    assinatura_ate = db.Column(db.Date, nullable=True)
    # Identificador do cliente no Asaas, para casar os pagamentos recebidos
    asaas_cliente_id = db.Column(db.String(60), nullable=True)

    @property
    def assinatura_em_dia(self):
        return self.assinatura_ate is not None and self.assinatura_ate >= dt.date.today()

    @property
    def dias_restantes(self):
        if self.assinatura_ate is None:
            return None
        return (self.assinatura_ate - dt.date.today()).days

    @property
    def usuarios_ativos(self):
        """Quantas pessoas da empresa têm acesso (o contador não entra: ele
        não é da empresa e não ocupa vaga)."""
        return len([u for u in self.usuarios if u.papel == PAPEL_USUARIO])

    @property
    def usuarios_extras(self):
        """Quantos passam do que a mensalidade já inclui."""
        return max(0, self.usuarios_ativos - USUARIOS_INCLUSOS)

    @property
    def vagas_livres(self):
        return max(0, USUARIOS_INCLUSOS - self.usuarios_ativos)

    def creditar_dias(self, dias=DIAS_POR_PAGAMENTO, a_partir_de=None):
        """Estende a assinatura. Se ainda está em dia, soma ao prazo que resta;
        se já venceu, conta a partir de hoje (não devolve o tempo parado)."""
        base = a_partir_de or dt.date.today()
        if self.assinatura_ate and self.assinatura_ate > base:
            base = self.assinatura_ate
        self.assinatura_ate = base + dt.timedelta(days=dias)
        return self.assinatura_ate


class Usuario(db.Model):
    """Conta de acesso. O papel decide o que a pessoa enxerga."""
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    senha_hash = db.Column(db.String(255), nullable=False)
    nome = db.Column(db.String(150), nullable=False)
    papel = db.Column(db.String(20), nullable=False, default=PAPEL_USUARIO)

    # Bloqueio manual pelo administrador (diferente do bloqueio por falta de
    # pagamento, que é calculado pela validade da assinatura da empresa)
    ativo = db.Column(db.Boolean, nullable=False, default=True)

    criado_em = db.Column(db.DateTime, nullable=False, default=dt.datetime.now)
    ultimo_acesso = db.Column(db.DateTime, nullable=True)

    # Dados da pessoa, preenchidos no autocadastro. Interessam sobretudo ao
    # contador: ele não tem empresa, então é aqui que ficam documento e
    # endereço dele. Para o cliente, esses dados ficam na Empresa, que é
    # quem paga a assinatura.
    cpf_cnpj = db.Column(db.String(20), nullable=True)
    endereco = db.Column(db.String(200), nullable=True)
    cep = db.Column(db.String(10), nullable=True)
    cidade = db.Column(db.String(80), nullable=True)
    uf = db.Column(db.String(2), nullable=True)
    telefone = db.Column(db.String(20), nullable=True)

    # Só o papel 'user' tem empresa; admin e contador não têm dados próprios
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=True)
    empresa = db.relationship('Empresa', backref='usuarios')

    # O que a pessoa é dentro da empresa. Só o sócio administra os usuários —
    # funcionário usa o sistema mas não convida nem remove ninguém.
    cargo = db.Column(db.String(20), nullable=True)

    # Freio contra tentativa de adivinhar senha. Fica no banco, e não na
    # memória, para o bloqueio sobreviver a um reinício do servidor e para o
    # administrador conseguir enxergar e liberar.
    tentativas_erradas = db.Column(db.Integer, nullable=False, default=0)
    bloqueado_ate = db.Column(db.DateTime, nullable=True)

    @property
    def eh_admin(self):
        return self.papel == PAPEL_ADMIN

    @property
    def eh_contador(self):
        return self.papel == PAPEL_CONTADOR

    @property
    def eh_socio(self):
        return self.papel == PAPEL_USUARIO and self.cargo == CARGO_SOCIO

    @property
    def em_castigo(self):
        """Trancado por errar a senha várias vezes seguidas."""
        return self.bloqueado_ate is not None and self.bloqueado_ate > dt.datetime.now()

    @property
    def minutos_de_castigo(self):
        if not self.em_castigo:
            return 0
        faltam = (self.bloqueado_ate - dt.datetime.now()).total_seconds() / 60
        return max(1, int(faltam + 0.5))

    def errou_a_senha(self):
        """Conta o erro e, no limite, tranca a conta por um tempo."""
        self.tentativas_erradas = (self.tentativas_erradas or 0) + 1
        if self.tentativas_erradas >= TENTATIVAS_ATE_BLOQUEIO:
            self.bloqueado_ate = dt.datetime.now() + dt.timedelta(minutes=MINUTOS_DE_BLOQUEIO)
            self.tentativas_erradas = 0
        return self.em_castigo

    def acertou_a_senha(self):
        self.tentativas_erradas = 0
        self.bloqueado_ate = None


class Pagamento(db.Model):
    """Cobrança confirmada no Asaas que já rendeu dias de acesso.

    O `asaas_id` é único: é ele que impede a mesma cobrança de creditar
    30 dias duas vezes quando a consulta periódica a encontrar de novo.
    """
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False)
    empresa = db.relationship('Empresa', backref='pagamentos')
    asaas_id = db.Column(db.String(60), unique=True, nullable=False)
    valor = db.Column(db.Float, nullable=True)
    pago_em = db.Column(db.Date, nullable=True)
    situacao = db.Column(db.String(30), nullable=True)
    registrado_em = db.Column(db.DateTime, nullable=False, default=dt.datetime.now)
    # Até quando a assinatura passou a valer por causa deste pagamento
    liberado_ate = db.Column(db.Date, nullable=True)


class Categoria(db.Model):
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False, index=True)
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(50), nullable=False)
    tipo = db.Column(db.String(20), nullable=False, default='Pagar')  # 'Receber' | 'Pagar'
    centro_custo = db.Column(db.String(80), nullable=True)


class Fornecedor(db.Model):
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False, index=True)
    id = db.Column(db.Integer, primary_key=True)
    cnpj_cpf = db.Column(db.String(20), nullable=False)
    nome = db.Column(db.String(150), nullable=False)
    endereco = db.Column(db.String(200), nullable=False)
    telefone = db.Column(db.String(20), nullable=True)
    email = db.Column(db.String(150), nullable=True)

    # Categoria sugerida automaticamente ao lançar uma conta a pagar para este fornecedor
    categoria_id = db.Column(db.Integer, db.ForeignKey('categoria.id'), nullable=True)
    categoria = db.relationship('Categoria')

    # Conta bancária sugerida automaticamente ao lançar uma conta a pagar para este fornecedor
    conta_bancaria_id = db.Column(db.Integer, db.ForeignKey('conta_bancaria.id'), nullable=True)
    conta_bancaria = db.relationship('ContaBancaria')


class Cliente(db.Model):
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False, index=True)
    id = db.Column(db.Integer, primary_key=True)
    cnpj_cpf = db.Column(db.String(20), nullable=False)
    nome = db.Column(db.String(150), nullable=False)
    endereco = db.Column(db.String(200), nullable=False)
    telefone = db.Column(db.String(20), nullable=True)
    email = db.Column(db.String(150), nullable=True)

    # Categoria sugerida automaticamente ao lançar uma conta a receber deste cliente
    categoria_id = db.Column(db.Integer, db.ForeignKey('categoria.id'), nullable=True)
    categoria = db.relationship('Categoria')

    # Conta bancária sugerida automaticamente ao lançar uma conta a receber deste cliente
    conta_bancaria_id = db.Column(db.Integer, db.ForeignKey('conta_bancaria.id'), nullable=True)
    conta_bancaria = db.relationship('ContaBancaria')


class ContaBancaria(db.Model):
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False, index=True)
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(80), nullable=False)
    banco = db.Column(db.String(10), nullable=True)  # código Febraban (ou vazio p/ caixa/dinheiro)
    agencia = db.Column(db.String(20), nullable=True)
    conta_numero = db.Column(db.String(30), nullable=True)
    saldo = db.Column(db.Float, nullable=True)  # saldo informado no último OFX importado
    saldo_data = db.Column(db.Date, nullable=True)


class ContaMovimentacao(db.Model):
    """Lançamento do extrato bancário, importado de um arquivo OFX."""
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False, index=True)
    id = db.Column(db.Integer, primary_key=True)
    conta_bancaria_id = db.Column(db.Integer, db.ForeignKey('conta_bancaria.id'), nullable=False)
    conta_bancaria = db.relationship('ContaBancaria')
    fitid = db.Column(db.String(80), nullable=False)  # id único do banco (evita importar 2x)
    data = db.Column(db.Date, nullable=False)
    descricao = db.Column(db.String(200), nullable=False)
    valor = db.Column(db.Float, nullable=False)
    tipo = db.Column(db.String(10), nullable=False)  # 'CREDITO' | 'DEBITO'
    __table_args__ = (db.UniqueConstraint('conta_bancaria_id', 'fitid'),)

    # Conciliação: lançamento (Transacao) confirmado como correspondente a
    # este movimento do extrato. Preenchido pela sugestão automática ou pela
    # escolha manual do usuário na tela de Movimentações.
    transacao_id = db.Column(db.Integer, db.ForeignKey('transacao.id'), nullable=True)
    transacao = db.relationship('Transacao')


class Transacao(db.Model):
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False, index=True)
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
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False, index=True)
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


class RegistroAuditoria(db.Model):
    """Quem fez o quê, nas ações que mexem em dinheiro ou em acesso.

    Não registra navegação nem consulta: só o que altera algo e, por isso,
    alguém pode precisar explicar depois. Com mais de uma pessoa usando a
    mesma empresa, é o que responde "quem deu baixa nessa conta?".
    """
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=True, index=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=True)
    usuario = db.relationship('Usuario')
    quando = db.Column(db.DateTime, nullable=False, default=dt.datetime.now, index=True)
    acao = db.Column(db.String(40), nullable=False)
    detalhe = db.Column(db.String(300), nullable=True)


class ConfiguracaoSistema(db.Model):
    """Par chave/valor que vale para o sistema inteiro, não para uma empresa.

    É onde ficam os ajustes do administrador (token do Asaas, resultado da
    última verificação de pagamentos). Fica separado da `Configuracao` de
    propósito: aquela é sempre de uma empresa, e misturar as duas abriria
    espaço para um cliente enxergar ou sobrescrever ajuste do sistema.
    """
    id = db.Column(db.Integer, primary_key=True)
    chave = db.Column(db.String(60), nullable=False, unique=True)
    valor = db.Column(db.Text, nullable=True)


class Configuracao(db.Model):
    """Par chave/valor para as configurações de UMA empresa (certificado digital etc.)."""
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False, index=True)
    id = db.Column(db.Integer, primary_key=True)
    chave = db.Column(db.String(60), nullable=False)
    valor = db.Column(db.Text, nullable=True)
    __table_args__ = (db.UniqueConstraint('empresa_id', 'chave'),)


class NotaServico(db.Model):
    """NFS-e baixada do Ambiente de Dados Nacional (Portal Nacional da NFS-e)."""
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False, index=True)
    id = db.Column(db.Integer, primary_key=True)
    chave_acesso = db.Column(db.String(60), nullable=False)
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
    transacao = db.relationship('Transacao', backref=db.backref('nota_servico', uselist=False))

    # A mesma nota pode existir para duas empresas (uma emitiu, a outra recebeu),
    # mas nunca duas vezes dentro da mesma — é o que evita duplicar na sincronização.
    __table_args__ = (db.UniqueConstraint('empresa_id', 'chave_acesso'),)


class NotaServicoEvento(db.Model):
    """Eventos da NFS-e (cancelamentos/substituições) recebidos na distribuição."""
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False, index=True)
    id = db.Column(db.Integer, primary_key=True)
    chave_acesso = db.Column(db.String(60), nullable=False)
    nsu = db.Column(db.Integer)
    tipo_evento = db.Column(db.String(10))
    descricao = db.Column(db.String(300))
    dh_evento = db.Column(db.String(40))
    __table_args__ = (db.UniqueConstraint('empresa_id', 'chave_acesso', 'tipo_evento', 'nsu'),)


class NotaEletronica(db.Model):
    """NF-e distribuída pelo Ambiente Nacional (webservice NFeDistribuicaoDFe)."""
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False, index=True)
    id = db.Column(db.Integer, primary_key=True)
    chave = db.Column(db.String(60), nullable=False)
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

    # Manifestação do destinatário (Ciência da Operação). Enquanto não é dada,
    # a SEFAZ só distribui o resumo das notas de compra, sem o XML completo.
    manifestacao_em = db.Column(db.String(30), nullable=True)
    manifestacao_protocolo = db.Column(db.String(30), nullable=True)

    transacao_id = db.Column(db.Integer, db.ForeignKey('transacao.id'), nullable=True)
    transacao = db.relationship('Transacao', backref=db.backref('nota_eletronica', uselist=False))

    __table_args__ = (db.UniqueConstraint('empresa_id', 'chave'),)
