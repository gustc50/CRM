# ERP Financeiro (Contas a Pagar/Receber)

Aplicação Flask simples para controle de contas a pagar e a receber, com
quatro abas:

- **Lançamentos**: cadastro de contas a pagar/receber, com editar, excluir,
  dar baixa e reabrir.
- **Relatório por Período**: filtro por data inicial/final (e status),
  mostrando total de entradas, saídas e o saldo do período, com exportação
  em CSV ou Excel (XLSX).
- **Fornecedores** e **Clientes**: cadastro simples (CNPJ/CPF, nome,
  endereço) com editar e excluir. Cada lançamento de "Conta a Pagar" é
  vinculado a um fornecedor, e cada "Conta a Receber" a um cliente.

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

## Limitações conhecidas (fora do escopo desta revisão)

- Não há autenticação/login — qualquer pessoa com acesso à máquina/rede onde
  o programa roda pode ver e editar os lançamentos. Adequado para uso local
  de um único usuário; não exponha essa porta na internet.
- Banco de dados local (SQLite), sem sincronização entre computadores.
