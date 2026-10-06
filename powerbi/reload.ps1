# Close Power BI, regenerate the project, reopen it, load the data and capture the window.
param([switch]$NoBuild, [string]$Page = "where")
$root = Split-Path $PSScriptRoot -Parent
Get-Process PBIDesktop -ErrorAction SilentlyContinue | Stop-Process -Force
if (-not $NoBuild) {
    & "$root\.venv\Scripts\python.exe" "$root\powerbi\build_pbip.py"
    & "$PSScriptRoot\validate_tmdl.ps1"; if ($LASTEXITCODE) { exit 1 }
}
$pj = "$root\powerbi\FinanceBI.Report\definition\pages\pages.json"
$meta = Get-Content $pj -Raw | ConvertFrom-Json; $meta.activePageName = $Page
[IO.File]::WriteAllText($pj, ($meta | ConvertTo-Json -Depth 5))
$started = Get-Date
Start-Process "C:\Program Files\Microsoft Power BI Desktop\bin\PBIDesktop.exe" -ArgumentList "`"$root\powerbi\FinanceBI.pbip`""
$deadline = $started.AddMinutes(4)
do {
    Start-Sleep -Seconds 3
    $title = (Get-Process PBIDesktop -ErrorAction SilentlyContinue | Where-Object MainWindowTitle | Select-Object -First 1).MainWindowTitle
} until ($title -like "FinanceBI*" -or (Get-Date) -gt $deadline)
if ($title -notlike "FinanceBI*") { "Power BI did not open the project (title: $title)"; exit 1 }
$ws = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\Power BI Desktop\AnalysisServicesWorkspaces" -Recurse -Filter msmdsrv.port.txt |
    Where-Object LastWriteTime -gt $started | Sort-Object LastWriteTime -Descending | Select-Object -First 1
Add-Type -Path "C:\Program Files\Microsoft Power BI Desktop\bin\Microsoft.PowerBI.AdomdClient.dll"
$c = New-Object Microsoft.AnalysisServices.AdomdClient.AdomdConnection ("Data Source=localhost:" + (Get-Content $ws.FullName -Encoding Unicode).Trim())
$c.Open(); $cmd = $c.CreateCommand()
$cmd.CommandText = '{"refresh":{"type":"full","objects":[{"database":"' + $c.Database + '"}]}}'
$cmd.ExecuteNonQuery() | Out-Null; $c.Close()
"opened and refreshed"
Start-Sleep -Seconds 8
& "$PSScriptRoot\capture_window.ps1" -Out "$root\.captures\pbi-$Page.png"
