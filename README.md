# ModuloEstoque

Sistema de controle de estoque para almoxarifado industrial da J.I Montadora.

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
- Cadastro e importacao Excel de B.O.M
- Controle de entrada, empenho, baixa e inventario com historico auditavel
- Backflush de componentes na entrada de itens com estrutura
- Importacao Excel de empenhos e baixas por consumo real
- Importacao Excel de contagens em massa no inventario
- Importacao Excel para somar saldo em massa no inventario
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

Para forcar o app local a ignorar uma `DATABASE_URL` salva no `.env` e usar SQLite, defina:

```text
ESTOQUE_DATABASE_MODE=local
```

Para usar Supabase/Postgres explicitamente, defina `ESTOQUE_DATABASE_MODE=online` e mantenha uma `DATABASE_URL` valida.

## App local usando Supabase

Para usar um atalho local no Windows conversando com a base online:

1. Gere o release com `cd estoque_app && build_exe.bat`.
2. Extraia `estoque_app/dist/EstoqueJIMontadora.zip`.
3. Copie `env_online_exemplo.txt` para `.env` na mesma pasta do `EstoqueJIMontadora.exe`.
4. Preencha `DATABASE_URL` com a string do Supabase/Render.
5. Preencha `ZEBRA_PRINTER_NAME` com o nome exato da fila Zebra do Windows. Neste computador, use `ZDesigner GC420t (EPL)`.
6. Abra o exe pelo atalho.

Nesse modo, os dados ficam no Supabase e a impressao Zebra continua local no computador conectado por USB.
