<#
    RunBatch.ps1 <folder>  -  upmix every song in a folder (full chain).
    Resumable: songs whose output already exists are skipped.
    Usage:  .\RunBatch.ps1 "D:\testsongs"  [-OutputFormat 7.1.2] [-Preset Immersive] [-SplitVocals on]
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)] [string] $Folder,
    [ValidateSet('5.1', '7.1', '7.1.2')] [string] $OutputFormat = '7.1.2',
    [string] $Preset = 'Immersive',
    [ValidateSet('auto', 'cuda', 'cpu')] [string] $Device = 'cuda',
    [ValidateSet('auto', 'on', 'off')] [string] $SplitVocals = 'on'
)
$ErrorActionPreference = 'Continue'
$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot } elseif ($PSCommandPath) { Split-Path -Parent $PSCommandPath } else { (Get-Location).Path }
$AllInOne = Join-Path $ScriptDir 'SurroundUpmix-AllInOne.ps1'

if (-not $Folder -or -not (Test-Path -LiteralPath $Folder)) { Write-Host "Folder not found: $Folder" -ForegroundColor Red; exit 1 }
$Folder = (Resolve-Path -LiteralPath $Folder).Path
$exts = '.flac', '.wav', '.mp3', '.m4a', '.aac', '.ogg', '.opus', '.wma'
$songs = Get-ChildItem -LiteralPath $Folder -File | Where-Object { $exts -contains $_.Extension.ToLower() } | Sort-Object Name
if (-not $songs) { Write-Host "No audio files in $Folder" -ForegroundColor Red; exit 1 }

$ext = if ($OutputFormat -eq '7.1.2') { 'wav' } else { 'flac' }
$outDir = Join-Path $Folder "Final_$OutputFormat"
Write-Host "Batch: $($songs.Count) songs -> $outDir  ($OutputFormat, $Preset, split=$SplitVocals)" -ForegroundColor Cyan

$i = 0; $done = 0; $skipped = 0; $failed = 0
foreach ($s in $songs) {
    $i++
    $label = [System.IO.Path]::GetFileNameWithoutExtension($s.Name)
    $out = Join-Path $outDir "$label`_$OutputFormat.$ext"
    Write-Host "`n########## [$i/$($songs.Count)] $($s.Name) ##########" -ForegroundColor Magenta
    if (Test-Path -LiteralPath $out) { Write-Host "  already done - skipping" -ForegroundColor DarkGray; $skipped++; continue }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $AllInOne $s.FullName `
        -OutputFormat $OutputFormat -Preset $Preset -Device $Device -SplitVocals $SplitVocals
    if (Test-Path -LiteralPath $out) { $done++ } else { $failed++; Write-Host "  !! no output for $($s.Name)" -ForegroundColor Red }
}
Write-Host "`n=== batch done: $done made, $skipped skipped, $failed failed ===" -ForegroundColor Green
