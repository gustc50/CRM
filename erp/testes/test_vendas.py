"""Aba Vendas: Serviços (NFS-e emitidas) e Produtos (NF-e emitidas).

    cd erp
    rm -f erp.db .chave_sessao && rm -rf backups
    python3 testes/test_vendas.py
"""
import datetime as dt
import io
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from openpyxl import load_workbook  # noqa: E402

import app as appmod  # noqa: E402
from models import NotaEletronica, NotaServico, Usuario, db  # noqa: E402

falhas = []
D = dt.date


def checar(condicao, descricao):
    print(('  OK    ' if condicao else '  FALHA ') + descricao)
    if not condicao:
        falhas.append(descricao)


def planilha(resposta):
    return [tuple(l) for l in load_workbook(io.BytesIO(resposta.data)).active.values]


with appmod.app.app_context():
    eid = Usuario.query.filter_by(email='cliente1@teste.com.br').first().empresa_id
    outra = Usuario.query.filter_by(email='cliente2@teste.com.br').first().empresa_id

    def nfse(empresa, chave, data, nome, doc, valor, papel='emitida', situacao='ATIVA'):
        n = NotaServico(empresa_id=empresa, chave_acesso=chave, papel=papel, numero=chave[-3:],
                        data_emissao=data, tomador_nome=nome, tomador_doc=doc,
                        valor_servico=valor, situacao=situacao)
        db.session.add(n)
        return n

    def nfe(empresa, chave, data, nome, doc, valor, papel='emitida', situacao='1'):
        n = NotaEletronica(empresa_id=empresa, chave=chave, tipo_doc='nfeProc', papel=papel,
                           data_emissao=data, dest_nome=nome, dest_doc=doc,
                           valor_total=valor, situacao=situacao)
        db.session.add(n)
        return n

    # Serviços: 3 válidas no período, fora de ordem de inserção
    s2 = nfse(eid, 'S002', D(2026, 10, 12), 'Beta Consultoria', '22333444000155', 2500.0)
    s1 = nfse(eid, 'S001', D(2026, 10, 3), 'Alfa Ltda', '11222333000181', 1000.0)
    s3 = nfse(eid, 'S003', D(2026, 10, 20), 'Carlos Souza', '52998224725', 750.5)
    nfse(eid, 'S004', D(2026, 10, 15), 'Cancelada SA', '33444555000166', 999.0, situacao='CANCELADA')
    nfse(eid, 'S005', D(2026, 10, 16), 'Fornecedor X', '44555666000177', 400.0, papel='recebida')
    nfse(eid, 'S006', D(2026, 9, 28), 'Setembro ME', '55666777000188', 300.0)
    s_outra = nfse(outra, 'S900', D(2026, 10, 10), 'SEGREDO DA OUTRA', '66777888000199', 123.0)

    # Produtos: 2 válidas, 1 cancelada, 1 denegada
    chave = '35261011222333000181550010000{:05d}1000000010'
    p1 = nfe(eid, chave.format(101), D(2026, 10, 5), 'Loja Um', '11222333000181', 4200.0)
    p2 = nfe(eid, chave.format(102), D(2026, 10, 25), 'Maria Silva', '52998224725', 89.9)
    nfe(eid, chave.format(103), D(2026, 10, 8), 'Cancelou', '11222333000181', 500.0, situacao='3')
    nfe(eid, chave.format(104), D(2026, 10, 9), 'Denegou', '11222333000181', 600.0, situacao='2')
    p_outra = nfe(outra, chave.format(999), D(2026, 10, 10), 'SEGREDO NFE', '66777888000199', 77.0)
    db.session.commit()
    ids_s = {'s1': s1.id, 's2': s2.id, 's3': s3.id, 'outra': s_outra.id}
    ids_p = {'p1': p1.id, 'p2': p2.id, 'outra': p_outra.id}

c = appmod.app.test_client()
c.post('/login', data={'email': 'cliente1@teste.com.br', 'senha': 'cliente-teste-123'},
       follow_redirects=True)
OUT = {'inicio': '2026-10-01', 'fim': '2026-10-31'}

# =============================================================== #
print('--- menu ---')
corpo = c.get('/vendas/servicos').get_data(as_text=True)
checar('Serviços' in corpo and 'Produtos' in corpo, 'Vendas tem as sub-abas Serviços e Produtos')
checar('/marketplace' in corpo, 'e o menu tem Marketplace')
corpo = c.get('/relatorio').get_data(as_text=True)
checar('Faturamento' in corpo, 'o antigo Relatórios › Vendas virou Faturamento (sem nome repetido)')

# =============================================================== #
print('\n--- Serviços ---')
corpo = c.get('/vendas/servicos?inicio=2026-10-01&fim=2026-10-31').get_data(as_text=True)
checar('Alfa Ltda' in corpo and 'Beta Consultoria' in corpo and 'Carlos Souza' in corpo,
       'lista as NFS-e emitidas do período')
checar(corpo.find('Alfa Ltda') < corpo.find('Beta Consultoria') < corpo.find('Carlos Souza'),
       'em ordem de data')
checar('11.222.333/0001-81' in corpo and '529.982.247-25' in corpo, 'com CNPJ e CPF formatados')
checar('Cancelada SA' not in corpo, 'nota cancelada fica de fora')
checar('1 nota(s) cancelada(s)' in corpo, 'e a tela diz que ela existe')
checar('Fornecedor X' not in corpo, 'nota RECEBIDA (a pagar) não é venda')
checar('Setembro ME' not in corpo, 'respeita o período')
checar('SEGREDO DA OUTRA' not in corpo, 'não mostra nota de outra empresa')
checar('R$ 4.250,50' in corpo, 'total do período: 1.000 + 2.500 + 750,50')
import re  # noqa: E402
caixas = re.findall(r'<input type="checkbox" name="ids"[^>]*>', corpo)
checar(len(caixas) == 3 and all('checked' in cx for cx in caixas),
       'cada nota tem caixa de seleção, todas já marcadas')

print('\n--- relatório só com as marcadas ---')
r = c.post('/vendas/servicos/relatorio',
           data={**OUT, 'ids': [str(ids_s['s1']), str(ids_s['s3'])]})
checar(r.status_code == 200 and 'spreadsheetml' in r.mimetype, 'gera um XLSX')
checar('vendas_servicos_2026-10-01_a_2026-10-31.xlsx' in r.headers.get('Content-Disposition', ''),
       'com o período no nome do arquivo')
linhas = planilha(r)
checar(linhas[0] == ('Data', 'Cliente', 'CNPJ/CPF', 'Valor (R$)'),
       'colunas na ordem pedida: data, cliente, CNPJ/CPF, valor')
checar(len(linhas) == 4, f'só as 2 marcadas + cabeçalho + total ({len(linhas)} linhas)')
checar(linhas[1] == ('03/10/2026', 'Alfa Ltda', '11.222.333/0001-81', 1000),
       f'primeira linha certa: {linhas[1]}')
checar(linhas[2][1] == 'Carlos Souza' and linhas[2][3] == 750.5, 'segunda linha certa')
checar('Beta Consultoria' not in str(linhas), 'a desmarcada não entra')
checar(linhas[3][3] == 1750.5, f'total só das marcadas: {linhas[3][3]}')

r = c.post('/vendas/servicos/relatorio', data={**OUT, 'ids': [str(ids_s['s1']), str(ids_s['outra'])]})
checar('SEGREDO DA OUTRA' not in str(planilha(r)),
       'id de outra empresa enfiado no formulário é ignorado')

r = c.post('/vendas/servicos/relatorio', data=OUT, follow_redirects=True)
checar('Marque pelo menos uma' in r.get_data(as_text=True), 'sem nada marcado, avisa em vez de gerar vazio')

# =============================================================== #
print('\n--- Produtos ---')
corpo = c.get('/vendas/produtos?inicio=2026-10-01&fim=2026-10-31').get_data(as_text=True)
checar('Loja Um' in corpo and 'Maria Silva' in corpo, 'lista as NF-e emitidas')
checar('Cancelou' not in corpo and 'Denegou' not in corpo, 'cancelada e denegada ficam de fora')
checar('2 nota(s) cancelada(s) ou denegada(s)' in corpo, 'e a tela conta as duas')
checar('SEGREDO NFE' not in corpo, 'não mostra NF-e de outra empresa')
checar('nº 101' in corpo, 'mostra o número da nota tirado da chave')

r = c.post('/vendas/produtos/relatorio', data={**OUT, 'ids': [str(ids_p['p2'])]})
linhas = planilha(r)
checar(len(linhas) == 3 and linhas[1][1] == 'Maria Silva' and linhas[1][3] == 89.9,
       'XLSX de produtos só com a marcada')

# =============================================================== #
print('\n--- contador ---')
c.post('/contabilidade/contador', data={'email_contador': 'contador@teste.com.br'},
       follow_redirects=True)
k = appmod.app.test_client()
k.post('/login', data={'email': 'contador@teste.com.br', 'senha': 'contador-teste-123'},
       follow_redirects=True)
k.get(f'/contador/abrir/{eid}', follow_redirects=True)
corpo = k.get('/vendas/servicos?inicio=2026-10-01&fim=2026-10-31').get_data(as_text=True)
checar('Alfa Ltda' in corpo, 'o contador vê as vendas do cliente')
r = k.post('/vendas/servicos/relatorio', data={**OUT, 'ids': [str(ids_s['s2'])]})
checar(r.status_code == 200 and 'spreadsheetml' in r.mimetype,
       'e gera o relatório, mesmo sendo POST (não grava nada)')
r = k.post('/adicionar', data={'tipo': 'Pagar', 'descricao': 'x', 'valor': '1',
                               'data_vencimento': '2026-10-01'}, follow_redirects=True)
checar('somente de leitura' in r.get_data(as_text=True),
       'mas segue barrado nos POSTs que gravam')

print()
if falhas:
    print('FALHAS:')
    for f in falhas:
        print('  -', f)
    raise SystemExit(1)
print('VENDAS OK')
