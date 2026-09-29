# ERP Financeiro (Contas a Pagar/Receber)

Sistema web (SaaS) de contas a pagar e a receber, em Flask. Roda **hospedado
em um servidor**: cada cliente entra pelo navegador com e-mail e senha, e
enxerga apenas os dados da própria empresa. Há três papéis — administrador,
cliente e contador — descritos na seção
[Papéis de acesso](#papéis-de-acesso-admin-cliente-e-contador).

A navegação fica em um menu lateral, com estas seções:

- **Início**: tela de abertura, com o resultado do mês em destaque (a margem
  de lucro ou prejuízo e a variação contra o mês anterior), o gráfico de
  entradas e saídas dos últimos 6 meses e a comparação do caixa com as contas
  a pagar em aberto.
- **Lançamentos**: cadastro de contas a pagar/receber, com busca/filtro,
  editar, excluir, dar baixa e reabrir. Contas vencidas e ainda pendentes
  aparecem destacadas com um selo "Atrasado".
- **Relatório por Período**: filtro por data inicial/final, status,
  categoria e conta, mostrando total de entradas, saídas e o saldo do
  período, com atalhos de fluxo de caixa projetado (7/15/30 dias) e
  exportação em CSV ou Excel (XLSX).
- **Recorrentes**: lançamentos que se repetem todo mês (aluguel, salários),
  gerados automaticamente.
- **Cadastros** (sub-abas **Fornecedores** e **Clientes**): CNPJ/CPF, nome,
  endereço, telefone, e-mail, com editar, excluir e exportação em CSV/XLSX.
  Cada lançamento de "Conta a Pagar" é vinculado a um fornecedor, e cada
  "Conta a Receber" a um cliente.
- **Categorias**: classificam os lançamentos, cada uma exclusiva de Contas
  a Pagar ou a Receber, com centro de custo opcional. Pode ser definida como
  categoria padrão no cadastro de um Fornecedor ou Cliente.
- **Contas** (bancárias): cadastro com banco, agência e número da conta.
  Permite importar o extrato em **OFX** (gerado pelo internet banking) e
  ver as movimentações importadas em uma sub-aba, filtráveis por período.
- **Notas Fiscais** (sub-abas **NFS-e** e **NF-e**): notas baixadas
  automaticamente pelo certificado digital A1, divididas em "A Receber" e
  "A Pagar", com o lançamento gerado sozinho e manifestação do destinatário
  nas compras.
- **Contabilidade**: envia ao contador, por e-mail, os lançamentos e os XMLs
  das notas de um período escolhido.
- **⚙ Configurações**: certificado digital, ambientes, controle de
  sincronização, envio de e-mail (SMTP) e backup do banco de dados.

## Como colocar no ar (servidor hospedado)

Pré-requisito: um servidor Linux com Python 3.10+.

```bash
git clone <este repositório>
cd erp
./iniciar.sh
```

O `iniciar.sh` cria o ambiente virtual, instala as dependências e sobe o
serviço com **gunicorn** em `127.0.0.1:5000`. Para mudar a porta:
`ERP_PORT=8080 ./iniciar.sh`.

**Um worker, várias threads** (é o que o script faz). O banco é SQLite: mais
de um processo gravando no mesmo arquivo dá erro de *database is locked*, e as
rotinas de fundo (consulta ao Asaas, sincronização fiscal) rodariam
duplicadas. Se um dia o volume exigir vários workers, o passo anterior é
migrar para PostgreSQL.

### HTTPS é obrigatório

O sistema trafega senha, dados financeiros e certificado digital. Deixe o
gunicorn escutando só em `127.0.0.1` e ponha um proxy na frente cuidando do
certificado (exemplo com nginx + Let's Encrypt):

```nginx
server {
    server_name seudominio.com.br;
    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        client_max_body_size 30M;   # upload de certificado e de OFX
    }
}
```

```bash
sudo certbot --nginx -d seudominio.com.br
```

### Subir sozinho depois de reiniciar a máquina (systemd)

`/etc/systemd/system/erp.service`:

```ini
[Unit]
Description=ERP Financeiro
After=network.target

[Service]
User=erp
WorkingDirectory=/opt/erp
ExecStart=/opt/erp/venv/bin/gunicorn --workers 1 --threads 8 --timeout 120 --bind 127.0.0.1:5000 wsgi:app
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now erp
```

### O que precisa entrar no backup

Tudo fica na pasta do projeto e **não** está no Git:

| Arquivo/pasta    | O que guarda                                        |
|------------------|-----------------------------------------------------|
| `erp.db`         | banco inteiro: lançamentos, notas, usuários, empresas |
| `anexos/`        | comprovantes anexados aos lançamentos                |
| `certificados/`  | certificados digitais A1, uma subpasta por empresa   |
| `.chave_secreta` | chave que decifra senha do certificado, SMTP e Asaas |
| `.chave_sessao`  | chave que assina os cookies de login                 |
| `backups/`       | cópias automáticas do `erp.db`                       |

Perder o `.chave_secreta` não perde os lançamentos, mas obriga a cadastrar de
novo a senha do certificado, o SMTP e o token do Asaas. A aba
**⚙ Configurações** tem um backup do banco sob demanda, e o sistema também faz
cópias sozinho em `backups/`.

## Testar na sua máquina

### Windows: `testar_local.bat`

Copie a pasta `erp` para o seu computador e dê duplo clique em
**`testar_local.bat`**. Ele cria o ambiente virtual, instala as dependências,
sobe o servidor e abre o navegador em `http://127.0.0.1:5000`. Na primeira vez
demora 1–2 minutos por causa da instalação; depois é imediato.

A janela preta precisa ficar aberta enquanto você testa — fechá-la encerra o
servidor. Para parar, `Ctrl+C` ou feche a janela.

Para começar do zero (apaga tudo que você lançou no teste), rode pelo Prompt
de Comando:

```
testar_local.bat limpar
```

Ele pede confirmação antes de apagar, e as contas de teste são recriadas na
subida seguinte. Para usar outra porta: `set ERP_PORT=8080` antes de chamar o
script.

O servidor escuta **só em `127.0.0.1`**, ou seja, ninguém na sua rede alcança
o sistema durante o teste. É o servidor embutido do Flask: serve para
experimentar, não para colocar no ar — para isso, veja
[Como colocar no ar](#como-colocar-no-ar-servidor-hospedado).

### Linux e macOS

```bash
pip install -r requirements.txt
python app.py
```

Acesse `http://127.0.0.1:5000`. `ERP_DEBUG=1` liga o recarregamento
automático — **nunca** use isso em servidor no ar.

## Revisão de código feita

Bugs/riscos corrigidos em relação à versão original enviada:

- **Crash com erro 500**: `valor` e `data_vencimento` não eram validados —
  qualquer campo vazio ou mal formatado derrubava a página com erro. Agora
  há validação com mensagens de erro amigáveis (`flash`).
- **`tipo` sem validação**: era possível gravar qualquer string no campo
  `tipo`, quebrando os cálculos do dashboard. Agora só aceita `Receber`/`Pagar`.
- **"Dar Baixa" via link GET**: uma ação que altera dados (marcar como
  concluído) ficava atrás de um simples link `<a href>`, que pode ser
  disparado sem querer (pré-carregamento de navegador, crawlers, etc.).
  Agora é um formulário `POST`.
- **`debug=True` incompatível com executável**: o reloader do Flask tenta
  reiniciar o próprio processo — dentro de um `.exe` gerado pelo PyInstaller
  isso pode travar ou abrir instâncias duplicadas. Agora o modo debug só é
  usado em desenvolvimento; no executável ele roda com `debug=False`.
- **Caminhos quebrados dentro do `.exe`**: o PyInstaller extrai o app para
  uma pasta temporária somente leitura. O código agora resolve `templates/`
  e `static/` a partir dessa pasta, mas mantém o banco `erp.db` gravável ao
  lado do executável.
- **Import não utilizado** em `models.py` (`datetime`).
- Pequenos ajustes de validação no formulário (`min="0.01"` no valor,
  `maxlength="100"` na descrição).

## Melhorias adicionadas depois da revisão inicial

- **Editar e excluir lançamentos**: cada linha da tabela agora tem os botões
  "Editar" (altera tipo, descrição, valor, vencimento e status) e "Excluir"
  (com confirmação, pois não pode ser desfeito). Também foi adicionado
  "Reabrir" para voltar um lançamento concluído para "Pendente".
- **Aba "Relatório por Período"**: filtre os lançamentos por data inicial,
  data final e status (Todos/Pendente/Concluído). Mostra o total de
  entradas, total de saídas e o saldo (entradas − saídas) do período.
- **Exportação CSV/XLSX**: na aba de relatório, os botões "Baixar CSV" e
  "Baixar Excel (XLSX)" exportam exatamente os lançamentos filtrados na
  tela. O CSV usa `;` como separador e vírgula decimal (padrão do Excel
  em português) e inclui BOM para os acentos abrirem corretamente.

## Segunda rodada de melhorias

- **Data de Pagamento**: nova coluna nos lançamentos, preenchida
  automaticamente com a data de hoje ao clicar em "Dar Baixa" (e limpa ao
  clicar em "Reabrir"). Também pode ser ajustada manualmente na tela de
  Editar. Bancos `erp.db` já existentes (de uma versão anterior) são
  migrados automaticamente na primeira vez que o app roda — a coluna é
  adicionada sem apagar nenhum lançamento já cadastrado.
- **Abas Fornecedores e Clientes**: cadastro com CNPJ/CPF (validado por
  quantidade de dígitos: 11 para CPF, 14 para CNPJ), nome e endereço.
  Mesma dinâmica de listar/adicionar/editar/excluir da aba de Lançamentos.

## Terceira rodada: integração de Fornecedor/Cliente ao Lançamento

- No formulário de "Adicionar Lançamento" (e na tela de Editar), ao
  escolher **Conta a Pagar** aparece o campo **Fornecedor**; ao escolher
  **Conta a Receber** aparece o campo **Cliente** — a troca é automática
  conforme o tipo selecionado (`static/lancamento.js`).
- Cada lançamento passa a exigir um fornecedor ou cliente válido (já
  cadastrado). Se ainda não houver nenhum fornecedor/cliente cadastrado,
  a tela de Lançamentos mostra um aviso com link direto para o cadastro.
- A tabela de Lançamentos e o Relatório por Período ganharam a coluna
  "Fornecedor/Cliente", e ela também foi incluída nas exportações CSV/XLSX.
- Ao editar um lançamento e trocar o tipo (de Pagar para Receber, ou
  vice-versa), o vínculo antigo é descartado e o novo campo passa a ser
  obrigatório.
- **Proteção contra exclusão indevida**: não é mais possível excluir um
  fornecedor ou cliente que já esteja vinculado a algum lançamento — a
  tela mostra um erro explicando o motivo. Isso evita órfãos no banco
  (um lançamento apontando para um fornecedor/cliente que não existe mais).
- Bancos `erp.db` de versões anteriores são migrados automaticamente
  (colunas `fornecedor_id`/`cliente_id` são adicionadas sem perda de dados);
  lançamentos antigos, criados antes dessa integração, ficam sem
  fornecedor/cliente vinculado e mostram "—" nas telas.

## Quarta rodada: confirmação da data de pagamento ao dar baixa

- Antes, "Dar Baixa" gravava a data de hoje automaticamente. Agora, ao
  clicar em "Dar Baixa", abre uma janela pedindo para informar a data em
  que o pagamento foi efetivamente realizado (já vem preenchida com a
  data de hoje, mas pode ser alterada antes de confirmar).
- A baixa só é registrada depois que essa data é confirmada; se o campo
  vier vazio ou inválido, o sistema recusa com uma mensagem de erro, sem
  marcar o lançamento como concluído.

## Quinta rodada: Financeiro, Usabilidade e Cadastros

**Financeiro**
- **Categorias** (aba "Categorias"): classifique cada lançamento (Aluguel,
  Salários, Vendas...). Campo opcional no formulário de lançamento; filtra
  tanto a lista de Lançamentos quanto o Relatório por Período.
- **Contas bancárias/caixa** (aba "Contas"): identifique de qual conta
  saiu ou entrou o dinheiro. Também opcional e filtrável.
- **Lançamentos recorrentes** (aba "Recorrentes"): cadastre uma conta que
  se repete todo mês (tipo, fornecedor/cliente, categoria, valor e dia do
  vencimento). O sistema gera o lançamento do mês automaticamente sempre
  que o programa é aberto — e se ficar dias ou meses sem abrir, ele
  "coloca em dia" gerando os meses que faltaram, sem duplicar nada. Também
  dá para forçar a geração na hora pelo botão "Gerar Lançamentos Agora".
- **Fluxo de caixa projetado**: na aba de Relatório, os atalhos "Próximos
  7/15/30 dias" mostram o que está para vencer, com o saldo projetado.

**Usabilidade no dia a dia**
- **Busca e filtro** na aba Lançamentos: por texto (descrição, fornecedor
  ou cliente), tipo, status e categoria — sem afetar os totais do
  dashboard, que continuam somando tudo.
- **Alerta de atraso**: lançamento Pendente com vencimento no passado
  ganha destaque visual (linha rosada) e o selo "Atrasado" ao lado do status.
- **Anexo de comprovante/nota fiscal**: na tela de Editar, anexe um PDF ou
  imagem (até 10 MB) a qualquer lançamento; um ícone 📎 na tabela abre o
  arquivo. Também é possível remover o anexo. Excluir o lançamento apaga
  o arquivo correspondente do disco.

**Cadastros**
- **Telefone e e-mail** nos cadastros de Fornecedor e Cliente (opcionais;
  e-mail é validado em formato básico).
- **Exportar Fornecedores/Clientes** em CSV ou Excel (XLSX), com os mesmos
  botões usados no Relatório.

Bancos `erp.db` de versões anteriores continuam funcionando: todas as
tabelas e colunas novas são criadas/migradas automaticamente na primeira
vez que a nova versão roda, sem perda de nenhum dado já cadastrado.

## Sexta rodada: Notas Fiscais (NFS-e e NF-e) com certificado digital

As funcionalidades dos sistemas "NFS-e Monitor" e "Consulta de NFe" foram
portadas para dentro do ERP, integradas ao fluxo de contas a pagar/receber.

### Configurações (aba ⚙ Configurações)

- **Certificado digital A1** (.pfx/.p12): envie o arquivo pelo navegador
  (ele é copiado para a pasta `certificados`, ao lado do programa) ou
  informe o caminho para deixá-lo onde já está. Ao salvar, o certificado é
  validado e o sistema extrai sozinho **CNPJ/CPF, razão social, validade e
  UF** — inclusive avisando se está vencido ou vence em menos de 30 dias.
- A **senha fica criptografada** (Fernet/AES) no banco; a chave é gerada na
  primeira execução e gravada em `.chave_secreta`, ao lado do programa.
  Proteja essa pasta — quem tem acesso a ela tem acesso ao certificado.
- Escolha do **ambiente** (Produção / Produção Restrita para NFS-e;
  Produção / Homologação para NF-e) e da UF da empresa.
- **Zerar cursor (NSU)**: se alguma nota conhecida não apareceu, zere o
  cursor para re-baixar tudo o que os servidores nacionais ainda guardam.
  As notas já salvas não são duplicadas nem apagadas.

### Aba NFS-e (Notas Fiscais de Serviço)

- Baixa as notas do **Portal Nacional da NFS-e** (Ambiente de Dados
  Nacional) por conexão autenticada com o certificado (mTLS), paginando
  por NSU e continuando de onde parou na vez anterior.
- Divide em **📤 A Receber** (notas em que a empresa é a prestadora) e
  **📥 A Pagar** (notas em que a empresa é a tomadora), com totais de
  quantidade, valor dos serviços e ISS.
- **Cancelamentos e substituições** chegam como eventos e são aplicados
  automaticamente — a nota aparece esmaecida com o selo correspondente e
  não permite gerar lançamento.
- Download do **XML** e do **DANFSe (PDF)** de cada nota.

### Aba NF-e (Notas Fiscais Eletrônicas)

- Consulta o webservice oficial **NFeDistribuicaoDFe** do Ambiente
  Nacional da SEFAZ (Nota Técnica 2014.002) — uma única consulta cobre
  notas de qualquer estado.
- Divide em **📤 A Receber** (notas emitidas pela empresa) e **📥 A Pagar**
  (compras: notas emitidas contra o CNPJ da empresa).
- Quando a nota completa (`nfeProc`) chega depois do resumo (`resNFe`), ela
  **substitui** o resumo — a lista nunca mostra a mesma nota duas vezes.
- **Paginação por NSU**: a SEFAZ varre uma janela de NSUs por consulta e
  devolve no máximo ~50 documentos. O sistema continua consultando enquanto
  o cursor (ultNSU) não alcançar o total disponível (maxNSU), inclusive
  quando a resposta é cStat 137 — nesse caso 137 significa só "nada nesta
  janela", não "acabou".
- **Regra de 1 hora da SEFAZ**: quando o cursor já alcançou o maxNSU (aí sim
  não há mais nada para baixar) ou a SEFAZ devolve 656 (consumo indevido),
  novas consultas ficam bloqueadas por ~1h. A tela mostra quanto falta e
  permite forçar, por conta e risco.

### Integração com as baixas do sistema

- **Toda nota ativa (não cancelada) já vem com o lançamento gerado
  sozinho**, assim que aparece no sistema — ao final de cada
  sincronização, e também ao abrir o programa (cobrindo notas de antes
  desta funcionalidade existir). Não é preciso clicar em nada: a nota
  já nasce com valor, data de vencimento e a contraparte certa.
- O **fornecedor ou cliente é localizado pelo CNPJ/CPF e, se ainda não
  existir, é cadastrado automaticamente** a partir dos dados da nota
  (nome e, para NFS-e, o município como endereço provisório).
- Se algo estiver errado ou incompleto (ex.: uma nota NF-e resumida sem
  o nome do destinatário), **edite o lançamento pelo botão "Editar"**,
  igual a qualquer outro lançamento — a origem fiscal não trava a edição.
- A própria linha da nota mostra **Pendente** com o botão **Dar Baixa**
  (pedindo a data do pagamento, como no resto do sistema) ou **Baixado em
  dd/mm/aaaa** quando já quitado.
- O botão **Gerar Lançamento** continua existindo como reforço manual —
  aparece só nas exceções: nota sem valor no momento da sincronização, ou
  cujo lançamento vinculado foi excluído depois.
- O lançamento aparece normalmente na aba Lançamentos, entra nos totais do
  dashboard, nos relatórios e nas exportações. Excluir o lançamento libera
  a nota para gerar um novo (automaticamente na próxima sincronização, ou
  pelo botão manual).
- Se uma nota for **cancelada depois** de já ter gerado um lançamento
  automático, a linha mostra um aviso "⚠ nota cancelada após gerar o
  lançamento — revise" para você decidir se exclui ou ajusta manualmente
  (o sistema não apaga lançamentos sozinho).

### Privacidade

O certificado e a senha ficam no servidor onde o sistema está hospedado, em
uma pasta separada por empresa, e a senha é guardada criptografada. As
conexões são feitas diretamente com os servidores oficiais do governo (Portal
Nacional da NFS-e e SEFAZ) — nada é enviado para servidores de terceiros.

> Quando o sistema ainda era um programa de mesa, isso tudo ficava só na
> máquina do usuário. Hospedado, quem responde pela guarda do certificado é
> quem opera o servidor.

## Sétima rodada: Categorias com tipo/centro de custo e Contas com OFX

**Categorias**
- Ao cadastrar uma categoria, agora é obrigatório escolher o **tipo**
  (Contas a Pagar ou Contas a Receber) — a categoria só aparece nos
  lançamentos do tipo correspondente. O **centro de custo** é opcional e,
  quando preenchido, aparece entre parênteses ao lado da categoria nas
  telas de Lançamentos e Recorrentes.
- O formulário de "Adicionar Lançamento" (e as telas de Editar) mostram a
  lista de categorias certa automaticamente conforme o tipo escolhido
  (Pagar/Receber), do mesmo jeito que já acontecia com Fornecedor/Cliente.
- **Categoria padrão no Fornecedor/Cliente**: o cadastro de Fornecedor e
  de Cliente ganhou um campo opcional "Categoria padrão". Ao escolher esse
  fornecedor/cliente em um novo lançamento, a categoria correspondente é
  pré-selecionada automaticamente (pode ser trocada antes de salvar). Notas
  fiscais (NFS-e/NF-e) sincronizadas para um fornecedor/cliente com
  categoria padrão já nascem com o lançamento classificado.

**Contas bancárias**
- O cadastro de conta passou a pedir **banco** (lista dos principais bancos
  e fintechs brasileiros, pelo código Febraban), **agência** e **número da
  conta**.
- **Importar OFX**: em cada conta, a sub-aba "Movimentações" tem um botão
  para selecionar um arquivo `.ofx`/`.qfx` exportado do internet banking.
  O sistema lê o arquivo (aceita tanto o formato SGML dos bancos brasileiros
  quanto OFX 2.x/XML) e importa todas as movimentações do extrato, além de
  preencher automaticamente banco/agência/conta (só os campos que ainda
  estiverem em branco) e o saldo mais recente informado no arquivo.
  Movimentações já importadas antes (mesmo identificador único do banco,
  o FITID) não são duplicadas em uma nova importação do mesmo extrato.
- A sub-aba de Movimentações mostra as transações importadas com filtro por
  data inicial/final, totais de créditos, débitos e saldo do período, além
  de exportação em CSV/XLSX e um botão para limpar as movimentações
  importadas (caso precise reimportar do zero).
- Essa é uma visualização do extrato importado — o sistema não faz
  conciliação automática entre as movimentações bancárias e os lançamentos
  já cadastrados.

Bancos `erp.db` de versões anteriores continuam funcionando: as colunas e
tabelas novas são criadas/migradas automaticamente na primeira vez que a
nova versão roda, sem perda de nenhum dado já cadastrado (categorias
antigas viram "Contas a Pagar" por padrão, e contas antigas ficam sem
banco/agência/conta até serem editadas ou até a primeira importação de OFX).

## Oitava rodada: integração entre abas e automações

Revisão de como Categorias, Contas, Fornecedores/Clientes, Recorrentes e
Notas Fiscais se conectam entre si, fechando lacunas encontradas e
automatizando passos que antes exigiam ação manual.

**Conciliação bancária (Contas → Movimentações)**
- Ao importar um extrato OFX, cada movimento sem vínculo já vem com uma
  **sugestão automática** do lançamento pendente correspondente (mesmo
  valor — tolerância de 1 centavo — e vencimento mais próximo da data do
  movimento), pré-selecionada num menu.
- Um clique em "Vincular e Dar Baixa" confirma o vínculo **e já dá baixa no
  lançamento com a data real informada pelo banco**, preenchendo também a
  conta bancária dele se ainda estivesse em branco — antes, "Dar Baixa"
  sempre pedia a data manualmente, mesmo quando o extrato já mostrava
  exatamente quando o dinheiro entrou ou saiu.
- "Desvincular" remove a associação sem mexer no status do lançamento
  (evita reabrir por engano um lançamento que também foi editado depois).
- Um lançamento só pode ficar vinculado a **um** movimento por vez, e o
  tipo é sempre validado (crédito → Receber, débito → Pagar).

**Conta bancária padrão no Fornecedor/Cliente**
- Mesmo padrão já existente para categoria: agora dá para escolher uma
  conta bancária padrão no cadastro de Fornecedor/Cliente. Ela é
  pré-selecionada automaticamente ao escolher esse fornecedor/cliente num
  novo lançamento, e também é herdada pelos lançamentos gerados sozinhos a
  partir de notas fiscais.

**CNPJ/CPF consistente**
- Cadastros feitos pela tela agora gravam o CNPJ/CPF só com dígitos — igual
  já acontecia com os cadastros criados automaticamente por uma nota fiscal
  — e a exibição em tela é sempre formatada (`00.000.000/0000-00` ou
  `000.000.000-00`), independente de como foi digitado. Bancos antigos são
  normalizados automaticamente na migração, sem duplicar nenhum cadastro.

**Rastreabilidade da origem do lançamento**
- Lançamentos gerados automaticamente por uma nota fiscal (NFS-e/NF-e)
  mostram um ícone 🧾 com link direto para baixar o XML, sem precisar
  voltar para a aba de Notas. Os gerados por uma recorrência mostram 🔁
  com link para a recorrência de origem.

**Lançamentos sem categoria**
- O dashboard avisa quando existem lançamentos sem categoria e tem um
  atalho para ver todos; a lista de Lançamentos também aceita filtrar por
  "Sem categoria" (disponível no Relatório também).

**Saldo bancário real no dashboard**
- Novo card "Saldo em Contas" somando o saldo mais recente de todas as
  contas com OFX já importado, com a data da última atualização.

**Pequenos ajustes de paridade**
- A aba Lançamentos ganhou filtro por Conta bancária (já existia no
  Relatório). A lista de Recorrentes passou a mostrar a coluna Conta.

Bancos `erp.db` de versões anteriores continuam funcionando: as colunas
novas são migradas automaticamente, sem perda de dados.

## Nona rodada: correção do buscador de NF-e (não achava notas e travava 1h)

Sintoma relatado: a busca de NF-e não encontrava nada mesmo em uma empresa
com notas, e ainda exibia o aviso de espera de 1 hora.

- **Causa principal**: o sistema tratava o `cStat 137` ("Nenhum documento
  localizado") como "está tudo em dia" e parava a sincronização ali. Só que
  a SEFAZ varre uma janela de NSUs por consulta e responde 137 sempre que
  **naquela janela** não havia documento de interesse — mesmo existindo
  notas mais à frente na fila (`maxNSU` maior que o `ultNSU`). Como o 137
  também dispara a espera de 1 hora, a sincronização parava no primeiro
  trecho vazio, dizia "0 notas" e se bloqueava — repetindo isso a cada hora
  sem nunca chegar nas notas. Agora o que manda é o cursor: enquanto
  `ultNSU < maxNSU` a busca continua, em qualquer um dos dois cStat.
- **Espera de 1 hora corrigida**: só vale para o 656 (consumo indevido) e
  para o 137 **quando o cursor já alcançou o maxNSU**. Com documentos ainda
  na fila, sincronizar de novo é o comportamento esperado pela própria
  SEFAZ e não trava mais.
- **Mensagem de status diagnóstica**: agora informa o progresso (`NSU 350 de
  1200`), o ambiente usado e, quando ainda falta baixar, avisa para clicar
  em sincronizar de novo. Quando a SEFAZ não tem nada para o CNPJ, explica
  que a distribuição só guarda ~90 dias e sugere conferir CNPJ/ambiente.
- **Notas baixadas que ficavam invisíveis**: o filtro de período dos painéis
  de NFS-e/NF-e vinha com o mês atual por padrão, escondendo notas de meses
  anteriores que já tinham sido baixadas. O padrão passou a ser os últimos
  90 dias (a mesma janela que o governo mantém) e, se ainda houver notas
  fora do período filtrado, um aviso mostra quantas são.
- **CPF além de CNPJ**: a consulta agora monta a tag correta (`<CPF>` para
  11 dígitos, `<CNPJ>` para 14) e normaliza o NSU com 15 dígitos, evitando
  rejeição por schema sem explicação clara.

## Décima rodada: backup, manifestação de NF-e e relatórios por centro de custo

**Backup automático do banco**
- Toda vez que o programa abre, é gravada uma cópia do banco em
  `backups/erp-AAAA-MM-DD.db` (uma por dia; as 30 mais recentes são
  mantidas). A cópia é feita **antes** de qualquer alteração de estrutura,
  então o estado anterior continua recuperável se uma atualização der errado.
- Usa a API de backup do próprio SQLite, não uma cópia de arquivo: o arquivo
  sai íntegro mesmo com o programa em uso.
- Em ⚙ Configurações há o botão **Fazer backup agora** e a lista das cópias
  existentes. Continue levando a pasta `backups` junto com o `erp.db` para um
  pendrive ou nuvem — cópia no mesmo computador não protege contra perda da
  máquina.

**Manifestação do destinatário (NF-e)**
- Notas de compra chegam como **Resumo** até a empresa se manifestar: a SEFAZ
  só libera o XML completo depois disso. O botão **Dar Ciência** no painel
  "A Pagar" registra o evento de Ciência da Operação (tpEvento 210210) e, na
  sincronização seguinte, a nota completa é baixada.
- O evento vai assinado digitalmente (XML-DSig com o certificado A1 da
  empresa), como a SEFAZ exige.
- É um evento fiscal gravado no CNPJ e **não pode ser desfeito**, por isso
  acontece só por clique explícito, com confirmação, nunca junto da
  sincronização automática. A nota passa a mostrar "Ciência dada" com o
  protocolo.

**Relatórios por categoria e centro de custo**
- O Relatório por Período ganhou dois quadros de totais: **por categoria** e
  **por centro de custo**, com entradas, saídas e saldo de cada grupo,
  respeitando os filtros aplicados na tela e com exportação CSV/XLSX própria.
  É o que faz o campo centro de custo valer a pena: dá para ver quanto cada
  área custou no período sem somar na mão.

**Correções**
- Nota **denegada** (situação 2) não gera mais lançamento a pagar — antes só
  a cancelada era ignorada, e uma nota denegada não gera obrigação nenhuma.
- **CNPJ/CPF repetido** é recusado no cadastro de fornecedores e clientes,
  com a mensagem apontando em qual cadastro aquele documento já está. Se um
  banco antigo já tiver repetidos (por terem sido digitados com pontuação
  diferente), a listagem marca as linhas como "repetido" para você unificar.
- A busca do fornecedor/cliente pelo CNPJ durante a sincronização passou a
  ser uma consulta direta em vez de varrer a tabela inteira na memória.

## Décima primeira rodada: visual novo e envio para o contador

**Menu lateral**
- As abas saíram do topo e viraram um menu lateral com ícones arredondados,
  que acompanha a rolagem da página. Em telas estreitas (celular) o menu volta
  a ficar em cima, quebrando em linhas.
- Abas agrupadas: **Notas Fiscais** reúne NFS-e e NF-e, e **Cadastros** reúne
  Fornecedores e Clientes. As sub-abas aparecem recuadas embaixo da seção
  assim que você entra nela.

**Nova aba Contabilidade**
- Escolha um período (data inicial e final) e o sistema monta o pacote para o
  contador: os lançamentos em **planilha (.xlsx)** e em **texto (.txt)**, mais
  um **.zip com os XMLs** das notas fiscais do período, organizados em pastas
  (`nfse/emitidas`, `nfse/recebidas`, `nfe/emitidas`, `nfe/recebidas`).
- Antes de enviar, a tela mostra quantos lançamentos e notas entram no pacote,
  quais arquivos serão anexados e o tamanho total — e recusa o envio se passar
  do limite de anexo aceito pelos provedores, sugerindo dividir por mês.
- A mensagem começa com *"Seu cliente [razão social e CNPJ] enviou uma
  mensagem"*, seguida do resumo do período. Dá para incluir uma observação.

**E-mail (SMTP)**
- Em ⚙ Configurações há a seção de e-mail, com os dados já prontos dos
  provedores de plano gratuito (**Gmail**, **Outlook/Hotmail**, **Brevo** e
  **Zoho**) — basta escolher um e informar a conta; ou usar "Outro servidor"
  para preencher servidor/porta/segurança na mão.
- A senha é guardada criptografada, com a mesma chave do certificado digital,
  e há um botão para **enviar um e-mail de teste** antes de usar pra valer.
- Vale saber: não existe SMTP que envie sem nenhuma conta. O programa não traz
  credencial embutida de propósito — seria uma conta compartilhada por todos
  os usuários, que cairia em spam e seria bloqueada rapidamente. Usando a sua
  conta, as mensagens saem do seu endereço, com sua reputação de remetente.
- **Gmail, Outlook e Zoho não aceitam a senha normal da conta**: é preciso
  gerar uma "senha de aplicativo" no painel do provedor. A tela avisa isso
  conforme o provedor escolhido.

## Décima segunda rodada: aba Início com os painéis

A tela que abre com o programa passou a ser o **Início** (os Lançamentos
mudaram de endereço, de `/` para `/lancamentos`). Ela responde três perguntas
sem exigir nenhum clique:

- **"O mês está dando lucro?"** — o resultado do mês aparece em destaque como
  *"15,7% de lucro"* ou *"18,8% de prejuízo"*, com o valor em reais e a
  variação contra o mês anterior. A margem é sobre o faturamento do mês; sem
  faturamento, a tela diz isso em vez de inventar uma porcentagem.
- **"E comparado com os meses anteriores?"** — gráfico de barras com entradas
  e saídas dos últimos 6 meses, com os valores do mês atual rotulados para dar
  a escala, os demais no hover, e um "ver os números em tabela" para quem
  prefere ler os valores exatos.
- **"Tenho dinheiro para pagar o que devo?"** — um medidor mostra quanto do
  saldo em conta já está comprometido com as contas a pagar em aberto, com
  os cards de saldo, total a pagar (destacando o que já venceu) e a sobra ou
  falta projetada.

Os números do resultado seguem a **data de vencimento** (competência), igual
ao Relatório por Período — e não a data de pagamento. Assim um mês em que
ainda falta dar baixa não aparece como prejuízo que não existe. Já o bloco de
caixa usa o saldo real das contas, vindo do último OFX importado.

Sobre as cores do gráfico: entradas e saídas usam **azul e laranja** em vez do
verde e vermelho do resto do sistema. O par verde/vermelho é o caso clássico
de confusão no daltonismo — medido, ele separa ΔE 3,5 para quem tem
deuteranopia (o mínimo seguro é 8), ou seja, as duas barras ficariam
praticamente iguais. Azul e laranja separam ΔE 24,7. No resto do sistema o
verde e o vermelho continuam, porque lá sempre vêm ao lado da palavra
("Receber"/"Pagar") — a cor não é o único sinal.

## Décima terceira rodada: virou SaaS (login, papéis e cobrança)

O sistema deixou de ser um programa de mesa para um usuário e passou a ser um
serviço hospedado, com contas separadas e assinatura mensal.

### Papéis de acesso (admin, cliente e contador)

| Papel        | Enxerga                                        | Pode gravar |
|--------------|------------------------------------------------|-------------|
| **admin**    | empresas, usuários, assinaturas e pagamentos    | sim (gestão do serviço, não dados financeiros) |
| **user**     | só os dados da própria empresa                  | sim         |
| **contador** | os clientes que informaram o e-mail dele        | **não** — somente leitura |

- O **admin** é o dono do serviço. Cadastra empresas e usuários, bloqueia e
  desbloqueia quem quiser, vê quantos clientes estão ativos e pagos, e
  configura o Asaas. Ele não tem lançamentos próprios.
- O **user** é o cliente final. Cada um pertence a uma empresa e tem um
  contador só.
- O **contador** não é cadastrado pelo cliente nem pelo admin: ele ganha
  acesso quando o cliente digita o e-mail dele na aba **Contabilidade**.
  Enquanto estiver lá, o contador abre os dados daquele cliente direto no
  sistema, **sem o cliente precisar gerar nem enviar arquivo nenhum**. Apagar
  o e-mail tira o acesso na hora — a permissão é conferida a cada requisição,
  não no login. Se preferir, o envio por e-mail com XLSX/TXT/XMLs continua
  funcionando do mesmo jeito.

### Isolamento entre empresas

Toda tabela de dados ganhou `empresa_id`, e as consultas passam por três
funções (`da_empresa`, `buscar_ou_404`, `novo_registro`) em vez de irem
direto no `Model.query`. Registro de outra empresa devolve **404**, inclusive
quando o id é digitado na URL na mão.

Isso é requisito de segurança, então tem teste automatizado: o
`test_isolamento.py` semeia dados com marcas distintas em duas empresas,
percorre as 12 telas com cada login exigindo que a marca alheia nunca
apareça, tenta abrir 7 registros do vizinho pelo id e tenta excluir um
lançamento da outra empresa. Foi assim que apareceu um vazamento real durante
o desenvolvimento: a validação de `fornecedor_id` no POST ainda usava
`Model.query` e aceitaria o id de outra empresa.

### Assinatura e cobrança pelo Asaas

- O admin cadastra o **token do Asaas** em *Assinaturas* (sandbox ou
  produção). Ele é guardado criptografado, com a mesma chave do certificado
  digital, e nunca mais aparece na tela.
- Cada empresa é ligada ao cliente correspondente do Asaas pelo
  **ID do cliente** (`cus_...`), em *Clientes*. É esse vínculo que diz de quem
  é cada cobrança paga.
- De 6 em 6 horas o servidor pergunta ao Asaas quais cobranças foram
  recebidas e **credita 30 dias** para quem pagou. Dá para conferir na hora
  pelo botão *Verificar agora*. É consulta periódica, não webhook: não
  depende de domínio nem de porta aberta, em troca o pagamento pode levar
  alguns minutos para refletir.
- **Passou dos 30 dias, bloqueia sozinho.** O usuário continua entrando, mas
  cai em uma tela explicando que a assinatura venceu.
- Cada cobrança credita **uma vez só** (o `asaas_id` é único). Renovação soma
  a partir do vencimento, não da data do pagamento, então quem paga adiantado
  não perde dias.
- Cobrança de cliente que não está vinculado a nenhuma empresa não é
  descartada em silêncio: aparece no resumo como "sem empresa vinculada".

### Autocadastro: o interessado cria a própria conta

A tela de entrada tem **"Cadastre-se agora"** abaixo do login. O formulário
pede, em quatro partes: o tipo de conta (cliente ou contador), os dados de
acesso (nome, e-mail e senha), os dados de cadastro (nome da empresa,
CNPJ/CPF, endereço, CEP, cidade, UF e telefone) e, por último, o pagamento.

**Cliente.** Nasce com a empresa criada e a assinatura zerada. As duas opções
de pagamento fazem coisas diferentes:

- **Pagar agora** — o sistema cria o cliente no Asaas, gera a mensalidade e
  manda a pessoa direto para a página de pagamento, onde ela escolhe entre
  pix, boleto e cartão. O acesso **continua barrado** até o pagamento ser
  confirmado: gerar cobrança não é receber.
- **Pagar depois** — cria só a conta. A pessoa consegue entrar, mas cai na
  tela de bloqueio e **nenhuma função do sistema abre**. De lá mesmo ela pode
  clicar em *Pagar agora* quando quiser.

Como a confirmação vem por consulta periódica, a tela de bloqueio tem
**"Já paguei — conferir agora"**, que consulta só aquela empresa na hora, em
vez de deixar a pessoa esperando a rodada de 6 em 6 horas.

**Contador.** Não paga e não tem empresa: a conta já entra liberada, mas
enquanto nenhum cliente informar o e-mail dele na aba Contabilidade, a lista
de clientes dele fica vazia. O CPF/CNPJ e o endereço dele ficam no próprio
usuário.

O valor da mensalidade é definido pelo admin em *Assinaturas* (padrão de
R$ 99,90). Sem token do Asaas configurado, a seção de pagamento some do
formulário e toda conta nasce como "pagar depois", com o aviso de que o
administrador libera depois de combinar o pagamento.

Três cuidados no cadastro aberto, por ser uma porta que qualquer um
atravessa:

- **O papel vem de uma lista fechada.** Um POST com `papel=admin` é recusado
  e não cria conta nenhuma — aceitar o que veio no formulário deixaria
  qualquer visitante virar administrador.
- **Limite de 5 contas por hora por endereço de origem**, contando só o que
  virou conta: quem erra o formulário algumas vezes não fica travado.
- **Pagar não desfaz bloqueio do administrador.** Quem o admin bloqueou na
  mão não consegue nem gerar cobrança.

### Contas de teste

Quando o banco está vazio, o sistema cria estas contas e mostra as senhas no
terminal:

| Papel    | E-mail                  | Senha                | Situação                    |
|----------|-------------------------|----------------------|-----------------------------|
| admin    | `admin@teste.com.br`    | `admin-teste-123`    | —                           |
| user     | `cliente1@teste.com.br` | `cliente-teste-123`  | assinatura em dia (30 dias) |
| user     | `cliente2@teste.com.br` | `cliente-teste-123`  | **assinatura vencida**, para testar o bloqueio |
| contador | `contador@teste.com.br` | `contador-teste-123` | atende a Padaria (é o e-mail indicado lá) |

> ⚠️ **São senhas de teste, públicas neste README.** Antes de colocar o
> sistema no ar, troque todas — ou apague esses usuários e crie os seus pelo
> painel do admin. Elas só são criadas em banco vazio, então um banco já em
> uso não ganha essas contas de volta.

Para testar o fluxo completo: entre como `cliente2` e veja o bloqueio; entre
como admin, vincule a empresa dele a um cliente do Asaas e clique em
*Verificar agora*; volte como `cliente2` e o acesso estará liberado por 30
dias.

### Outras mudanças desta rodada

- **Ajustes do sistema saíram da tabela das empresas.** Token do Asaas e
  resultado da última verificação foram para uma tabela própria
  (`ConfiguracaoSistema`), porque não pertencem a nenhuma empresa. Antes
  disso, salvar a configuração do Asaas quebrava (`Configuração sem empresa
  definida`) e a verificação automática em segundo plano teria quebrado junto,
  já que ela roda fora de uma requisição.
- **A chave de sessão passou a ser gravada em disco** (`.chave_sessao`). Com
  `os.urandom` a cada boot, todo reinício do servidor deslogava todo mundo.
- **Certificados digitais foram separados por empresa**, em
  `certificados/<id da empresa>/`.
- **E-mail inexistente e senha errada dão a mesma mensagem**, para a tela de
  login não virar uma forma de descobrir quem é cliente.
- **O `.exe` deixou de ser o produto.** `build.bat` saiu; entraram `wsgi.py` e
  `iniciar.sh` (gunicorn), e o servidor não abre mais o navegador sozinho.
- **Banco antigo não sobe por engano**: se o `erp.db` for da versão sem login,
  o servidor recusa a subir com um recado explicando o que fazer, em vez de
  misturar dados sem dono.

### Como foi testado

Três suítes contra banco limpo, todas passando:

- **Papéis e bloqueio** (22 conferências): login, senha errada, redirecionamento
  de quem não está logado, bloqueio por assinatura vencida, painel do admin,
  cliente comum barrado na área do admin, bloquear/desbloquear usuário,
  contador vendo só quem o indicou e impedido de gravar, logout.
- **Isolamento entre empresas**: descrito acima.
- **Fluxo de pagamento**, contra um Asaas de mentira rodando em `localhost`:
  token salvo criptografado, cobrança paga creditando 30 dias, cliente
  destravando sozinho, verificação repetida **não** duplicando o prazo,
  renovação somando a partir do vencimento, cobrança sem dono reportada,
  vencimento automático e token inválido virando recado em português.
- **Autocadastro** (41 conferências), também contra o Asaas de mentira:
  conta criada com senha em hash e CNPJ só com dígitos, "pagar depois"
  deixando o acesso barrado, "pagar agora" gerando cliente e cobrança e
  levando ao link, `papel=admin` recusado, as sete validações do formulário,
  o limite por IP, o "já paguei" confirmando na hora e o bloqueio do admin
  que não se resolve pagando.

### Correções que apareceram nos testes desta rodada

- **A consulta de pagamentos só enxergava uma situação.** O filtro da API ia
  fixo em `status=RECEIVED`, enquanto o código tratava como pagas também as
  cobranças `CONFIRMED` (cartão aprovado e ainda não repassado) e
  `RECEIVED_IN_CASH`. Quem pagasse no cartão ficaria sem acesso. Agora a
  consulta percorre as três situações — o teste anterior não pegou isso
  porque o Asaas de mentira devolvia tudo, ignorando o filtro.
- **A tela de bloqueio não mostrava recado nenhum**: faltava o bloco de
  mensagens, então avisos como "ainda não encontramos o pagamento" sumiam em
  silêncio.
- **O limite por IP contava tentativas recusadas**, o que trancava por uma
  hora quem apenas errasse o formulário cinco vezes.

## Limitações conhecidas (fora do escopo desta revisão)

- Banco de dados SQLite, com um worker só. Aguenta bem dezenas de empresas;
  para muito mais que isso, migre para PostgreSQL antes de aumentar os
  workers.
- Não há recuperação de senha por e-mail — quem redefine é o admin.
- O cadastro não confirma o e-mail nem valida o dígito verificador do
  CNPJ/CPF: confere só o formato e o tamanho.
- O limite de cadastros é por endereço de origem e fica na memória do
  processo, então reiniciar o servidor zera a contagem.
- A cobrança é conferida por consulta periódica (6 em 6 horas). Com webhook o
  crédito seria imediato, mas exigiria domínio e HTTPS publicados.
- A sincronização de notas é disparada por botão (ou ao abrir a aba). Para
  uso intenso, o ideal seria uma rotina agendada de 1x por hora.
- A manifestação do destinatário disponível é a **Ciência da Operação**, que
  é a que libera o XML completo. As outras (Confirmação, Desconhecimento e
  Operação não Realizada) não são enviadas pelo sistema.
- A manifestação é feita **uma nota por vez**, no botão: não há envio em lote
  nem manifestação automática, de propósito — é um evento fiscal definitivo.
- Certificados A1 valem 1 ano: quando renovar, envie o novo arquivo na aba
  Configurações.
