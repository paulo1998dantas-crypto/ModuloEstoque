# ModuloEstoque

Sistema local/offline de controle de estoque para almoxarifado industrial da J.I Montadora.

O projeto principal fica em [`estoque_app`](./estoque_app).

## Executar em desenvolvimento

```powershell
cd estoque_app
run_windows.bat
```

## Gerar release Windows

```powershell
cd estoque_app
build_exe.bat
```

O pacote distribuivel e gerado em:

```text
estoque_app/dist/EstoqueJIMontadora.zip
```

## Funcionalidades

- Login com perfil ADM e OPERADOR
- Cadastro e importacao Excel de SKUs
- Controle de entrada, saida e inventario com historico auditavel
- Impressao Zebra via ZPL
- Etiquetas em massa para inventario
- Relatorios Excel
- Backup local SQLite
