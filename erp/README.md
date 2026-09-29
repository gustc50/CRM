# ERP Financeiro (Contas a Pagar/Receber)

Aplicação Flask simples para controle de contas a pagar e a receber. A
navegação fica em um menu lateral, com estas seções:

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

## Como gerar o executável (.exe) no Windows

Pré-requisito: ter o [Python 3.10+](https://www.python.org/downloads/) instalado
(na instalação, marque a opção **"Add python.exe to PATH"**).

1. Copie a pasta `erp` inteira para o seu computador Windows.
2. Dentro da pasta `erp`, dê duplo clique em **`build.bat`**.
3. O script vai criar um ambiente virtual, instalar as dependências e gerar o
   executável em `dist\ERP-Financeiro.exe`. Isso leva 1–2 minutos na primeira vez.
4. Copie `dist\ERP-Financeiro.exe` para onde quiser (área de trabalho, pasta do
   sistema, etc.) e rode com duplo clique.

Ao rodar o `.exe`:
- Uma janela preta (console) abre mostrando os logs do servidor — **não feche
  essa janela**, ela precisa ficar aberta enquanto o programa está em uso.
  Fechá-la encerra o programa.
- O navegador abre automaticamente em `http://127.0.0.1:5000`.
- Um arquivo `erp.db` (banco de dados SQLite) é criado **ao lado do .exe** na
  primeira execução. Esse arquivo guarda todos os lançamentos — faça backup
  dele periodicamente (ex.: copiar para um pendrive/nuvem) e não delete.
- Uma pasta `anexos` também é criada ao lado do `.exe`, guardando os
  comprovantes/notas fiscais anexados aos lançamentos. Inclua-a no backup
  junto com o `erp.db`.

## Rodar em modo desenvolvimento (sem gerar .exe)

```bash
pip install -r requirements.txt
python app.py
```

Acesse `http://127.0.0.1:5000`.

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

O certificado e a senha ficam **apenas no seu computador**. As conexões são
feitas diretamente com os servidores oficiais do governo (Portal Nacional
da NFS-e e SEFAZ) — nada é enviado para servidores de terceiros.

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

## Limitações conhecidas (fora do escopo desta revisão)

- Não há autenticação/login — qualquer pessoa com acesso à máquina/rede onde
  o programa roda pode ver e editar os lançamentos. Adequado para uso local
  de um único usuário; não exponha essa porta na internet.
- Banco de dados local (SQLite), sem sincronização entre computadores.
- A sincronização de notas é disparada por botão (ou ao abrir a aba). Para
  uso intenso, o ideal seria uma rotina agendada de 1x por hora.
- A manifestação do destinatário disponível é a **Ciência da Operação**, que
  é a que libera o XML completo. As outras (Confirmação, Desconhecimento e
  Operação não Realizada) não são enviadas pelo sistema.
- A manifestação é feita **uma nota por vez**, no botão: não há envio em lote
  nem manifestação automática, de propósito — é um evento fiscal definitivo.
- Certificados A1 valem 1 ano: quando renovar, envie o novo arquivo na aba
  Configurações.
