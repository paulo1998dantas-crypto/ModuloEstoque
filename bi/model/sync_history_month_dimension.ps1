$ErrorActionPreference = 'Stop'

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
$port = ((Get-Content -LiteralPath (Join-Path $workspaceRoot 'Data\msmdsrv.port.txt') -Raw) -replace "`0", '').Trim()
if (-not $port) { throw 'Porta local do modelo nao encontrada.' }

$pbiBin = Split-Path -Parent $pbi.ExecutablePath
[Reflection.Assembly]::LoadFrom((Join-Path $pbiBin 'Microsoft.AnalysisServices.Server.Core.dll')) | Out-Null
[Reflection.Assembly]::LoadFrom((Join-Path $pbiBin 'Microsoft.AnalysisServices.Server.Tabular.dll')) | Out-Null

$server = New-Object Microsoft.AnalysisServices.Tabular.Server
$server.Connect("localhost:$port")
try {
    $model = $server.Databases[0].Model

    foreach ($relationship in @($model.Relationships | Where-Object {
        $_.Name -eq 'rel_dim_mes_historico_dCalendario_AnoMes' -or
        $_.FromColumn.Table.Name -eq 'dim_mes_historico' -or
        $_.ToColumn.Table.Name -eq 'dim_mes_historico'
    })) {
        $model.Relationships.Remove($relationship)
    }
    if ($model.Tables.ContainsName('dim_mes_historico')) {
        $model.Tables.Remove('dim_mes_historico')
    }

    $table = New-Object Microsoft.AnalysisServices.Tabular.Table
    $table.Name = 'dim_mes_historico'
    $table.Description = 'Meses com ao menos uma finalizacao, entrega ou retirada.'
    $model.Tables.Add($table)

    foreach ($definition in @(
        @('data_mes', [Microsoft.AnalysisServices.Tabular.DataType]::DateTime, 'dd/MM/yyyy'),
        @('ano_mes', [Microsoft.AnalysisServices.Tabular.DataType]::String, ''),
        @('ano', [Microsoft.AnalysisServices.Tabular.DataType]::Int64, '0'),
        @('mes_numero', [Microsoft.AnalysisServices.Tabular.DataType]::Int64, '0')
    )) {
        $column = New-Object Microsoft.AnalysisServices.Tabular.DataColumn
        $column.Name = $definition[0]
        $column.SourceColumn = $definition[0]
        $column.DataType = $definition[1]
        $column.SummarizeBy = [Microsoft.AnalysisServices.Tabular.AggregateFunction]::None
        if ($definition[2]) { $column.FormatString = $definition[2] }
        $table.Columns.Add($column)
    }

    $partition = New-Object Microsoft.AnalysisServices.Tabular.Partition
    $partition.Name = 'dim_mes_historico'
    $partition.Mode = [Microsoft.AnalysisServices.Tabular.ModeType]::Import
    $source = New-Object Microsoft.AnalysisServices.Tabular.MPartitionSource
    $source.Expression = @'
let
    Fonte = PostgreSQL.Database("db.rodtxswtqbsbtukmvobn.supabase.co:5432", "postgres", [CreateNavigationProperties = false]),
    Dados = Fonte{[Schema = "bi", Item = "dim_mes_historico"]}[Data]
in
    Dados
'@
    $partition.Source = $source
    $table.Partitions.Add($partition)

    $relationship = New-Object Microsoft.AnalysisServices.Tabular.SingleColumnRelationship
    $relationship.Name = 'rel_dim_mes_historico_dCalendario_AnoMes'
    $relationship.FromColumn = $model.Tables['dCalendario'].Columns['AnoMes']
    $relationship.ToColumn = $table.Columns['ano_mes']
    $relationship.CrossFilteringBehavior = [Microsoft.AnalysisServices.Tabular.CrossFilteringBehavior]::OneDirection
    $relationship.IsActive = $true
    $model.Relationships.Add($relationship)

    $model.RequestRefresh([Microsoft.AnalysisServices.Tabular.RefreshType]::Full)
    $model.SaveChanges() | Out-Null
    "HISTORY_MONTH_DIMENSION_OK rows=$($table.Partitions[0].State) port=$port"
}
finally {
    $server.Disconnect()
}
