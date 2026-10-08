# Run a DAX query against the model open in Power BI Desktop and print rows as JSON.
param([Parameter(Mandatory)][string]$Dax)
$bin = "C:\Program Files\Microsoft Power BI Desktop\bin"
Add-Type -Path "$bin\Microsoft.PowerBI.AdomdClient.dll"
$portFile = "$PSScriptRoot\..\.captures\pbi-port.txt"   # written by reload.ps1 for the window it opened
if (-not (Test-Path $portFile)) { "no Power BI window opened by reload.ps1; run it first"; exit 1 }
$port = (Get-Content $portFile).Trim()
$conn = New-Object Microsoft.AnalysisServices.AdomdClient.AdomdConnection "Data Source=localhost:$port"
$conn.Open()
$cmd = $conn.CreateCommand(); $cmd.CommandText = $Dax
$r = $cmd.ExecuteReader()
$rows = @()
while ($r.Read()) {
    $o = [ordered]@{}
    for ($i = 0; $i -lt $r.FieldCount; $i++) { $o[$r.GetName($i)] = $r.GetValue($i) }
    $rows += [pscustomobject]$o
}
$conn.Close()
$rows | ConvertTo-Json -Depth 3 -Compress
