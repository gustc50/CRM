"""Relatórios novos (DRE e Vendas) e a navegação reorganizada."""
import datetime as dt
import io
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import app as appmod  # noqa: E402
from models import (Categoria, Cliente, Empresa, Fornecedor, Transacao,  # noqa: E402
                    Usuario, db)

falhas = []


def checar(condicao, descricao):
    print(('  OK    ' if condicao else '  FALHA ') + descricao)
    if not condicao:
        falhas.append(descricao)


# --------------------------------------------------------------- #
# Dados conhecidos, para poder conferir os números na mão
# --------------------------------------------------------------- #
with appmod.app.app_context():
    empresa = Usuario.query.filter_by(email='cliente1@teste.com.br').first().empresa
    eid = empresa.id

    vendas_cat = Categoria(empresa_id=eid, nome='Vendas de balcão', tipo='Receber',
                           centro_custo='Comercial')
    servicos_cat = Categoria(empresa_id=eid, nome='Consultoria', tipo='Receber',
                             centro_custo='Comercial')
    aluguel_cat = Categoria(empresa_id=eid, nome='Aluguel da loja', tipo='Pagar',
                            centro_custo='Administrativo')
    folha_cat = Categoria(empresa_id=eid, nome='Salários', tipo='Pagar',
                          centro_custo='Pessoal')
    db.session.add_all([vendas_cat, servicos_cat, aluguel_cat, folha_cat])

    padaria = Cliente(empresa_id=eid, cnpj_cpf='11111111111111', nome='Mercado Central',
                      endereco='Rua A')
    bar = Cliente(empresa_id=eid, cnpj_cpf='22222222222222', nome='Bar do Zé',
                  endereco='Rua B')
    forn = Fornecedor(empresa_id=eid, cnpj_cpf='33333333333333', nome='Imobiliária',
                      endereco='Rua C')
    db.session.add_all([padaria, bar, forn])
    db.session.flush()

    # Período do teste: outubro/2026
    def lanc(tipo, desc, valor, venc, cat, cliente=None, fornecedor=None, pago=None):
        return Transacao(
            empresa_id=eid, tipo=tipo, descricao=desc, valor=valor,
            data_vencimento=venc, categoria_id=cat.id,
            cliente_id=cliente.id if cliente else None,
            fornecedor_id=fornecedor.id if fornecedor else None,
            status='Concluído' if pago else 'Pendente', data_pagamento=pago)

    db.session.add_all([
        # Receitas: 10.000 + 5.000 + 3.000 = 18.000 (sendo 13.000 já recebidos)
        lanc('Receber', 'Venda 1', 10000.0, dt.date(2026, 10, 5), vendas_cat, padaria,
             pago=dt.date(2026, 10, 5)),
        lanc('Receber', 'Venda 2', 5000.0, dt.date(2026, 10, 12), vendas_cat, bar,
             pago=dt.date(2026, 10, 12)),
        lanc('Receber', 'Consultoria', 3000.0, dt.date(2026, 10, 20), servicos_cat, padaria),
        # Despesas: 4.000 + 6.000 = 10.000
        lanc('Pagar', 'Aluguel', 4000.0, dt.date(2026, 10, 10), aluguel_cat,
             fornecedor=forn, pago=dt.date(2026, 10, 10)),
        lanc('Pagar', 'Folha', 6000.0, dt.date(2026, 10, 30), folha_cat, fornecedor=forn),
        # Fora do período, para provar que o filtro funciona
        lanc('Receber', 'Venda de setembro', 99999.0, dt.date(2026, 9, 10), vendas_cat, bar),
    ])
    db.session.commit()

c = appmod.app.test_client()
c.post('/login', data={'email': 'cliente1@teste.com.br', 'senha': 'cliente-teste-123'},
       follow_redirects=True)

PERIODO = 'inicio=2026-10-01&fim=2026-10-31'

# --------------------------------------------------------------- #
# 1) Navegação reorganizada
# --------------------------------------------------------------- #
print('--- navegação ---')
corpo = c.get('/').get_data(as_text=True)
checar('Relatórios' in corpo, 'o menu mostra "Relatórios" (plural)')
checar('/relatorios/dre' not in corpo,
       'as sub-abas ficam escondidas fora da seção, como em Cadastros')

dentro = c.get('/relatorio').get_data(as_text=True)
checar('/relatorios/dre' in dentro, 'dentro de Relatórios aparece a sub-aba DRE')
checar('/relatorios/vendas' in dentro, 'e a sub-aba Vendas')
checar('Por período' in dentro, 'junto com a sub-aba Por período')

corpo = c.get('/categorias').get_data(as_text=True)
checar(corpo.count('/categorias') >= 1, 'Categorias continua acessível')
# Dentro de Cadastros, as três sub-abas aparecem juntas
checar('/fornecedores' in corpo and '/clientes' in corpo,
       'e aparece ao lado de Fornecedores e Clientes (virou sub-aba de Cadastros)')

# --------------------------------------------------------------- #
# 2) Abas horizontais nas notas
# --------------------------------------------------------------- #
print('\n--- abas das notas fiscais ---')
for url, nome in (('/notas-eletronicas', 'NF-e'), ('/notas-servico', 'NFS-e')):
    corpo = c.get(url).get_data(as_text=True)
    checar('abas-horizontais' in corpo, f'{nome} tem a barra de abas')
    checar(corpo.count('data-painel="receber"') >= 2 and
           corpo.count('data-painel="pagar"') >= 2,
           f'{nome} tem os dois painéis ligados às duas abas')
    checar('hidden' not in corpo.split('painel-aba')[1][:200],
           f'{nome} começa com os painéis visíveis (funciona sem JS)')

# --------------------------------------------------------------- #
# 3) DRE — conferindo os números na mão
# --------------------------------------------------------------- #
print('\n--- DRE por competência ---')
r = c.get(f'/relatorios/dre?{PERIODO}')
corpo = r.get_data(as_text=True)
checar(r.status_code == 200, 'a tela abre')
checar('R$ 18.000,00' in corpo, 'receitas somam 18.000 (10.000 + 5.000 + 3.000)')
checar('R$ 10.000,00' in corpo, 'despesas somam 10.000 (4.000 + 6.000)')
checar('R$ 8.000,00' in corpo, 'resultado é 8.000')
checar('44,4% de lucro' in corpo, 'margem de 44,4% aparece em destaque')
checar('99.999' not in corpo, 'e o lançamento de setembro NÃO entra')

checar('Comercial' in corpo and 'Administrativo' in corpo and 'Pessoal' in corpo,
       'agrupa pelos centros de custo')
checar('Aluguel da loja' in corpo and 'Salários' in corpo,
       'e detalha as categorias dentro de cada um')

print('\n--- DRE por caixa ---')
corpo = c.get(f'/relatorios/dre?{PERIODO}&regime=caixa').get_data(as_text=True)
checar('R$ 15.000,00' in corpo, 'receitas caem para 15.000 (só o que foi recebido)')
checar('R$ 4.000,00' in corpo, 'despesas caem para 4.000 (só o que foi pago)')
checar('R$ 11.000,00' in corpo, 'resultado de caixa é 11.000')
checar('Consultoria' not in corpo, 'a consultoria não recebida fica de fora')

# --------------------------------------------------------------- #
# 4) Vendas
# --------------------------------------------------------------- #
print('\n--- vendas ---')
r = c.get(f'/relatorios/vendas?{PERIODO}')
corpo = r.get_data(as_text=True)
checar(r.status_code == 200, 'a tela abre')
checar('R$ 18.000,00' in corpo, 'vendido no período: 18.000')
checar('R$ 15.000,00' in corpo, 'já recebido: 15.000')
checar('R$ 3.000,00' in corpo, 'em aberto: 3.000')
checar('R$ 6.000,00' in corpo, 'ticket médio: 6.000 (18.000 / 3 vendas)')

checar('Mercado Central' in corpo and 'Bar do Zé' in corpo, 'lista os clientes')
pos_mercado = corpo.find('Mercado Central')
pos_bar = corpo.find('Bar do Zé')
checar(pos_mercado < pos_bar, 'ordenados por quanto compraram (maior primeiro)')
checar('72,2%' in corpo, 'com a participação de cada um (13.000 de 18.000)')

# --------------------------------------------------------------- #
# 5) Exportações
# --------------------------------------------------------------- #
print('\n--- exportação ---')
for url, nome in ((f'/relatorios/dre/exportar?{PERIODO}', 'DRE'),
                  (f'/relatorios/vendas/exportar?{PERIODO}', 'Vendas')):
    r = c.get(url)
    checar(r.status_code == 200 and 'spreadsheetml' in r.mimetype,
           f'{nome} exporta uma planilha')
    checar(r.data[:2] == b'PK', f'{nome}: arquivo xlsx válido')

from openpyxl import load_workbook  # noqa: E402
wb = load_workbook(io.BytesIO(c.get(f'/relatorios/dre/exportar?{PERIODO}').data))
valores = [tuple(linha) for linha in wb.active.values]
texto = str(valores)
checar('RESULTADO DO PERÍODO' in texto, 'a planilha do DRE traz o resultado')
numeros = [v[2] for v in valores if len(v) > 2 and isinstance(v[2], (int, float))]
checar(8000 in numeros, 'com o valor certo (8.000)')

# --------------------------------------------------------------- #
# 6) Isolamento: outra empresa não vê estes números
# --------------------------------------------------------------- #
print('\n--- isolamento ---')
with appmod.app.app_context():
    outro = Usuario.query.filter_by(email='cliente2@teste.com.br').first()
    outro.empresa.creditar_dias(30)
    db.session.commit()
k = appmod.app.test_client()
k.post('/login', data={'email': 'cliente2@teste.com.br', 'senha': 'cliente-teste-123'},
       follow_redirects=True)
corpo = k.get(f'/relatorios/dre?{PERIODO}').get_data(as_text=True)
checar('Mercado Central' not in corpo and 'R$ 18.000,00' not in corpo,
       'o DRE da outra empresa não mostra estes dados')
corpo = k.get(f'/relatorios/vendas?{PERIODO}').get_data(as_text=True)
checar('Mercado Central' not in corpo, 'nem o relatório de vendas')

# --------------------------------------------------------------- #
# 7) Contador enxerga os relatórios (é leitura)
# --------------------------------------------------------------- #
print('\n--- contador ---')
c.post('/contabilidade/contador', data={'email_contador': 'contador@teste.com.br'},
       follow_redirects=True)
t = appmod.app.test_client()
t.post('/login', data={'email': 'contador@teste.com.br', 'senha': 'contador-teste-123'},
       follow_redirects=True)
t.get(f'/contador/abrir/{eid}', follow_redirects=True)
checar(t.get(f'/relatorios/dre?{PERIODO}').status_code == 200, 'o contador abre o DRE')
checar('R$ 18.000,00' in t.get(f'/relatorios/dre?{PERIODO}').get_data(as_text=True),
       'com os números do cliente que o indicou')
corpo = t.get('/relatorio').get_data(as_text=True)
checar('/relatorios/dre' in corpo, 'e o menu dele traz as sub-abas de relatório')

# --------------------------------------------------------------- #
# 8) Período vazio não quebra
# --------------------------------------------------------------- #
print('\n--- período sem movimento ---')
corpo = c.get('/relatorios/dre?inicio=2030-01-01&fim=2030-01-31').get_data(as_text=True)
checar('Nenhuma receita no período' in corpo, 'DRE vazio avisa em vez de quebrar')
checar('sem faturamento no período' in corpo, 'e não tenta calcular margem de zero')
corpo = c.get('/relatorios/vendas?inicio=2030-01-01&fim=2030-01-31').get_data(as_text=True)
checar('Nenhuma venda no período' in corpo, 'vendas vazias também')

print()
if falhas:
    print('FALHAS:')
    for f in falhas:
        print('  -', f)
    raise SystemExit(1)
print('RELATÓRIOS OK')
