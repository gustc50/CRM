"""Contador: o cliente informa só o e-mail; o sistema cria a conta, manda o
acesso e os dados da empresa, e os documentos saem pela conta de e-mail do
sistema (a do administrador). Um servidor SMTP de mentira, em localhost,
recebe as mensagens para conferir.

    cd erp
    rm -f erp.db .chave_sessao && rm -rf backups
    python3 testes/test_contador.py
"""
import email
import email.policy
import pathlib
import re
import socketserver
import sys
import threading

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import app as appmod  # noqa: E402
from models import PAPEL_CONTADOR, Transacao, Usuario, db  # noqa: E402

falhas = []


def checar(condicao, descricao):
    print(('  OK    ' if condicao else '  FALHA ') + descricao)
    if not condicao:
        falhas.append(descricao)


# ================================================================ #
# SMTP de mentira: guarda cada mensagem recebida
# ================================================================ #
caixa = []


class SMTPDeMentira(socketserver.StreamRequestHandler):
    def responder(self, linha):
        self.wfile.write((linha + '\r\n').encode())

    def handle(self):
        self.responder('220 teste ESMTP')
        destinatarios = []
        while True:
            linha = self.rfile.readline().decode(errors='replace').strip()
            if not linha:
                return
            comando = linha.split(' ')[0].upper()
            if comando in ('EHLO', 'HELO'):
                self.responder('250-teste')
                self.responder('250 8BITMIME')
            elif comando == 'MAIL':
                destinatarios = []
                self.responder('250 OK')
            elif comando == 'RCPT':
                destinatarios.append(re.search(r'<(.+?)>', linha).group(1))
                self.responder('250 OK')
            elif comando == 'DATA':
                self.responder('354 manda')
                dados = []
                while True:
                    parte = self.rfile.readline()
                    if parte in (b'.\r\n', b'.\n'):
                        break
                    dados.append(parte[1:] if parte.startswith(b'..') else parte)
                msg = email.message_from_bytes(b''.join(dados), policy=email.policy.default)
                caixa.append({'para': destinatarios, 'msg': msg})
                self.responder('250 recebido')
            elif comando == 'QUIT':
                self.responder('221 tchau')
                return
            else:
                self.responder('250 OK')


socketserver.ThreadingTCPServer.allow_reuse_address = True
smtp = socketserver.ThreadingTCPServer(('127.0.0.1', 0), SMTPDeMentira)
threading.Thread(target=smtp.serve_forever, daemon=True).start()


def ligar_smtp_do_sistema():
    with appmod.app.app_context():
        for chave, valor in (('sistema_smtp_servidor', '127.0.0.1'),
                             ('sistema_smtp_porta', str(smtp.server_address[1])),
                             ('sistema_smtp_seguranca', 'nenhuma'),
                             ('sistema_smtp_usuario', ''),
                             ('sistema_smtp_remetente', 'nao-responda@sistema.test')):
            appmod.fiscal_sync.sistema_set(chave, valor)


def texto(m):
    return m.get_body(preferencelist=('plain',)).get_content()


def entrar(email_, senha):
    c = appmod.app.test_client()
    r = c.post('/login', data={'email': email_, 'senha': senha}, follow_redirects=True)
    return c, r


def flash(resposta):
    return ' '.join(re.findall(r'<li class="(?:sucesso|erro)">(.*?)</li>', resposta.get_data(as_text=True), re.S))


with appmod.app.app_context():
    eid1 = Usuario.query.filter_by(email='cliente1@teste.com.br').first().empresa_id
    eid2 = Usuario.query.filter_by(email='cliente2@teste.com.br').first().empresa_id

c1, _ = entrar('cliente1@teste.com.br', 'cliente-teste-123')
c2, _ = entrar('cliente2@teste.com.br', 'cliente-teste-123')

# ================================================================ #
print('--- 1) Configurações do cliente: só o e-mail do contador ---')
corpo = c1.get('/configuracoes').get_data(as_text=True)
checar('name="email_contador"' in corpo and 'id="contador"' in corpo, 'tem o campo do e-mail do contador')
checar(not any(campo in corpo for campo in ('smtp_servidor', 'smtp_senha', 'smtp_provedor', 'Enviar e-mail de teste')),
       'e nenhuma configuração de servidor de e-mail')
checar(c1.post('/configuracoes/email').status_code in (404, 405)
       and c1.post('/configuracoes/email/testar').status_code in (404, 405),
       'as rotas do SMTP por cliente não existem mais')

# ================================================================ #
print('\n--- 2) sem o e-mail do sistema: cria a conta e mostra a senha ---')
r = c1.post('/configuracoes/contador', data={'email_contador': 'Novo.Contador@Escritorio.com.br'},
            follow_redirects=True)
mensagem = flash(r)
with appmod.app.app_context():
    conta = Usuario.query.filter_by(email='novo.contador@escritorio.com.br').first()
    checar(conta is not None and conta.papel == PAPEL_CONTADOR and conta.empresa_id is None,
           'cria a conta como contador (sem empresa), com o e-mail em minúsculas')
senha = re.search(r'senha provisória (\S+) ', mensagem)
checar(senha is not None and 'o e-mail não saiu' in mensagem,
       'o e-mail não saiu, então a senha aparece para o cliente repassar')
checar(not caixa, 'nada foi enviado')
k, r = entrar('novo.contador@escritorio.com.br', senha.group(1) if senha else '-')
checar('Padaria Pão Quente' in k.get('/contador').get_data(as_text=True),
       'com essa senha o contador entra e já vê a empresa em "Meus clientes"')

# ================================================================ #
print('\n--- 3) com o e-mail do sistema: manda o acesso e os dados do cliente ---')
ligar_smtp_do_sistema()
with appmod.app.app_context():
    emp2 = db.session.get(appmod.Empresa, eid2)
    emp2.cnpj = '11222333000181'
    emp2.assinatura_ate = appmod.date.today() + appmod.timedelta(days=30)  # a oficina vem bloqueada
    db.session.commit()
c2, _ = entrar('cliente2@teste.com.br', 'cliente-teste-123')
r = c2.post('/configuracoes/contador', data={'email_contador': 'carla@contabil.test'}, follow_redirects=True)
checar('mandamos para carla@contabil.test o acesso' in flash(r), 'avisa o cliente que o acesso foi enviado')
checar(len(caixa) == 1 and caixa[0]['para'] == ['carla@contabil.test'], 'um e-mail para o contador')
m = caixa[0]['msg'] if caixa else None
corpo_email = texto(m) if m else ''
senha2 = re.search(r'Senha provisória: (\S+)', corpo_email)
checar(senha2 is not None and 'carla@contabil.test' in corpo_email, 'com o login e a senha provisória')
checar('Oficina Roda Livre' in corpo_email and '11.222.333/0001-81' in corpo_email
       and 'cliente2@teste.com.br' in corpo_email, 'e os dados do cliente: empresa, CNPJ e responsável')
checar(m is not None and m['Reply-To'] == 'cliente2@teste.com.br', 'responder vai para o cliente')
checar(m is not None and 'nao-responda@sistema.test' in m['From'], 'sai pela conta do sistema')
checar(senha2 is not None and senha2.group(1) not in flash(r), 'a senha não aparece na tela quando o e-mail saiu')
k2, _ = entrar('carla@contabil.test', senha2.group(1) if senha2 else '-')
checar('Oficina Roda Livre' in k2.get('/contador').get_data(as_text=True), 'e a senha do e-mail funciona')

caixa.clear()
r = c1.post('/configuracoes/contador', data={'email_contador': 'carla@contabil.test'}, follow_redirects=True)
checar('já usava o sistema' in flash(r), 'contador que já tem conta só ganha mais um cliente')
corpo_email = texto(caixa[0]['msg']) if caixa else ''
checar(caixa and 'Padaria Pão Quente' in corpo_email and 'Senha provisória' not in corpo_email,
       'e recebe um aviso, sem senha nova')
with appmod.app.app_context():
    checar(Usuario.query.filter_by(email='carla@contabil.test').count() == 1, 'sem criar outra conta')
corpo = k2.get('/contador').get_data(as_text=True)
checar('Padaria Pão Quente' in corpo and 'Oficina Roda Livre' in corpo, 'a contadora vê as duas empresas')
checar('Padaria Pão Quente' not in k.get('/contador').get_data(as_text=True),
       'e o contador anterior da padaria perde o acesso')

caixa.clear()
r = c1.post('/configuracoes/contador', data={'email_contador': 'carla@contabil.test'}, follow_redirects=True)
checar('já está com acesso' in flash(r) and not caixa, 'salvar o mesmo e-mail de novo não manda nada')

# ================================================================ #
print('\n--- 4) o que é recusado ---')
r = c1.post('/configuracoes/contador', data={'email_contador': 'cliente2@teste.com.br'}, follow_redirects=True)
checar('não é de contador' in flash(r), 'e-mail de um cliente não vira contador')
checar('value="cliente2@teste.com.br"' in r.get_data(as_text=True), 'e o campo volta com o que foi digitado')
with appmod.app.app_context():
    checar(Usuario.query.filter_by(email='cliente2@teste.com.br').first().papel != PAPEL_CONTADOR,
           'a conta do cliente continua como estava')
    checar(appmod.fiscal_sync.config_get('email_contador', empresa_id=eid1) == 'carla@contabil.test',
           'e o contador da empresa não mudou')
r = c1.post('/configuracoes/contador', data={'email_contador': 'admin@teste.com.br'}, follow_redirects=True)
checar('não é de contador' in flash(r), 'nem o do administrador')
r = c1.post('/configuracoes/contador', data={'email_contador': 'sem-arroba'}, follow_redirects=True)
checar('e-mail válido' in flash(r), 'e-mail inválido é recusado')
r = k2.post('/configuracoes/contador', data={'email_contador': 'outro@x.test'}, follow_redirects=True)
checar('somente de leitura' in r.get_data(as_text=True), 'o contador não troca o contador de ninguém')

# ================================================================ #
print('\n--- 5) envio dos documentos pela conta do sistema ---')
with appmod.app.app_context():
    db.session.add(Transacao(empresa_id=eid1, tipo='Pagar', descricao='Aluguel', valor=1500.0,
                             data_vencimento=appmod.date(2026, 10, 5), status='Pendente'))
    db.session.commit()
corpo = c1.get('/contabilidade').get_data(as_text=True)
checar('Vai para <strong>carla@contabil.test</strong>' in corpo and 'name="email_contador"' not in corpo,
       'a aba Contabilidade mostra para quem vai, sem campo para digitar')
caixa.clear()
r = c1.post('/contabilidade/enviar', data={'inicio': '2026-10-01', 'fim': '2026-10-31',
                                           'email_contador': 'desvio@golpe.test', 'mensagem': 'Segue outubro'},
            follow_redirects=True)
checar('Enviado para carla@contabil.test' in flash(r), 'envia ao contador cadastrado')
checar(len(caixa) == 1 and caixa[0]['para'] == ['carla@contabil.test'],
       'e só a ele: um e-mail digitado no formulário é ignorado')
m = caixa[0]['msg'] if caixa else None
checar(m is not None and m['From'].addresses[0].display_name == 'Padaria Pão Quente Ltda via Gestão Financeira'
       and 'nao-responda@sistema.test' in m['From'],
       f'com o nome da empresa no remetente, pela conta do sistema ({m and m["From"]})')
checar(m is not None and m['Reply-To'] == 'cliente1@teste.com.br', 'e a resposta do contador vai para o cliente')
anexos = [a.get_filename() for a in m.iter_attachments()] if m else []
checar(any(n.endswith('.xlsx') for n in anexos) and 'Segue outubro' in texto(m),
       f'com a planilha anexada e a observação ({anexos})')

r = c1.post('/configuracoes/contador', data={'email_contador': 'carla@contabil.test', 'acao': 'remover'},
            follow_redirects=True)
checar('ninguém mais tem acesso' in flash(r), 'o botão "Tirar o acesso" tira o acesso')
checar('Padaria Pão Quente' not in k2.get('/contador').get_data(as_text=True),
       'e a contadora deixa de ver a padaria')
r = c1.post('/contabilidade/enviar', data={'inicio': '2026-10-01', 'fim': '2026-10-31'}, follow_redirects=True)
checar('Informe primeiro o e-mail do seu contador' in flash(r), 'sem contador, o envio pede para informar')

with appmod.app.app_context():
    appmod.fiscal_sync.sistema_set('sistema_smtp_servidor', '')
c1.post('/configuracoes/contador', data={'email_contador': 'carla@contabil.test'})
r = c1.post('/contabilidade/enviar', data={'inicio': '2026-10-01', 'fim': '2026-10-31'}, follow_redirects=True)
checar('ainda não está disponível' in flash(r), 'sem o e-mail do sistema, o envio explica e sugere o .zip')

# ================================================================ #
print('\n--- 6) histórico ---')
corpo = c1.get('/equipe/historico').get_data(as_text=True)
checar('Liberou o acesso do contador' in corpo and 'Tirou o acesso do contador' in corpo,
       'liberar e tirar o acesso ficam no histórico da equipe')

smtp.shutdown()
print()
if falhas:
    print('FALHAS:')
    for f in falhas:
        print('  -', f)
    raise SystemExit(1)
print('CONTADOR OK')
