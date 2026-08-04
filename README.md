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
- Cadastro e importacao Excel de CODs
- Cadastro e importacao Excel de B.O.M
- Controle de entrada, empenho, baixa e inventario com historico auditavel
- Backflush de componentes na entrada de itens com estrutura
- Importacao Excel de empenhos e baixas por consumo real
- Exportacao dos empenhos pendentes e retorno da mesma planilha para baixa vinculada
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

## Integração ERP e ativação gradual

As integrações novas permanecem desligadas por padrão. Depois de aplicar e validar
as migrations aditivas no ambiente de staging, aplique por último
`202607301320_reconcile_shared_user_roles_cutover.sql` e só então ative
separadamente:

```text
ERP_FEATURE_FLAG=true
ERP_PO_SUGGESTION_ENABLED=true
ERP_MOVEMENT_CONTEXT_ENABLED=true
ERP_SHARED_RBAC_ENABLED=true
```

Quando `ERP_SHARED_RBAC_ENABLED=true`, a ausência de qualquer tabela/coluna do
contrato RBAC bloqueia autorização e faz `/healthz` responder `503`; não há
fallback para `users.role`. Com a flag desligada, o comportamento legado é
preservado. A tela central de usuários já grava `erp_user_roles` sempre que o
schema existe, inclusive durante a janela anterior à ativação da flag.

As migrations de RBAC abortam a transação se houver usuário ativo sem perfil
ativo ou se não existir ao menos um `ADMIN` ativo. Não corrija isso atribuindo
perfil por inferência: trate os casos ambíguos individualmente antes do corte.
Depois que uma migration entrar no histórico do Supabase, rollback de schema é
operacional (flags/deploy anterior); qualquer ajuste estrutural deve ser uma
nova migration aditiva de forward-fix. Os arquivos em `supabase/rollbacks` não
apagam tabelas, auditoria, vínculos nem reabrem acesso direto da Data API.

- A sugestão de O.C. nunca movimenta saldo no navegador: a escolha leva à
  Inspeção de Recebimento, cujo backend confirma recebimento, pedido e movimento
  na mesma transação.
- Uma entrada direta com O.C. pendente exige motivo de exceção.
- Empenhos e baixas novos exigem O.S. ativa, setor ou referência quando
  `ERP_MOVEMENT_CONTEXT_ENABLED=true`.
- Baixas vinculadas herdam o contexto do empenho; a correção posterior exige
  motivo e gera histórico.
- Baixa direta pela coluna/ID do empenho e correção auditada do empenho são
  exclusivas do perfil ADMIN. A baixa manual normal por COD permanece conforme
  as permissões operacionais do usuário.
- Cancelamentos preservam a movimentação original e, quando necessário, geram
  movimento compensatório. Recebimentos devem ser estornados pela própria
  Inspeção de Recebimento.
- `ERP_BACKEND_TOKEN` deve ser igual no Estoque e nos backends autorizados a
  consultar os endpoints internos. Nunca coloque esse token no frontend.
