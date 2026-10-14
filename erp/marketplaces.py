"""Integração com marketplaces: Mercado Livre, Shopee e Amazon.

Nenhum dos três funciona com um "link de API". Os três exigem:

1. Um **aplicativo** registrado no portal de desenvolvedor do marketplace — é
   do dono do sistema, um só para todos os clientes, e as credenciais dele
   ficam nas Configurações do administrador.
2. A **autorização** de cada vendedor: o cliente clica em "Conectar", entra na
   conta dele no marketplace e aprova o acesso. O marketplace devolve um
   código que o sistema troca por tokens — guardados criptografados, por loja.

Depois disso, o sistema renova os tokens sozinho e busca os pedidos.

Cada conector devolve os pedidos no mesmo formato (dicionário normalizado),
e a parte de integração com o resto do sistema (cliente, categorias,
lançamentos) é uma só, em `sincronizar`.

As URLs de cada API podem ser trocadas por variável de ambiente (`ERP_ML_API`,
`ERP_ML_AUTH`, `ERP_SHOPEE_URL`, `ERP_AMAZON_URL`, `ERP_LWA_URL`): é o que os
testes usam para apontar para servidores de mentira.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import os
import re
import time
from urllib.parse import quote, urlencode

import requests

from models import (
    MARKETPLACES, MODO_NAO_LANCAR, MODO_RECEITA_E_TAXAS, MODO_SO_TAXAS,
    PEDIDO_CANCELADO, PEDIDO_PAGO, PEDIDO_PENDENTE,
    Categoria, Cliente, PedidoMarketplace, Transacao, db,
)

TIMEOUT = 30

# Margem para renovar o token antes de ele vencer no meio de uma consulta
MARGEM_RENOVACAO = dt.timedelta(minutes=5)

CENTRO_DE_CUSTO = 'Marketplace'
CATEGORIA_TAXAS = 'Taxas de marketplace'


class ErroMarketplace(Exception):
    pass


# ------------------------------------------------------------------ #
# Utilidades
# ------------------------------------------------------------------ #

def _so_digitos(valor):
    return re.sub(r'\D', '', str(valor or ''))


def _data(valor):
    """Data de um texto ISO (com ou sem hora) ou de um timestamp Unix."""
    if valor in (None, ''):
        return None
    if isinstance(valor, (int, float)):
        return dt.datetime.fromtimestamp(valor).date()
    try:
        return dt.date.fromisoformat(str(valor)[:10])
    except ValueError:
        return None


def _numero(valor):
    try:
        return round(float(valor or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _http(metodo, url, **kwargs):
    """Chamada HTTP com os erros traduzidos para português."""
    kwargs.setdefault('timeout', TIMEOUT)
    try:
        resposta = requests.request(metodo, url, **kwargs)
    except requests.RequestException as exc:
        raise ErroMarketplace(f'Não foi possível falar com o marketplace: {exc}') from exc

    if resposta.status_code in (401, 403):
        raise ErroMarketplace('O marketplace recusou o acesso. A autorização pode ter '
                              'expirado ou sido revogada: conecte a loja de novo.')
    if resposta.status_code == 429:
        raise ErroMarketplace('O marketplace pediu para esperar (limite de consultas). '
                              'Tente de novo em alguns minutos.')
    if resposta.status_code >= 400:
        raise ErroMarketplace(f'O marketplace respondeu {resposta.status_code}: '
                              f'{resposta.text[:200]}')
    try:
        return resposta.json()
    except ValueError as exc:
        raise ErroMarketplace('O marketplace respondeu algo que não é JSON.') from exc


def _config(chave, padrao=''):
    from fiscal_sync import sistema_get
    return sistema_get(chave, padrao)


def _segredo(chave, caminho_chave):
    """Lê um segredo do sistema guardado criptografado."""
    from fiscal_certificado import descriptografar_senha
    cripto = _config(chave)
    if not cripto:
        return ''
    return descriptografar_senha(cripto, caminho_chave)


def _cripto(texto, caminho_chave):
    from fiscal_certificado import criptografar_senha
    return criptografar_senha(texto, caminho_chave) if texto else None


def _decripto(texto, caminho_chave):
    from fiscal_certificado import descriptografar_senha
    return descriptografar_senha(texto, caminho_chave) if texto else ''


def _situacao(status, pagos, cancelados):
    if status in cancelados:
        return PEDIDO_CANCELADO
    if status in pagos:
        return PEDIDO_PAGO
    return PEDIDO_PENDENTE


# ------------------------------------------------------------------ #
# Mercado Livre
# ------------------------------------------------------------------ #

class MercadoLivre:
    """API do Mercado Livre (OAuth 2.0 + /orders/search).

    O token de acesso vale 6 horas; o de renovação é de uso único — cada
    renovação devolve um novo, que precisa ser guardado no lugar do antigo.
    """
    chave = 'mercadolivre'
    nome = 'Mercado Livre'

    PAGOS = {'paid', 'partially_refunded'}
    CANCELADOS = {'cancelled', 'invalid'}

    @property
    def api(self):
        return os.environ.get('ERP_ML_API', 'https://api.mercadolibre.com').rstrip('/')

    @property
    def auth(self):
        return os.environ.get('ERP_ML_AUTH', 'https://auth.mercadolivre.com.br').rstrip('/')

    def configurado(self):
        return bool(_config('mkt_mercadolivre_client_id') and _config('mkt_mercadolivre_secret_cripto'))

    def url_autorizacao(self, redirect_uri, state, caminho_chave):
        return (f'{self.auth}/authorization?' + urlencode({
            'response_type': 'code',
            'client_id': _config('mkt_mercadolivre_client_id'),
            'redirect_uri': redirect_uri,
            'state': state,
        }))

    def _token(self, dados, caminho_chave):
        dados.update({
            'client_id': _config('mkt_mercadolivre_client_id'),
            'client_secret': _segredo('mkt_mercadolivre_secret_cripto', caminho_chave),
        })
        r = _http('POST', f'{self.api}/oauth/token', data=dados,
                  headers={'Accept': 'application/json'})
        if not r.get('access_token'):
            raise ErroMarketplace(f"Mercado Livre não devolveu o token: {r.get('message') or r}")
        return {
            'access_token': r['access_token'],
            'refresh_token': r.get('refresh_token'),
            'expires_in': int(r.get('expires_in') or 21600),
            'conta_id': str(r.get('user_id') or ''),
        }

    def concluir_autorizacao(self, args, redirect_uri, caminho_chave):
        codigo = args.get('code')
        if not codigo:
            raise ErroMarketplace('O Mercado Livre não devolveu o código de autorização.')
        tokens = self._token({'grant_type': 'authorization_code', 'code': codigo,
                              'redirect_uri': redirect_uri}, caminho_chave)
        eu = _http('GET', f'{self.api}/users/me',
                   headers={'Authorization': f"Bearer {tokens['access_token']}"})
        tokens['conta_id'] = str(eu.get('id') or tokens['conta_id'])
        tokens['conta_nome'] = eu.get('nickname') or eu.get('first_name') or tokens['conta_id']
        return tokens

    def renovar(self, refresh_token, conexao, caminho_chave):
        return self._token({'grant_type': 'refresh_token', 'refresh_token': refresh_token},
                           caminho_chave)

    def _documento(self, pedido, cabecalho):
        """CPF/CNPJ do comprador. Não vem no pedido: fica nos dados de faturamento."""
        comprador = pedido.get('buyer') or {}
        direto = (comprador.get('billing_info') or {}).get('doc_number')
        if direto:
            return _so_digitos(direto)
        try:
            info = _http('GET', f"{self.api}/orders/{pedido['id']}/billing_info", headers=cabecalho)
        except ErroMarketplace:
            return ''
        # O formato mudou entre versões da API; aceita os dois
        antigo = (info.get('billing_info') or {}).get('doc_number')
        novo = (((info.get('buyer') or {}).get('billing_info') or {})
                .get('identification') or {}).get('number')
        return _so_digitos(antigo or novo)

    def pedidos(self, access_token, conexao, inicio, fim, caminho_chave):
        cabecalho = {'Authorization': f'Bearer {access_token}'}
        parametros = {
            'seller': conexao.conta_id,
            'order.date_created.from': f'{inicio.isoformat()}T00:00:00.000-03:00',
            'order.date_created.to': f'{fim.isoformat()}T23:59:59.999-03:00',
            'sort': 'date_asc',
            'limit': 50,
            'offset': 0,
        }
        resultado = []
        while True:
            pagina = _http('GET', f'{self.api}/orders/search', params=parametros, headers=cabecalho)
            for p in pagina.get('results', []):
                resultado.append(self._normalizar(p, cabecalho))
            paging = pagina.get('paging') or {}
            parametros['offset'] += parametros['limit']
            if parametros['offset'] >= int(paging.get('total') or 0) or not pagina.get('results'):
                break
        return resultado

    def _normalizar(self, p, cabecalho):
        itens = p.get('order_items') or []
        # sale_fee é a comissão por unidade vendida
        taxas = sum(_numero(i.get('sale_fee')) * int(i.get('quantity') or 1) for i in itens)
        bruto = _numero(p.get('total_amount'))
        comprador = p.get('buyer') or {}
        nome = ' '.join(x for x in (comprador.get('first_name'), comprador.get('last_name')) if x)
        status = p.get('status') or ''
        situacao = _situacao(status, self.PAGOS, self.CANCELADOS)
        return {
            'pedido_id': str(p.get('id')),
            'data': _data(p.get('date_created')),
            'situacao': situacao,
            'status_original': status,
            'comprador_nome': nome or comprador.get('nickname') or '',
            'comprador_doc': self._documento(p, cabecalho) if situacao == PEDIDO_PAGO else '',
            'itens': ', '.join(f"{i.get('quantity', 1)}x {(i.get('item') or {}).get('title', '')}"
                               for i in itens),
            'valor_bruto': bruto,
            'taxas': round(taxas, 2),
            'frete': 0.0,
            'valor_liquido': round(bruto - taxas, 2),
        }


# ------------------------------------------------------------------ #
# Shopee
# ------------------------------------------------------------------ #

class Shopee:
    """Shopee Open Platform v2.

    Toda chamada é assinada: HMAC-SHA256, com a chave do parceiro, do texto
    partner_id + caminho + timestamp (+ access_token + shop_id nas chamadas
    da loja). A listagem de pedidos aceita no máximo 15 dias por consulta, por
    isso o período é fatiado.
    """
    chave = 'shopee'
    nome = 'Shopee'

    HOSTS = {
        'producao': 'https://partner.shopeemobile.com',
        'sandbox': 'https://partner.test-stable.shopeemobile.com',
    }
    PAGOS = {'READY_TO_SHIP', 'PROCESSED', 'SHIPPED', 'TO_CONFIRM_RECEIVE', 'COMPLETED',
             'RETRY_SHIP', 'TO_RETURN'}
    CANCELADOS = {'CANCELLED', 'IN_CANCEL'}
    JANELA_DIAS = 15

    @property
    def host(self):
        sobrescrito = os.environ.get('ERP_SHOPEE_URL') or _config('mkt_shopee_host')
        if sobrescrito:
            return sobrescrito.rstrip('/')
        return self.HOSTS.get(_config('mkt_shopee_ambiente', 'sandbox'), self.HOSTS['sandbox'])

    @property
    def partner_id(self):
        return _config('mkt_shopee_partner_id')

    def configurado(self):
        return bool(self.partner_id and _config('mkt_shopee_key_cripto'))

    @staticmethod
    def assinar(partner_id, chave, caminho, timestamp, access_token='', shop_id=''):
        base = f'{partner_id}{caminho}{timestamp}{access_token}{shop_id}'
        return hmac.new(chave.encode(), base.encode(), hashlib.sha256).hexdigest()

    def _parametros(self, caminho, caminho_chave, access_token='', shop_id=''):
        ts = int(time.time())
        chave = _segredo('mkt_shopee_key_cripto', caminho_chave)
        parametros = {'partner_id': int(self.partner_id), 'timestamp': ts,
                      'sign': self.assinar(self.partner_id, chave, caminho, ts, access_token, shop_id)}
        if access_token:
            parametros.update({'access_token': access_token, 'shop_id': int(shop_id)})
        return parametros

    def _verificar(self, r):
        if r.get('error'):
            raise ErroMarketplace(f"Shopee: {r.get('message') or r.get('error')}")
        return r

    def url_autorizacao(self, redirect_uri, state, caminho_chave):
        # A Shopee não tem parâmetro "state": ele vai dentro do endereço de
        # retorno, e ela acrescenta code e shop_id a esse endereço.
        caminho = '/api/v2/shop/auth_partner'
        parametros = self._parametros(caminho, caminho_chave)
        separador = '&' if '?' in redirect_uri else '?'
        parametros['redirect'] = f'{redirect_uri}{separador}state={state}'
        return f'{self.host}{caminho}?{urlencode(parametros, quote_via=quote)}'

    def _token(self, caminho, corpo, caminho_chave):
        corpo['partner_id'] = int(self.partner_id)
        r = self._verificar(_http('POST', f'{self.host}{caminho}',
                                  params=self._parametros(caminho, caminho_chave), json=corpo))
        if not r.get('access_token'):
            raise ErroMarketplace('A Shopee não devolveu o token de acesso.')
        return {'access_token': r['access_token'], 'refresh_token': r.get('refresh_token'),
                'expires_in': int(r.get('expire_in') or 14400)}

    def concluir_autorizacao(self, args, redirect_uri, caminho_chave):
        codigo, shop_id = args.get('code'), args.get('shop_id')
        if not codigo or not shop_id:
            raise ErroMarketplace('A Shopee não devolveu o código de autorização e a loja.')
        tokens = self._token('/api/v2/auth/token/get',
                             {'code': codigo, 'shop_id': int(shop_id)}, caminho_chave)
        tokens['conta_id'] = str(shop_id)
        try:
            caminho = '/api/v2/shop/get_shop_info'
            info = self._verificar(_http('GET', f'{self.host}{caminho}', params=self._parametros(
                caminho, caminho_chave, tokens['access_token'], shop_id)))
            tokens['conta_nome'] = info.get('shop_name') or f'Loja {shop_id}'
        except ErroMarketplace:
            tokens['conta_nome'] = f'Loja {shop_id}'
        return tokens

    def renovar(self, refresh_token, conexao, caminho_chave):
        return self._token('/api/v2/auth/access_token/get',
                           {'refresh_token': refresh_token, 'shop_id': int(conexao.conta_id)},
                           caminho_chave)

    def _get(self, caminho, access_token, conexao, caminho_chave, **extra):
        parametros = self._parametros(caminho, caminho_chave, access_token, conexao.conta_id)
        parametros.update(extra)
        return self._verificar(_http('GET', f'{self.host}{caminho}', params=parametros))

    def pedidos(self, access_token, conexao, inicio, fim, caminho_chave):
        numeros = []
        janela = inicio
        while janela <= fim:
            ate = min(fim, janela + dt.timedelta(days=self.JANELA_DIAS - 1))
            de_ts = int(dt.datetime.combine(janela, dt.time.min).timestamp())
            ate_ts = int(dt.datetime.combine(ate, dt.time.max).timestamp())
            cursor = ''
            while True:
                r = self._get('/api/v2/order/get_order_list', access_token, conexao, caminho_chave,
                              time_range_field='create_time', time_from=de_ts, time_to=ate_ts,
                              page_size=100, cursor=cursor)
                corpo = r.get('response') or {}
                numeros += [o['order_sn'] for o in corpo.get('order_list', []) if o.get('order_sn')]
                if not corpo.get('more'):
                    break
                cursor = corpo.get('next_cursor') or ''
            janela = ate + dt.timedelta(days=1)

        resultado = []
        for i in range(0, len(numeros), 50):  # o detalhe aceita até 50 por vez
            r = self._get('/api/v2/order/get_order_detail', access_token, conexao, caminho_chave,
                          order_sn_list=','.join(numeros[i:i + 50]),
                          response_optional_fields='buyer_username,total_amount,item_list,'
                                                   'pay_time,buyer_cpf_id,recipient_address')
            for pedido in (r.get('response') or {}).get('order_list', []):
                resultado.append(self._normalizar(pedido, access_token, conexao, caminho_chave))
        return resultado

    def _taxas(self, numero, access_token, conexao, caminho_chave):
        """Comissão e tarifas, que vêm no extrato do pedido (escrow)."""
        try:
            r = self._get('/api/v2/payment/get_escrow_detail', access_token, conexao,
                          caminho_chave, order_sn=numero)
        except ErroMarketplace:
            return 0.0
        renda = (r.get('response') or {}).get('order_income') or {}
        return round(sum(_numero(renda.get(c)) for c in
                         ('commission_fee', 'service_fee', 'seller_transaction_fee',
                          'transaction_fee')), 2)

    def _normalizar(self, p, access_token, conexao, caminho_chave):
        status = p.get('order_status') or ''
        situacao = _situacao(status, self.PAGOS, self.CANCELADOS)
        bruto = _numero(p.get('total_amount'))
        taxas = self._taxas(p['order_sn'], access_token, conexao, caminho_chave) \
            if situacao == PEDIDO_PAGO else 0.0
        endereco = p.get('recipient_address') or {}
        itens = p.get('item_list') or []
        return {
            'pedido_id': p['order_sn'],
            'data': _data(p.get('create_time')),
            'situacao': situacao,
            'status_original': status,
            'comprador_nome': endereco.get('name') or p.get('buyer_username') or '',
            'comprador_doc': _so_digitos(p.get('buyer_cpf_id')),
            'itens': ', '.join(f"{i.get('model_quantity_purchased', 1)}x {i.get('item_name', '')}"
                               for i in itens),
            'valor_bruto': bruto,
            'taxas': taxas,
            'frete': 0.0,
            'valor_liquido': round(bruto - taxas, 2),
        }


# ------------------------------------------------------------------ #
# Amazon
# ------------------------------------------------------------------ #

class Amazon:
    """Amazon Selling Partner API (SP-API), região América do Norte, que é a
    do Brasil.

    O acesso é pelo Login with Amazon (LWA): um refresh token de longa duração
    gera access tokens de 1 hora. Ele vem da autorização pelo Seller Central
    ou, para aplicativo privado, da autoautorização — por isso dá para colar o
    refresh token direto.

    Nome e CPF/CNPJ do comprador são dados restritos na Amazon (exigem um
    token especial de dados pessoais), então normalmente não vêm: o pedido
    entra sem cliente vinculado.
    """
    chave = 'amazon'
    nome = 'Amazon'

    HOSTS = {
        'producao': 'https://sellingpartnerapi-na.amazon.com',
        'sandbox': 'https://sandbox.sellingpartnerapi-na.amazon.com',
    }
    MARKETPLACE_BRASIL = 'A2Q3Y263D00KWC'
    PAGOS = {'Unshipped', 'PartiallyShipped', 'Shipped', 'InvoiceUnconfirmed'}
    CANCELADOS = {'Canceled', 'Unfulfillable'}

    @property
    def host(self):
        return (os.environ.get('ERP_AMAZON_URL')
                or self.HOSTS.get(_config('mkt_amazon_ambiente', 'sandbox'), self.HOSTS['sandbox'])
                ).rstrip('/')

    @property
    def lwa(self):
        return os.environ.get('ERP_LWA_URL', 'https://api.amazon.com').rstrip('/')

    def configurado(self):
        return bool(_config('mkt_amazon_client_id') and _config('mkt_amazon_secret_cripto'))

    def url_autorizacao(self, redirect_uri, state, caminho_chave):
        parametros = {'application_id': _config('mkt_amazon_app_id'), 'state': state,
                      'redirect_uri': redirect_uri}
        if _config('mkt_amazon_beta') == '1':
            parametros['version'] = 'beta'  # app ainda em rascunho
        return 'https://sellercentral.amazon.com.br/apps/authorize/consent?' + urlencode(parametros)

    def _token(self, dados, caminho_chave):
        dados.update({'client_id': _config('mkt_amazon_client_id'),
                      'client_secret': _segredo('mkt_amazon_secret_cripto', caminho_chave)})
        r = _http('POST', f'{self.lwa}/auth/o2/token', data=dados)
        if not r.get('access_token'):
            raise ErroMarketplace(f"Amazon não devolveu o token: {r.get('error_description') or r}")
        return {'access_token': r['access_token'], 'refresh_token': r.get('refresh_token'),
                'expires_in': int(r.get('expires_in') or 3600)}

    def concluir_autorizacao(self, args, redirect_uri, caminho_chave):
        codigo = args.get('spapi_oauth_code')
        if not codigo:
            raise ErroMarketplace('A Amazon não devolveu o código de autorização.')
        tokens = self._token({'grant_type': 'authorization_code', 'code': codigo,
                              'redirect_uri': redirect_uri}, caminho_chave)
        tokens['conta_id'] = args.get('selling_partner_id') or ''
        tokens['conta_nome'] = tokens['conta_id'] or 'Conta Amazon'
        return tokens

    def conectar_com_refresh_token(self, refresh_token, conta_id, caminho_chave):
        """Autoautorização: o vendedor gera o refresh token no Seller Central."""
        tokens = self._token({'grant_type': 'refresh_token', 'refresh_token': refresh_token},
                             caminho_chave)
        tokens['refresh_token'] = tokens.get('refresh_token') or refresh_token
        tokens['conta_id'] = conta_id or ''
        tokens['conta_nome'] = conta_id or 'Conta Amazon'
        return tokens

    def renovar(self, refresh_token, conexao, caminho_chave):
        tokens = self._token({'grant_type': 'refresh_token', 'refresh_token': refresh_token},
                             caminho_chave)
        tokens['refresh_token'] = tokens.get('refresh_token') or refresh_token
        return tokens

    def pedidos(self, access_token, conexao, inicio, fim, caminho_chave):
        cabecalho = {'x-amz-access-token': access_token}
        # A API recusa CreatedBefore a menos de 2 minutos do momento atual
        agora = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=3)
        antes = min(dt.datetime.combine(fim, dt.time.max, tzinfo=dt.timezone.utc), agora)
        parametros = {
            'MarketplaceIds': self.MARKETPLACE_BRASIL,
            'CreatedAfter': dt.datetime.combine(inicio, dt.time.min,
                                                tzinfo=dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'CreatedBefore': antes.strftime('%Y-%m-%dT%H:%M:%SZ'),
        }
        resultado = []
        while True:
            r = _http('GET', f'{self.host}/orders/v0/orders', params=parametros, headers=cabecalho)
            corpo = r.get('payload') or {}
            for p in corpo.get('Orders', []):
                resultado.append(self._normalizar(p, cabecalho))
            proximo = corpo.get('NextToken')
            if not proximo:
                break
            parametros = {'MarketplaceIds': self.MARKETPLACE_BRASIL, 'NextToken': proximo}
        return resultado

    def _taxas(self, numero, cabecalho):
        """Comissões e tarifas, nos eventos financeiros do pedido (vêm negativas)."""
        try:
            r = _http('GET', f'{self.host}/finances/v0/orders/{numero}/financialEvents',
                      headers=cabecalho)
        except ErroMarketplace:
            return 0.0
        eventos = ((r.get('payload') or {}).get('FinancialEvents') or {})
        total = 0.0
        for envio in eventos.get('ShipmentEventList') or []:
            for item in envio.get('ShipmentItemList') or []:
                for taxa in item.get('ItemFeeList') or []:
                    total += abs(_numero((taxa.get('FeeAmount') or {}).get('CurrencyAmount')))
        return round(total, 2)

    def _normalizar(self, p, cabecalho):
        status = p.get('OrderStatus') or ''
        situacao = _situacao(status, self.PAGOS, self.CANCELADOS)
        bruto = _numero((p.get('OrderTotal') or {}).get('Amount'))
        taxas = self._taxas(p['AmazonOrderId'], cabecalho) if situacao == PEDIDO_PAGO else 0.0
        comprador = p.get('BuyerInfo') or {}
        fiscal = comprador.get('BuyerTaxInfo') or {}
        documento = ''
        for item in fiscal.get('TaxClassifications') or []:
            documento = documento or _so_digitos(item.get('Value'))
        qtd = int(p.get('NumberOfItemsShipped') or 0) + int(p.get('NumberOfItemsUnshipped') or 0)
        return {
            'pedido_id': p['AmazonOrderId'],
            'data': _data(p.get('PurchaseDate')),
            'situacao': situacao,
            'status_original': status,
            'comprador_nome': comprador.get('BuyerName') or '',
            'comprador_doc': documento,
            'itens': f'{qtd} item(ns)' if qtd else '',
            'valor_bruto': bruto,
            'taxas': taxas,
            'frete': 0.0,
            'valor_liquido': round(bruto - taxas, 2),
        }


CONECTORES = {c.chave: c for c in (MercadoLivre(), Shopee(), Amazon())}


# ------------------------------------------------------------------ #
# Tokens
# ------------------------------------------------------------------ #

def salvar_tokens(conexao, tokens, caminho_chave):
    conexao.access_token_cripto = _cripto(tokens['access_token'], caminho_chave)
    if tokens.get('refresh_token'):
        conexao.refresh_token_cripto = _cripto(tokens['refresh_token'], caminho_chave)
    conexao.expira_em = dt.datetime.now() + dt.timedelta(seconds=tokens.get('expires_in') or 3600)


def token_valido(conexao, caminho_chave):
    """Access token em dia, renovando antes se estiver para vencer."""
    conector = CONECTORES[conexao.marketplace]
    if conexao.expira_em and conexao.expira_em - MARGEM_RENOVACAO > dt.datetime.now():
        return _decripto(conexao.access_token_cripto, caminho_chave)

    refresh = _decripto(conexao.refresh_token_cripto, caminho_chave)
    if not refresh:
        raise ErroMarketplace('A autorização venceu e não há como renová-la: conecte a loja de novo.')
    tokens = conector.renovar(refresh, conexao, caminho_chave)
    salvar_tokens(conexao, tokens, caminho_chave)
    db.session.commit()
    return tokens['access_token']


# ------------------------------------------------------------------ #
# Integração com o resto do sistema
# ------------------------------------------------------------------ #

def _categoria(empresa_id, nome, tipo):
    categoria = Categoria.query.filter_by(empresa_id=empresa_id, nome=nome, tipo=tipo).first()
    if categoria is None:
        categoria = Categoria(empresa_id=empresa_id, nome=nome, tipo=tipo,
                              centro_custo=CENTRO_DE_CUSTO)
        db.session.add(categoria)
        db.session.flush()
    return categoria


def _cliente(empresa_id, marketplace, documento, nome, categoria):
    """Cliente pelo CPF/CNPJ do comprador — reaproveita se já existir.

    Sem documento não cria nada: um cadastro de cliente sem CPF/CNPJ não serve
    para nota nem para casar pagamento, e só encheria a lista.
    """
    if len(documento) not in (11, 14):
        return None, False
    cliente = Cliente.query.filter_by(empresa_id=empresa_id, cnpj_cpf=documento).first()
    if cliente is not None:
        return cliente, False
    cliente = Cliente(empresa_id=empresa_id, cnpj_cpf=documento,
                      nome=(nome or f'Comprador {MARKETPLACES[marketplace]}')[:150],
                      endereco=f'Informado no {MARKETPLACES[marketplace]}',
                      categoria_id=categoria.id)
    db.session.add(cliente)
    db.session.flush()
    return cliente, True


def _remover_se_pendente(transacao_id):
    """Apaga o lançamento de um pedido cancelado, se ninguém deu baixa nele."""
    if not transacao_id:
        return True
    transacao = db.session.get(Transacao, transacao_id)
    if transacao is None:
        return True
    if transacao.status == 'Pendente':
        db.session.delete(transacao)
        return True
    return False  # já foi baixado: mexer agora bagunçaria o caixa


def integrar(pedido, modo):
    """Leva um pedido para cliente, categorias e lançamentos, conforme o modo.

    Retorna o que fez, para o resumo. Pode ser chamado de novo sem duplicar:
    cada lançamento só é criado se o pedido ainda não tiver o seu.
    """
    feito = {'receitas': 0, 'taxas': 0, 'clientes': 0, 'removidos': 0}
    nome_mkt = MARKETPLACES[pedido.marketplace]

    if pedido.situacao == PEDIDO_CANCELADO:
        if _remover_se_pendente(pedido.transacao_receita_id):
            if pedido.transacao_receita_id:
                feito['removidos'] += 1
            pedido.transacao_receita_id = None
        if _remover_se_pendente(pedido.transacao_taxas_id):
            if pedido.transacao_taxas_id:
                feito['removidos'] += 1
            pedido.transacao_taxas_id = None
        return feito

    if pedido.situacao != PEDIDO_PAGO or modo == MODO_NAO_LANCAR:
        return feito

    cat_venda = _categoria(pedido.empresa_id, f'Vendas {nome_mkt}', 'Receber')
    if pedido.cliente_id is None:
        cliente, criado = _cliente(pedido.empresa_id, pedido.marketplace,
                                   pedido.comprador_doc or '', pedido.comprador_nome, cat_venda)
        if cliente is not None:
            pedido.cliente_id = cliente.id
            feito['clientes'] += int(criado)

    rotulo = f'{nome_mkt} pedido {pedido.pedido_id}'
    if modo == MODO_RECEITA_E_TAXAS and not pedido.transacao_receita_id and pedido.valor_bruto > 0:
        receita = Transacao(empresa_id=pedido.empresa_id, tipo='Receber',
                            descricao=rotulo[:100], valor=pedido.valor_bruto,
                            data_vencimento=pedido.data or dt.date.today(),
                            cliente_id=pedido.cliente_id, categoria_id=cat_venda.id)
        db.session.add(receita)
        db.session.flush()
        pedido.transacao_receita_id = receita.id
        feito['receitas'] += 1

    if modo in (MODO_RECEITA_E_TAXAS, MODO_SO_TAXAS) and not pedido.transacao_taxas_id \
            and pedido.taxas > 0:
        cat_taxa = _categoria(pedido.empresa_id, CATEGORIA_TAXAS, 'Pagar')
        taxa = Transacao(empresa_id=pedido.empresa_id, tipo='Pagar',
                         descricao=f'Taxas {rotulo}'[:100], valor=pedido.taxas,
                         data_vencimento=pedido.data or dt.date.today(),
                         categoria_id=cat_taxa.id)
        db.session.add(taxa)
        db.session.flush()
        pedido.transacao_taxas_id = taxa.id
        feito['taxas'] += 1

    return feito


def sincronizar(conexao, inicio, fim, caminho_chave):
    """Busca os pedidos do período e integra ao sistema. Retorna o resumo em texto."""
    conector = CONECTORES[conexao.marketplace]
    token = token_valido(conexao, caminho_chave)
    pedidos = conector.pedidos(token, conexao, inicio, fim, caminho_chave)

    contagem = {'novos': 0, 'atualizados': 0, 'receitas': 0, 'taxas': 0,
                'clientes': 0, 'removidos': 0}
    agora = dt.datetime.now()

    for dados in pedidos:
        registro = PedidoMarketplace.query.filter_by(
            empresa_id=conexao.empresa_id, marketplace=conexao.marketplace,
            pedido_id=dados['pedido_id']).first()
        if registro is None:
            registro = PedidoMarketplace(empresa_id=conexao.empresa_id,
                                         marketplace=conexao.marketplace,
                                         pedido_id=dados['pedido_id'])
            db.session.add(registro)
            contagem['novos'] += 1
        else:
            contagem['atualizados'] += 1

        for campo in ('data', 'situacao', 'status_original', 'comprador_nome', 'itens',
                      'valor_bruto', 'taxas', 'frete', 'valor_liquido'):
            setattr(registro, campo, dados[campo])
        # Documento que já veio uma vez não é apagado se a consulta seguinte não trouxer
        if dados['comprador_doc']:
            registro.comprador_doc = dados['comprador_doc']
        registro.comprador_nome = (registro.comprador_nome or '')[:200]
        registro.itens = (registro.itens or '')[:300]
        registro.atualizado_em = agora
        db.session.flush()

        for chave, valor in integrar(registro, conexao.modo_lancamento).items():
            contagem[chave] += valor

    partes = [f"{len(pedidos)} pedido(s) de {inicio:%d/%m} a {fim:%d/%m}",
              f"{contagem['novos']} novo(s)"]
    if contagem['receitas']:
        partes.append(f"{contagem['receitas']} conta(s) a receber")
    if contagem['taxas']:
        partes.append(f"{contagem['taxas']} lançamento(s) de taxa")
    if contagem['clientes']:
        partes.append(f"{contagem['clientes']} cliente(s) novo(s)")
    if contagem['removidos']:
        partes.append(f"{contagem['removidos']} lançamento(s) de pedido cancelado removido(s)")

    conexao.ultima_sync = agora
    conexao.ultimo_resultado = (', '.join(partes) + '.')[:300]
    db.session.commit()
    return conexao.ultimo_resultado
