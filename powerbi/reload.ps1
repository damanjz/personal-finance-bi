# Close the window this script opened last time, regenerate the project, reopen it, load the data and capture the window.
# Other Power BI windows are never touched. The new window stays open; its pid and port go to .captures for the other helpers.
param([switch]$NoBuild, [string]$Page = "where", [switch]$CloseOnly)   # -CloseOnly: just close the window opened last time
$root = Split-Path $PSScriptRoot -Parent
$caps = "$root\.captures"
New-Item -ItemType Directory -Force $caps | Out-Null

function Close-Own($proc) {
    # close only a window this script started (and its engine), then wait until it has really gone
    $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$($proc.Id)")
    # a force-closed window skips Power BI's own cleanup and leaves its workspace folder behind; note ours first
    $workspaces = @($children | Where-Object { $_.Name -eq 'msmdsrv.exe' } | ForEach-Object {
        if ($_.CommandLine -match '-s "([^"]+)\\Data"') { $Matches[1] } })
    $children | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    $proc.WaitForExit(30000) | Out-Null
    Start-Sleep -Seconds 3
    foreach ($w in $workspaces) {
        if ($w -like "$env:LOCALAPPDATA\Microsoft\Power BI Desktop\AnalysisServicesWorkspaces\AnalysisServicesWorkspace_*") {
            Remove-Item -LiteralPath $w -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

# the window this script opened last time, if it is still open
if (Test-Path "$caps\pbi-pid.txt") {
    $old = Get-Process -Id ([int](Get-Content "$caps\pbi-pid.txt")) -ErrorAction SilentlyContinue
    if ($old -and $old.Name -eq "PBIDesktop" -and $old.MainWindowTitle -like "FinanceBI*") { Close-Own $old }
    Remove-Item "$caps\pbi-pid.txt", "$caps\pbi-port.txt" -ErrorAction SilentlyContinue
}
if ($CloseOnly) { "closed our window"; exit 0 }
if (-not $NoBuild) {
    & "$root\.venv\Scripts\python.exe" "$root\powerbi\build_pbip.py"
    & "$PSScriptRoot\validate_tmdl.ps1"; if ($LASTEXITCODE) { exit 1 }
}
$pj = "$root\powerbi\FinanceBI.Report\definition\pages\pages.json"
$meta = Get-Content $pj -Raw | ConvertFrom-Json; $meta.activePageName = $Page
[IO.File]::WriteAllText($pj, ($meta | ConvertTo-Json -Depth 5))
$started = Get-Date
$p = Start-Process "C:\Program Files\Microsoft Power BI Desktop\bin\PBIDesktop.exe" -ArgumentList "`"$root\powerbi\FinanceBI.pbip`"" -PassThru
$deadline = $started.AddMinutes(4)
do { Start-Sleep -Seconds 3; $p.Refresh() } until ($p.MainWindowTitle -like "FinanceBI*" -or $p.HasExited -or (Get-Date) -gt $deadline)
if ($p.HasExited -or $p.MainWindowTitle -notlike "FinanceBI*") {
    if (-not $p.HasExited) { Close-Own $p }
    "Power BI did not open the project (title: $($p.MainWindowTitle)); closed our window"; exit 1
}
Set-Content "$caps\pbi-pid.txt" $p.Id

# this window's own analysis engine and its port
do {
    Start-Sleep -Seconds 2
    $engine = Get-CimInstance Win32_Process -Filter "ParentProcessId=$($p.Id) AND Name='msmdsrv.exe'" | Select-Object -First 1
} until ($engine -or (Get-Date) -gt $deadline)
if (-not $engine -or $engine.CommandLine -notmatch '-s "([^"]+)\\Data"') { "no analysis engine for our window"; exit 1 }
$workspace = $Matches[1]
do {
    Start-Sleep -Seconds 1
    $portFile = Get-ChildItem $workspace -Recurse -Filter msmdsrv.port.txt -ErrorAction SilentlyContinue | Select-Object -First 1
} until ($portFile -or (Get-Date) -gt $deadline)
if (-not $portFile) { "no port file for our window"; exit 1 }
$port = (Get-Content $portFile.FullName -Encoding Unicode).Trim()
Set-Content "$caps\pbi-port.txt" $port
Add-Type -Path "C:\Program Files\Microsoft Power BI Desktop\bin\Microsoft.PowerBI.AdomdClient.dll"
$c = New-Object Microsoft.AnalysisServices.AdomdClient.AdomdConnection ("Data Source=localhost:$port")
$c.Open(); $cmd = $c.CreateCommand()
$cmd.CommandText = '{"refresh":{"type":"full","objects":[{"database":"' + $c.Database + '"}]}}'
$cmd.ExecuteNonQuery() | Out-Null; $c.Close()
"opened and refreshed (port $port, pid $($p.Id))"
Start-Sleep -Seconds 8
& "$PSScriptRoot\capture_window.ps1" -Out "$caps\pbi-$Page.png"
