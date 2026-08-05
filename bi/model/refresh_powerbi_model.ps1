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
$portFile = Join-Path $workspaceRoot 'Data\msmdsrv.port.txt'
$port = ((Get-Content -LiteralPath $portFile -Raw) -replace "`0", '').Trim()
if (-not $port) { throw 'Porta local do modelo nao encontrada.' }

$pbiBin = Split-Path -Parent $pbi.ExecutablePath
[Reflection.Assembly]::LoadFrom((Join-Path $pbiBin 'Microsoft.AnalysisServices.Server.Core.dll')) | Out-Null
[Reflection.Assembly]::LoadFrom((Join-Path $pbiBin 'Microsoft.AnalysisServices.Server.Tabular.dll')) | Out-Null

$server = New-Object Microsoft.AnalysisServices.Tabular.Server
$server.Connect("localhost:$port")
try {
    $model = $server.Databases[0].Model
    $model.RequestRefresh([Microsoft.AnalysisServices.Tabular.RefreshType]::Full)
    $model.SaveChanges() | Out-Null

    $states = foreach ($table in $model.Tables) {
        [pscustomobject]@{
            Table = $table.Name
            State = $table.Partitions[0].State
        }
    }
    $notReady = @($states | Where-Object State -ne 'Ready')
    if ($notReady.Count -gt 0) {
        $notReady | Format-Table -AutoSize | Out-String | Write-Host
        throw "$($notReady.Count) tabela(s) nao ficaram prontas apos a atualizacao."
    }

    "REFRESH_OK tables=$($states.Count) port=$port"
}
finally {
    $server.Disconnect()
}
