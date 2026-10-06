# Run a DAX query against the model open in Power BI Desktop and print rows as JSON.
param([Parameter(Mandatory)][string]$Dax)
$bin = "C:\Program Files\Microsoft Power BI Desktop\bin"
Add-Type -Path "$bin\Microsoft.PowerBI.AdomdClient.dll"
$ws = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\Power BI Desktop\AnalysisServicesWorkspaces" -Recurse -Filter msmdsrv.port.txt |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
$port = (Get-Content $ws.FullName -Encoding Unicode).Trim()
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
