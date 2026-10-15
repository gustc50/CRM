"""Liberação manual pelo admin e automática pelo Asaas, casando CPF/CNPJ.

Sobe um Asaas de mentira em localhost que responde cobranças pagas e o
cadastro dos clientes (é de lá que vem o CPF/CNPJ), e confere a regra única:
a data de liberação é registrada e confrontada com a de hoje; vencido, a
próxima liberação começa um período novo; em dia, soma ao fim.

    cd erp
    rm -f erp.db .chave_sessao && rm -rf backups
    python3 testes/test_liberacao.py
"""
import datetime as dt
import json
import os
import pathlib
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

TOKEN = 'token_de_teste_liberacao'
HOJE = dt.date.today()
falhas = []

# O "banco" do Asaas de mentira
clientes_asaas = {}   # id -> {'id', 'cpfCnpj', 'name'}
cobrancas = []        # dicts no formato da API


def checar(condicao, descricao):
    print(('  OK    ' if condicao else '  FALHA ') + descricao)
    if not condicao:
        falhas.append(descricao)


class MockAsaas(BaseHTTPRequestHandler):
    def _json(self, codigo, corpo):
        dados = json.dumps(corpo).encode()
        self.send_response(codigo)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

    def do_GET(self):
        if self.headers.get('access_token') != TOKEN:
            return self._json(401, {'errors': [{'description': 'token'}]})
        url = urlparse(self.path)
        params = parse_qs(url.query)
        partes = url.path.rstrip('/').split('/')

        if partes[-1] == 'payments':
            situacao = params.get('status', [None])[0]
            cliente = params.get('customer', [None])[0]
            desde = params.get('paymentDate[ge]', [None])[0]
            dados = [c for c in cobrancas
                     if (not situacao or c['status'] == situacao)
                     and (not cliente or c['customer'] == cliente)
                     and (not desde or c['paymentDate'] >= desde)]
            return self._json(200, {'data': dados, 'hasMore': False})

        if partes[-1] == 'customers':
            doc = params.get('cpfCnpj', [None])[0]
            dados = [c for c in clientes_asaas.values() if not doc or c['cpfCnpj'] == doc]
            return self._json(200, {'data': dados, 'hasMore': False})

        if partes[-2] == 'customers':
            cliente = clientes_asaas.get(partes[-1])
            return self._json(200, cliente) if cliente else self._json(404, {})

        self._json(404, {})

    def log_message(self, *_):
        pass


servidor = HTTPServer(('127.0.0.1', 0), MockAsaas)
threading.Thread(target=servidor.serve_forever, daemon=True).start()
os.environ['ERP_ASAAS_URL'] = f'http://127.0.0.1:{servidor.server_address[1]}'

import app as appmod  # noqa: E402
from models import (ORIGEM_ASAAS, ORIGEM_CORTESIA, ORIGEM_MANUAL, Empresa, Liberacao,  # noqa: E402
                    Pagamento, Usuario, db)

# E-mails: em vez de mandar, anota quem seria avisado
avisados = []
appmod.avisar = lambda destino, assunto_e_corpo: avisados.append((destino, assunto_e_corpo[0])) or True


def entrar(cliente, email, senha):
    return cliente.post('/login', data={'email': email, 'senha': senha}, follow_redirects=True)


def cobranca_paga(cid, cliente, dias_atras=0, status='RECEIVED', valor=99.9):
    cobrancas.append({'id': cid, 'customer': cliente, 'value': valor, 'status': status,
                      'paymentDate': (HOJE - dt.timedelta(days=dias_atras)).isoformat()})


def empresa_nova(nome, cnpj=None, vencida_ha=None, cpf_do_socio=None):
    with appmod.app.app_context():
        e = Empresa(nome=nome, cnpj=cnpj)
        if vencida_ha is not None:
            e.assinatura_ate = HOJE - dt.timedelta(days=vencida_ha)
        db.session.add(e)
        db.session.flush()
        email = nome.lower().replace(' ', '') + '@teste.com.br'
        appmod.auth.criar_usuario(email, 'senha-boa-123', nome, empresa=e,
                                  cargo='socio', cpf_cnpj=cpf_do_socio)
        db.session.commit()
        return e.id, email


def empresa(eid):
    return db.session.get(Empresa, eid)


adm = appmod.app.test_client()
entrar(adm, 'admin@teste.com.br', 'admin-teste-123')
with appmod.app.app_context():
    id_admin = Usuario.query.filter_by(email='admin@teste.com.br').first().id
    appmod.fiscal_sync.sistema_set('asaas_token_cripto', appmod.fiscal_certificado.criptografar_senha(
        TOKEN, appmod.data_path('.chave_secreta')))

# =============================================================== #
print('--- 1) liberação manual de quem está vencido ---')
eid, email = empresa_nova('Vencida Manual', vencida_ha=10)

c = appmod.app.test_client()
r = entrar(c, email, 'senha-boa-123')
checar('Acesso bloqueado' in r.get_data(as_text=True), 'vencida há 10 dias: o sistema já está bloqueado')

r = adm.post(f'/admin/clientes/{eid}/assinatura', data={'acao': 'liberar'}, follow_redirects=True)
checar('liberado por 30 dia(s): de hoje' in r.get_data(as_text=True),
       'admin clica em Liberar (sem escolher os dias, valem 30)')
with appmod.app.app_context():
    e = empresa(eid)
    l = e.ultima_liberacao
    checar(l is not None and l.origem == ORIGEM_MANUAL, 'a liberação fica registrada como manual')
    checar(l.liberado_em.date() == HOJE, f'com a data de liberação de hoje ({l.liberado_em:%d/%m/%Y %H:%M})')
    checar(l.usuario_id == id_admin, 'e com quem clicou')
    checar(l.inicio == HOJE, 'nova data inicial = hoje (não aproveita o período vencido)')
    checar(e.assinatura_ate == HOJE + dt.timedelta(days=30), f'válido por 30 dias, até {e.assinatura_ate:%d/%m/%Y}')
    checar(not l.renovacao, 'não é renovação')

r = c.get('/lancamentos', follow_redirects=True)
checar('Acesso bloqueado' not in r.get_data(as_text=True), 'o cliente volta a entrar na hora')

# =============================================================== #
print('\n--- 2) confronto com a data do sistema: passou do prazo, bloqueia ---')
with appmod.app.app_context():
    e = empresa(eid)
    e.assinatura_ate = HOJE - dt.timedelta(days=1)  # o prazo acabou ontem
    db.session.commit()
r = c.get('/lancamentos', follow_redirects=True)
checar('Acesso bloqueado' in r.get_data(as_text=True), 'no dia seguinte ao prazo, o acesso é bloqueado sozinho')

corpo = adm.get('/admin/clientes').get_data(as_text=True)
checar('venceu em' in corpo and 'há 1 dia(s)' in corpo, 'o admin vê há quantos dias venceu')

adm.post(f'/admin/clientes/{eid}/assinatura', data={'acao': 'liberar'}, follow_redirects=True)
with appmod.app.app_context():
    e = empresa(eid)
    checar(e.ultima_liberacao.inicio == HOJE, 'o novo clique gera uma nova data inicial (hoje)')
    checar(len(e.liberacoes) == 2, 'e fica um segundo registro no histórico')
checar('Acesso bloqueado' not in c.get('/lancamentos', follow_redirects=True).get_data(as_text=True),
       'e o acesso volta')

# =============================================================== #
print('\n--- 3) liberar quem ainda está em dia não come dias ---')
r = adm.post(f'/admin/clientes/{eid}/assinatura', data={'acao': 'liberar'}, follow_redirects=True)
checar('ainda estava em dia' in r.get_data(as_text=True), 'o admin é avisado de que era renovação')
with appmod.app.app_context():
    e = empresa(eid)
    checar(e.assinatura_ate == HOJE + dt.timedelta(days=60), 'soma ao fim: 30 que restavam + 30 novos')
    checar(e.ultima_liberacao.renovacao, 'registrado como renovação')

# =============================================================== #
print('\n--- 4) encerrar ---')
adm.post(f'/admin/clientes/{eid}/assinatura', data={'acao': 'encerrar'}, follow_redirects=True)
checar('Acesso bloqueado' in c.get('/lancamentos', follow_redirects=True).get_data(as_text=True),
       'encerrar bloqueia na hora')
adm.post(f'/admin/clientes/{eid}/assinatura', data={'acao': 'liberar'}, follow_redirects=True)
with appmod.app.app_context():
    checar(empresa(eid).assinatura_ate == HOJE + dt.timedelta(days=30),
           'e o próximo Liberar recomeça de hoje, não do que tinha antes')

# =============================================================== #
print('\n--- 5) Asaas: pagamento reconhecido pelo CNPJ ---')
eid_cnpj, email_cnpj = empresa_nova('Pelo Cnpj', cnpj='11222333000181', vencida_ha=5)
clientes_asaas['cus_CNPJ'] = {'id': 'cus_CNPJ', 'cpfCnpj': '11.222.333/0001-81', 'name': 'Pelo CNPJ'}
cobranca_paga('pay_CNPJ_1', 'cus_CNPJ', dias_atras=2)

avisados.clear()
r = adm.post('/admin/asaas/verificar', follow_redirects=True)
texto = r.get_data(as_text=True)
checar('reconhecida(s) pelo CPF/CNPJ' in texto, 'a consulta informa que reconheceu pelo CPF/CNPJ')
with appmod.app.app_context():
    e = empresa(eid_cnpj)
    l = e.ultima_liberacao
    checar(e.assinatura_em_dia, 'empresa sem vínculo nenhum com o Asaas é liberada pelo CNPJ')
    checar(l.origem == ORIGEM_ASAAS, 'registrado como liberação pelo Asaas')
    checar(l.inicio == HOJE - dt.timedelta(days=2), 'a data inicial é a do pagamento')
    checar(e.assinatura_ate == HOJE + dt.timedelta(days=28), 'e vale 30 dias a partir dela')
    checar(e.asaas_cliente_id == 'cus_CNPJ', 'o vínculo com o cliente do Asaas fica gravado')
    checar('CPF/CNPJ' in (l.observacao or ''), 'o histórico diz que casou pelo documento')
checar(any(d == email_cnpj for d, _ in avisados), 'o sócio recebe o e-mail de pagamento confirmado')

# =============================================================== #
print('\n--- 6) Asaas: pagamento pelo CPF do sócio ---')
eid_cpf, _ = empresa_nova('Pelo Cpf', cnpj='99888777000166', vencida_ha=3,
                          cpf_do_socio='52998224725')
clientes_asaas['cus_CPF'] = {'id': 'cus_CPF', 'cpfCnpj': '529.982.247-25', 'name': 'Sócio'}
cobranca_paga('pay_CPF_1', 'cus_CPF')
adm.post('/admin/asaas/verificar', follow_redirects=True)
with appmod.app.app_context():
    checar(empresa(eid_cpf).assinatura_em_dia,
           'pagamento no CPF do sócio libera a empresa dele (CNPJ diferente)')

# =============================================================== #
print('\n--- 7) documento que bate com duas empresas: não libera ninguém ---')
eid_a, _ = empresa_nova('Duplicada A', cnpj='44555666000177', vencida_ha=1)
eid_b, _ = empresa_nova('Duplicada B', cnpj='44555666000177', vencida_ha=1)
clientes_asaas['cus_DUP'] = {'id': 'cus_DUP', 'cpfCnpj': '44555666000177', 'name': 'Dup'}
cobranca_paga('pay_DUP_1', 'cus_DUP')
r = adm.post('/admin/asaas/verificar', follow_redirects=True)
checar('mais de uma empresa' in r.get_data(as_text=True), 'a consulta avisa a ambiguidade')
with appmod.app.app_context():
    checar(not empresa(eid_a).assinatura_em_dia and not empresa(eid_b).assinatura_em_dia,
           'nenhuma das duas é liberada, para não liberar a errada')
    checar(Pagamento.query.filter_by(asaas_id='pay_DUP_1').first() is None,
           'e o pagamento fica pendente para a próxima consulta, depois de corrigir o cadastro')

# =============================================================== #
print('\n--- 8) documento que não existe aqui ---')
clientes_asaas['cus_NADA'] = {'id': 'cus_NADA', 'cpfCnpj': '12345678000199', 'name': 'Ninguém'}
cobranca_paga('pay_NADA', 'cus_NADA')
r = adm.post('/admin/asaas/verificar', follow_redirects=True)
checar('sem empresa com esse CPF/CNPJ' in r.get_data(as_text=True), 'é relatado, não engolido')

# =============================================================== #
print('\n--- 9) a mesma cobrança não rende duas vezes ---')
with appmod.app.app_context():
    antes = empresa(eid_cnpj).assinatura_ate
adm.post('/admin/asaas/verificar', follow_redirects=True)
adm.post('/admin/asaas/verificar', follow_redirects=True)
with appmod.app.app_context():
    checar(empresa(eid_cnpj).assinatura_ate == antes, 'consultar de novo não soma mais dias')
    checar(Liberacao.query.filter_by(asaas_id='pay_CNPJ_1').count() == 1, 'nem cria outra liberação')

# =============================================================== #
print('\n--- 10) admin libera na mão e depois a consulta acha o mesmo pagamento ---')
eid_ab, _ = empresa_nova('Absorve', cnpj='55666777000188', vencida_ha=4)
clientes_asaas['cus_ABS'] = {'id': 'cus_ABS', 'cpfCnpj': '55666777000188', 'name': 'Absorve'}
cobranca_paga('pay_ABS_1', 'cus_ABS', dias_atras=0)
# O admin viu o pagamento no Asaas e clicou antes da consulta passar
adm.post(f'/admin/clientes/{eid_ab}/assinatura', data={'acao': 'liberar'}, follow_redirects=True)
with appmod.app.app_context():
    depois_do_clique = empresa(eid_ab).assinatura_ate
r = adm.post('/admin/asaas/verificar', follow_redirects=True)
with appmod.app.app_context():
    e = empresa(eid_ab)
    checar(e.assinatura_ate == depois_do_clique,
           f'o pagamento NÃO soma outros 30 dias (segue até {e.assinatura_ate:%d/%m/%Y})')
    manual = [l for l in e.liberacoes if l.origem == ORIGEM_MANUAL][0]
    checar(manual.asaas_id == 'pay_ABS_1', 'a liberação manual fica amarrada ao pagamento')
    checar(Pagamento.query.filter_by(asaas_id='pay_ABS_1').first() is not None,
           'e o pagamento é marcado como processado')
checar('liberada(s) na mão' in r.get_data(as_text=True), 'o resumo explica o que houve')

# Mas um pagamento NOVO conta normalmente: a liberação manual já foi
# amarrada ao primeiro e não absorve mais nada.
cobranca_paga('pay_ABS_2', 'cus_ABS', dias_atras=0)
adm.post('/admin/asaas/verificar', follow_redirects=True)
with appmod.app.app_context():
    checar(empresa(eid_ab).assinatura_ate == depois_do_clique + dt.timedelta(days=30),
           'um segundo pagamento soma normalmente: a liberação manual já foi usada pelo primeiro')

# =============================================================== #
print('\n--- 11) dois pagamentos fora de ordem dão o mesmo resultado ---')
eid_ord, _ = empresa_nova('Ordem', cnpj='66777888000199', vencida_ha=40)
clientes_asaas['cus_ORD'] = {'id': 'cus_ORD', 'cpfCnpj': '66777888000199', 'name': 'Ordem'}
# O mais recente vem primeiro na lista, como o Asaas costuma devolver
cobranca_paga('pay_ORD_NOVO', 'cus_ORD', dias_atras=5)
cobranca_paga('pay_ORD_VELHO', 'cus_ORD', dias_atras=35)
adm.post('/admin/asaas/verificar', follow_redirects=True)
with appmod.app.app_context():
    e = empresa(eid_ord)
    # Cronológico: o de 35 dias atrás vale até 5 dias atrás; o de 5 dias atrás
    # encontra o período ainda em dia (vence no mesmo dia) e soma ao fim.
    esperado = HOJE - dt.timedelta(days=35) + dt.timedelta(days=60)
    checar(e.assinatura_ate == esperado,
           f'aplicados em ordem de data: até {e.assinatura_ate:%d/%m/%Y} (esperado {esperado:%d/%m/%Y})')

# =============================================================== #
print('\n--- 12) "já paguei" de quem ainda não tem vínculo com o Asaas ---')
eid_jp, email_jp = empresa_nova('Ja Paguei', cnpj='77888999000110', vencida_ha=2)
clientes_asaas['cus_JP'] = {'id': 'cus_JP', 'cpfCnpj': '77888999000110', 'name': 'Já paguei'}
cobranca_paga('pay_JP', 'cus_JP')
cj = appmod.app.test_client()
entrar(cj, email_jp, 'senha-boa-123')
r = cj.post('/assinatura/conferir', follow_redirects=True)
checar('Pagamento confirmado' in r.get_data(as_text=True),
       'o botão acha o cliente no Asaas pelo CNPJ e libera na hora')
with appmod.app.app_context():
    checar(empresa(eid_jp).asaas_cliente_id == 'cus_JP', 'e grava o vínculo')

# =============================================================== #
print('\n--- 13) telas do admin ---')
corpo = adm.get('/admin/clientes').get_data(as_text=True)
checar('Última liberação' in corpo, 'a lista de clientes mostra a última liberação')
checar('Pagamento no Asaas' in corpo and 'Liberado pelo admin' in corpo,
       'com a origem de cada uma')
checar('Renovar' in corpo and 'Liberar' in corpo, 'botão muda conforme a situação')
corpo = adm.get('/admin/assinaturas').get_data(as_text=True)
checar('Histórico de liberações' in corpo, 'a tela de assinaturas traz o histórico')
checar('casado pelo CPF/CNPJ' in corpo, 'mostrando quando foi pelo documento')

c2 = appmod.app.test_client()
entrar(c2, 'cliente1@teste.com.br', 'cliente-teste-123')
r = c2.post(f'/admin/clientes/{eid}/assinatura', data={'acao': 'liberar'}, follow_redirects=True)
with appmod.app.app_context():
    checar(Liberacao.query.filter_by(empresa_id=eid).count() == 4,
           'cliente comum não consegue liberar nada')

# =============================================================== #
print('\n--- 14) liberar escolhendo os dias: degustação ---')
import re  # noqa: E402
eid_d, _ = empresa_nova('Degusta', cnpj='88999000000111', vencida_ha=3)
corpo = adm.get('/admin/clientes').get_data(as_text=True)
janela = corpo[corpo.find(f'id="dialogo-liberar-{eid_d}"'):]
janela = janela[:janela.find('</dialog>')]
checar('name="dias"' in janela and f'value="{appmod.DIAS_POR_PAGAMENTO}"' in janela,
       'Liberar abre a janela com a quantidade de dias (30 já preenchido)')
checar(re.search(r'value="degustacao" checked', janela) is not None,
       'quem nunca pagou já vem marcado como degustação')
janela_paga = corpo[corpo.find(f'id="dialogo-liberar-{eid}"'):]
janela_paga = janela_paga[:janela_paga.find('</dialog>')]
checar(re.search(r'value="pagamento" checked', janela_paga) is not None,
       'quem já foi liberado antes vem marcado como pagamento')

r = adm.post(f'/admin/clientes/{eid_d}/assinatura',
             data={'acao': 'liberar', 'dias': '7', 'motivo': 'degustacao', 'observacao': 'feira de outubro'},
             follow_redirects=True)
checar('liberado por 7 dia(s) de degustação' in r.get_data(as_text=True), 'libera 7 dias de degustação')
with appmod.app.app_context():
    e = empresa(eid_d)
    l = e.ultima_liberacao
    checar(e.assinatura_ate == HOJE + dt.timedelta(days=7), f'até hoje + 7 ({e.assinatura_ate:%d/%m/%Y})')
    checar(l.origem == ORIGEM_CORTESIA and l.dias == 7 and l.observacao == 'feira de outubro'
           and l.usuario_id == id_admin, 'registrada como cortesia, com os dias, a observação e quem liberou')
corpo = adm.get('/admin/clientes').get_data(as_text=True)
checar('Cortesia / degustação · 7 dia(s)' in corpo and 'feira de outubro' in corpo,
       'a coluna "Última liberação" mostra que foi degustação, de quantos dias')

clientes_asaas['cus_DEG'] = {'id': 'cus_DEG', 'cpfCnpj': '88999000000111', 'name': 'Degusta'}
cobranca_paga('pay_DEG', 'cus_DEG', dias_atras=0)
adm.post('/admin/asaas/verificar', follow_redirects=True)
with appmod.app.app_context():
    e = empresa(eid_d)
    checar(e.assinatura_ate == HOJE + dt.timedelta(days=7 + 30),
           f'pagou durante a degustação: os 30 dias somam ao fim dela ({e.assinatura_ate:%d/%m/%Y})')
    checar(e.ultima_liberacao.origem == ORIGEM_ASAAS and e.ultima_liberacao.asaas_id == 'pay_DEG',
           'e o pagamento não é engolido pela degustação')

for ruim in ('0', '400', 'abc', '-5'):
    r = adm.post(f'/admin/clientes/{eid_d}/assinatura', data={'acao': 'liberar', 'dias': ruim},
                 follow_redirects=True)
    if 'Informe de 1 a 365 dias' not in r.get_data(as_text=True):
        break
with appmod.app.app_context():
    checar('Informe de 1 a 365 dias' in r.get_data(as_text=True)
           and empresa(eid_d).assinatura_ate == HOJE + dt.timedelta(days=37),
           'dias fora de 1 a 365 (0, 400, letras, negativo) são recusados sem mexer em nada')

r = adm.post(f'/admin/clientes/{eid_d}/assinatura', data={'acao': 'liberar', 'dias': '15', 'motivo': 'pagamento'},
             follow_redirects=True)
with appmod.app.app_context():
    e = empresa(eid_d)
    checar(e.assinatura_ate == HOJE + dt.timedelta(days=37 + 15) and e.ultima_liberacao.renovacao
           and e.ultima_liberacao.origem == ORIGEM_MANUAL,
           'renovar quem está em dia com 15 dias soma ao fim, como pagamento manual')
checar('mais 15 dia(s) somados ao fim' in r.get_data(as_text=True), 'e a mensagem diz isso')

print()
if falhas:
    print('FALHAS:')
    for f in falhas:
        print('  -', f)
    raise SystemExit(1)
print('LIBERAÇÃO OK')
