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

    $measureDefinitions = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'powerbi_measures.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($definition in $measureDefinitions) {
        Set-Measure $table $definition.name $definition.expression $definition.folder $definition.format
    }

    foreach ($modelTable in $model.Tables) {
        foreach ($column in $modelTable.Columns) {
            if ($column -is [Microsoft.AnalysisServices.Tabular.DataColumn]) {
                $column.SummarizeBy = [Microsoft.AnalysisServices.Tabular.AggregateFunction]::None
            }
        }
    }

    $model.SaveChanges()
    Write-Output "MEASURES_OK count=$($table.Measures.Count) port=$port"
}
finally {
    $server.Disconnect()
}
