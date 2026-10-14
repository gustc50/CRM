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
| `test_relatorios.py` | DRE e Faturamento: números conferidos na mão, os dois regimes, exportação, isolamento entre empresas e acesso do contador |
| `test_liberacao.py` | Liberação manual (vencido, em dia, encerrado), bloqueio pelo prazo, Asaas casando CNPJ e CPF, documento ambíguo, pagamento repetido, liberação manual que já cobria o pagamento, ordem dos pagamentos e o "já paguei" sem vínculo |
| `test_vendas.py` | Aba Vendas: NFS-e e NF-e emitidas, canceladas/denegadas/recebidas fora, período, XLSX só com as marcadas, isolamento entre empresas e o contador gerando o relatório |
| `test_marketplace.py` | Mercado Livre, Shopee e Amazon contra um servidor de mentira: autorização, assinatura da Shopee, janela de 15 dias, renovação de token, paginação, os três modos de lançamento, cancelamento, sincronizar sem duplicar, cliente pelo CPF/CNPJ, credenciais criptografadas e o XLSX |

> Estes testes viviam fora do repositório e se perderam quando o ambiente de
> desenvolvimento foi recriado. Ficam aqui para sobreviver a isso.
