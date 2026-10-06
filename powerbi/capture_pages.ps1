# Open each report page in turn and save a canvas-only screenshot to docs/img.
$root = Split-Path $PSScriptRoot -Parent
$pages = [ordered]@{ where = "01-where"; own = "02-own"; track = "03-track"; stop = "04-stop" }
foreach ($p in $pages.Keys) {
    & "$PSScriptRoot\reload.ps1" -NoBuild -Page $p | Out-Null
    Start-Sleep -Seconds 12
    & "$PSScriptRoot\capture_canvas.ps1" -Out "$root\docs\img\$($pages[$p]).png"
}
& "$PSScriptRoot\reload.ps1" -NoBuild -Page where | Out-Null
"left Power BI open on page 1"
