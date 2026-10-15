"""Os mesmos passos do teste de usabilidade, num navegador de verdade.

Sobe o sistema numa porta livre, abre o Chromium pelo Playwright e repete o
que deu errado no teste original: excluir "Conta d'água" e clicar em
Cancelar, digitar "150,50" no valor, usar o menu e as telas no celular.

    cd erp
    rm -f erp.db .chave_sessao && rm -rf backups
    python3 testes/test_navegador.py

Precisa do Playwright (pip install playwright). Se o Chromium não estiver no
lugar padrão, informe o executável em PLAYWRIGHT_CHROMIUM.
"""
import os
import pathlib
import sys
import threading

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print('Playwright não instalado — teste de navegador pulado.')
    raise SystemExit(0)
from werkzeug.serving import WSGIRequestHandler, make_server  # noqa: E402

import app as appmod  # noqa: E402
from models import Fornecedor, Transacao, Usuario, db  # noqa: E402

falhas = []


def checar(condicao, descricao):
    print(('  OK    ' if condicao else '  FALHA ') + descricao)
    if not condicao:
        falhas.append(descricao)


class SemLog(WSGIRequestHandler):
    def log_request(self, *args, **kwargs):
        pass


servidor = make_server('127.0.0.1', 0, appmod.app, threaded=True, request_handler=SemLog)
threading.Thread(target=servidor.serve_forever, daemon=True).start()
B = f'http://127.0.0.1:{servidor.server_port}'

executavel = os.environ.get('PLAYWRIGHT_CHROMIUM') or (
    '/opt/pw-browsers/chromium' if os.path.exists('/opt/pw-browsers/chromium') else None)


def entrar(pg, email='cliente1@teste.com.br', senha='cliente-teste-123'):
    pg.goto(B + '/login')
    pg.fill('input[name=email]', email)
    pg.fill('input[name=senha]', senha)
    pg.click('button[type=submit]')
    pg.wait_for_load_state()


def flashes(pg):
    return ' '.join(e.inner_text() for e in pg.query_selector_all('.flashes li'))


with sync_playwright() as p:
    b = p.chromium.launch(executable_path=executavel, args=['--no-sandbox'])
    ctx = b.new_context(viewport={'width': 1366, 'height': 900}, locale='pt-BR')
    pg = ctx.new_page()
    respostas = []
    pg.on('dialog', lambda d: (respostas.append(d.message), d.dismiss() if responder['cancelar'] else d.accept()))
    responder = {'cancelar': True}
    erros_js = []
    pg.on('pageerror', lambda e: erros_js.append(str(e)))
    entrar(pg)

    print('--- valor digitado com vírgula ---')
    pg.goto(B + '/lancamentos')
    pg.select_option('#tipo-select', 'Pagar')
    pg.fill('input[name=descricao]', 'Teste virgula')
    pg.click('input[name=valor]')
    pg.keyboard.type('150,50')
    pg.click('input[name=descricao]')  # sai do campo
    checar(pg.input_value('input[name=valor]') == '150,50', 'ao sair do campo, o valor aparece como 150,50')
    pg.fill('input[name=data_vencimento]', '2026-10-21')
    pg.click('text=Adicionar lançamento')
    pg.wait_for_load_state()
    with appmod.app.app_context():
        t = Transacao.query.filter_by(descricao='Teste virgula').first()
        checar(t is not None and t.valor == 150.5, f'grava R$ 150,50 (antes virava 15.050): {t and t.valor}')

    pg.goto(B + '/lancamentos')
    pg.fill('input[name=valor]', '1,234')
    valido = pg.eval_on_selector('input[name=valor]', 'e => e.checkValidity()')
    checar(not valido, '"1,234" (ambíguo) é barrado no próprio navegador')

    print('\n--- fornecedor cadastrado na hora ---')
    pg.goto(B + '/lancamentos')
    pg.select_option('#tipo-select', 'Pagar')
    campo_novo = pg.locator('#fornecedor-novo-campo')
    checar(not campo_novo.is_visible(), 'o campo do nome novo começa escondido')
    pg.select_option('#fornecedor-select', '__novo__')
    checar(campo_novo.is_visible(), 'escolher "+ Cadastrar novo" mostra o campo do nome')
    pg.fill('input[name=fornecedor_novo]', 'Banco do Brasil')
    pg.fill('input[name=descricao]', 'Tarifa')
    pg.fill('input[name=valor]', '15,90')
    pg.fill('input[name=data_vencimento]', '2026-10-21')
    pg.click('text=Adicionar lançamento')
    pg.wait_for_load_state()
    checar('cadastrado só com o nome' in flashes(pg), 'lança e cadastra o fornecedor juntos')
    with appmod.app.app_context():
        checar(Fornecedor.query.filter_by(nome='Banco do Brasil').count() == 1, 'fornecedor criado')

    print("\n--- excluir \"Conta d'água\" ---")
    pg.goto(B + '/lancamentos')
    pg.select_option('#tipo-select', 'Pagar')
    pg.fill('input[name=descricao]', "Conta d'água")
    pg.fill('input[name=valor]', '89,90')
    pg.fill('input[name=data_vencimento]', '2026-10-20')
    pg.click('text=Adicionar lançamento')
    pg.wait_for_load_state()
    linha = pg.locator('tr', has_text="Conta d'água")
    respostas.clear()
    linha.locator('button:has-text("Excluir")').click()
    pg.wait_for_timeout(300)
    checar(len(respostas) == 1 and "Conta d'água" in respostas[0], 'a pergunta aparece, com o nome certo')
    with appmod.app.app_context():
        checar(Transacao.query.filter_by(descricao="Conta d'água").count() == 1,
               'Cancelar NÃO exclui (antes excluía direto)')
    responder['cancelar'] = False
    linha.locator('button:has-text("Excluir")').click()
    pg.wait_for_load_state()
    with appmod.app.app_context():
        checar(Transacao.query.filter_by(descricao="Conta d'água").count() == 0, 'OK exclui')
    checar(not erros_js, f'nenhum erro de script nas telas {erros_js[:1]}')

    print('\n--- marketplace: clicar no nome conecta ---')
    pg.goto(B + '/marketplace')
    pg.click('a.cabecalho-link:has-text("Mercado Livre")')
    pg.wait_for_load_state()
    checar(pg.url.endswith('/marketplace/mercadolivre/conectar')
           and pg.locator('input[name=app_id]').is_visible()
           and pg.locator('button:has-text("Salvar e conectar")').is_visible(),
           'clicar no nome do marketplace abre a tela para colar a chave da API, com Salvar')
    checar(pg.get_attribute('input[name=app_segredo]', 'type') == 'password',
           'e a chave secreta fica escondida enquanto é digitada')

    print('\n--- contador: tirar o acesso pergunta antes ---')
    pg.goto(B + '/configuracoes')
    respostas.clear()
    responder['cancelar'] = True
    pg.click('button:has-text("Tirar o acesso")')
    pg.wait_for_timeout(300)
    with appmod.app.app_context():
        eid = Usuario.query.filter_by(email='cliente1@teste.com.br').first().empresa_id
        ainda = appmod.fiscal_sync.config_get('email_contador', empresa_id=eid)
    checar(len(respostas) == 1 and 'Tirar o acesso' in respostas[0] and ainda,
           'a pergunta aparece e Cancelar mantém o contador')
    responder['cancelar'] = False

    print('\n--- erro do servidor devolve o que foi digitado ---')
    pg.goto(B + '/login')
    sair = ctx.new_page()
    sair.goto(B + '/logout')
    sair.close()
    pg.goto(B + '/login')
    pg.fill('input[name=email]', 'cliente1@teste.com.br')
    pg.fill('input[name=senha]', 'senha-errada')
    pg.click('button[type=submit]')
    pg.wait_for_load_state()
    checar(pg.input_value('input[name=email]') == 'cliente1@teste.com.br',
           'senha errada: o e-mail continua preenchido')
    entrar(pg)

    print('\n--- celular ---')
    m = ctx.new_page()
    m.set_viewport_size({'width': 390, 'height': 844})
    m.goto(B + '/lancamentos')
    checar(not m.locator('#menu-principal').is_visible(), 'o menu começa recolhido')
    m.click('#botao-menu')
    checar(m.locator('#menu-principal').is_visible(), 'o botão Menu abre a lista de abas')
    aberto = m.evaluate("document.querySelector('details.bloco-recolhivel').open")
    checar(not aberto, 'o formulário de novo lançamento começa fechado, e a lista aparece primeiro')
    largas = []
    for url in ('/', '/lancamentos', '/recorrentes', '/fornecedores', '/categorias', '/contas',
                '/relatorio', '/relatorios/dre', '/relatorios/vendas', '/vendas/servicos', '/marketplace',
                '/notas-servico', '/notas-eletronicas', '/contabilidade', '/equipe', '/configuracoes',
                '/minha-conta'):
        m.goto(B + url)
        if m.evaluate('document.documentElement.scrollWidth') > 392:
            largas.append(url)
    checar(not largas, f'nenhuma tela passa da largura do celular {largas or ""}')

    print('\n--- admin: liberar escolhendo os dias ---')
    adm_ctx = b.new_context(viewport={'width': 1366, 'height': 900}, locale='pt-BR')
    a = adm_ctx.new_page()
    a.on('pageerror', lambda e: erros_js.append(str(e)))
    entrar(a, 'admin@teste.com.br', 'admin-teste-123')
    a.goto(B + '/admin/clientes')
    linha = a.locator('tr', has_text='Oficina Roda Livre')
    linha.locator('button:has-text("Liberar")').click()
    janela = linha.locator('dialog')
    checar(janela.is_visible() and janela.locator('input[name=dias]').input_value() == '30',
           'Liberar abre a janela com 30 dias preenchidos')
    janela.locator('button:has-text("7 dias")').click()
    texto = janela.locator('.resultado-liberacao').inner_text()
    import datetime as _dt
    fim = (_dt.date.today() + _dt.timedelta(days=7)).strftime('%d/%m/%Y')
    checar(f'até {fim} (7 dias)' in texto, f'o atalho "7 dias" atualiza a data final na hora ({texto.strip()})')
    janela.locator('input[name=dias]').fill('45')
    fim45 = (_dt.date.today() + _dt.timedelta(days=45)).strftime('%d/%m/%Y')
    checar(f'até {fim45} (45 dias)' in janela.locator('.resultado-liberacao').inner_text(),
           'digitar outro número também')
    janela.locator('input[name=dias]').fill('7')
    janela.locator('button:has-text("Confirmar")').click()
    a.wait_for_load_state()
    checar('liberado por 7 dia(s): de hoje' in flashes(a), 'confirmar libera os 7 dias')
    checar(not erros_js, f'sem erro de script {erros_js[:1]}')
    adm_ctx.close()

    b.close()

servidor.shutdown()
print()
if falhas:
    print('FALHAS:')
    for f in falhas:
        print('  -', f)
    raise SystemExit(1)
print('NAVEGADOR OK')
