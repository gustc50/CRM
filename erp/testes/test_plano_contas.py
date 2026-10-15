"""Plano de contas e conciliação contábil: o contador importa o plano
(Domínio, Questor, Alterdata, Calima), liga categorias e bancos às contas e
os lançamentos ganham débito e crédito sozinhos. O cliente vê, mas não mexe.

Os arquivos em testes/planos/ foram montados a partir dos leiautes usuais de
cada sistema — não são exportações reais.

    cd erp
    rm -f erp.db .chave_sessao && rm -rf backups
    python3 testes/test_plano_contas.py
"""
import html
import io
import pathlib
import re
import sys

PASTA = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(PASTA.parent))

from openpyxl import load_workbook  # noqa: E402

import app as appmod  # noqa: E402
import contabil  # noqa: E402
from models import (  # noqa: E402
    Categoria, ContaBancaria, ContaContabil, Fornecedor, MapeamentoContabil, Transacao, Usuario, db,
)

falhas = []


def checar(condicao, descricao):
    print(('  OK    ' if condicao else '  FALHA ') + descricao)
    if not condicao:
        falhas.append(descricao)


def arquivo(nome):
    return (PASTA / 'planos' / nome).read_bytes()


def flash(r):
    return ' '.join(re.findall(r'<li class="(?:sucesso|erro)">(.*?)</li>', r.get_data(as_text=True), re.S))


def entrar(email, senha):
    c = appmod.app.test_client()
    c.post('/login', data={'email': email, 'senha': senha})
    return c


def importar(cliente, nome, sistema='dominio', mascara='', conteudo=None):
    return cliente.post('/contabil/plano', data={
        'sistema': sistema, 'mascara': mascara,
        'arquivo': (io.BytesIO(conteudo if conteudo is not None else arquivo(nome)), nome),
    }, content_type='multipart/form-data', follow_redirects=True)


# ================================================================ #
print('--- 1) leitura dos formatos de cada sistema ---')
contas, resumo = contabil.ler_plano(arquivo('dominio.txt'), 'dominio.txt')
por = {c['classificacao']: c for c in contas}
checar(resumo['contas'] == 20 and resumo['analiticas'] == 12, f'Domínio (CSV com ;, cp1252): {resumo}')
checar(por['1.1.1.02.001'] == {'classificacao': '1.1.1.02.001', 'codigo': '9', 'descricao': 'BANCO DO BRASIL S/A',
                               'tipo': 'A', 'natureza': None, 'nivel': 5}, 'com acentos, código reduzido e tipo certos')

contas, resumo = contabil.ler_plano(arquivo('dominio_relatorio.txt'), 'dominio_relatorio.txt', '9.9.9.99.999')
por = {c['classificacao']: c for c in contas}
checar(resumo['contas'] == 6 and '1.1.1.02.001' in por and '1.1.1.01' in por,
       'Domínio em relatório de texto, classificação sem pontos + máscara')
checar(por['1.1.1.01']['tipo'] == 'S' and resumo['ignoradas'] == 1,
       'título da página e linha de total são pulados')

contas, resumo = contabil.ler_plano(arquivo('questor.csv'), 'questor.csv')
por = {c['classificacao']: c for c in contas}
checar(resumo['contas'] == 6 and por['3.1.01.001']['natureza'] == 'C' and por['3.1.01.001']['codigo'] == '20',
       'Questor: "Conta" é o código reduzido, com natureza D/C')

contas, resumo = contabil.ler_plano(arquivo('calima.csv'), 'calima.csv')
por = {c['classificacao']: c for c in contas}
checar(not resumo['com_cabecalho'] and por['4.1.2.01.001']['descricao'] == 'ALUGUEL'
       and por['4.1.2.01.001']['codigo'] == '51', 'Calima sem cabeçalho: colunas reconhecidas pelo conteúdo')

contas, resumo = contabil.ler_plano(arquivo('alterdata.xlsx'), 'alterdata.xlsx')
por = {c['classificacao']: c for c in contas}
checar(resumo['contas'] == 5 and por['1.1.1.02.001']['codigo'] == '102' and por['1']['tipo'] == 'S',
       'Alterdata (.xlsx): a coluna "Código" com pontos vira a classificação; "Sintética" vira S')

sem_tipo = 'Classificação;Descrição\n1;ATIVO\n1.1;CIRCULANTE\n1.1.01;CAIXA\n'.encode()
contas, _ = contabil.ler_plano(sem_tipo, 'x.csv')
checar([c['tipo'] for c in contas] == ['S', 'S', 'A'], 'sem coluna de tipo: quem tem conta filha é sintética')

for ruim, nome in ((b'qualquer coisa\nsem conta nenhuma\n', 'x.txt'), (b'', 'vazio.csv')):
    try:
        contabil.ler_plano(ruim, nome)
        checar(False, f'arquivo que não é plano ({nome}) é recusado')
    except contabil.ErroPlano:
        checar(True, f'arquivo que não é plano ({nome}) é recusado com explicação')
try:
    contabil.ler_plano(b'x', 'antigo.xls')
except contabil.ErroPlano as exc:
    checar('.xlsx' in str(exc), '.xls antigo pede para salvar como .xlsx')

# ================================================================ #
print('\n--- 2) dados da empresa ---')
with appmod.app.app_context():
    eid = Usuario.query.filter_by(email='cliente1@teste.com.br').first().empresa_id
    eid2 = Usuario.query.filter_by(email='cliente2@teste.com.br').first().empresa_id

    def cat(nome, tipo):
        c = Categoria.query.filter_by(empresa_id=eid, nome=nome).first()
        if c is None:
            c = Categoria(empresa_id=eid, nome=nome, tipo=tipo)
            db.session.add(c)
            db.session.flush()
        return c

    aluguel, energia, agua = cat('Aluguel', 'Pagar'), cat('Energia elétrica', 'Pagar'), cat('Água', 'Pagar')
    limpeza, vendas = cat('Material de limpeza', 'Pagar'), cat('Vendas', 'Receber')
    bb = ContaBancaria(empresa_id=eid, nome='Conta corrente', banco='001')
    db.session.add(bb)
    banco_sabesp = Fornecedor(empresa_id=eid, nome='Banco do Brasil', cnpj_cpf='', endereco='')
    db.session.add(banco_sabesp)
    db.session.flush()
    hoje = appmod.date.today()

    def lanc(desc, tipo, valor, categoria=None, banco=None, fornecedor=None):
        t = Transacao(empresa_id=eid, tipo=tipo, descricao=desc, valor=valor, data_vencimento=hoje,
                      categoria_id=categoria.id if categoria else None,
                      conta_bancaria_id=banco.id if banco else None,
                      fornecedor_id=fornecedor.id if fornecedor else None)
        db.session.add(t)
        return t

    ids = {}
    for desc, tipo, valor, categoria, banco, forn in (
            ('Aluguel outubro', 'Pagar', 1500, aluguel, bb, None),
            ('Conta de luz', 'Pagar', 300, energia, None, None),
            ('Venda balcão', 'Receber', 5000, vendas, bb, None),
            ('Tarifa DOC', 'Pagar', 12.5, None, bb, banco_sabesp),
            ('Produtos de limpeza', 'Pagar', 80, limpeza, bb, None)):
        ids[desc] = lanc(desc, tipo, valor, categoria, banco, forn)
    db.session.commit()
    ids = {k: v.id for k, v in ids.items()}
    ids_cat = {'limpeza': limpeza.id, 'agua': agua.id, 'aluguel': aluguel.id, 'energia': energia.id,
               'vendas': vendas.id, 'bb': bb.id}

c = entrar('cliente1@teste.com.br', 'cliente-teste-123')
k = entrar('contador@teste.com.br', 'contador-teste-123')
k.get(f'/contador/abrir/{eid}')

# ================================================================ #
print('\n--- 3) quem pode o quê ---')
corpo = k.get('/lancamentos').get_data(as_text=True)
checar('href="/contabil"' in corpo and '>Configurações<' in corpo,
       'dentro do cliente, o contador tem o item Configurações')
corpo = k.get('/contabil').get_data(as_text=True)
checar('Importar plano de contas' in corpo and 'name="arquivo"' in corpo, 'onde ele importa o plano de contas')
corpo = c.get('/contabil').get_data(as_text=True)
checar('configurada pelo seu contador' in corpo and 'name="arquivo"' not in corpo,
       'o cliente vê a tela, sem o formulário de importar')
checar('Plano de contas' in c.get('/contabilidade').get_data(as_text=True),
       'para o cliente, é uma sub-aba de Contabilidade')
r = importar(c, 'dominio.txt')
checar('Só o contador' in flash(r), 'o cliente não consegue importar')
with appmod.app.app_context():
    checar(ContaContabil.query.filter_by(empresa_id=eid).count() == 0, 'e nada foi gravado')

# ================================================================ #
print('\n--- 4) importação e sugestões automáticas ---')
r = importar(k, 'dominio.txt')
checar('20 contas, 12 analíticas' in flash(r), 'o contador importa o plano do Domínio')
with appmod.app.app_context():
    def conta_de(tipo, ref=None):
        m = MapeamentoContabil.query.filter_by(empresa_id=eid, tipo=tipo, referencia_id=ref).first()
        return m.conta.classificacao if m else None
    checar(conta_de('categoria', ids_cat['aluguel']) == '4.1.1.01.001', 'Aluguel → ALUGUÉIS E CONDOMÍNIOS (pelo nome)')
    checar(conta_de('categoria', ids_cat['energia']) == '4.1.1.01.002', 'Energia elétrica → ENERGIA ELÉTRICA')
    checar(conta_de('categoria', ids_cat['vendas']) == '3.1.1.01.001',
           'Vendas → RECEITA DE VENDAS, não DEVOLUÇÃO DE VENDAS')
    checar(conta_de('banco', ids_cat['bb']) == '1.1.1.02.001', 'o banco 001 → BANCO DO BRASIL S/A')
    checar(conta_de('caixa') == '1.1.1.01.001', 'e a conta Caixa → CAIXA GERAL')
    checar(conta_de('categoria', ids_cat['limpeza']) is None, 'o que não casa pelo nome fica sem vínculo')
    checar(all(m.origem == 'sugestao' for m in MapeamentoContabil.query.filter_by(empresa_id=eid)),
           'tudo marcado como sugestão, para o contador conferir')
    checar(ContaContabil.query.filter_by(empresa_id=eid2).count() == 0, 'a outra empresa não ganha plano nenhum')


def contabil_de(corpo, descricao):
    trecho = corpo[corpo.find(descricao):]
    achado = re.search(r'class="linha-contabil( pendente)?"[^>]*>\s*([^<]+?)\s*</span>', trecho)
    return html.unescape(achado.group(2)).replace('\xa0', ' ') if achado else ''


corpo = c.get('/lancamentos').get_data(as_text=True)
checar(contabil_de(corpo, 'Aluguel outubro') == 'D 4.1.1.01.001 · C 1.1.1.02.001',
       'nos Lançamentos do cliente: aluguel debita a despesa e credita o banco')
checar(contabil_de(corpo, 'Conta de luz') == 'D 4.1.1.01.002 · C 1.1.1.01.001', 'sem banco, credita o Caixa')
checar(contabil_de(corpo, 'Venda balcão') == 'D 1.1.1.02.001 · C 3.1.1.01.001',
       'receita: debita o banco e credita a receita')
checar('categoria "Material de limpeza" sem conta' in contabil_de(corpo, 'Produtos de limpeza'),
       'o que falta aparece como pendente, dizendo o motivo')

# ================================================================ #
print('\n--- 5) o contador ajusta ---')
with appmod.app.app_context():
    despesas_bancarias = ContaContabil.query.filter_by(empresa_id=eid, classificacao='4.1.1.02.001').first().id
    outra_conta = ContaContabil.query.filter_by(empresa_id=eid, classificacao='4.1.1.01.003').first().id
    sintetica = ContaContabil.query.filter_by(empresa_id=eid, classificacao='4.1.1.01').first()
    sintetica = sintetica.id if sintetica else ContaContabil.query.filter_by(empresa_id=eid, tipo='S').first().id

r = k.post('/contabil/regras', data={'texto': 'tarifa', 'tipo_lancamento': 'Pagar', 'conta_id': despesas_bancarias},
           follow_redirects=True)
checar('Regra criada' in flash(r), 'regra por palavra: "tarifa" → DESPESAS BANCÁRIAS')
corpo = c.get('/lancamentos').get_data(as_text=True)
checar(contabil_de(corpo, 'Tarifa DOC') == 'D 4.1.1.02.001 · C 1.1.1.02.001', 'e a tarifa já sai conciliada')

r = k.post('/contabil/regras', data={'texto': 'x', 'conta_id': despesas_bancarias}, follow_redirects=True)
checar('de 2 a 80' in flash(r), 'regra com palavra curta demais é recusada')
r = k.post('/contabil/regras', data={'texto': 'imposto', 'conta_id': sintetica}, follow_redirects=True)
checar('analítica' in flash(r), 'regra apontando para conta sintética é recusada')

campos = {f'categoria_{ids_cat["limpeza"]}': outra_conta, f'categoria_{ids_cat["agua"]}': ''}
r = k.post('/contabil/vinculos', data=campos, follow_redirects=True)
checar('Conciliação salva' in flash(r), 'o contador salva o de-para')
corpo = c.get('/lancamentos').get_data(as_text=True)
checar(contabil_de(corpo, 'Produtos de limpeza') == 'D 4.1.1.01.003 · C 1.1.1.02.001',
       'o lançamento antigo muda na hora, sem reprocessar nada')
corpo = c.get('/contabil').get_data(as_text=True)
checar('5 de 5' in corpo and 'tudo conciliado' in corpo, 'o resumo mostra tudo conciliado')
checar('name="categoria_' not in corpo and 'ÁGUA E ESGOTO' in corpo,
       'o cliente vê os vínculos, sem poder trocar')

r = c.post('/contabil/vinculos', data={f'categoria_{ids_cat["limpeza"]}': despesas_bancarias},
           follow_redirects=True)
checar('Só o contador' in flash(r), 'o cliente não troca vínculo')
r = c.post('/contabil/regras', data={'texto': 'aluguel', 'conta_id': despesas_bancarias}, follow_redirects=True)
checar('Só o contador' in flash(r), 'nem cria regra')
r = k.post('/contabil/vinculos', data={f'categoria_{ids_cat["limpeza"]}': 999999}, follow_redirects=True)
checar('foram ignoradas' in flash(r), 'conta que não é do plano desta empresa é ignorada')

# ================================================================ #
print('\n--- 6) reimportar mantém os vínculos ---')
novo = arquivo('dominio.txt').decode('cp1252')
novo = novo.replace('402;4.1.1.01.002;ENERGIA ELÉTRICA;A', '402;4.1.1.01.002;ENERGIA ELÉTRICA - CEMIG;A')
novo = novo.replace('403;4.1.1.01.003;ÁGUA E ESGOTO;A\n', '')
r = importar(k, 'dominio_v2.txt', conteudo=novo.encode('cp1252'))
mensagens = flash(r)
checar('19 contas' in mensagens and 'atualizadas, sem perder os vínculos' in mensagens,
       'reimportar atualiza as contas pelo número da classificação')
checar('saíram do plano mas ainda estão em vínculos' in mensagens,
       'conta usada num vínculo que sumiu do arquivo é avisada (e não apagada)')
with appmod.app.app_context():
    checar(conta_de('categoria', ids_cat['energia']) == '4.1.1.01.002', 'o vínculo da energia continua')
    checar(ContaContabil.query.filter_by(empresa_id=eid, classificacao='4.1.1.01.002').first().descricao
           == 'ENERGIA ELÉTRICA - CEMIG', 'com a descrição nova')

# ================================================================ #
print('\n--- 7) o que vai para o contador ---')
r = c.get(f'/contabilidade/baixar?inicio={hoje.replace(day=1)}&fim={hoje}')
import zipfile  # noqa: E402
pacote = zipfile.ZipFile(io.BytesIO(r.data))
planilha = [n for n in pacote.namelist() if n.endswith('.xlsx')][0]
linhas = [tuple(l) for l in load_workbook(io.BytesIO(pacote.read(planilha))).active.values]
cab = linhas[0]
checar('Débito (classificação)' in cab and 'Crédito (reduzido)' in cab and 'Conciliação' in cab,
       'a planilha do pacote ganha débito, crédito e situação')
aluguel_linha = [l for l in linhas if l[0] == 'Aluguel outubro'][0]
checar(aluguel_linha[cab.index('Débito (classificação)')] == '4.1.1.01.001'
       and aluguel_linha[cab.index('Crédito (reduzido)')] == '9',
       'com o código reduzido e a classificação de cada lado')

corpo = c.get('/equipe/historico').get_data(as_text=True)
checar('Importou o plano de contas' in corpo and 'Configurou a conciliação contábil' in corpo,
       'o que o contador fez aparece no histórico do cliente')

# ================================================================ #
print('\n--- 8) apagar ---')
r = c.post('/contabil/plano/apagar', follow_redirects=True)
checar('Só o contador' in flash(r), 'o cliente não apaga o plano')
r = k.post('/contabil/plano/apagar', follow_redirects=True)
with appmod.app.app_context():
    checar(ContaContabil.query.filter_by(empresa_id=eid).count() == 0
           and MapeamentoContabil.query.filter_by(empresa_id=eid).count() == 0,
           'o contador apaga o plano e os vínculos')
checar('linha-contabil' not in c.get('/lancamentos').get_data(as_text=True),
       'e os lançamentos deixam de mostrar a parte contábil')

print()
if falhas:
    print('FALHAS:')
    for f in falhas:
        print('  -', f)
    raise SystemExit(1)
print('PLANO DE CONTAS OK')
