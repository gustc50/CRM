# ERP Financeiro (Contas a Pagar/Receber)

Aplicação Flask simples para controle de contas a pagar e a receber, com
seis abas:

- **Lançamentos**: cadastro de contas a pagar/receber, com busca/filtro,
  editar, excluir, dar baixa e reabrir. Contas vencidas e ainda pendentes
  aparecem destacadas com um selo "Atrasado".
- **Relatório por Período**: filtro por data inicial/final, status,
  categoria e conta, mostrando total de entradas, saídas e o saldo do
  período, com atalhos de fluxo de caixa projetado (7/15/30 dias) e
  exportação em CSV ou Excel (XLSX).
- **Recorrentes**: lançamentos que se repetem todo mês (aluguel, salários),
  gerados automaticamente.
- **Fornecedores** e **Clientes**: cadastro (CNPJ/CPF, nome, endereço,
  telefone, e-mail) com editar, excluir e exportação em CSV/XLSX. Cada
  lançamento de "Conta a Pagar" é vinculado a um fornecedor, e cada
  "Conta a Receber" a um cliente.
- **Categorias** e **Contas** (bancárias/caixa): cadastros simples usados
  para classificar os lançamentos.
- **NFS-e** e **NF-e**: notas fiscais baixadas automaticamente pelo
  certificado digital A1, divididas em "A Receber" e "A Pagar", com
  geração de lançamento em um clique.
- **⚙ Configurações**: certificado digital, ambientes e controle de
  sincronização.

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
- **Regra de 1 hora da SEFAZ**: depois de uma consulta sem novidade
  (cStat 137), novas consultas ficam bloqueadas por ~1h para não gerar o
  bloqueio por consumo indevido (656). A tela mostra quanto falta e
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

## Limitações conhecidas (fora do escopo desta revisão)

- Não há autenticação/login — qualquer pessoa com acesso à máquina/rede onde
  o programa roda pode ver e editar os lançamentos. Adequado para uso local
  de um único usuário; não exponha essa porta na internet.
- Banco de dados local (SQLite), sem sincronização entre computadores.
- A sincronização de notas é disparada por botão (ou ao abrir a aba). Para
  uso intenso, o ideal seria uma rotina agendada de 1x por hora.
- Não é enviado o evento de **manifestação do destinatário**: antes de se
  manifestar, a SEFAZ libera apenas o *resumo* da NF-e de compra, não o XML
  completo — por isso algumas notas aparecem como "Resumo".
- Certificados A1 valem 1 ano: quando renovar, envie o novo arquivo na aba
  Configurações.
