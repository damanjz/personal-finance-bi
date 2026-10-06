# Capture the Power BI window at full resolution and crop to the report canvas only.
# The canvas is found from the left rail colour, so no pixel coordinates are hard-coded.
param([Parameter(Mandatory)][string]$Out)
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System; using System.Runtime.InteropServices;
public class Cap2 {
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, uint flags);
}
"@
$h = (Get-Process PBIDesktop | Where-Object MainWindowTitle | Select-Object -First 1).MainWindowHandle
$r = New-Object Cap2+RECT; [Cap2]::GetWindowRect($h, [ref]$r) | Out-Null
$bmp = New-Object System.Drawing.Bitmap ($r.R - $r.L), ($r.B - $r.T)
$g = [System.Drawing.Graphics]::FromImage($bmp); $dc = $g.GetHdc()
[Cap2]::PrintWindow($h, $dc, 2) | Out-Null; $g.ReleaseHdc($dc)

function IsRail($c) { [Math]::Abs($c.R - 0xf2) -le 3 -and [Math]::Abs($c.G - 0xef) -le 3 -and [Math]::Abs($c.B - 0xe7) -le 3 }
$midY = [int]($bmp.Height / 2)
$left = -1
for ($x = 0; $x -lt $bmp.Width / 2; $x++) { if (IsRail $bmp.GetPixel($x, $midY)) { $left = $x; break } }
if ($left -lt 0) { "rail not found"; exit 1 }
$probe = $left + 3
$top = $midY; while ($top -gt 0 -and (IsRail $bmp.GetPixel($probe, $top - 1))) { $top-- }
$bottom = $midY; while ($bottom -lt $bmp.Height - 1 -and (IsRail $bmp.GetPixel($probe, $bottom + 1))) { $bottom++ }
$height = $bottom - $top + 1
$width = [int]($height * 1280 / 720)
$crop = $bmp.Clone((New-Object System.Drawing.Rectangle $left, $top, $width, $height), $bmp.PixelFormat)
$crop.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
"saved $Out ($width x $height)"
