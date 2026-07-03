# ERP Financeiro (Contas a Pagar/Receber)

Aplicação Flask simples para controle de contas a pagar e a receber.

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

## Limitações conhecidas (fora do escopo desta revisão)

- Não há autenticação/login — qualquer pessoa com acesso à máquina/rede onde
  o programa roda pode ver e editar os lançamentos. Adequado para uso local
  de um único usuário; não exponha essa porta na internet.
- Não há edição/exclusão de lançamentos, apenas inclusão e "dar baixa".
- Banco de dados local (SQLite), sem sincronização entre computadores.
