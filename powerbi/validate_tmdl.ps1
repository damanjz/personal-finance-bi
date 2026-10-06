# Parse the TMDL model with Power BI Desktop's own serializer, without opening Desktop.
param([string]$Folder = "$PSScriptRoot\FinanceBI.SemanticModel\definition")
$bin = "C:\Program Files\Microsoft Power BI Desktop\bin"
[AppDomain]::CurrentDomain.add_AssemblyResolve({
    param($s, $e)
    $f = Join-Path "C:\Program Files\Microsoft Power BI Desktop\bin" (($e.Name -split ',')[0] + ".dll")
    if (Test-Path $f) { [Reflection.Assembly]::LoadFrom($f) }
})
$asm = [Reflection.Assembly]::LoadFrom("$bin\Microsoft.PowerBI.Tabular.dll")
$ser = $asm.GetType("Microsoft.AnalysisServices.Tabular.TmdlSerializer")
try {
    $db = $ser.GetMethod("DeserializeDatabaseFromFolder", [type[]]@([string])).Invoke($null, @($Folder))
    $m = $db.Model
    "TMDL OK: $($m.Tables.Count) tables, $(($m.Tables | % { $_.Measures.Count } | Measure -Sum).Sum) measures, $($m.Relationships.Count) relationships"
} catch {
    $e = $_.Exception; while ($e.InnerException) { $e = $e.InnerException }
    "TMDL FAIL: $($e.Message)"; exit 1
}
