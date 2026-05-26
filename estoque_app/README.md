# Controle de Estoque Offline para Almoxarifado Industrial

Sistema local em Python/Flask com SQLite para cadastro de SKUs, etiquetas Zebra em ZPL, entrada, saida, inventario, relatórios Excel, backup e auditoria de movimentacoes.

## Requisitos

- Windows
- Python 3.11 ou superior
- Impressora Zebra instalada no Windows quando for imprimir de verdade

## Uso online com Render + Supabase

O sistema tambem roda online para inventario via celular. Quando a variavel `DATABASE_URL` existe, o banco usado passa a ser Supabase/Postgres. Quando ela nao existe, o app continua usando SQLite local.

Variaveis usadas no Render:

```text
DATABASE_URL
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

O template incluido esta ajustado para etiqueta 80x40 mm em Zebra 203 dpi e foi baseado no export do ZebraDesigner:

```text
Largura: 640 dots
Altura: 320 dots
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

A descricao impressa usa `{{DESCRICAO_58}}`, limitada automaticamente a 58 caracteres.

## Configurar impressora Zebra

1. Instale a Zebra no Windows.
2. Confirme o nome exato da impressora em `Painel de Controle > Dispositivos e Impressoras`.
3. No sistema, acesse `Config` e preencha `Nome da impressora Zebra no Windows`.
4. Se deixar em branco, o sistema tenta usar a impressora padrao do Windows.

A impressao usa RAW/ZPL via `pywin32` (`win32print`).

## Importacao de SKUs por Excel

Use o menu `SKUs > Importar Excel`.

Colunas obrigatorias:

```text
SKU
DESCRICAO
UNIDADE
CATEGORIA
LOCALIZACAO
ESTOQUE_MINIMO
```

Regras:

- SKU novo e criado.
- SKU existente e atualizado.
- SKUs ausentes da planilha nao sao apagados.
- Entrada/saida so aceita SKU cadastrado e ativo.

O sistema gera os arquivos:

- `template_importacao_skus.xlsx`
- `dados_exemplo.xlsx`
- `template_etiquetas_lote.xlsx`

Tambem e possivel baixa-los pela interface.

## Fluxo do almoxarife

1. Acesse `Etiqueta`.
2. Leia/digite o SKU e imprima a etiqueta.
3. Acesse `Entrada`.
4. Leia o codigo de barras com o leitor USB.
5. Informe quantidade, documento/nota e confirme.
6. Para retirada, use `Saida`.

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
- saidas
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
- Use entrada, saida ou inventario para manter auditoria.
- Saida com saldo negativo vem bloqueada por padrao.
- A liberacao de saldo negativo e configuracao ADM.
- O sistema nao depende de internet para rodar depois de instalado.
