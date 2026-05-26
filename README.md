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
- Inventario online via mobile
- Impressao Zebra via ZPL
- Etiquetas em massa para inventario
- Relatorios Excel
- Backup local SQLite

## Deploy online

O sistema esta preparado para rodar no Render usando Supabase Postgres.

Arquivos importantes:

- `render.yaml`: blueprint do Render
- `Procfile`: comando web alternativo
- `.env.example`: variaveis de ambiente esperadas
- `runtime.txt`: versao Python usada no deploy

Quando `DATABASE_URL` estiver configurado, o app usa Supabase/Postgres. Sem `DATABASE_URL`, continua usando SQLite local.
