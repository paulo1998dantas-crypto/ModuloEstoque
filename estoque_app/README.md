# Controle de Estoque Offline para Almoxarifado Industrial

Sistema local em Python/Flask com SQLite para cadastro de SKUs, etiquetas Zebra em ZPL, entrada, empenho, baixa, inventario, relatórios Excel, backup e auditoria de movimentacoes.

## Requisitos

- Windows
- Python 3.11 ou superior
- Impressora Zebra instalada no Windows quando for imprimir de verdade

## Uso online com Render + Supabase

O sistema tambem roda online para inventario via celular. Quando a variavel `DATABASE_URL` existe, o banco usado passa a ser Supabase/Postgres. Quando ela nao existe, o app continua usando SQLite local.

Se existir uma `DATABASE_URL` antiga ou invalida no `.env`, o app local pode ser forcado a usar SQLite com:

```text
ESTOQUE_DATABASE_MODE=local
```

Para voltar ao Supabase/Postgres, use `ESTOQUE_DATABASE_MODE=online` junto com uma `DATABASE_URL` valida.

Variaveis usadas no Render:

```text
DATABASE_URL
ESTOQUE_DATABASE_MODE
ESTOQUE_SECRET_KEY
ESTOQUE_ADMIN_USER
ESTOQUE_ADMIN_PASSWORD
```

Comando de build:

```text
pip install -r estoque_app/requirements.txt
```

Comando de start:

```text
cd estoque_app && gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120
```

Depois do deploy, use `/inventario` no celular para ajustar saldos por inventario.

## Uso local conectado ao Supabase

Tambem e possivel abrir o app local no Windows e usar a mesma base do Supabase. Esse e o modo recomendado para o computador conectado a Zebra: o estoque, os lancamentos e o inventario ficam online, mas a impressao sai pela USB/local.

Fluxo recomendado:

1. Gere ou extraia o pacote `dist/EstoqueJIMontadora.zip`.
2. Na pasta `EstoqueJIMontadora`, copie `env_online_exemplo.txt` para um novo arquivo chamado `.env`.
3. No `.env`, preencha `DATABASE_URL` com a mesma string usada no Render.
4. Preencha `ZEBRA_PRINTER_NAME=ZDesigner GC420t (EPL)` neste computador, pois essa e a fila conectada a Zebra fisica pela USB correta. Em outro computador, use exatamente o nome da fila Zebra exibido no Windows.
5. Abra `EstoqueJIMontadora.exe`.

Quando o `.env` tiver `DATABASE_URL`, o exe nao usa o SQLite local para os dados principais; ele conversa direto com o Supabase. As pastas locais continuam sendo usadas para logs, exports, ZPLs gerados e template da etiqueta.

Se a fila escolhida tiver `(EPL)` no nome, o sistema converte a etiqueta para EPL antes de enviar para a impressora. Em filas ZPL, o envio continua em ZPL.

Para rodar pelo PyCharm ou por `app.py`, coloque o mesmo `.env` dentro da pasta `estoque_app`.

## Instalacao

Abra o PowerShell dentro da pasta `estoque_app`:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
```

## Executar

```powershell
py app.py
```

Tambem existe o atalho `run_windows.bat`, que cria a `.venv`, instala dependencias e inicia o sistema.

## Executavel para distribuir

Para gerar uma versao em `.exe` e `.zip`, execute:

```powershell
build_exe.bat
```

O pacote final fica em:

```text
dist/EstoqueJIMontadora.zip
```

Para usar em outro computador:

1. Extraia o zip.
2. Abra a pasta `EstoqueJIMontadora`.
3. Execute `EstoqueJIMontadora.exe`.

O executavel abre o sistema em uma janela de aplicativo local. Se o Windows nao tiver o componente de WebView disponivel, ele abre no navegador padrao como fallback.

Quando o projeto for atualizado, rode `build_exe.bat` de novo e envie o novo `dist/EstoqueJIMontadora.zip`.

Para atualizar uma instalacao que ja tem dados reais, preserve estas pastas da instalacao antiga:

```text
data
exports
backups
logs
templates_zpl
```

O banco local fica em `data/estoque.db`.

Acesse:

```text
http://127.0.0.1:5000
```

O usuario administrador e criado automaticamente no primeiro deploy.
Troque a senha inicial assim que entrar.

## Estrutura de pastas

- `data/estoque.db`: banco SQLite local
- `templates_zpl/etiqueta_base.zpl`: template Zebra ZPL com placeholders
- `exports/`: relatorios Excel e ZPLs gerados para conferencia
- `backups/`: backups manuais do banco
- `logs/app.log`: log de erros

## Template ZPL Zebra

O sistema usa o arquivo:

```text
templates_zpl/etiqueta_base.zpl
```

O template incluido esta ajustado para etiqueta 100x50 mm em Zebra 203 dpi e foi baseado no export do ZebraDesigner:

```text
Largura: 800 dots
Altura: 400 dots
```

Ele aceita estes placeholders:

```text
{{SKU}}
{{DESCRICAO}}
{{DESCRICAO_58}}
{{DATA}}
```

Para usar uma etiqueta criada no ZebraDesigner:

1. Exporte a etiqueta do ZebraDesigner em texto/ZPL.
2. Substitua os campos variaveis pelos placeholders acima.
3. Salve o conteudo em `templates_zpl/etiqueta_base.zpl`.
4. Para este template, a quantidade e enviada no ZPL com `^PQ{{QTD}}`.
5. Faca um teste pelo botao `Salvar ZPL` antes de imprimir.

A descricao impressa usa `{{DESCRICAO_58}}`, limitada automaticamente a 58 caracteres e com fonte reduzida para descricoes longas.

## Configurar impressora Zebra

1. Instale a Zebra no Windows.
2. Confirme o nome exato da impressora em `Painel de Controle > Dispositivos e Impressoras`.
3. No sistema, acesse `Config` e preencha `Nome da impressora Zebra no Windows`.
4. Se deixar em branco, o sistema tenta usar a impressora padrao do Windows.

A impressao direta usa RAW/ZPL via `pywin32` (`win32print`) e funciona somente no Windows local ou no `.exe`.
No Render/Linux, o servidor nao acessa a Zebra instalada no computador do usuario.

Para imprimir usando o Render em um desktop Windows:

1. Abra o app local ou o `.exe` no computador conectado a Zebra.
2. Deixe esse app local rodando em `http://127.0.0.1:5000`.
3. Acesse o sistema online do Render pelo mesmo desktop.
4. Use o botao `Imprimir neste desktop`.

No celular, a impressao direta fica bloqueada. Use um desktop conectado a Zebra ou baixe o ZPL.

Se o Render estiver em dominio proprio, configure no app local a variavel `ESTOQUE_PRINT_BRIDGE_ORIGINS` com a URL do site online para liberar a ponte local.

## Importacao de SKUs por Excel

Use o menu `SKUs > Importar Excel`.

Colunas obrigatorias:

```text
SKU
DESCRICAO
```

Colunas opcionais aceitas:

```text
SALDO_ATUAL ou ESTOQUE
ESTOQUE_MINIMO
UNIDADE
CATEGORIA
LOCALIZACAO
```

Regras:

- SKU novo e criado.
- SKU existente e atualizado.
- SKUs ausentes da planilha nao sao apagados.
- Entrada, empenho e baixa so aceitam SKU cadastrado e ativo.

O sistema gera os arquivos:

- `template_importacao_skus.xlsx`
- `dados_exemplo.xlsx`
- `template_etiquetas_lote.xlsx`
- `template_baixa_consumo.xlsx`
- `template_empenhos.xlsx`
- `template_bom.xlsx`

Tambem e possivel baixa-los pela interface.

## Fluxo do almoxarife

1. Acesse `Etiqueta`.
2. Leia/digite o SKU e imprima a etiqueta.
3. Acesse `Entrada`.
4. Leia o codigo de barras com o leitor USB.
5. Informe quantidade, documento/nota e confirme.
6. Para reserva de consumo, use `Empenho`.
7. Para carregar empenhos existentes antes do inventario, use `Empenho > Importar empenhos` com `SKU`, `UNIDADE_DE_MEDIDA` e `SALDO_EMPENHADO`.
8. Para itens com estrutura de produto, use `B.O.M` e importe `ITEM_CODIGO`, `COMPONENTE_CODIGO`, `DESCRICAO`, `UNIDADE` e `QUANTIDADE`.
9. Ao dar entrada em um item pai com B.O.M cadastrada, revise o pop-up de backflush para alterar, incluir ou excluir componentes antes de confirmar.
10. Para baixa do consumo real, use `Baixa` e importe a planilha com `SKU`, `UNIDADE_DE_MEDIDA` e `SALDO_CONSUMIDO`.

Os campos de leitura recebem foco automatico e aceitam leitores USB que funcionam como teclado.

## Inventario e etiquetas em massa

A tela ADM `Inventario ADM` permite:

- abrir sessao de inventario
- visualizar status e divergencias
- gerar etiquetas para todos os SKUs ativos
- gerar etiquetas somente para SKUs com saldo maior que zero
- importar fila de etiquetas por Excel (`SKU`, `QUANTIDADE`)
- selecionar varios SKUs e quantidade por SKU
- salvar ZPL consolidado para conferencia
- imprimir fila progressivamente
- reimprimir job individual
- marcar etiqueta como impressa
- contar SKU com leitor de codigo de barras
- exportar previa do inventario
- finalizar inventario gerando ajustes auditaveis

Ao finalizar, o sistema cria movimentacoes do tipo `INVENTARIO` para corrigir os saldos dos SKUs contados. O historico anterior permanece intacto.

## Relatorios Excel

Menu `Relatorios`:

- estoque atual
- entradas
- empenhos
- baixas
- movimentacoes completas
- inventario

Todos incluem data/hora de geracao, usuario e filtros quando aplicavel.

## Backup

ADM pode gerar backup em `Config > Gerar backup`.

O arquivo e salvo em:

```text
backups/estoque_backup_AAAAMMDD_HHMMSS.db
```

## Observacoes operacionais

- O saldo nunca deve ser alterado diretamente no banco.
- Use entrada, empenho, baixa ou inventario para manter auditoria.
- Baixa com saldo negativo vem bloqueada por padrao.
- A liberacao de saldo negativo e configuracao ADM.
- ADM pode excluir uma movimentacao especifica pelo historico; o sistema reverte o efeito dela no saldo atual.
- O sistema nao depende de internet para rodar depois de instalado.
