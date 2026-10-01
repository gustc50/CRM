# Testes

Cada arquivo sobe o sistema em memória (`app.test_client()`), semeia dados
conhecidos e confere o resultado. Não precisam de servidor rodando nem de
internet: as integrações externas (Asaas, SMTP) são substituídas por
servidores de mentira em `localhost`.

Rode sempre com o banco limpo — as contas de teste só são criadas quando
não existe nenhum usuário:

```bash
cd erp
rm -f erp.db .chave_sessao && rm -rf backups
python3 testes/test_relatorios.py
```

O que cada um cobre:

| Arquivo | Cobre |
|---|---|
| `test_relatorios.py` | DRE e Vendas: números conferidos na mão, os dois regimes, exportação, isolamento entre empresas e acesso do contador |

> Estes testes viviam fora do repositório e se perderam quando o ambiente de
> desenvolvimento foi recriado. Ficam aqui para sobreviver a isso.
