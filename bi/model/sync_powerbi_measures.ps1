param()

$ErrorActionPreference = 'Stop'

function Set-Measure($Table, [string]$Name, [string]$Expression, [string]$Folder, [string]$FormatString) {
    if ($Table.Measures.ContainsName($Name)) {
        $measure = $Table.Measures[$Name]
    }
    else {
        $measure = New-Object Microsoft.AnalysisServices.Tabular.Measure
        $measure.Name = $Name
        $Table.Measures.Add($measure)
    }
    $measure.Expression = $Expression
    $measure.DisplayFolder = $Folder
    $measure.FormatString = $FormatString
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
$port = ((Get-Content -LiteralPath (Join-Path $workspaceRoot 'Data\msmdsrv.port.txt') -Raw) -replace "`0", '').Trim()

$pbiBin = Split-Path -Parent $pbi.ExecutablePath
[Reflection.Assembly]::LoadFrom((Join-Path $pbiBin 'Microsoft.AnalysisServices.Server.Core.dll')) | Out-Null
[Reflection.Assembly]::LoadFrom((Join-Path $pbiBin 'Microsoft.AnalysisServices.Server.Tabular.dll')) | Out-Null

$server = New-Object Microsoft.AnalysisServices.Tabular.Server
$server.Connect("localhost:$port")
try {
    $model = $server.Databases[0].Model
    $table = $model.Tables['fato_mrp']

    Set-Measure $table 'Estoque Atual' 'SUM(fato_estoque_atual[estoque_atual])' '1. Estoque' '#,0.###'
    Set-Measure $table 'Empenhado Total' 'SUM(fato_estoque_atual[empenhado_total])' '1. Estoque' '#,0.###'
    Set-Measure $table 'Estoque Disponivel' 'SUM(fato_estoque_atual[estoque_disponivel])' '1. Estoque' '#,0.###'
    Set-Measure $table 'Entradas' 'CALCULATE(SUM(fato_movimentacoes_estoque[quantidade]), fato_movimentacoes_estoque[tipo] = "ENTRADA", fato_movimentacoes_estoque[movement_status] = "ATIVA")' '1. Estoque' '#,0.###'
    Set-Measure $table 'Consumo' 'ABS(CALCULATE(SUM(fato_movimentacoes_estoque[quantidade]), fato_movimentacoes_estoque[tipo] = "BAIXA", fato_movimentacoes_estoque[movement_status] = "ATIVA"))' '1. Estoque' '#,0.###'

    Set-Measure $table 'Necessidade Total' 'SUM(fato_mrp[necessidade_total])' '4. MRP' '#,0.###'
    Set-Measure $table 'Em Transito' 'SUM(fato_mrp[quantidade_transito])' '4. MRP' '#,0.###'
    Set-Measure $table 'MRP Estoque Disponivel' 'SUM(fato_mrp[estoque_disponivel])' '4. MRP' '#,0.###'
    Set-Measure $table 'Necessidade de Compra' 'SUM(fato_mrp[necessidade_compra])' '4. MRP' '#,0.###'
    Set-Measure $table 'Necessidade de Compra c/ Minimo' 'SUM(fato_mrp[necessidade_compra_com_estoque_minimo])' '4. MRP' '#,0.###'

    $model.SaveChanges()
    Write-Output "MEASURES_OK count=$($table.Measures.Count) port=$port"
}
finally {
    $server.Disconnect()
}
