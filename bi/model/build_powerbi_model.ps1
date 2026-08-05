param(
    [string]$MetadataPath = 'C:\Users\paulo\AppData\Local\Temp\powerbi-bi-metadata.json',
    [string]$PostgresServer = 'db.rodtxswtqbsbtukmvobn.supabase.co:5432',
    [string]$PostgresDatabase = 'postgres',
    [switch]$RefreshData
)

$ErrorActionPreference = 'Stop'

function Get-TomDataType([string]$PostgresType) {
    switch ($PostgresType) {
        { $_ -in @('smallint', 'integer', 'bigint') } { return [Microsoft.AnalysisServices.Tabular.DataType]::Int64 }
        { $_ -in @('numeric', 'decimal', 'money') } { return [Microsoft.AnalysisServices.Tabular.DataType]::Decimal }
        { $_ -in @('real', 'double precision') } { return [Microsoft.AnalysisServices.Tabular.DataType]::Double }
        'boolean' { return [Microsoft.AnalysisServices.Tabular.DataType]::Boolean }
        { $_ -in @('date', 'timestamp without time zone', 'timestamp with time zone') } { return [Microsoft.AnalysisServices.Tabular.DataType]::DateTime }
        default { return [Microsoft.AnalysisServices.Tabular.DataType]::String }
    }
}

function Add-Measure($Table, [string]$Name, [string]$Expression, [string]$Folder, [string]$FormatString = '') {
    if ($Table.Measures.ContainsName($Name)) {
        $Table.Measures.Remove($Name)
    }
    $measure = New-Object Microsoft.AnalysisServices.Tabular.Measure
    $measure.Name = $Name
    $measure.Expression = $Expression
    $measure.DisplayFolder = $Folder
    if ($FormatString) { $measure.FormatString = $FormatString }
    $Table.Measures.Add($measure)
}

$pbi = Get-CimInstance Win32_Process -Filter "Name='PBIDesktop.exe'" |
    Sort-Object CreationDate -Descending |
    Select-Object -First 1
if (-not $pbi) { throw 'Power BI Desktop nao esta aberto.' }

$engine = Get-CimInstance Win32_Process -Filter "Name='msmdsrv.exe'" |
    Where-Object { $_.CommandLine -match 'AnalysisServicesWorkspace_' } |
    Sort-Object CreationDate -Descending |
    Select-Object -First 1
if (-not $engine) { throw 'Instancia local do modelo Power BI nao encontrada.' }

$workspaceName = [regex]::Match($engine.CommandLine, 'AnalysisServicesWorkspace_[0-9a-f-]+').Value
$workspaceRoot = "C:\Users\paulo\Microsoft\Power BI Desktop Store App\AnalysisServicesWorkspaces\$workspaceName"
$portFile = Join-Path $workspaceRoot 'Data\msmdsrv.port.txt'
$port = ((Get-Content -LiteralPath $portFile -Raw) -replace "`0", '').Trim()
if (-not $port) { throw 'Porta local do modelo nao encontrada.' }

$pbiBin = Split-Path -Parent $pbi.ExecutablePath
[Reflection.Assembly]::LoadFrom((Join-Path $pbiBin 'Microsoft.AnalysisServices.Server.Core.dll')) | Out-Null
[Reflection.Assembly]::LoadFrom((Join-Path $pbiBin 'Microsoft.AnalysisServices.Server.Tabular.dll')) | Out-Null

$metadata = Get-Content -LiteralPath $MetadataPath -Raw -Encoding utf8 | ConvertFrom-Json
$viewNames = @($metadata | Select-Object -ExpandProperty table_name -Unique)

$server = New-Object Microsoft.AnalysisServices.Tabular.Server
$server.Connect("localhost:$port")
try {
    $database = $server.Databases[0]
    $model = $database.Model
    $model.DiscourageImplicitMeasures = $true

    foreach ($existingRelationship in @($model.Relationships)) {
        $model.Relationships.Remove($existingRelationship)
    }

    foreach ($viewName in $viewNames) {
        if ($model.Tables.ContainsName($viewName)) {
            $model.Tables.Remove($viewName)
        }

        $table = New-Object Microsoft.AnalysisServices.Tabular.Table
        $table.Name = $viewName
        $table.Description = "View consultiva bi.$viewName no Supabase."
        $model.Tables.Add($table)

        $columns = @($metadata | Where-Object table_name -eq $viewName | Sort-Object ordinal_position)
        foreach ($source in $columns) {
            $column = New-Object Microsoft.AnalysisServices.Tabular.DataColumn
            $column.Name = $source.column_name
            $column.SourceColumn = $source.column_name
            $column.DataType = Get-TomDataType $source.data_type
            if ($source.data_type -in @('date', 'timestamp without time zone', 'timestamp with time zone')) {
                $column.FormatString = if ($source.data_type -eq 'date') { 'dd/MM/yyyy' } else { 'dd/MM/yyyy HH:mm' }
            }
            if ($source.column_name -match '(^|_)id$|^numero_|^codigo$|^item$|^ano$|^mes_numero$|^semana$') {
                $column.SummarizeBy = [Microsoft.AnalysisServices.Tabular.AggregateFunction]::None
            }
            $table.Columns.Add($column)
        }

        $selectList = foreach ($source in $columns) {
            $quoted = '"' + ($source.column_name -replace '"', '""') + '"'
            if ($source.data_type -eq 'ARRAY') { "$quoted::text as $quoted" } else { $quoted }
        }
        $mServer = $PostgresServer -replace '"', '""'
        $mDatabase = $PostgresDatabase -replace '"', '""'
        $mViewName = $viewName -replace '"', '""'

        $partition = New-Object Microsoft.AnalysisServices.Tabular.Partition
        $partition.Name = $viewName
        $partition.Mode = [Microsoft.AnalysisServices.Tabular.ModeType]::Import
        $sourceExpression = New-Object Microsoft.AnalysisServices.Tabular.MPartitionSource
        if ($columns.data_type -contains 'ARRAY') {
            $sql = 'select ' + ($selectList -join ', ') + ' from bi."' + ($viewName -replace '"', '""') + '"'
            $mSql = $sql -replace '"', '""'
            $sourceExpression.Expression = @"
let
    Fonte = PostgreSQL.Database("$mServer", "$mDatabase", [CreateNavigationProperties = false]),
    Dados = Value.NativeQuery(Fonte, "$mSql", null, [EnableFolding = true])
in
    Dados
"@
        }
        else {
            $sourceExpression.Expression = @"
let
    Fonte = PostgreSQL.Database("$mServer", "$mDatabase", [CreateNavigationProperties = false]),
    Dados = Fonte{[Schema = "bi", Item = "$mViewName"]}[Data]
in
    Dados
"@
        }
        $partition.Source = $sourceExpression
        $table.Partitions.Add($partition)
    }

    if ($model.Tables.ContainsName('dCalendario')) { $model.Tables.Remove('dCalendario') }
    $calendar = New-Object Microsoft.AnalysisServices.Tabular.Table
    $calendar.Name = 'dCalendario'
    $calendar.Description = 'Calendario operacional para filtros de periodo.'
    $model.Tables.Add($calendar)
    foreach ($definition in @(
        @('Data', [Microsoft.AnalysisServices.Tabular.DataType]::DateTime, 'dd/MM/yyyy'),
        @('Ano', [Microsoft.AnalysisServices.Tabular.DataType]::Int64, '0'),
        @('MesNumero', [Microsoft.AnalysisServices.Tabular.DataType]::Int64, '0'),
        @('Mes', [Microsoft.AnalysisServices.Tabular.DataType]::String, ''),
        @('AnoMes', [Microsoft.AnalysisServices.Tabular.DataType]::String, ''),
        @('Trimestre', [Microsoft.AnalysisServices.Tabular.DataType]::String, ''),
        @('Semana', [Microsoft.AnalysisServices.Tabular.DataType]::Int64, '0')
    )) {
        $column = New-Object Microsoft.AnalysisServices.Tabular.DataColumn
        $column.Name = $definition[0]
        $column.SourceColumn = $definition[0]
        $column.DataType = $definition[1]
        $column.SummarizeBy = [Microsoft.AnalysisServices.Tabular.AggregateFunction]::None
        if ($definition[2]) { $column.FormatString = $definition[2] }
        $calendar.Columns.Add($column)
    }
    $calendarPartition = New-Object Microsoft.AnalysisServices.Tabular.Partition
    $calendarPartition.Name = 'dCalendario'
    $calendarPartition.Mode = [Microsoft.AnalysisServices.Tabular.ModeType]::Import
    $calendarSource = New-Object Microsoft.AnalysisServices.Tabular.MPartitionSource
    $calendarSource.Expression = @'
let
    Inicio = #date(2024, 1, 1),
    Fim = Date.EndOfYear(Date.AddYears(Date.From(DateTime.LocalNow()), 2)),
    Datas = List.Dates(Inicio, Duration.Days(Fim - Inicio) + 1, #duration(1, 0, 0, 0)),
    Tabela = Table.FromList(Datas, Splitter.SplitByNothing(), {"Data"}),
    Tipos = Table.TransformColumnTypes(Tabela, {{"Data", type date}}),
    Ano = Table.AddColumn(Tipos, "Ano", each Date.Year([Data]), Int64.Type),
    MesNumero = Table.AddColumn(Ano, "MesNumero", each Date.Month([Data]), Int64.Type),
    Mes = Table.AddColumn(MesNumero, "Mes", each Date.ToText([Data], "MMM", "pt-BR"), type text),
    AnoMes = Table.AddColumn(Mes, "AnoMes", each Date.ToText([Data], "yyyy-MM"), type text),
    Trimestre = Table.AddColumn(AnoMes, "Trimestre", each "T" & Text.From(Date.QuarterOfYear([Data])), type text),
    Semana = Table.AddColumn(Trimestre, "Semana", each Date.WeekOfYear([Data], Day.Monday), Int64.Type)
in
    Semana
'@
    $calendarPartition.Source = $calendarSource
    $calendar.Partitions.Add($calendarPartition)

    $relationships = @(
        @('dim_sku', 'sku_id', 'fato_estoque_atual', 'sku_id'),
        @('dim_sku', 'sku_id', 'fato_empenhos_abertos', 'sku_id'),
        @('dim_sku', 'sku_id', 'fato_movimentacoes_estoque', 'sku_id'),
        @('dim_sku', 'sku_id', 'fato_inventarios', 'sku_id'),
        @('dim_sku', 'sku_id', 'fato_necessidades_os', 'sku_id'),
        @('dim_sku', 'sku_id', 'fato_compras_transito', 'sku_id'),
        @('dim_sku', 'sku_id', 'fato_recebimentos_inspecao', 'sku_id'),
        @('dim_sku', 'sku_id', 'fato_forecast_necessidades', 'sku_id'),
        @('dim_sku', 'sku_id', 'fato_mrp', 'sku_id'),
        @('dim_ordem_servico', 'work_order_id', 'fato_etapas_producao', 'work_order_id'),
        @('dim_ordem_servico', 'work_order_id', 'fato_necessidades_os', 'work_order_id'),
        @('dim_ordem_servico', 'work_order_id', 'fato_empenhos_abertos', 'work_order_id'),
        @('dim_ordem_servico', 'work_order_id', 'fato_movimentacoes_estoque', 'work_order_id'),
        @('dim_ordem_servico', 'work_order_id', 'fato_compras_transito', 'work_order_id'),
        @('dCalendario', 'Data', 'fato_movimentacoes_estoque', 'data'),
        @('dCalendario', 'Data', 'fato_inventarios', 'data_contagem'),
        @('dCalendario', 'Data', 'fato_compras_transito', 'data_necessidade'),
        @('dCalendario', 'Data', 'fato_recebimentos_inspecao', 'data'),
        @('dCalendario', 'Data', 'fato_forecast', 'data_entrega_prevista'),
        @('dCalendario', 'Data', 'dim_ordem_servico', 'data_entrega_vigente', $false)
    )
    foreach ($definition in $relationships) {
        $dimension = $model.Tables[$definition[0]]
        $fact = $model.Tables[$definition[2]]
        if (-not $dimension -or -not $fact) { continue }
        $relationship = New-Object Microsoft.AnalysisServices.Tabular.SingleColumnRelationship
        $relationship.Name = "rel_$($definition[0])_$($definition[2])_$($definition[3])"
        $relationship.FromColumn = $fact.Columns[$definition[3]]
        $relationship.ToColumn = $dimension.Columns[$definition[1]]
        $relationship.CrossFilteringBehavior = [Microsoft.AnalysisServices.Tabular.CrossFilteringBehavior]::OneDirection
        $relationship.IsActive = if ($definition.Count -gt 4) { [bool]$definition[4] } else { $true }
        $model.Relationships.Add($relationship)
    }

    $measureTable = $model.Tables['fato_mrp']
    Add-Measure $measureTable 'SKUs com Saldo' 'CALCULATE(DISTINCTCOUNT(fato_estoque_atual[sku_id]), fato_estoque_atual[estoque_atual] > 0)' '1. Estoque' '0'
    Add-Measure $measureTable 'SKUs Empenhados' 'CALCULATE(DISTINCTCOUNT(fato_estoque_atual[sku_id]), fato_estoque_atual[empenhado_total] > 0)' '1. Estoque' '0'
    Add-Measure $measureTable 'SKUs com Disponivel' 'CALCULATE(DISTINCTCOUNT(fato_estoque_atual[sku_id]), fato_estoque_atual[estoque_disponivel] > 0)' '1. Estoque' '0'
    Add-Measure $measureTable 'SKUs Zerados' 'CALCULATE(DISTINCTCOUNT(fato_estoque_atual[sku_id]), fato_estoque_atual[status_estoque] = "ZERADO")' '1. Estoque' '0'
    Add-Measure $measureTable 'SKUs Baixos' 'CALCULATE(DISTINCTCOUNT(fato_estoque_atual[sku_id]), fato_estoque_atual[status_estoque] IN {"BAIXO", "SALDO_COMPROMETIDO"})' '1. Estoque' '0'
    Add-Measure $measureTable 'Estoque Atual' 'SUM(fato_estoque_atual[estoque_atual])' '1. Estoque' '#,0.###'
    Add-Measure $measureTable 'Empenhado Total' 'SUM(fato_estoque_atual[empenhado_total])' '1. Estoque' '#,0.###'
    Add-Measure $measureTable 'Estoque Disponivel' 'SUM(fato_estoque_atual[estoque_disponivel])' '1. Estoque' '#,0.###'
    Add-Measure $measureTable 'Entradas' 'CALCULATE(SUM(fato_movimentacoes_estoque[quantidade]), fato_movimentacoes_estoque[tipo] = "ENTRADA", fato_movimentacoes_estoque[movement_status] = "ATIVA")' '1. Estoque' '#,0.###'
    Add-Measure $measureTable 'Consumo' 'ABS(CALCULATE(SUM(fato_movimentacoes_estoque[quantidade]), fato_movimentacoes_estoque[tipo] = "BAIXA", fato_movimentacoes_estoque[movement_status] = "ATIVA"))' '1. Estoque' '#,0.###'

    Add-Measure $measureTable 'O.S. no WIP' 'CALCULATE(DISTINCTCOUNT(dim_ordem_servico[work_order_id]), dim_ordem_servico[em_wip] = TRUE())' '2. PCP' '0'
    Add-Measure $measureTable 'O.S. em Producao' 'CALCULATE(DISTINCTCOUNT(dim_ordem_servico[work_order_id]), dim_ordem_servico[fase_wip] = "PRODUCAO")' '2. PCP' '0'
    Add-Measure $measureTable 'O.S. Atrasadas' 'CALCULATE(DISTINCTCOUNT(dim_ordem_servico[work_order_id]), dim_ordem_servico[entrega_atrasada] = TRUE())' '2. PCP' '0'
    Add-Measure $measureTable 'Avanco Medio %' 'AVERAGEX(FILTER(dim_ordem_servico, dim_ordem_servico[em_wip] = TRUE()), dim_ordem_servico[percentual_avanco]) / 100' '2. PCP' '0.0%'
    Add-Measure $measureTable 'O.S. com Material Pendente' 'CALCULATE(DISTINCTCOUNT(fato_necessidades_os[work_order_id]), fato_necessidades_os[quantidade_pendente] > 0)' '2. PCP' '0'

    Add-Measure $measureTable 'Linhas em Transito' 'CALCULATE(COUNTROWS(fato_compras_transito), fato_compras_transito[em_transito] = TRUE())' '3. Compras' '0'
    Add-Measure $measureTable 'Valor em Transito' 'CALCULATE(SUM(fato_compras_transito[valor_pendente]), fato_compras_transito[em_transito] = TRUE())' '3. Compras' 'R$ #,0.00'
    Add-Measure $measureTable 'Linhas Atrasadas' 'CALCULATE(COUNTROWS(fato_compras_transito), fato_compras_transito[em_transito] = TRUE(), fato_compras_transito[situacao_transito] = "ATRASADA")' '3. Compras' '0'
    Add-Measure $measureTable 'O.C. Abertas' 'CALCULATE(DISTINCTCOUNT(fato_compras_transito[purchase_order_id]), fato_compras_transito[em_transito] = TRUE())' '3. Compras' '0'
    Add-Measure $measureTable 'Recebido Fisico' 'CALCULATE(SUM(fato_recebimentos_inspecao[quantidade_fisica]), fato_recebimentos_inspecao[status_recebimento] = "CONFIRMADO")' '3. Compras' '#,0.###'
    Add-Measure $measureTable 'Recebido Aprovado' 'CALCULATE(SUM(fato_recebimentos_inspecao[quantidade_aprovada]), fato_recebimentos_inspecao[status_recebimento] = "CONFIRMADO")' '3. Compras' '#,0.###'
    Add-Measure $measureTable 'Taxa Aprovacao %' 'DIVIDE([Recebido Aprovado], [Recebido Fisico])' '3. Compras' '0.0%'

    Add-Measure $measureTable 'SKUs com Demanda' 'CALCULATE(DISTINCTCOUNT(fato_mrp[sku_id]), fato_mrp[necessidade_total] > 0)' '4. MRP' '0'
    Add-Measure $measureTable 'SKUs Cobertos' 'CALCULATE(DISTINCTCOUNT(fato_mrp[sku_id]), fato_mrp[status_mrp] = "COBERTO")' '4. MRP' '0'
    Add-Measure $measureTable 'SKUs a Comprar' 'CALCULATE(DISTINCTCOUNT(fato_mrp[sku_id]), fato_mrp[status_mrp] = "COMPRAR")' '4. MRP' '0'
    Add-Measure $measureTable 'Forecasts sem Estrutura' 'CALCULATE(DISTINCTCOUNT(fato_forecast[forecast_id]), fato_forecast[status] = "ATIVO", fato_forecast[possui_estrutura_materiais] = FALSE())' '4. MRP' '0'
    Add-Measure $measureTable 'Necessidade Total' 'SUM(fato_mrp[necessidade_total])' '4. MRP' '#,0.###'
    Add-Measure $measureTable 'Em Transito' 'SUM(fato_mrp[quantidade_transito])' '4. MRP' '#,0.###'
    Add-Measure $measureTable 'MRP Estoque Disponivel' 'SUM(fato_mrp[estoque_disponivel])' '4. MRP' '#,0.###'
    Add-Measure $measureTable 'Necessidade de Compra' 'SUM(fato_mrp[necessidade_compra])' '4. MRP' '#,0.###'
    Add-Measure $measureTable 'Necessidade de Compra c/ Minimo' 'SUM(fato_mrp[necessidade_compra_com_estoque_minimo])' '4. MRP' '#,0.###'
    Add-Measure $measureTable 'Ultima Atualizacao' 'MAX(fato_mrp[atualizado_em])' '4. MRP' 'dd/MM/yyyy HH:mm'

    $model.SaveChanges()
    if ($RefreshData) {
        $model.RequestRefresh([Microsoft.AnalysisServices.Tabular.RefreshType]::Full)
        $model.SaveChanges()
    }
    Write-Output "MODEL_OK tables=$($model.Tables.Count) columns=$(($model.Tables | ForEach-Object Columns | Measure-Object).Count) relationships=$($model.Relationships.Count) measures=$($measureTable.Measures.Count) port=$port"
}
finally {
    $server.Disconnect()
}
