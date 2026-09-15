# Consulta de NFe — Distribuição DFe (app desktop)

App desktop (Python + [pywebview](https://pywebview.flowrl.com/)) que consulta
automaticamente **todas as notas fiscais eletrônicas emitidas contra o CNPJ do
seu cliente**, usando o certificado digital A1 (.pfx) que fica na máquina dele.

## Como funciona

- Usa o Web Service oficial **`NFeDistribuicaoDFe`** do Ambiente Nacional da
  NF-e (Nota Técnica 2014.002) — não é a API da SERPRO (que exige saber a
  chave de antemão) nem um serviço de terceiro.
- Autenticação é **mTLS**: o certificado do cliente autentica a conexão HTTPS
  direto com a SEFAZ. Nenhuma senha ou certificado é enviado para qualquer
  servidor seu.
- O certificado é lido do arquivo `.pfx`, convertido temporariamente em
  memória/arquivo temporário só durante a chamada, e apagado (com
  sobrescrita) logo depois — nunca fica salvo.
- A busca usa paginação por **NSU** (número sequencial único): cada consulta
  retorna até ~50 documentos nova; o app repete automaticamente até não haver
  mais novidade (`cStat=137`).

## Como rodar

**Windows — sem digitar nada:** dê duplo-clique em `executar.bat`.
Ele instala o Python-dependências automaticamente (precisa do Python 3
instalado — [baixe aqui](https://www.python.org/downloads/) marcando
"Add Python to PATH" na instalação) e abre o app.

**Mac/Linux:** rode `./executar.sh` no terminal.

**Manual (qualquer sistema):**
```bash
cd nfe-app
pip install -r requirements.txt
python main.py
```

Isso abre a janela do app. Primeiro cadastre uma empresa (nome, CNPJ,
caminho do `.pfx`), valide o certificado com a senha, e depois clique em
"Buscar novas notas". A senha só é pedida de novo se ainda não tiver sido
digitada nesta sessão do app (ela fica só em memória, nunca em disco, e
some quando o app é fechado). A lista de notas pode ser filtrada por data
de emissão (padrão: últimos 5 anos) e separada em abas
Todas / Recebidas / Emitidas; cada nota mostra a UF onde foi emitida
(derivada da chave de acesso).

**A busca é nacional**: o webservice usado é o do Ambiente Nacional, que
centraliza os documentos de todas as SEFAZ estaduais — uma única consulta
já cobre notas emitidas contra o CNPJ em qualquer UF do Brasil. O campo
"Estado da empresa" no cadastro é só um metadado exigido pelo layout da
mensagem (identifica o autor da consulta), não um filtro — ele é um
seletor com os nomes dos estados e é preenchido automaticamente com o
estado encontrado no próprio certificado digital ao validar.

### Gerar um .exe de verdade (sem precisar de Python instalado no PC do cliente)

Rode `gerar_exe.bat` numa máquina **Windows** (não funciona em Mac/Linux
gerando `.exe` — cada SO só empacota executável pra si mesmo). Isso cria
`dist\ConsultaNFe.exe`, um arquivo único que você distribui pros clientes
sem eles precisarem instalar Python nem nada além do próprio `.exe`.

## ⚠️ Coisas que você PRECISA saber antes de usar em produção

1. **Regra de 1 hora**: se a SEFAZ não tiver nada novo (`cStat=137`), você
   não pode consultar de novo antes de 1h — senão o CNPJ é bloqueado
   temporariamente (`656 - Consumo Indevido`). O app já limita a 10 lotes por
   clique, mas você (ou seus clientes) não devem ficar clicando repetidamente.
   Para uso real, isso deveria virar uma rotina agendada (ex: 1x por hora),
   não um botão clicado manualmente o tempo todo.

2. **Manifestação do destinatário**: antes do cliente "se manifestar" sobre
   uma nota (Ciência da Operação / Confirmação / Operação não Realizada), a
   SEFAZ só libera o **resumo** (`resNFe`) da nota, não o XML completo
   (`nfeProc`). Este app ainda não implementa o envio do evento de
   manifestação — é o próximo passo natural (outro webservice: `RecepcaoEvento`).

3. **Certificado A1 vencendo**: certificados A1 duram só 1 ano. Você vai
   precisar de um fluxo para o cliente atualizar o `.pfx` cadastrado quando
   renovar.

4. **Homologação vs Produção**: teste primeiro no ambiente de homologação
   (`hom1.nfe.fazenda.gov.br`) antes de apontar para produção — a SEFAZ é
   rígida com uso indevido do ambiente real.

5. **Consumer Key/Secret da SERPRO NÃO se aplica aqui** — esse serviço é
   direto com a SEFAZ (gratuito, sem contrato comercial), diferente da API
   paga da SERPRO que vocês discutiram antes.

## Estrutura do projeto

```
nfe-app/
├── main.py            # janela do app + ponte Python↔JS
├── db.py               # SQLite local (empresas, notas, log)
├── certificado.py       # leitura segura do .pfx, sem persistir segredo
├── sefaz_client.py      # cliente SOAP do NFeDistribuicaoDFe
├── gui/
│   ├── index.html
│   ├── style.css
│   └── app.js
├── tests/               # suíte de regressão (não vai no zip do cliente)
└── data/app.db          # criado automaticamente na 1ª execução
```

## Testes

```
cd tests
python -m unittest discover -s . -t .
```

Só biblioteca padrão — não precisa instalar nada além do que já está no
`requirements.txt`, e nem do certificado real (o `certificado.py` é
substituído por um dublê). O banco usado é temporário, então rodar os
testes nunca toca no `data/app.db` com as notas dos clientes.

A suíte cobre principalmente as regras que já causaram bloqueio de 1 hora
do CNPJ em produção (cStat 656): o cursor NSU só andar pra frente, o
re-sincronizar não disparar consulta forçada, e o `tpAmb` nunca divergir do
endpoint. Ao mexer nessas partes, rode os testes antes de gerar um zip novo.

## Sobre o .exe gerado

O `ConsultaNFe.exe` produzido pelo `gerar_exe.bat` já embute o Python e
todas as dependências — o cliente só precisa desse único arquivo, dar
duplo-clique, e ter o próprio certificado `.pfx` em algum lugar do
computador dele para escolher no cadastro.
