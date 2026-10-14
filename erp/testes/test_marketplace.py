"""Marketplaces: Mercado Livre, Shopee e Amazon contra APIs de mentira.

O servidor de mentira imita o formato de cada API e é exigente como a real:
confere token, assinatura HMAC da Shopee, janela máxima de 15 dias da
Shopee, rotação do refresh token do Mercado Livre e paginação de todos.

    cd erp
    rm -f erp.db .chave_sessao && rm -rf backups
    python3 testes/test_marketplace.py
"""
import datetime as dt
import hashlib
import hmac
import io
import json
import os
import pathlib
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

falhas = []
HOJE = dt.date.today()
INICIO = HOJE - dt.timedelta(days=40)


def checar(condicao, descricao):
    print(('  OK    ' if condicao else '  FALHA ') + descricao)
    if not condicao:
        falhas.append(descricao)


def dia(n):
    """n dias depois do início do período de teste."""
    return INICIO + dt.timedelta(days=n)


def ts(data):
    return int(dt.datetime.combine(data, dt.time(12, 0)).timestamp())


# ================================================================ #
# Estado das APIs de mentira
# ================================================================ #

ML = {'client_id': 'ML_APP_1', 'secret': 'ML_SEGREDO',
      'refresh_valido': 'RT_ML_1', 'access_valido': 'AT_ML_1', 'versao': 1,
      'chamadas_billing': 0}
SH = {'partner_id': '2001234', 'key': 'chave-parceiro-shopee', 'access': 'AT_SH_1',
      'refresh': 'RT_SH_1', 'janela_maxima_vista': 0}
AMZ = {'client_id': 'amzn1.application-oa2-client.TESTE', 'secret': 'AMZ_SEGREDO',
       'access': 'Atza|AT_AMZ_1', 'refresh': 'Atzr|RT_AMZ_1'}

# Pedidos do Mercado Livre: 3 interessantes + 50 de enchimento (força 2 páginas)
ml_pedidos = [
    {'id': 9001, 'status': 'paid', 'date_created': f'{dia(5)}T10:00:00.000-03:00',
     'total_amount': 150.0, 'buyer': {'nickname': 'ANALIMA', 'first_name': 'Ana', 'last_name': 'Lima'},
     'order_items': [{'item': {'title': 'Camiseta'}, 'quantity': 2, 'sale_fee': 9.0}]},
    {'id': 9002, 'status': 'paid', 'date_created': f'{dia(8)}T10:00:00.000-03:00',
     'total_amount': 500.0, 'buyer': {'nickname': 'EMPRESAX'},
     'order_items': [{'item': {'title': 'Kit'}, 'quantity': 1, 'sale_fee': 60.0}]},
    {'id': 9003, 'status': 'cancelled', 'date_created': f'{dia(9)}T10:00:00.000-03:00',
     'total_amount': 80.0, 'buyer': {'nickname': 'DESISTIU'},
     'order_items': [{'item': {'title': 'Boné'}, 'quantity': 1, 'sale_fee': 9.6}]},
] + [
    {'id': 8000 + i, 'status': 'paid', 'date_created': f'{dia(10)}T10:00:00.000-03:00',
     'total_amount': 10.0, 'buyer': {'nickname': f'C{i}'},
     'order_items': [{'item': {'title': 'Adesivo'}, 'quantity': 1, 'sale_fee': 1.0}]}
    for i in range(50)
]
ml_billing = {
    9001: {'billing_info': {'doc_type': 'CPF', 'doc_number': '529.982.247-25'}},           # formato antigo
    9002: {'buyer': {'billing_info': {'identification': {'type': 'CNPJ', 'number': '11222333000181'}}}},  # novo
}

# Shopee: pedidos espalhados por 40 dias (exige fatiar em janelas de 15)
sh_pedidos = {
    'SH001': {'create_time': ts(dia(1)), 'order_status': 'COMPLETED', 'total_amount': 200.0,
              'buyer_username': 'joao', 'buyer_cpf_id': '529.982.247-25',
              'recipient_address': {'name': 'João Lima'},
              'item_list': [{'item_name': 'Caneca', 'model_quantity_purchased': 1}]},
    'SH002': {'create_time': ts(dia(20)), 'order_status': 'SHIPPED', 'total_amount': 90.0,
              'buyer_username': 'maria', 'recipient_address': {'name': 'Maria'},
              'item_list': [{'item_name': 'Chaveiro', 'model_quantity_purchased': 3}]},
    'SH003': {'create_time': ts(dia(35)), 'order_status': 'UNPAID', 'total_amount': 40.0,
              'buyer_username': 'pedro', 'item_list': []},
}
sh_escrow = {'SH001': {'commission_fee': 24.0, 'service_fee': 6.0},
             'SH002': {'commission_fee': 10.8, 'service_fee': 2.7}}

# Amazon: 3 pedidos em 2 páginas
amz_paginas = [
    [{'AmazonOrderId': '701-0000001', 'PurchaseDate': f'{dia(6)}T12:00:00Z', 'OrderStatus': 'Shipped',
      'OrderTotal': {'CurrencyCode': 'BRL', 'Amount': '300.00'}, 'NumberOfItemsShipped': 1},
     {'AmazonOrderId': '701-0000002', 'PurchaseDate': f'{dia(7)}T12:00:00Z', 'OrderStatus': 'Canceled',
      'OrderTotal': {'CurrencyCode': 'BRL', 'Amount': '50.00'}}],
    [{'AmazonOrderId': '701-0000003', 'PurchaseDate': f'{dia(12)}T12:00:00Z', 'OrderStatus': 'Unshipped',
      'OrderTotal': {'CurrencyCode': 'BRL', 'Amount': '120.00'}, 'NumberOfItemsUnshipped': 2}],
]
amz_taxas = {'701-0000001': [-36.0, -12.5], '701-0000003': [-14.4]}


class Mock(BaseHTTPRequestHandler):
    def _json(self, codigo, corpo):
        dados = json.dumps(corpo).encode()
        self.send_response(codigo)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

    def _corpo(self):
        tamanho = int(self.headers.get('Content-Length') or 0)
        return self.rfile.read(tamanho).decode() if tamanho else ''

    def log_message(self, *_):
        pass

    def do_GET(self):
        self._rota('GET')

    def do_POST(self):
        self._rota('POST')

    # ---------------- roteamento ----------------
    def _rota(self, metodo):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        partes = url.path.split('/')
        prefixo = partes[1]
        caminho = '/' + '/'.join(partes[2:])
        if prefixo == 'ml':
            return self._ml(metodo, caminho, q)
        if prefixo == 'shopee':
            return self._shopee(metodo, caminho, q)
        if prefixo == 'lwa':
            return self._lwa(q)
        if prefixo == 'amz':
            return self._amazon(caminho, q)
        self._json(404, {})

    # ---------------- Mercado Livre ----------------
    def _ml(self, metodo, caminho, q):
        if caminho == '/oauth/token':
            f = {k: v[0] for k, v in parse_qs(self._corpo()).items()}
            if f.get('client_id') != ML['client_id'] or f.get('client_secret') != ML['secret']:
                return self._json(400, {'error': 'invalid_client'})
            if f['grant_type'] == 'authorization_code':
                if f.get('code') != 'CODIGO_ML':
                    return self._json(400, {'error': 'invalid_grant'})
            elif f['grant_type'] == 'refresh_token':
                if f.get('refresh_token') != ML['refresh_valido']:
                    return self._json(400, {'error': 'invalid_grant', 'message': 'refresh usado'})
                ML['versao'] += 1  # rotação: o refresh usado deixa de valer
            ML['access_valido'] = f"AT_ML_{ML['versao']}"
            ML['refresh_valido'] = f"RT_ML_{ML['versao']}"
            return self._json(200, {'access_token': ML['access_valido'], 'expires_in': 21600,
                                    'user_id': 777, 'refresh_token': ML['refresh_valido']})

        if self.headers.get('Authorization') != f"Bearer {ML['access_valido']}":
            return self._json(401, {'message': 'invalid access token'})

        if caminho == '/users/me':
            return self._json(200, {'id': 777, 'nickname': 'LOJA_TESTE'})
        if caminho == '/orders/search':
            if q.get('seller') != '777':
                return self._json(403, {})
            de, ate = q['order.date_created.from'][:10], q['order.date_created.to'][:10]
            achados = [p for p in ml_pedidos if de <= p['date_created'][:10] <= ate]
            ini, lim = int(q.get('offset', 0)), int(q.get('limit', 50))
            return self._json(200, {'results': achados[ini:ini + lim],
                                    'paging': {'total': len(achados), 'offset': ini, 'limit': lim}})
        if caminho.startswith('/orders/') and caminho.endswith('/billing_info'):
            ML['chamadas_billing'] += 1
            pid = int(caminho.split('/')[2])
            return self._json(200, ml_billing[pid]) if pid in ml_billing else self._json(404, {})
        self._json(404, {})

    # ---------------- Shopee ----------------
    def _shopee(self, metodo, caminho, q):
        token = q.get('access_token', '')
        loja = q.get('shop_id', '')
        base = f"{SH['partner_id']}{caminho}{q.get('timestamp')}{token}{loja}"
        esperado = hmac.new(SH['key'].encode(), base.encode(), hashlib.sha256).hexdigest()
        if q.get('sign') != esperado or q.get('partner_id') != SH['partner_id']:
            return self._json(200, {'error': 'error_sign', 'message': 'Wrong sign.'})

        if caminho == '/api/v2/auth/token/get':
            corpo = json.loads(self._corpo())
            if corpo.get('code') != 'CODE_SH' or corpo.get('shop_id') != 555:
                return self._json(200, {'error': 'error_auth', 'message': 'invalid code'})
            return self._json(200, {'error': '', 'access_token': SH['access'],
                                    'refresh_token': SH['refresh'], 'expire_in': 14400})
        if caminho == '/api/v2/auth/access_token/get':
            corpo = json.loads(self._corpo())
            if corpo.get('refresh_token') != SH['refresh']:
                return self._json(200, {'error': 'error_auth', 'message': 'invalid refresh'})
            SH['access'], SH['refresh'] = 'AT_SH_2', 'RT_SH_2'
            return self._json(200, {'error': '', 'access_token': SH['access'],
                                    'refresh_token': SH['refresh'], 'expire_in': 14400})

        if token != SH['access'] or loja != '555':
            return self._json(200, {'error': 'invalid_access_token', 'message': 'Invalid access_token.'})
        if caminho == '/api/v2/shop/get_shop_info':
            return self._json(200, {'error': '', 'shop_name': 'Loja Shopee Teste'})
        if caminho == '/api/v2/order/get_order_list':
            de, ate = int(q['time_from']), int(q['time_to'])
            SH['janela_maxima_vista'] = max(SH['janela_maxima_vista'], ate - de)
            if ate - de > 15 * 86400:
                return self._json(200, {'error': 'error_param',
                                        'message': 'time_to - time_from must be within 15 days'})
            lista = [{'order_sn': sn, 'order_status': p['order_status']}
                     for sn, p in sh_pedidos.items() if de <= p['create_time'] <= ate]
            return self._json(200, {'error': '', 'response': {'more': False, 'next_cursor': '',
                                                              'order_list': lista}})
        if caminho == '/api/v2/order/get_order_detail':
            numeros = q['order_sn_list'].split(',')
            if len(numeros) > 50:
                return self._json(200, {'error': 'error_param', 'message': 'max 50'})
            return self._json(200, {'error': '', 'response': {'order_list': [
                {'order_sn': sn, **sh_pedidos[sn]} for sn in numeros]}})
        if caminho == '/api/v2/payment/get_escrow_detail':
            return self._json(200, {'error': '', 'response': {
                'order_income': sh_escrow.get(q['order_sn'], {})}})
        self._json(404, {})

    # ---------------- Amazon ----------------
    def _lwa(self, q):
        f = {k: v[0] for k, v in parse_qs(self._corpo()).items()}
        if f.get('client_id') != AMZ['client_id'] or f.get('client_secret') != AMZ['secret']:
            return self._json(401, {'error': 'invalid_client'})
        if f['grant_type'] == 'authorization_code':
            if f.get('code') != 'SPAPI_CODE':
                return self._json(400, {'error': 'invalid_grant'})
            return self._json(200, {'access_token': AMZ['access'], 'refresh_token': AMZ['refresh'],
                                    'expires_in': 3600, 'token_type': 'bearer'})
        if f.get('refresh_token') not in (AMZ['refresh'], 'Atzr|COLADO'):
            return self._json(400, {'error': 'invalid_grant'})
        return self._json(200, {'access_token': AMZ['access'], 'expires_in': 3600})

    def _amazon(self, caminho, q):
        if self.headers.get('x-amz-access-token') != AMZ['access']:
            return self._json(403, {'errors': [{'code': 'Unauthorized'}]})
        if caminho == '/orders/v0/orders':
            if q.get('MarketplaceIds') != 'A2Q3Y263D00KWC':
                return self._json(400, {'errors': [{'message': 'marketplace'}]})
            pagina = 1 if q.get('NextToken') == 'PAGINA2' else 0
            corpo = {'Orders': amz_paginas[pagina]}
            if pagina == 0:
                corpo['NextToken'] = 'PAGINA2'
            return self._json(200, {'payload': corpo})
        if caminho.startswith('/finances/v0/orders/'):
            pid = caminho.split('/')[4]
            taxas = [{'FeeType': 'Fee', 'FeeAmount': {'CurrencyCode': 'BRL', 'CurrencyAmount': v}}
                     for v in amz_taxas.get(pid, [])]
            return self._json(200, {'payload': {'FinancialEvents': {'ShipmentEventList': [
                {'ShipmentItemList': [{'ItemFeeList': taxas}]}]}}})
        self._json(404, {})


servidor = HTTPServer(('127.0.0.1', 0), Mock)
threading.Thread(target=servidor.serve_forever, daemon=True).start()
BASE = f'http://127.0.0.1:{servidor.server_address[1]}'
os.environ.update({'ERP_ML_API': f'{BASE}/ml', 'ERP_ML_AUTH': f'{BASE}/mlauth',
                   'ERP_SHOPEE_URL': f'{BASE}/shopee', 'ERP_AMAZON_URL': f'{BASE}/amz',
                   'ERP_LWA_URL': f'{BASE}/lwa'})

import app as appmod  # noqa: E402
import marketplaces  # noqa: E402
from models import (Categoria, Cliente, ConexaoMarketplace, PedidoMarketplace,  # noqa: E402
                    Transacao, Usuario, db)


def entrar(cliente, email, senha):
    return cliente.post('/login', data={'email': email, 'senha': senha}, follow_redirects=True)


def conectar(cliente, chave, **retorno):
    """Faz o vai e volta da autorização como o navegador faria."""
    r = cliente.get(f'/marketplace/{chave}/conectar')
    destino = r.headers.get('Location', '')
    q = {k: v[0] for k, v in parse_qs(urlparse(destino).query).items()}
    if chave == 'shopee':
        state = parse_qs(urlparse(q['redirect']).query)['state'][0]
    else:
        state = q.get('state')
    r2 = cliente.get(f'/marketplace/{chave}/retorno', query_string={**retorno, 'state': state},
                     follow_redirects=True)
    return destino, q, r2


def sync(cliente, chave, inicio=INICIO, fim=HOJE):
    return cliente.post(f'/marketplace/{chave}/sincronizar',
                        data={'inicio': inicio.isoformat(), 'fim': fim.isoformat()},
                        follow_redirects=True)


adm = appmod.app.test_client()
entrar(adm, 'admin@teste.com.br', 'admin-teste-123')
c = appmod.app.test_client()
entrar(c, 'cliente1@teste.com.br', 'cliente-teste-123')
with appmod.app.app_context():
    eid = Usuario.query.filter_by(email='cliente1@teste.com.br').first().empresa_id

# ================================================================ #
print('--- 0) antes de o admin ativar ---')
corpo = c.get('/marketplace').get_data(as_text=True)
checar(corpo.count('Não ativado') == 3, 'os três aparecem como "não ativado"')
r = c.get('/marketplace/mercadolivre/conectar', follow_redirects=True)
checar('ainda não foi ativada' in r.get_data(as_text=True), 'e Conectar avisa em vez de quebrar')

adm.post('/admin/marketplaces', data={
    'ml_client_id': ML['client_id'], 'ml_secret': ML['secret'],
    'shopee_partner_id': SH['partner_id'], 'shopee_key': SH['key'], 'shopee_ambiente': 'sandbox',
    'amazon_app_id': 'amzn1.sp.solution.TESTE', 'amazon_client_id': AMZ['client_id'],
    'amazon_secret': AMZ['secret'], 'amazon_ambiente': 'sandbox', 'amazon_beta': 'on',
}, follow_redirects=True)
with appmod.app.app_context():
    checar(ML['secret'] not in (appmod.fiscal_sync.sistema_get('mkt_mercadolivre_secret_cripto') or ''),
           'segredos dos aplicativos ficam criptografados')
corpo = adm.get('/admin/configuracoes').get_data(as_text=True)
checar('/marketplace/mercadolivre/retorno' in corpo, 'o admin vê o endereço de retorno para registrar')
checar(ML['secret'] not in corpo, 'e o segredo não volta para a tela')

# ================================================================ #
print('\n--- 1) Mercado Livre: autorização ---')
destino, q, r = conectar(c, 'mercadolivre', code='CODIGO_ML')
checar(destino.startswith(f'{BASE}/mlauth/authorization?'), 'Conectar leva à página de autorização do ML')
checar(q.get('client_id') == ML['client_id'] and q.get('response_type') == 'code',
       'com o client_id do aplicativo')
checar(q.get('redirect_uri') == 'http://localhost/marketplace/mercadolivre/retorno',
       'e o endereço de retorno')
checar('LOJA_TESTE' in r.get_data(as_text=True), 'na volta, troca o código pelo token e mostra a loja')
with appmod.app.app_context():
    conexao = ConexaoMarketplace.query.filter_by(empresa_id=eid, marketplace='mercadolivre').first()
    checar(conexao is not None and conexao.conta_id == '777', 'conexão salva com o id do vendedor')
    checar('AT_ML' not in (conexao.access_token_cripto or '') and
           'RT_ML' not in (conexao.refresh_token_cripto or ''), 'tokens guardados criptografados')

# Retorno forjado (state errado) é ignorado
c2 = appmod.app.test_client()
entrar(c2, 'cliente2@teste.com.br', 'cliente-teste-123')
with appmod.app.app_context():
    outra = Usuario.query.filter_by(email='cliente2@teste.com.br').first().empresa
    outra.creditar_dias(30)
    db.session.commit()
    id_outra = outra.id
c2.get('/marketplace/mercadolivre/conectar')
r = c2.get('/marketplace/mercadolivre/retorno?code=CODIGO_ML&state=FORJADO', follow_redirects=True)
checar('foi ignorada' in r.get_data(as_text=True), 'retorno com state forjado é recusado')
with appmod.app.app_context():
    checar(ConexaoMarketplace.query.filter_by(empresa_id=id_outra).count() == 0,
           'e não cria conexão nenhuma')

# ================================================================ #
print('\n--- 2) Mercado Livre: pedidos e integração ---')
r = sync(c, 'mercadolivre')
texto = r.get_data(as_text=True)
checar('53 pedido(s)' in texto, 'traz os 53 pedidos (2 páginas de 50)')
with appmod.app.app_context():
    pedidos = {p.pedido_id: p for p in PedidoMarketplace.query.filter_by(marketplace='mercadolivre')}
    a, b, cancelado = pedidos['9001'], pedidos['9002'], pedidos['9003']
    checar(a.valor_bruto == 150 and a.taxas == 18 and a.valor_liquido == 132,
           f'pedido 9001: bruto 150, taxa 9×2 = 18, líquido 132 ({a.valor_bruto}/{a.taxas}/{a.valor_liquido})')
    checar(a.comprador_nome == 'Ana Lima' and a.comprador_doc == '52998224725',
           'comprador com nome e CPF (formato antigo do billing_info)')
    checar(b.comprador_doc == '11222333000181', 'CNPJ no formato novo do billing_info também')
    checar(cancelado.situacao == 'cancelado' and not cancelado.transacao_receita_id,
           'pedido cancelado é guardado, mas não vira lançamento')
    checar(ML['chamadas_billing'] == 52, f"busca documento só dos pagos ({ML['chamadas_billing']} chamadas)")

    receita_a = db.session.get(Transacao, a.transacao_receita_id)
    taxa_a = db.session.get(Transacao, a.transacao_taxas_id)
    checar(receita_a.tipo == 'Receber' and receita_a.valor == 150, 'vira conta a receber pelo bruto')
    checar(taxa_a.tipo == 'Pagar' and taxa_a.valor == 18, 'e conta a pagar com a comissão')
    checar(receita_a.data_vencimento == dia(5), 'na data do pedido')
    checar(receita_a.categoria.nome == 'Vendas Mercado Livre' and
           receita_a.categoria.centro_custo == 'Marketplace', 'na categoria do marketplace')
    checar(taxa_a.categoria.nome == 'Taxas de marketplace', 'taxa na categoria de taxas')

    cliente_a = db.session.get(Cliente, a.cliente_id)
    checar(cliente_a and cliente_a.cnpj_cpf == '52998224725' and cliente_a.nome == 'Ana Lima',
           'o comprador com CPF vira cliente')
    checar(receita_a.cliente_id == cliente_a.id, 'e a conta a receber fica ligada a ele')
    checar(Cliente.query.filter_by(empresa_id=eid).count() == 2,
           'quem veio sem documento não gera cadastro de cliente')
    n_receitas = Transacao.query.filter(Transacao.descricao.like('Mercado Livre pedido%')).count()
    checar(n_receitas == 52, f'52 contas a receber (pagos) — {n_receitas}')

print('\n--- 3) sincronizar de novo não duplica ---')
sync(c, 'mercadolivre')
with appmod.app.app_context():
    checar(PedidoMarketplace.query.filter_by(marketplace='mercadolivre').count() == 53, 'mesmos 53 pedidos')
    checar(Transacao.query.filter(Transacao.descricao.like('Mercado Livre pedido%')).count() == 52,
           'mesmas 52 contas a receber')
    checar(Cliente.query.filter_by(empresa_id=eid).count() == 2, 'mesmos 2 clientes')

print('\n--- 4) pedido cancelado depois ---')
with appmod.app.app_context():
    b_receita = db.session.get(Transacao, PedidoMarketplace.query.filter_by(pedido_id='9002').first().transacao_receita_id)
    b_receita.status, b_receita.data_pagamento = 'Concluído', dia(9)  # já recebido
    db.session.commit()
    id_receita_b = b_receita.id
    id_receita_a = PedidoMarketplace.query.filter_by(pedido_id='9001').first().transacao_receita_id
ml_pedidos[0]['status'] = 'cancelled'
ml_pedidos[1]['status'] = 'cancelled'
r = sync(c, 'mercadolivre')
with appmod.app.app_context():
    checar(db.session.get(Transacao, id_receita_a) is None,
           'cancelou com lançamento pendente: o lançamento é removido')
    checar(db.session.get(Transacao, id_receita_b) is not None,
           'cancelou com lançamento já baixado: fica, para não bagunçar o caixa')
checar('cancelado removido' in r.get_data(as_text=True), 'e o resumo conta o que foi removido')

print('\n--- 5) token vencido: renova sozinho, guardando o refresh novo ---')
with appmod.app.app_context():
    conexao = ConexaoMarketplace.query.filter_by(empresa_id=eid, marketplace='mercadolivre').first()
    conexao.expira_em = dt.datetime.now() - dt.timedelta(minutes=1)
    db.session.commit()
r = sync(c, 'mercadolivre')
checar('pedido(s)' in r.get_data(as_text=True), 'sincroniza normalmente depois de renovar')
with appmod.app.app_context():
    conexao = ConexaoMarketplace.query.filter_by(empresa_id=eid, marketplace='mercadolivre').first()
    guardado = marketplaces._decripto(conexao.refresh_token_cripto, appmod.data_path('.chave_secreta'))
    checar(guardado == ML['refresh_valido'] == 'RT_ML_2',
           f'o refresh token novo (de uso único) foi guardado: {guardado}')
    conexao.expira_em = dt.datetime.now() - dt.timedelta(minutes=1)
    db.session.commit()
r = sync(c, 'mercadolivre')
checar('pedido(s)' in r.get_data(as_text=True), 'e a segunda renovação usa o novo, não o já gasto')

print('\n--- 6) modo "só as taxas" ---')
c.post('/marketplace/mercadolivre/modo', data={'modo': 'so_taxas'}, follow_redirects=True)
ml_pedidos.append({'id': 9500, 'status': 'paid', 'date_created': f'{dia(30)}T10:00:00.000-03:00',
                   'total_amount': 1000.0, 'buyer': {'nickname': 'NOVO'},
                   'order_items': [{'item': {'title': 'TV'}, 'quantity': 1, 'sale_fee': 130.0}]})
sync(c, 'mercadolivre')
with appmod.app.app_context():
    novo = PedidoMarketplace.query.filter_by(pedido_id='9500').first()
    checar(novo.transacao_receita_id is None, 'não lança a receita (ela vem pela NF-e)')
    checar(novo.transacao_taxas_id is not None and
           db.session.get(Transacao, novo.transacao_taxas_id).valor == 130, 'mas lança a comissão')

print('\n--- 6b) modo "não lançar" ---')
c.post('/marketplace/mercadolivre/modo', data={'modo': 'nao_lancar'}, follow_redirects=True)
ml_pedidos.append({'id': 9501, 'status': 'paid', 'date_created': f'{dia(31)}T10:00:00.000-03:00',
                   'total_amount': 400.0, 'buyer': {'nickname': 'SO_LISTA'},
                   'order_items': [{'item': {'title': 'Fone'}, 'quantity': 1, 'sale_fee': 52.0}]})
sync(c, 'mercadolivre')
with appmod.app.app_context():
    so_lista = PedidoMarketplace.query.filter_by(pedido_id='9501').first()
    checar(so_lista is not None and so_lista.valor_bruto == 400,
           'o pedido entra na lista mesmo assim')
    checar(not so_lista.transacao_receita_id and not so_lista.transacao_taxas_id,
           'mas não vira lançamento nenhum')
r = c.post('/marketplace/mercadolivre/modo', data={'modo': 'inventado'}, follow_redirects=True)
with appmod.app.app_context():
    checar(ConexaoMarketplace.query.filter_by(empresa_id=eid, marketplace='mercadolivre')
           .first().modo_lancamento == 'nao_lancar', 'modo inexistente é ignorado')

# ================================================================ #
print('\n--- 7) Shopee: autorização assinada ---')
destino, q, r = conectar(c, 'shopee', code='CODE_SH', shop_id='555')
esperado = hmac.new(SH['key'].encode(),
                    f"{SH['partner_id']}/api/v2/shop/auth_partner{q['timestamp']}".encode(),
                    hashlib.sha256).hexdigest()
checar(destino.startswith(f'{BASE}/shopee/api/v2/shop/auth_partner?'), 'leva à autorização da Shopee')
checar(q.get('sign') == esperado, 'com a assinatura HMAC-SHA256 correta')
checar('Loja Shopee Teste' in r.get_data(as_text=True),
       'na volta, troca o código (chamada assinada) e mostra a loja')

print('\n--- 8) Shopee: 40 dias fatiados em janelas de 15 ---')
r = sync(c, 'shopee')
checar('3 pedido(s)' in r.get_data(as_text=True), 'acha os 3 pedidos espalhados em 40 dias')
checar(SH['janela_maxima_vista'] <= 15 * 86400, 'nunca pede mais de 15 dias de uma vez')
with appmod.app.app_context():
    p = {x.pedido_id: x for x in PedidoMarketplace.query.filter_by(marketplace='shopee')}
    checar(p['SH001'].taxas == 30 and p['SH001'].valor_liquido == 170,
           'taxas = comissão + serviço do extrato (24 + 6)')
    checar(p['SH001'].comprador_doc == '52998224725' and p['SH001'].cliente_id is not None,
           'CPF do comprador reaproveita o cliente que já existia (mesmo CPF)')
    checar(p['SH003'].situacao == 'pendente' and not p['SH003'].transacao_receita_id,
           'pedido ainda não pago não vira lançamento')

# ================================================================ #
print('\n--- 9) Amazon ---')
destino, q, r = conectar(c, 'amazon', spapi_oauth_code='SPAPI_CODE', selling_partner_id='A1SELLER')
checar(destino.startswith('https://sellercentral.amazon.com.br/apps/authorize/consent?'),
       'leva ao consentimento no Seller Central')
checar(q.get('application_id') == 'amzn1.sp.solution.TESTE' and q.get('version') == 'beta',
       'com o application_id e a flag de app em rascunho')
checar('A1SELLER' in r.get_data(as_text=True), 'na volta, troca o código no LWA')
r = sync(c, 'amazon')
checar('3 pedido(s)' in r.get_data(as_text=True), 'traz os pedidos das 2 páginas (NextToken)')
with appmod.app.app_context():
    p = {x.pedido_id: x for x in PedidoMarketplace.query.filter_by(marketplace='amazon')}
    checar(p['701-0000001'].taxas == 48.5 and p['701-0000001'].valor_liquido == 251.5,
           'taxas dos eventos financeiros (36 + 12,50), que vêm negativas')
    checar(p['701-0000002'].situacao == 'cancelado', 'Canceled vira cancelado')
    checar(p['701-0000003'].situacao == 'pago', 'Unshipped já é venda paga')
    checar(p['701-0000001'].cliente_id is None, 'sem dado do comprador, não inventa cliente')

c.post('/marketplace/amazon/desconectar', follow_redirects=True)
r = c.post('/marketplace/amazon/token', data={'refresh_token': 'qualquer-coisa'}, follow_redirects=True)
checar('Atzr|' in r.get_data(as_text=True), 'refresh token em formato errado é recusado')
r = c.post('/marketplace/amazon/token', data={'refresh_token': 'Atzr|COLADO', 'seller_id': 'A9'},
           follow_redirects=True)
checar('Amazon conectada' in r.get_data(as_text=True), 'colar um refresh token válido conecta')

# ================================================================ #
print('\n--- 10) o resto do sistema enxerga ---')
corpo = c.get(f'/marketplace?inicio={INICIO}&fim={HOJE}').get_data(as_text=True)
checar('Taxas do marketplace' in corpo and 'Líquido' in corpo, 'a aba mostra bruto, taxas e líquido')
corpo = c.get(f'/relatorios/dre?inicio={INICIO}&fim={HOJE}').get_data(as_text=True)
checar('Vendas Mercado Livre' in corpo and 'Taxas de marketplace' in corpo,
       'o DRE separa a receita e as taxas do marketplace')
checar('Marketplace' in corpo, 'num centro de custo próprio')

with appmod.app.app_context():
    ids = [str(x.id) for x in PedidoMarketplace.query.filter_by(empresa_id=eid, marketplace='shopee')]
r = c.post('/marketplace/relatorio', data={'ids': ids})
from openpyxl import load_workbook  # noqa: E402
linhas = [tuple(l) for l in load_workbook(io.BytesIO(r.data)).active.values]
checar(len(linhas) == 5 and linhas[0][0] == 'Data', 'XLSX só com os pedidos marcados (3 + cabeçalho + total)')
nao_pago = [l for l in linhas if l[2] == 'SH003'][0]
checar(nao_pago[7] is None and nao_pago[8] is None,
       'pedido não pago sai sem taxa nem líquido (não entrou dinheiro)')
checar(linhas[-1][8] == round(170 + (90 - 13.5), 2),
       f'total líquido só dos pagos: {linhas[-1][8]}')

corpo = c2.get(f'/marketplace?inicio={INICIO}&fim={HOJE}').get_data(as_text=True)
checar('Ana Lima' not in corpo and 'SH001' not in corpo, 'outra empresa não vê esses pedidos')

with appmod.app.app_context():
    resumo = appmod.tarefas_do_dia()
    checar(resumo['marketplaces'] == 3, f"a rotina diária sincroniza as 3 lojas ({resumo['marketplaces']})")

# ================================================================ #
print('\n--- 11) contador: vê e baixa, mas não mexe nas conexões ---')
c.post('/contabilidade/contador', data={'email_contador': 'contador@teste.com.br'},
       follow_redirects=True)
k = appmod.app.test_client()
k.post('/login', data={'email': 'contador@teste.com.br', 'senha': 'contador-teste-123'},
       follow_redirects=True)
k.get(f'/contador/abrir/{eid}', follow_redirects=True)
corpo = k.get(f'/marketplace?inicio={INICIO}&fim={HOJE}').get_data(as_text=True)
checar('SH001' in corpo, 'o contador vê os pedidos do cliente')
r = k.post('/marketplace/relatorio', data={'ids': ids})
checar(r.status_code == 200 and 'spreadsheetml' in r.mimetype, 'e gera o XLSX')

with appmod.app.app_context():
    antes = {x.marketplace: (x.modo_lancamento, x.ultima_sync)
             for x in ConexaoMarketplace.query.filter_by(empresa_id=eid)}
    qtde_antes = PedidoMarketplace.query.filter_by(empresa_id=eid).count()
k.post('/marketplace/shopee/sincronizar', data={'inicio': str(INICIO), 'fim': str(HOJE)})
k.post('/marketplace/shopee/modo', data={'modo': 'nao_lancar'})
k.post('/marketplace/shopee/desconectar')
r = k.get('/marketplace/mercadolivre/conectar')
checar(r.status_code == 302 and '/marketplace' in r.headers['Location']
       and 'mercadoli' not in r.headers['Location'],
       'Conectar não leva o contador para a autorização')
with appmod.app.app_context():
    depois = {x.marketplace: (x.modo_lancamento, x.ultima_sync)
              for x in ConexaoMarketplace.query.filter_by(empresa_id=eid)}
    checar(depois == antes, 'sincronizar, trocar o modo e desconectar não fazem nada')
    checar(PedidoMarketplace.query.filter_by(empresa_id=eid).count() == qtde_antes,
           'e nenhum pedido novo entra')

print()
if falhas:
    print('FALHAS:')
    for f in falhas:
        print('  -', f)
    raise SystemExit(1)
print('MARKETPLACES OK')
