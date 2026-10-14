"""Leitura e escrita de valores em reais, no formato brasileiro.

Os campos de valor eram `<input type="number">`, que deixa a vírgula a cargo
do navegador: num Chrome em inglês, "150,50" virava 15050 e o sistema gravava
R$ 15.050,00 sem reclamar. Agora o campo é texto e quem interpreta é o
servidor, sempre do mesmo jeito.
"""
import re

_SO_NUMERO = re.compile(r'^\d+(\.\d+)?$')


def ler_valor(texto):
    """Converte o que a pessoa digitou em float, ou None se não der para ler.

    Aceita o formato brasileiro e os jeitos comuns de escrever:

        "150,50"  "1.234,56"  "1234,56"  "R$ 1.234,56"  "150.50"  "1.234"

    A regra para separadores:
    - com vírgula e ponto, o que vem por último separa os centavos;
    - só vírgula: é a dos centavos;
    - só ponto: mais de um, ou exatamente 3 dígitos depois dele, é milhar
      ("1.234" = mil duzentos e trinta e quatro); senão, centavos ("150.5").

    Mais de duas casas decimais é recusado em vez de arredondado: "1,234"
    pode ser mil e duzentos ou um real e vinte e três, e chutar seria pior.
    """
    if texto is None:
        return None
    if isinstance(texto, (int, float)):
        return float(texto)
    t = str(texto).replace('R$', '').replace('\xa0', '').replace(' ', '').strip()
    if not t:
        return None

    tem_virgula, tem_ponto = ',' in t, '.' in t
    if tem_virgula and tem_ponto:
        decimal, milhar = (',', '.') if t.rfind(',') > t.rfind('.') else ('.', ',')
        inteiro, _, centavos = t.rpartition(decimal)
        grupos = inteiro.split(milhar)
        if milhar in centavos or any(len(g) != 3 for g in grupos[1:]) or not grupos[0]:
            return None
        t = ''.join(grupos) + '.' + centavos
    elif tem_virgula:
        if t.count(',') > 1:
            return None
        t = t.replace(',', '.')
    elif tem_ponto:
        partes = t.split('.')
        if len(partes) > 2 or len(partes[-1]) == 3:
            # milhar: cada grupo depois do primeiro precisa ter 3 dígitos
            if any(len(p) != 3 for p in partes[1:]):
                return None
            t = ''.join(partes)

    if not _SO_NUMERO.match(t):
        return None
    if '.' in t and len(t.split('.')[1]) > 2:
        return None
    return round(float(t), 2)


def numero_br(valor, casas=2):
    """1234.5 -> '1.234,50' (sem o "R$")."""
    if valor is None:
        return ''
    texto = f'{abs(float(valor)):,.{casas}f}'
    texto = texto.replace(',', 'X').replace('.', ',').replace('X', '.')
    return ('-' if float(valor) < 0 else '') + texto


def moeda(valor):
    """1234.5 -> 'R$ 1.234,50'; negativo -> '-R$ 1.234,50'."""
    if valor is None:
        return '—'
    texto = numero_br(valor)
    if texto.startswith('-'):
        return '-R$ ' + texto[1:]
    return 'R$ ' + texto


def valor_campo(valor):
    """Valor para pré-preencher um campo de edição: 1234.5 -> '1.234,50'."""
    if valor in (None, ''):
        return ''
    if isinstance(valor, str):
        return valor  # já é o que a pessoa digitou (voltando de um erro)
    return numero_br(valor)
