<#
    SurroundUpmix-AllInOne.ps1
    Full chain: a stereo SONG file  ->  Demucs stem separation  ->  surround upmix.
    Drives the Demucs command-line tool, then hands the stems to SurroundUpmix.ps1.

    Requires Demucs on the machine (pip install demucs). It is auto-detected:
      * a 'demucs' command on PATH, or
      * 'py -3.10 -m demucs' / 'python -m demucs'
    Override with -DemucsCmd "py -3.10 -m demucs".
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)] [string] $SongFile,
    [string] $Model = 'htdemucs_ft',
    [ValidateSet('auto', 'cuda', 'cpu')] [string] $Device = 'auto',
    [ValidateSet('5.1', '7.1', '7.1.2')]             [string] $OutputFormat = '5.1',
    [ValidateSet('None', 'Music', 'Movie', 'Anime', 'PLIIx', 'Focus', 'Immersive', 'Envelop', 'Concert', 'WideStage')] [string] $Preset = 'Immersive',
    [ValidateSet('classic', 'full')]                 [string] $SurroundBlend = 'classic',
    [ValidateSet('preset', 'sameside', 'difference', 'blend')] [string] $SurroundSource = 'preset',
    [double] $RearGain = 0,
    [double] $RearBelowFront = 16,
    [ValidateSet('auto', 'spread', 'forward')] [string] $VocalMode = 'auto',   # auto = analyse the vocal for doubling
    [ValidateSet('auto', 'on', 'off')]         [string] $EchoThrow = 'auto',
    [ValidateSet('auto', 'on', 'off')]         [string] $SplitVocals = 'auto', # lead/backing split (Roformer karaoke)
    [string] $BackingGain = 'auto',                  # 'auto' = balance vs the lead per song; or a dB number
    [double] $BackingBelowLead = 8,                  # auto target: keep backing ~this many dB under the lead
    [ValidateSet('rear', 'halo', 'choir', 'blend')] [string] $BackingMode = 'blend',
    [string] $UpmixBass = '4.0', [string] $UpmixDrums = '4.0',
    [string] $UpmixVocals = '5.0', [string] $UpmixOther = '5.0',
    [string] $UpmixGuitar = '4.0', [string] $UpmixPiano = '4.0',
    [double] $NormLevel = -0.1,
    [int]    $LfeCrossover = 120,
    [int]    $OutputRate = 0,
    [string] $OutputPath = '',
    [string] $WorkPath = '',
    [string] $TrackLabel = '',
    [switch] $LoudnessMatch,
    [switch] $KeepStems,
    [switch] $DebugMode,
    [string] $DemucsCmd = ''
)

$ErrorActionPreference = 'Stop'
[System.Threading.Thread]::CurrentThread.CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture

$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot }
             elseif ($PSCommandPath) { Split-Path -Parent $PSCommandPath }
             elseif ($MyInvocation.MyCommand.Path) { Split-Path -Parent $MyInvocation.MyCommand.Path }
             else { (Get-Location).Path }
$Engine   = Join-Path $ScriptDir 'SurroundUpmix.ps1'
$Sox      = Join-Path $ScriptDir 'bin\SoX\sox.exe'
$Detector = Join-Path $ScriptDir 'detect_vocal.py'
$SplitPy      = Join-Path $ScriptDir 'bin\splitter_venv\Scripts\python.exe'
$SplitScript  = Join-Path $ScriptDir 'split_vocals.py'
$SplitModels  = Join-Path $ScriptDir 'bin\splitter_models'

function Fail($m) { Write-Host "ERROR: $m" -ForegroundColor Red; exit 1 }
function Info($m) { Write-Host $m -ForegroundColor Cyan }
function Step($m) { Write-Host "`n==== $m ====" -ForegroundColor Green }

# RMS amplitude of a wav/flac via SoX (0 if unavailable)
function Get-Rms($wav) {
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    $s = & $Sox $wav -n stat 2>&1
    $ErrorActionPreference = $prev
    $m = $s | Select-String 'RMS.*amplitude'
    if ($m) { return [double]($m[0].ToString().Split(':')[1].Trim()) }
    return 0.0
}

# a python interpreter that has torch (for the vocal analysis); $null if none
function Resolve-Python {
    foreach ($c in @(, @('py', '-3.10')) + @(, @('py')) + @(, @('python'))) {
        try {
            $rest = if ($c.Count -gt 1) { $c[1..($c.Count - 1)] } else { @() }
            & $c[0] @rest -c "import torch" *> $null
            if ($LASTEXITCODE -eq 0) { return $c }
        } catch {}
    }
    return $null
}

if (-not $SongFile)            { Fail "No song file given." }
if (-not (Test-Path -LiteralPath $SongFile)) { Fail "Song file not found: $SongFile" }
if (-not (Test-Path -LiteralPath $Engine))  { Fail "SurroundUpmix.ps1 not found next to this script." }
$SongFile = (Resolve-Path -LiteralPath $SongFile).Path

# ---------------------------------------------------------------- locate demucs
function Test-Cmd($parts) {
    try {
        $rest = @()
        if ($parts.Count -gt 1) { $rest = $parts[1..($parts.Count - 1)] }
        & $parts[0] @rest '--help' *> $null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}
function Resolve-Demucs {
    if ($DemucsCmd) { return ($DemucsCmd -split '\s+') }
    $g = Get-Command demucs -ErrorAction SilentlyContinue
    if ($g) { return @($g.Source) }
    $candidates = New-Object System.Collections.ArrayList
    [void]$candidates.Add(@('py', '-3.10', '-m', 'demucs'))
    [void]$candidates.Add(@('py', '-m', 'demucs'))
    [void]$candidates.Add(@('python', '-m', 'demucs'))
    foreach ($c in $candidates) {
        if (Test-Cmd $c) { return $c }
    }
    return $null
}

Step "Locating Demucs"
$demucs = Resolve-Demucs
if (-not $demucs) {
    Fail @"
Demucs was not found. Install it once with:
    py -3.10 -m pip install -U demucs
(for NVIDIA GPU also: py -3.10 -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121)
Then run again, or pass -DemucsCmd "py -3.10 -m demucs".
"@
}
$demucs = @($demucs)   # guarantee an array (a single-element return unwraps to a scalar string otherwise)
Info ("Demucs: " + ($demucs -join ' '))

# ---------------------------------------------------------------- folders
$track = [System.IO.Path]::GetFileNameWithoutExtension($SongFile)
$songDir = Split-Path -Parent $SongFile
$work = if ($WorkPath) { $WorkPath } else { Join-Path $songDir 'SurroundUpmix_work' }
$sep  = Join-Path $work 'stems'
[System.IO.Directory]::CreateDirectory($sep) | Out-Null
$finalOut = if ($OutputPath) { $OutputPath } else { Join-Path $songDir "Final_$OutputFormat" }
if (-not $TrackLabel) { $TrackLabel = $track }
Info "Work folder : $work"
Info "Output folder: $finalOut"

# ---------------------------------------------------------------- separate
Step "Separating stems with Demucs ($Model, device=$Device)"
Info "This is the slow part - a few minutes per track on GPU, longer on CPU."
$dargs = @('-n', $Model, '--flac', '-o', $sep)
if ($Device -ne 'auto') { $dargs += @('-d', $Device) }
$dargs += $SongFile
$pre = @()
if ($demucs.Count -gt 1) { $pre = $demucs[1..($demucs.Count - 1)] }
if ($DebugMode) { Info ("  " + ($demucs -join ' ') + ' ' + ($dargs -join ' ')) }
# Demucs prints progress + warnings to stderr; don't let that abort under ErrorActionPreference=Stop
$prevE = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
& $demucs[0] @pre @dargs 2>&1 | ForEach-Object { Write-Host $_ }
$demucsExit = $LASTEXITCODE
$ErrorActionPreference = $prevE
if ($demucsExit -ne 0) { Fail "Demucs failed (exit $demucsExit)." }

$stemsDir = Join-Path $sep (Join-Path $Model $track)
if (-not (Test-Path -LiteralPath (Join-Path $stemsDir 'vocals.flac'))) {
    # demucs sometimes sanitises the track name; grab the only subfolder if unambiguous
    $modelDir = Join-Path $sep $Model
    $subs = Get-ChildItem -LiteralPath $modelDir -Directory -ErrorAction SilentlyContinue
    if ($subs.Count -eq 1) { $stemsDir = $subs[0].FullName }
}
if (-not (Test-Path -LiteralPath (Join-Path $stemsDir 'vocals.flac'))) { Fail "Could not find separated stems under $sep." }
Info "Stems: $stemsDir"

# ---------------------------------------------------------------- lead / backing split
# Split the vocal into LEAD (-> stays the 'vocals' stem, front) and BACKING (-> wraps the sides).
# Real source separation = phase-safe placement of the harmonies around the listener.
if ($SplitVocals -ne 'off' -and (Test-Path -LiteralPath $SplitPy) -and (Test-Path -LiteralPath $SplitScript)) {
    Step "Splitting vocal into lead + backing (Roformer karaoke)"
    Info "This runs on CPU in the isolated venv - it takes a few minutes per track."
    $vf = Join-Path $stemsDir 'vocals.flac'
    $splitOut = Join-Path $work 'vocalsplit'
    $prevE = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    & $SplitPy $SplitScript $vf $splitOut $SplitModels 2>&1 | ForEach-Object { Write-Host $_ }
    $splitExit = $LASTEXITCODE
    $ErrorActionPreference = $prevE
    $lead = Join-Path $splitOut 'lead.flac'
    $backing = Join-Path $splitOut 'backing.flac'
    if ($splitExit -eq 0 -and (Test-Path -LiteralPath $lead) -and (Test-Path -LiteralPath $backing)) {
        Copy-Item -LiteralPath $vf (Join-Path $stemsDir 'vocals_full.flac') -Force  # keep full vocal for the blend bed
        Copy-Item -LiteralPath $lead $vf -Force                                   # lead becomes the vocal stem
        Copy-Item -LiteralPath $backing (Join-Path $stemsDir 'backing.flac') -Force
        Info "  lead -> vocals (front),  backing -> surround wrap"
    } else {
        Info "  (vocal split unavailable/failed - continuing with the full vocal)"
    }
} elseif ($SplitVocals -eq 'on') {
    Info "  (SplitVocals=on but the splitter venv is missing - continuing without it)"
}

# ---------------------------------------------------------------- vocal width analysis
# A short-delay-doubled vocal must NOT be spread/echoed (it would comb-filter into a
# phasing "many voices" mess). Detect that case and keep such a vocal fully forward.
$vocMode = $VocalMode
$echo    = $EchoThrow
if ($vocMode -eq 'auto') {
    $vocMode = 'spread'
    $vocFile = Join-Path $stemsDir 'vocals.flac'
    $py = @(Resolve-Python)
    if ((Test-Path -LiteralPath $vocFile) -and ($py.Count -gt 0) -and (Test-Path -LiteralPath $Detector)) {
        Step "Analysing vocal width (double vs. reverb)"
        $ana = Join-Path $work 'vocal_analysis.wav'
        $prevE = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
        & $Sox $vocFile -r 22050 -b 16 -e signed $ana 2>&1 | Out-Null
        $pyrest = if ($py.Count -gt 1) { $py[1..($py.Count - 1)] } else { @() }
        $det = & $py[0] @pyrest $Detector $ana 2>&1 | Select-String 'RESULT'
        $ErrorActionPreference = $prevE
        if ($det) {
            $line = $det.ToString().Trim()
            Info "  $line"
            if ($line -match 'RESULT DOUBLE') {
                $vocMode = 'forward'
                Info "  -> vocal is doubled: keeping it forward, no echo/spread (avoids phasing)"
            } else {
                Info "  -> vocal width is diffuse: enveloping spread allowed"
            }
        }
    } else {
        Info "  (skipping vocal analysis - no python/torch found; using default spread)"
    }
}
if ($vocMode -eq 'forward') { $echo = 'off' }

# ---------------------------------------------------------------- adaptive backing level
# Songs differ hugely in how loud the backing vocals are, so a fixed level is wrong. Balance
# the backing to sit a set amount under the LEAD per song: strong backings get pulled down,
# subtle ones get lifted, so the choir behind you is consistent from track to track.
$backingGainEff = -6
$backingPath = Join-Path $stemsDir 'backing.flac'
if ($BackingGain -ne 'auto') {
    $backingGainEff = [double]$BackingGain
} elseif (Test-Path -LiteralPath $backingPath) {
    $lr = Get-Rms (Join-Path $stemsDir 'vocals.flac'); $br = Get-Rms $backingPath
    if ($lr -gt 0 -and $br -gt 0) {
        $g = 20 * [Math]::Log10($lr) - 20 * [Math]::Log10($br) - $BackingBelowLead
        $backingGainEff = [Math]::Max(-12.0, [Math]::Min(6.0, $g))
        Info ("  adaptive backing level: {0:0.#} dB  (lead vs backing = {1:0.#} dB; target {2:0} dB under lead)" -f $backingGainEff, (20*[Math]::Log10($lr) - 20*[Math]::Log10($br)), $BackingBelowLead)
    }
}

# ---------------------------------------------------------------- upmix
Step "Upmixing to $OutputFormat"
$p = @{
    StemsFolder   = $stemsDir
    OutputFormat  = $OutputFormat
    Preset        = $Preset
    SurroundBlend = $SurroundBlend
    SurroundSource = $SurroundSource
    RearGain      = $RearGain
    RearBelowFront = $RearBelowFront
    BackingGain   = $backingGainEff
    BackingMode   = $BackingMode
    VocalMode     = $vocMode
    EchoThrow     = $echo
    UpmixBass     = $UpmixBass
    UpmixDrums    = $UpmixDrums
    UpmixVocals   = $UpmixVocals
    UpmixOther    = $UpmixOther
    UpmixGuitar   = $UpmixGuitar
    UpmixPiano    = $UpmixPiano
    NormLevel     = $NormLevel
    LfeCrossover  = $LfeCrossover
    OutputRate    = $OutputRate
    OutputPath    = $finalOut
    TrackLabel    = $TrackLabel
}
if ($LoudnessMatch) { $p.LoudnessMatch = $true; $p.SourceFile = $SongFile }
if ($DebugMode)     { $p.DebugMode = $true }
& $Engine @p

# ---------------------------------------------------------------- cleanup
if (-not $KeepStems) {
    Step "Cleaning up work folder"
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    Info "Removed $work"
} else {
    Info "Kept stems in $work"
}
Step "All done"
