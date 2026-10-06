# Capture only the Power BI Desktop window (works even when it is behind other windows).
param([string]$Out = "$PSScriptRoot\..\.captures\pbi.png", [int]$Width = 1600)
New-Item -ItemType Directory -Force (Split-Path $Out) | Out-Null
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System; using System.Runtime.InteropServices;
public class Cap {
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, uint flags);
}
"@
$h = (Get-Process PBIDesktop | Where-Object MainWindowTitle | Select-Object -First 1).MainWindowHandle
$r = New-Object Cap+RECT; [Cap]::GetWindowRect($h, [ref]$r) | Out-Null
$w = $r.R - $r.L; $ht = $r.B - $r.T
$bmp = New-Object System.Drawing.Bitmap $w, $ht
$g = [System.Drawing.Graphics]::FromImage($bmp); $dc = $g.GetHdc()
[Cap]::PrintWindow($h, $dc, 2) | Out-Null; $g.ReleaseHdc($dc)
$sh = [int]($ht * $Width / $w)
$small = New-Object System.Drawing.Bitmap $Width, $sh
$g2 = [System.Drawing.Graphics]::FromImage($small)
$g2.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
$g2.DrawImage($bmp, 0, 0, $Width, $sh); $small.Save($Out)
"saved $Out ($w x $ht)"
