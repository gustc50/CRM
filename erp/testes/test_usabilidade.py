"""Rodada de usabilidade: valores em reais, confirmações, formulários que não
perdem o que foi digitado, fornecedor opcional, Lançamentos por mês, menu e
Configurações do cliente.

    cd erp
    rm -f erp.db .chave_sessao && rm -rf backups
    python3 testes/test_usabilidade.py
"""
import datetime as dt
import pathlib
import re
import sys

RAIZ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import app as appmod  # noqa: E402
import formatos  # noqa: E402
from models import (  # noqa: E402
    Cliente, ConfiguracaoSistema, Fornecedor, LancamentoRecorrente, Transacao, Usuario, db,
)

falhas = []
HOJE = dt.date.today()


def checar(condicao, descricao):
    print(('  OK    ' if condicao else '  FALHA ') + descricao)
    if not condicao:
        falhas.append(descricao)


def entrar(email, senha):
    c = appmod.app.test_client()
    c.post('/login', data={'email': email, 'senha': senha}, follow_redirects=True)
    return c


def texto(resposta):
    return resposta.get_data(as_text=True)


def lancar(c, **campos):
    dados = {'tipo': 'Pagar', 'descricao': 'Teste', 'valor': '10,00',
             'data_vencimento': HOJE.isoformat()}
    dados.update(campos)
    return c.post('/adicionar', data=dados)


with appmod.app.app_context():
    eid = Usuario.query.filter_by(email='cliente1@teste.com.br').first().empresa_id

c = entrar('cliente1@teste.com.br', 'cliente-teste-123')

# ================================================================ #
print('--- 1) valor em reais ---')
casos = {'150,50': 150.5, '1.234,56': 1234.56, 'R$ 1.234,56': 1234.56, '150.50': 150.5,
         '1.234': 1234, '1.234.567,89': 1234567.89, '0,5': 0.5,
         'abc': None, '1,234': None, '1.2.3,45': None, '-5': None, '': None}
erradas = {k: formatos.ler_valor(k) for k, v in casos.items() if formatos.ler_valor(k) != v}
checar(not erradas, f'ler_valor entende o formato brasileiro e recusa o ambíguo {erradas or ""}')
checar(formatos.moeda(1234.5) == 'R$ 1.234,50' and formatos.moeda(-10) == '-R$ 10,00',
       'moeda escreve R$ 1.234,50')

lancar(c, descricao='Virgula', valor='150,50')
lancar(c, descricao='Milhar', valor='1.234,56')
with appmod.app.app_context():
    v1 = Transacao.query.filter_by(descricao='Virgula').first()
    v2 = Transacao.query.filter_by(descricao='Milhar').first()
    checar(v1 is not None and v1.valor == 150.5, f'"150,50" grava 150,50, não 15.050 ({v1 and v1.valor})')
    checar(v2 is not None and v2.valor == 1234.56, '"1.234,56" grava 1.234,56')

corpo = texto(c.get('/lancamentos'))
checar('R$ 1.234,56' in corpo and 'R$ 150,50' in corpo, 'a lista mostra R$ 1.234,56')
checar('1234.56' not in corpo.replace('data-valor', ''), 'e não mais o formato americano 1234.56')
checar('type="number" step="0.01"' not in corpo, 'o campo de valor não é mais type=number')

# ================================================================ #
print('\n--- 2) erro não apaga o que foi digitado ---')
r = lancar(c, descricao='Conta de luz de outubro', valor='abc')
checar(r.status_code == 302, 'valor inválido volta para a tela')
corpo = texto(c.get('/lancamentos'))
checar('Valor inválido' in corpo, 'com a mensagem do que está errado')
checar('value="Conta de luz de outubro"' in corpo and 'value="abc"' in corpo,
       'e com a descrição e o valor que a pessoa tinha digitado')
corpo = texto(c.get('/lancamentos'))
checar('value="Conta de luz de outubro"' not in corpo, 'só na volta do erro: depois o formulário vem limpo')

r = c.post('/login', data={'email': 'cliente1@teste.com.br', 'senha': 'errada'})
corpo = texto(appmod.app.test_client().get('/login'))  # outra sessão não vê nada
checar('cliente1@teste.com.br' not in corpo, 'o e-mail guardado é só da sessão de quem errou')
x = appmod.app.test_client()
x.post('/login', data={'email': 'cliente2@teste.com.br', 'senha': 'errada'})
corpo = texto(x.get('/login'))
checar('value="cliente2@teste.com.br"' in corpo, 'senha errada no login mantém o e-mail preenchido')
checar('errada' not in corpo, 'mas nunca a senha')

with appmod.app.app_context():
    tid = Transacao.query.filter_by(descricao='Virgula').first().id
r = c.post(f'/editar/{tid}', data={'tipo': 'Pagar', 'descricao': 'Editado à mão', 'valor': 'xyz',
                                   'data_vencimento': HOJE.isoformat(), 'status': 'Pendente',
                                   'voltar': '/lancamentos?filtro_tipo=Pagar'})
checar('/editar/' in r.headers['Location'] and 'voltar=' in r.headers['Location'],
       'erro na edição volta para a edição, lembrando para onde voltar depois')
corpo = texto(c.get(r.headers['Location']))
checar('value="Editado à mão"' in corpo, 'com o texto que a pessoa tinha mudado')

c.post('/fornecedores/adicionar', data={'nome': 'Fornecedor com erro', 'cnpj_cpf': '123'})
corpo = texto(c.get('/fornecedores'))
checar('value="Fornecedor com erro"' in corpo and 'CNPJ/CPF inválido' in corpo,
       'cadastro de fornecedor com CNPJ errado volta preenchido')

# ================================================================ #
print('\n--- 3) fornecedor e cliente opcionais, e cadastro na hora ---')
lancar(c, descricao='Tarifa bancária', valor='15,90')
with appmod.app.app_context():
    t = Transacao.query.filter_by(descricao='Tarifa bancária').first()
    checar(t is not None and t.fornecedor_id is None, 'lança uma conta a pagar sem fornecedor')

r = lancar(c, descricao='Tarifa DOC', fornecedor_id=appmod.NOVO_CADASTRO, fornecedor_novo='Banco do Brasil')
checar('cadastrado só com o nome' in texto(c.get('/lancamentos')), 'avisa que o fornecedor novo foi criado só com o nome')
lancar(c, descricao='Tarifa TED', fornecedor_id=appmod.NOVO_CADASTRO, fornecedor_novo='banco do brasil')
with appmod.app.app_context():
    bbs = Fornecedor.query.filter(db.func.lower(Fornecedor.nome) == 'banco do brasil',
                                  Fornecedor.empresa_id == eid).all()
    checar(len(bbs) == 1 and bbs[0].cnpj_cpf == '' and bbs[0].endereco == '',
           'cria o fornecedor só com o nome, e o mesmo nome não duplica')
    doc = Transacao.query.filter_by(descricao='Tarifa DOC').first()
    ted = Transacao.query.filter_by(descricao='Tarifa TED').first()
    checar(doc.fornecedor_id == ted.fornecedor_id == bbs[0].id, 'os dois lançamentos ficam ligados a ele')

c.post('/adicionar', data={'tipo': 'Receber', 'descricao': 'Venda avulsa', 'valor': '50',
                           'data_vencimento': HOJE.isoformat(),
                           'cliente_id': appmod.NOVO_CADASTRO, 'cliente_novo': 'Dona Maria'})
with appmod.app.app_context():
    venda = Transacao.query.filter_by(descricao='Venda avulsa').first()
    checar(venda.cliente and venda.cliente.nome == 'Dona Maria', 'o mesmo vale para cliente novo')

lancar(c, descricao='Sem nome', fornecedor_id=appmod.NOVO_CADASTRO, fornecedor_novo='  ')
checar('Digite o nome do novo fornecedor' in texto(c.get('/lancamentos')),
       '"cadastrar novo" sem nome é recusado com explicação')

with appmod.app.app_context():
    outro = Fornecedor(empresa_id=eid + 1000, nome='De outra empresa', cnpj_cpf='', endereco='')
    db.session.add(outro)
    db.session.commit()
    id_outro = outro.id
lancar(c, descricao='Fornecedor alheio', fornecedor_id=str(id_outro))
with appmod.app.app_context():
    checar(Transacao.query.filter_by(descricao='Fornecedor alheio').first() is None,
           'fornecedor de outra empresa enfiado no formulário é recusado')

c.post('/recorrentes/adicionar', data={'tipo': 'Pagar', 'descricao': 'Aluguel', 'valor': '2.500,00',
                                       'dia_vencimento': '5', 'ativo': 'on'})
with appmod.app.app_context():
    rec = LancamentoRecorrente.query.filter_by(descricao='Aluguel').first()
    checar(rec is not None and rec.valor == 2500 and rec.fornecedor_id is None,
           'recorrente aceita "2.500,00" e fornecedor em branco')

c.post('/fornecedores/adicionar', data={'nome': 'Padaria da esquina'})
c.post('/fornecedores/adicionar', data={'nome': 'Feira do sábado'})
corpo = texto(c.get('/fornecedores'))
checar('Padaria da esquina' in corpo and 'Feira do sábado' in corpo,
       'fornecedor só com o nome (sem CNPJ e sem endereço) é aceito')
checar('repetido' not in corpo, 'e dois sem CNPJ não são marcados como "repetido"')

# ================================================================ #
print('\n--- 4) confirmação de exclusão segura ---')
pasta = RAIZ / 'templates'
inline = [a.name for a in pasta.glob('*.html') if 'return confirm(' in a.read_text()]
checar(not inline, f'nenhuma tela usa mais confirm() escrito no HTML {inline or ""}')
lancar(c, descricao="Conta d'água", valor='89,90')
corpo = texto(c.get('/lancamentos'))
checar('data-confirmar="Excluir o lançamento \'Conta d&#39;água\'?' in corpo,
       'o nome com apóstrofo vai como atributo escapado, sem quebrar a pergunta')
checar(re.search(r'<script src="/static/app\.js', corpo) is not None, 'e o app.js que faz a pergunta está na página')

# ================================================================ #
print('\n--- 5) voltar para a lista com os filtros ---')
with appmod.app.app_context():
    tid = Transacao.query.filter_by(descricao='Tarifa bancária').first().id
r = c.post(f'/concluir/{tid}', data={'data_pagamento': HOJE.isoformat(),
                                     'voltar': '/lancamentos?filtro_tipo=Pagar&busca=Tarifa'})
checar(r.headers['Location'].endswith('/lancamentos?filtro_tipo=Pagar&busca=Tarifa'),
       'dar baixa volta para a lista filtrada')
r = c.post(f'/reabrir/{tid}', data={'voltar': '//golpe.example.com/'})
checar(r.headers['Location'].endswith('/lancamentos'), 'endereço de fora no "voltar" é ignorado')
r = c.post(f'/reabrir/{tid}', data={'voltar': '/\\golpe.example.com'})
checar(r.headers['Location'].endswith('/lancamentos'), 'inclusive com barra invertida')
corpo = texto(c.get('/lancamentos?filtro_tipo=Pagar'))
checar('voltar=/lancamentos?filtro_tipo%3DPagar' in corpo or 'voltar=%2Flancamentos%3Ffiltro_tipo%3DPagar' in corpo,
       'o Editar leva junto o endereço da lista filtrada')

# ================================================================ #
print('\n--- 6) Lançamentos abre no mês atual ---')
inicio_mes = HOJE.replace(day=1)
mes_passado = inicio_mes - dt.timedelta(days=10)
mes_que_vem = inicio_mes + dt.timedelta(days=40)
with appmod.app.app_context():
    for desc, dia, status in (('Vencida do mês passado', mes_passado, 'Pendente'),
                              ('Paga do mês passado', mes_passado, 'Concluído'),
                              ('Do mês que vem', mes_que_vem, 'Pendente')):
        db.session.add(Transacao(empresa_id=eid, tipo='Pagar', descricao=desc, valor=77.0,
                                 data_vencimento=dia, status=status))
    db.session.commit()
corpo = texto(c.get('/lancamentos'))
checar('Do mês que vem' not in corpo and 'Vencida do mês passado' not in corpo,
       'sem filtro, mostra só o mês atual')
checar(re.search(r'Há <strong>1 conta\(s\) vencida\(s\)</strong> de antes deste período', corpo) is not None,
       'e avisa da vencida do mês passado que ficou de fora')
corpo = texto(c.get('/lancamentos?periodo=tudo'))
checar('Do mês que vem' in corpo and 'Vencida do mês passado' in corpo, '"Tudo" mostra todos os períodos')
checar('<h2>Lançamentos <span class="subtitulo">' in corpo, 'a tela tem título')

with appmod.app.app_context():
    for i in range(105):
        db.session.add(Transacao(empresa_id=eid, tipo='Receber', descricao=f'Lote {i:03d}', valor=1.0,
                                 data_vencimento=mes_que_vem, status='Pendente'))
    db.session.commit()
filtro = f'filtro_inicio={mes_que_vem.replace(day=1)}&filtro_fim={appmod._fim_do_mes(mes_que_vem)}'
corpo = texto(c.get(f'/lancamentos?{filtro}'))
checar(corpo.count('data-rotulo="Descrição"') == 100 and 'Página 1 de 2' in corpo,
       'mais de 100 no período: pagina de 100 em 100')
checar('106 lançamento(s)' in corpo, 'o rodapé conta todos, não só os da página')
corpo = texto(c.get(f'/lancamentos?{filtro}&pagina=2'))
checar(corpo.count('data-rotulo="Descrição"') == 6, 'a página 2 traz o resto (105 do lote + 1 que já estava)')
corpo = texto(c.get('/lancamentos?periodo=tudo&pagina=2'))
checar('Página 2 de' in corpo, '"Tudo" continua valendo ao trocar de página')

# ================================================================ #
print('\n--- 7) menu ---')
corpo = texto(c.get('/lancamentos'))
checar('>Bancos<' in corpo and '>Contas<' not in corpo, 'o item "Contas" virou "Bancos"')
checar(all(g in corpo for g in ('Financeiro', 'Vendas', 'Análise', 'Ajustes')), 'o menu tem grupos')
checar('class="menu-subitem' in corpo and 'Recorrentes' in corpo, 'Recorrentes virou sub-aba de Lançamentos')
checar('id="botao-menu"' in corpo, 'e tem o botão de abrir o menu no celular')

c.post('/configuracoes/contador', data={'email_contador': 'contador@teste.com.br'})
k = entrar('contador@teste.com.br', 'contador-teste-123')
k.get(f'/contador/abrir/{eid}')
corpo = texto(k.get('/lancamentos'))
checar('Recorrentes' not in corpo, 'o contador continua sem Recorrentes')
checar('+ Novo lançamento' not in corpo and '>Excluir<' not in corpo,
       'e não vê formulário nem botões de mexer (antes via e só levava "não pode")')

# ================================================================ #
print('\n--- 8) Configurações do cliente num servidor compartilhado ---')
corpo = texto(c.get('/configuracoes'))
checar('cert_caminho' not in corpo and 'C:\\' not in corpo, 'não pede mais o caminho do certificado')
checar('/home/' not in corpo and 'Fazer backup agora' not in corpo,
       'não mostra a pasta do servidor nem o botão de backup do banco inteiro')
checar('NSU' not in corpo and 'cursor' not in corpo and 'mTLS' not in corpo, 'sem jargão (NSU, cursor, mTLS)')
c.post('/configuracoes', data={'cert_caminho': '/etc/passwd', 'cert_senha': '123456',
                               'nfse_ambiente': 'producao', 'nfe_ambiente': 'producao'})
with appmod.app.app_context():
    caminho = appmod.fiscal_sync.config_get('cert_caminho', empresa_id=eid)
    checar(not caminho, f'caminho enviado à mão é ignorado ({caminho!r})')

r = c.post('/configuracoes/backup')
checar(r.status_code in (404, 405), 'a rota antiga de backup do cliente não existe mais')
r = c.post('/admin/backup', follow_redirects=True)
checar('não tem acesso' in texto(r) and 'Backup gravado' not in texto(r), 'cliente não dispara o backup do admin')
a = entrar('admin@teste.com.br', 'admin-teste-123')
r = a.post('/admin/backup', follow_redirects=True)
checar('Backup gravado' in texto(r) and 'id="backup"' in texto(r), 'o admin faz o backup na tela dele')

# ================================================================ #
print('\n--- 9) admin: valores e formulários ---')
a.post('/admin/asaas', data={'valor': '1.234,56', 'valor_usuario': '19,90'})
with appmod.app.app_context():
    checar(appmod.fiscal_sync.sistema_get('asaas_valor') == '1234.56'
           and appmod.fiscal_sync.sistema_get('asaas_valor_usuario') == '19.90',
           'mensalidade "1.234,56" e adicional "19,90" gravados certos')
a.post('/admin/clientes/novo', data={'nome': 'Empresa Nova', 'email': 'invalido', 'senha': '12345678'})
corpo = texto(a.get('/admin/clientes'))
checar('value="Empresa Nova"' in corpo and 'value="invalido"' in corpo,
       'cadastro de cliente com erro volta preenchido (menos a senha)')

print()
if falhas:
    print('FALHAS:')
    for f in falhas:
        print('  -', f)
    raise SystemExit(1)
print('USABILIDADE OK')
