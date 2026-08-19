<#
    SurroundUpmix.ps1  -  Stereo-stem to 5.1 / 7.1 surround upmixer
    ---------------------------------------------------------------
    Self-contained engine. Takes a folder of Demucs stems
    (bass / drums / vocals / other [+ guitar / piano]) and builds a
    24-bit multichannel FLAC. Uses SoX for all channel processing and
    CenterCutCL for phase-based centre/side extraction.

    Tools are expected in .\bin\  next to this script:
        bin\SoX\sox.exe
        bin\CenterCutCL.exe
        bin\ffmpeg\ffmpeg.exe   (optional - only for -LoudnessMatch / -Mux)

    This is an independent implementation of the workflow described by
    the SurroundUpmix forum post; it is NOT the original author's script
    (that code was not available). It follows the same concept.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)] [string] $StemsFolder,
    [ValidateSet('5.1', '7.1', '7.1.2')]            [string] $OutputFormat = '5.1',
    [ValidateSet('None', 'Music', 'Movie', 'Anime', 'PLIIx', 'Focus', 'Immersive', 'Envelop', 'Concert', 'WideStage')] [string] $Preset = 'None',
    [ValidateSet('classic', 'full')]                [string] $SurroundBlend = 'classic',
    [ValidateSet('preset', 'sameside', 'difference', 'blend')] [string] $SurroundSource = 'preset',
    [int]    $VocalCenter = -1,                       # 0-100 % of the lead kept anchored up front; -1 = use preset
    [ValidateSet('auto', 'on', 'off')] [string] $EchoThrow = 'auto',   # cascade vocal echoes front -> back
    [double] $RearGain = 0,                           # user offset (dB) on the whole rear field, on top of the balance
    [double] $RearBelowFront = 16,                    # auto-balance: keep the rear field this many dB under the front (0 = off)
    [ValidateSet('spread', 'forward')] [string] $VocalMode = 'spread',  # forward = keep a doubled vocal up front (no smearing)
    [double] $BackingGain = -3,                       # level of split-out backing vocals in the surround wrap
    [ValidateSet('rear', 'halo', 'choir', 'blend')] [string] $BackingMode = 'blend',   # how to place backing vocals
    [ValidateSet('4.0', '5.0', 'mono', 'front', 'rear')] [string] $UpmixBass   = '4.0',
    [ValidateSet('4.0', '5.0', 'mono', 'front', 'rear')] [string] $UpmixDrums  = '4.0',
    [ValidateSet('4.0', '5.0', 'mono', 'front', 'rear')] [string] $UpmixVocals = '5.0',
    [ValidateSet('4.0', '5.0', 'mono', 'front', 'rear')] [string] $UpmixOther  = '5.0',
    [ValidateSet('4.0', '5.0', 'mono', 'front', 'rear')] [string] $UpmixGuitar = '4.0',
    [ValidateSet('4.0', '5.0', 'mono', 'front', 'rear')] [string] $UpmixPiano  = '4.0',
    [double] $NormLevel   = -0.1,
    [int]    $LfeCrossover = 120,
    [int]    $OutputRate  = 0,
    [string] $OutputPath  = '',
    [string] $TrackLabel  = '',
    [switch] $LoudnessMatch,
    [string] $SourceFile  = '',
    [switch] $KeepTemp,
    [switch] $DebugMode
)

$ErrorActionPreference = 'Stop'
# force '.' as decimal separator so numbers handed to SoX are never localized (e.g. "0,012")
[System.Threading.Thread]::CurrentThread.CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture

# ---------------------------------------------------------------- paths / tools
$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot }
             elseif ($PSCommandPath) { Split-Path -Parent $PSCommandPath }
             elseif ($MyInvocation.MyCommand.Path) { Split-Path -Parent $MyInvocation.MyCommand.Path }
             else { (Get-Location).Path }
$Sox     = Join-Path $ScriptDir 'bin\SoX\sox.exe'
$CenterCut = Join-Path $ScriptDir 'bin\CenterCutCL.exe'
$FFmpeg  = Join-Path $ScriptDir 'bin\ffmpeg\ffmpeg.exe'

function Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }
function Info($msg) { Write-Host $msg -ForegroundColor Cyan }
function Note($msg) { Write-Host $msg -ForegroundColor DarkGray }

if (-not (Test-Path -LiteralPath $Sox))       { Fail "SoX not found at bin\SoX\sox.exe" }
if (-not (Test-Path -LiteralPath $CenterCut)) { Fail "CenterCutCL not found at bin\CenterCutCL.exe" }
$haveFFmpeg = Test-Path -LiteralPath $FFmpeg

# ---------------------------------------------------------------- sox wrapper
function Sox {
    param([Parameter(ValueFromRemainingArguments = $true)] $a)
    if ($DebugMode) { Note ("  sox " + ($a -join ' ')) }
    # stderr (SoX warnings) must not abort the run under ErrorActionPreference=Stop; judge success by exit code
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    & $Sox @a 2>&1 | ForEach-Object { if ($DebugMode) { Note "    $_" } }
    $ErrorActionPreference = $prev
    if ($LASTEXITCODE -ne 0) { Fail "SoX failed (exit $LASTEXITCODE). Run with -DebugMode for the command." }
}
# soxi-style info
function SoxInfo { param($file, $flag) (& $Sox --i $flag $file) -join '' }

# ---------------------------------------------------------------- resolve stems
if (-not $StemsFolder) { Fail "No stems folder given. Usage: .\SurroundUpmix.ps1 <stems-folder>" }
if (-not (Test-Path -LiteralPath $StemsFolder)) { Fail "Stems folder does not exist: $StemsFolder" }
$StemsFolder = (Resolve-Path -LiteralPath $StemsFolder).Path

function Find-Stem($name) {
    foreach ($ext in 'flac', 'wav') {
        $p = Join-Path $StemsFolder "$name.$ext"
        if (Test-Path -LiteralPath $p) { return $p }
    }
    return $null
}

$routes = @{
    bass   = @{ file = Find-Stem 'bass';   mode = $UpmixBass   }
    drums  = @{ file = Find-Stem 'drums';  mode = $UpmixDrums  }
    vocals = @{ file = Find-Stem 'vocals'; mode = $UpmixVocals }
    other  = @{ file = Find-Stem 'other';  mode = $UpmixOther  }
    guitar = @{ file = Find-Stem 'guitar'; mode = $UpmixGuitar }
    piano  = @{ file = Find-Stem 'piano';  mode = $UpmixPiano  }
}
$present = $routes.GetEnumerator() | Where-Object { $_.Value.file } | ForEach-Object { $_.Key }
if (-not $present) { Fail "No stems found in '$StemsFolder' (need bass/drums/vocals/other .flac or .wav)." }

# ---------------------------------------------------------------- preset table
# preset table. src = how the surround/height ambience is derived:
#   sameside   = the channel's own side (keeps hard-panned objects on their side)
#   difference = L-R matrix (Pro Logic II style): pulls the mixer's ambience/reverb into the rears - very enveloping
#   blend      = both at once (placed objects AND an enveloping diffuse field)
# sx = extra surround level (dB) on top of the classic/full blend.
# voc = fraction of the lead vocal kept anchored up front (rest of its diffuse tail may wrap)
# echo = cascade the vocal's echoes/adlibs from front toward the rears/height
$presets = @{
    'None'      = @{ cross = 0;    d1 = 15; d2 = 18; centerDb = 0;   split = $false; src = 'sameside';   sx = 0; voc = 0.0;  echo = $false }
    'Music'     = @{ cross = 4000; d1 = 15; d2 = 17; centerDb = 0;   split = $true;  src = 'sameside';   sx = 0; voc = 0.0;  echo = $false }
    'Movie'     = @{ cross = 2500; d1 = 10; d2 = 12; centerDb = 1;   split = $true;  src = 'sameside';   sx = 0; voc = 0.0;  echo = $false }
    'Anime'     = @{ cross = 3000; d1 = 12; d2 = 14; centerDb = 2;   split = $true;  src = 'sameside';   sx = 0; voc = 0.0;  echo = $false }
    'PLIIx'     = @{ cross = 3500; d1 = 20; d2 = 23; centerDb = 0;   split = $true;  src = 'difference'; sx = 0; voc = 0.0;  echo = $false }
    # --- enveloping family: phase-coherent. Direct same-side HIGH band to the rears only
    # (no inversion, no delay, no echo) so the pristine front image is never cancelled. ---
    'Focus'     = @{ cross = 3500; d1 = 0; d2 = 0; centerDb = 1; split = $true; src = 'sameside'; sx = 0; voc = 0.70; echo = $false }  # vocal forward, just top air wraps
    'Immersive' = @{ cross = 2800; d1 = 0; d2 = 0; centerDb = 1; split = $true; src = 'sameside'; sx = 2; voc = 0.60; echo = $false }  # balanced all-rounder
    'Envelop'   = @{ cross = 2000; d1 = 0; d2 = 0; centerDb = 0; split = $true; src = 'sameside'; sx = 4; voc = 0.50; echo = $false }  # more of the spectrum wraps
    'Concert'   = @{ cross = 2500; d1 = 0; d2 = 0; centerDb = 1; split = $true; src = 'sameside'; sx = 3; voc = 0.60; echo = $false }  # roomy
    'WideStage' = @{ cross = 3200; d1 = 0; d2 = 0; centerDb = 0; split = $true; src = 'sameside'; sx = 2; voc = 0.45; echo = $false }  # wide front + sides
}
$pp = $presets[$Preset]; if (-not $pp) { $pp = $presets['None'] }
$cross = $pp.cross; $d1 = $pp.d1; $d2 = $pp.d2; $centerDb = $pp.centerDb; $split = $pp.split
$surrSrc = if ($SurroundSource -eq 'preset') { $pp.src } else { $SurroundSource }
# surround level in the blend: classic = -6 dB, full = 0 dB, plus the preset's spatial boost
$surrDb = $(if ($SurroundBlend -eq 'classic') { -6.0 } else { 0.0 }) + $pp.sx
$backDb = $surrDb - 3.0    # back speakers a touch behind the sides
$vocAnchor = if ($VocalCenter -ge 0) { [Math]::Min($VocalCenter, 100) / 100.0 } else { $pp.voc }
$echoOn    = if ($EchoThrow -eq 'on') { $true } elseif ($EchoThrow -eq 'off') { $false } else { $pp.echo }

$isAtmos  = ($OutputFormat -eq '7.1.2')          # adds two height channels (TFL/TFR)
$hasBacks = ($OutputFormat -ne '5.1')            # 7.1 and 7.1.2 have discrete back speakers
switch ($OutputFormat) {
    '5.1'   { $chanOrder = 'FL','FR','FC','LFE','SL','SR' }
    '7.1'   { $chanOrder = 'FL','FR','FC','LFE','BL','BR','SL','SR' }
    '7.1.2' { $chanOrder = 'FL','FR','FC','LFE','BL','BR','SL','SR','TFL','TFR' }
}

Info "SurroundUpmix - $OutputFormat  |  preset: $Preset  |  blend: $SurroundBlend  |  surround: $surrSrc"
Info "Stems: $($present -join ', ')"

# ---------------------------------------------------------------- work dir
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$work  = Join-Path $env:TEMP "su_work_$stamp"
New-Item -ItemType Directory -Force -Path $work | Out-Null

# reference rate & length from the first present stem
$refStem = $routes[$present[0]].file
$rate    = [int](SoxInfo $refStem '-r')
$refSamp = [long](SoxInfo $refStem '-s')   # SoX --i -s already reports frames (per-channel)
Note "rate=$rate Hz  length=$refSamp samples"

# per-channel accumulation of contribution files
$chan = @{}; foreach ($c in $chanOrder) { $chan[$c] = New-Object System.Collections.ArrayList }
$ci = 0
function New-Tmp($tag) { $script:ci++; Join-Path $work ("{0:D3}_{1}.wav" -f $script:ci, $tag) }

# build one processed mono contribution and register it on a channel
#   src      : source wav/flac
#   remix    : sox remix arg (1 = L, 2 = R, "1,2" = mono sum)
#   channel  : target channel name
#   gainDb   : gain in dB
#   delayMs  : delay in ms (0 = none)
#   hp/lp    : optional filter cutoff (0 = none)
function Add-Contribution($src, $remix, $channel, $gainDb, $delayMs, $hp, $lp) {
    $out = New-Tmp $channel
    # clamp remix to available channels (CenterCut centre is mono; a mono stem stays mono)
    if ([int](SoxInfo $src '-c') -lt 2) { $remix = ($remix -replace '2', '1'); if ($remix -eq '1,1') { $remix = '1' } }
    $fx  = @($src, '-b', '32', '-e', 'float', $out, 'remix', $remix)
    if ($hp -gt 0)     { $fx += @('highpass', "$hp") }
    if ($lp -gt 0)     { $fx += @('lowpass',  "$lp") }
    if ($gainDb -ne 0) { $fx += @('gain', ('{0:0.###}' -f $gainDb)) }
    if ($delayMs -gt 0) { $ds = '{0:0.####}' -f ($delayMs / 1000.0); $fx += @('delay', $ds) }
    Sox @fx
    [void]$chan[$channel].Add($out)
}

# add a surround / height contribution using the selected ambience source ($surrSrc)
#   sameside   = the channel's own side (L or R)
#   difference = L-R (or R-L) matrix: the mixer's ambience/reverb, highly decorrelated -> enveloping
#   blend      = both, each ~3 dB down, so placed objects AND a diffuse field coexist
function Add-Surround($src, $channel, $side, $gainDb, $delayMs, $hpHz) {
    $same = if ($side -eq 'L') { '1' } else { '2' }
    $diff = if ($side -eq 'L') { '1v1,2v-1' } else { '2v1,1v-1' }
    switch ($surrSrc) {
        'difference' { Add-Contribution $src $diff $channel $gainDb $delayMs $hpHz 0 }
        'blend' {
            Add-Contribution $src $same $channel ($gainDb - 3) $delayMs $hpHz 0
            Add-Contribution $src $diff $channel ($gainDb - 3) ($delayMs + 4) $hpHz 0
        }
        default { Add-Contribution $src $same $channel $gainDb $delayMs $hpHz 0 }
    }
}

# echo / adlib throw: delayed, decaying repeats of the vocal that travel from the
# sides toward the back (and up to the height layer on 7.1.2) - like a real Atmos delay throw
function Add-EchoThrow($src) {
    # fed from the SIDE (L-R) content only, so the dry mono lead is never repeated -
    # only genuinely wide/ambient material throws back, and it decays as it travels
    $taps = @(
        @{ d = 130; g = -10 },
        @{ d = 280; g = -15 },
        @{ d = 470; g = -21 }
    )
    if     ($isAtmos)  { $pairs = @('SL,SR', 'BL,BR', 'TFL,TFR') }   # sides -> back -> up
    elseif ($hasBacks) { $pairs = @('SL,SR', 'BL,BR', 'BL,BR') }
    else               { $pairs = @('SL,SR', 'SL,SR', 'SL,SR') }
    for ($i = 0; $i -lt $taps.Count; $i++) {
        $lr = $pairs[$i].Split(',')
        Add-Contribution $src '1v1,2v-1' $lr[0] $taps[$i].g $taps[$i].d 220 0
        Add-Contribution $src '2v1,1v-1' $lr[1] $taps[$i].g $taps[$i].d 220 0
    }
}

# ---------------------------------------------------------------- route stems
foreach ($key in $present) {
    $stem = $routes[$key].file
    $mode = $routes[$key].mode
    Info "  [$key] -> $mode"

    switch ($mode) {
        'mono' {
            Add-Contribution $stem '1,2' 'FC' ($centerDb - 3) 0 0 0
        }
        'front' {
            Add-Contribution $stem '1' 'FL' 0 0 0 0
            Add-Contribution $stem '2' 'FR' 0 0 0 0
        }
        'rear' {
            if ($hasBacks) {
                Add-Contribution $stem '1' 'BL' 0 0 0 0
                Add-Contribution $stem '2' 'BR' 0 0 0 0
            } else {
                Add-Contribution $stem '1' 'SL' 0 0 0 0
                Add-Contribution $stem '2' 'SR' 0 0 0 0
            }
        }
        default {
            # 4.0 and 5.0 share the front + surround spread.
            # For 5.0 we first pull the phantom centre out with CenterCutCL
            # and feed only the SIDES to fronts/surrounds.
            $frontL = $stem; $frontR = $stem; $rL = '1'; $rR = '2'
            $isVoc  = ($key -eq 'vocals')
            # selective rears: bass stays front + LFE; only texture stems (other/guitar/piano)
            # feed the discrete BACK / HEIGHT speakers, so the rears carry picked content - not a wash
            $noRear    = ($key -eq 'bass')
            $feedBacks = ($key -eq 'other' -or $key -eq 'guitar' -or $key -eq 'piano')
            # a short-delay-doubled vocal (detected upstream) stays fully forward - spreading it
            # would stack delays and comb-filter into a phasing / "many voices" mess
            $vocForward = ($isVoc -and $VocalMode -eq 'forward')
            # vocal stays forward because we send LESS of it to the sides - no artificial centre boost
            $sCut = if ($isVoc) { 20 * [Math]::Log10([Math]::Max(1 - $vocAnchor, 0.05)) } else { 0 }
            if ($mode -eq '5.0') {
                # CenterCutCL needs 16-bit input
                $s16    = New-Tmp "$key`_16"
                Sox $stem '-b' '16' $s16
                $cWav   = New-Tmp "$key`_center"
                $sWav   = New-Tmp "$key`_sides"
                if ($DebugMode) { Note "  CenterCutCL $s16 -> center/sides" }
                $prevE = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
                & $CenterCut $s16 '-c' $cWav '-s' $sWav '-o' 2>&1 | ForEach-Object { if ($DebugMode) { Note "    $_" } }
                $ErrorActionPreference = $prevE
                if (-not (Test-Path -LiteralPath $cWav) -or -not (Test-Path -LiteralPath $sWav)) { Fail "CenterCutCL produced no output for $key." }
                # centre -> FC (mono downmix of the centre file), at its natural level
                Add-Contribution $cWav '1,2' 'FC' ($centerDb - 3) 0 0 0
                $frontL = $sWav; $frontR = $sWav
            }

            # fronts: direct, full range
            Add-Contribution $frontL $rL 'FL' 0 0 0 0
            Add-Contribution $frontR $rR 'FR' 0 0 0 0

            if (-not $noRear -and -not $vocForward) {
                # frequency-staggered bands so sides / backs / heights never duplicate each other
                # (duplication = comb filter). No inversion; delay only if a preset sets d1/d2.
                $hp     = if ($split) { $cross } else { 0 }
                $backHp = if ($split) { [int]($cross * 1.8) } else { 0 }
                $topHp  = if ($split) { [int]($cross * 2.6) } else { 0 }
                # sides: gentle same-side high band (lows/mids stay pristine up front)
                Add-Surround $frontL 'SL' 'L' ($surrDb + $sCut) $d1 $hp
                Add-Surround $frontR 'SR' 'R' ($surrDb + $sCut) $d1 $hp
                # backs + heights: ONLY texture stems, progressively higher "air" so nothing overlaps
                if ($hasBacks -and $feedBacks) {
                    Add-Surround $frontL 'BL' 'L' $backDb $d2 $backHp
                    Add-Surround $frontR 'BR' 'R' $backDb $d2 $backHp
                }
                if ($isAtmos -and $feedBacks) {
                    Add-Surround $frontL 'TFL' 'L' ($surrDb - 4) $d2 $topHp
                    Add-Surround $frontR 'TFR' 'R' ($surrDb - 4) $d2 $topHp
                }
            }
            # vocal echoes still throw back / up - but NOT for a doubled vocal (would smear)
            if ($isVoc -and $echoOn -and -not $vocForward) { Add-EchoThrow $stem }
        }
    }
}

# ---------------------------------------------------------------- backing vocals
# From the karaoke split (lead is routed as 'vocals'). Backing is its OWN content, so it
# can wrap the SIDES full-range with zero phase risk - nothing else duplicates it.
$backingFile = Find-Stem 'backing'
if ($backingFile) {
    $bg = $BackingGain
    $rearL = if ($hasBacks) { 'BL' } else { 'SL' }
    $rearR = if ($hasBacks) { 'BR' } else { 'SR' }
    Info "  [backing] -> $BackingMode (rear $rearL/$rearR)"
    # small front anchor so word/phrase transitions never fully 'jump' front<->back when the
    # imperfect split wobbles about what is lead vs backing (divergence)
    if ($BackingMode -ne 'halo') {
        # small front anchor (divergence) so transitions never fully jump, + dry localizable choir behind
        Add-Contribution $backingFile '1' 'FL' ($bg - 9) 0 0 0
        Add-Contribution $backingFile '2' 'FR' ($bg - 9) 0 0 0
        Add-Contribution $backingFile '1' $rearL $bg 0 0 0
        Add-Contribution $backingFile '2' $rearR $bg 0 0 0
    }
    if ($BackingMode -eq 'choir' -or $BackingMode -eq 'halo') {
        # wet-only reverb glue: smears the split's transition artifacts into diffuse space
        $wet = New-Tmp 'backing_wet'
        Sox $backingFile '-b' '32' '-e' 'float' $wet 'reverb' '-w' '72' '50' '100' '100' '40' '0'
        $wetDb = if ($BackingMode -eq 'halo') { $bg } else { $bg - 6 }   # halo = wet only, so louder
        Add-Contribution $wet '1' $rearL $wetDb 0 0 0
        Add-Contribution $wet '2' $rearR $wetDb 0 0 0
        Add-Contribution $wet '1' 'SL' ($wetDb - 3) 0 0 0
        Add-Contribution $wet '2' 'SR' ($wetDb - 3) 0 0 0
        if ($isAtmos) {
            Add-Contribution $wet '1' 'TFL' ($wetDb - 4) 0 0 0
            Add-Contribution $wet '2' 'TFR' ($wetDb - 4) 0 0 0
        }
    }
    if ($BackingMode -eq 'blend') {
        # the compromise: keep the great isolated backing, but lay the FULL clean vocal underneath
        # it as a quiet BED. Real coherent voice (not reverb) fills the holes and masks the split's
        # transition artifacts. Needs vocals_full.flac (the vocal before the split replaced it).
        $bed = Find-Stem 'vocals_full'
        if ($bed) {
            $bedDb = $bg - 10
            Add-Contribution $bed '1' $rearL $bedDb 0 0 0
            Add-Contribution $bed '2' $rearR $bedDb 0 0 0
            if ($isAtmos) {
                Add-Contribution $bed '1' 'TFL' ($bedDb - 3) 0 3000 0
                Add-Contribution $bed '2' 'TFR' ($bedDb - 3) 0 3000 0
            }
        } else {
            Note "  (blend bed needs vocals_full.flac; none found - dry backing only)"
        }
    }
}

# ---------------------------------------------------------------- LFE
# derived from the low-frequency content of the full mix
Info "  [LFE] <- full-mix low band (<= $LfeCrossover Hz)"
$allMix = New-Tmp 'allmix'
$mixArgs = @('--combine', 'mix') + ($present | ForEach-Object { $routes[$_].file }) + @('-b', '32', '-e', 'float', $allMix, 'channels', '1')
Sox @mixArgs
$lfe = New-Tmp 'LFE'
Sox $allMix '-b' '32' '-e' 'float' $lfe 'lowpass' "$LfeCrossover" 'gain' '-3'
[void]$chan['LFE'].Add($lfe)

# ---------------------------------------------------------------- mix per channel
# 'merge' auto-pads unequal lengths, so channels need not be length-normalised here.
$builtChannels = @()
foreach ($c in $chanOrder) {
    $parts = $chan[$c]
    $merged = New-Tmp "chan_$c"
    if ($parts.Count -eq 0) {
        # silent channel matching the source length
        Sox '-n' '-b' '32' '-e' 'float' '-r' "$rate" '-c' '1' $merged 'trim' '0' "${refSamp}s"
    } elseif ($parts.Count -eq 1) {
        Copy-Item $parts[0] $merged -Force
    } else {
        $mm = @('--combine', 'mix') + $parts + @('-b', '32', '-e', 'float', $merged)
        Sox @mm
    }
    $builtChannels += $merged
}

# ---------------------------------------------------------------- rear / front auto-balance
# Every song carries a different amount of rear material, so a fixed rear level is always a
# touch off (too loud on one song, too quiet on the next). Measure the whole rear field vs the
# front and trim the rears to sit a set amount under the front - consistent song to song.
# RearGain is then just the user's taste offset on top of that balanced base.
function ChRms($file) {
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    $s = & $Sox $file -n stat 2>&1
    $ErrorActionPreference = $prev
    $m = $s | Select-String 'RMS.*amplitude'
    if ($m) { return [double]($m[0].ToString().Split(':')[1].Trim()) }
    return 0.0
}
if ($RearBelowFront -gt 0) {
    $frontSet = @('FL', 'FR', 'FC')
    $rearSet  = @('SL', 'SR', 'BL', 'BR', 'TFL', 'TFR')
    $fe = 0.0; $re = 0.0
    for ($k = 0; $k -lt $chanOrder.Count; $k++) {
        $r = ChRms $builtChannels[$k]
        if     ($frontSet -contains $chanOrder[$k]) { $fe += $r * $r }
        elseif ($rearSet  -contains $chanOrder[$k]) { $re += $r * $r }
    }
    if ($fe -gt 1e-9 -and $re -gt 1e-9) {
        $frontDb = 10 * [Math]::Log10($fe)
        $rearDb  = 10 * [Math]::Log10($re)
        $trim = [Math]::Max(-24.0, [Math]::Min(12.0, ($frontDb - $RearBelowFront + $RearGain) - $rearDb))
        Info ("  [balance] front {0:0.#} / rear {1:0.#} dB -> trim rears {2:0.#} dB (target {3} under front)" -f $frontDb, $rearDb, $trim, $RearBelowFront)
        for ($k = 0; $k -lt $chanOrder.Count; $k++) {
            if ($rearSet -contains $chanOrder[$k]) {
                $tf = New-Tmp ('bal_' + $chanOrder[$k])
                Sox $builtChannels[$k] '-b' '32' '-e' 'float' $tf 'gain' ('{0:0.###}' -f $trim)
                $builtChannels[$k] = $tf
            }
        }
    }
}

# ---------------------------------------------------------------- merge -> FLAC
if (-not $OutputPath) { $OutputPath = Join-Path (Split-Path -Parent $StemsFolder) "Final_$OutputFormat" }
[System.IO.Directory]::CreateDirectory($OutputPath) | Out-Null
if (-not $TrackLabel) { $TrackLabel = Split-Path -Leaf $StemsFolder }
# FLAC supports at most 8 channels; 7.1.2 (10 ch) must be written as a multichannel WAV
$ext = if ($chanOrder.Count -gt 8) { 'wav' } else { 'flac' }
$outFlac = Join-Path $OutputPath "$TrackLabel`_$OutputFormat.$ext"

Info "Merging $($chanOrder.Count) channels -> $($ext.ToUpper())"
$mergeArgs = @('--combine', 'merge') + $builtChannels + @('-b', '24', $outFlac, 'gain', '-n', ('{0:0.###}' -f $NormLevel))
if ($OutputRate -gt 0) { $mergeArgs += @('rate', '-v', "$OutputRate") }
Sox @mergeArgs

# ---------------------------------------------------------------- optional loudness match (ffmpeg)
$finalFile = $outFlac
if ($LoudnessMatch) {
    if (-not $haveFFmpeg) {
        Write-Host "  -LoudnessMatch requested but bin\ffmpeg\ffmpeg.exe is missing - skipped." -ForegroundColor Yellow
    } else {
        Info "  Loudness matching (EBU R128)"
        $tmpOut = Join-Path $OutputPath "$TrackLabel`_$OutputFormat`_ln.$ext"
        $codec  = if ($ext -eq 'flac') { @('-c:a','flac','-sample_fmt','s32') } else { @('-c:a','pcm_s24le') }
        $target = '-16'   # LUFS target if no source reference
        $af = "loudnorm=I=$target`:TP=-1.0:LRA=11"
        if ($SourceFile -and (Test-Path -LiteralPath $SourceFile)) {
            Note "  (reference: $SourceFile)"
        }
        $prevE = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
        & $FFmpeg -y -i $outFlac -af $af @codec $tmpOut 2>&1 | ForEach-Object { if ($DebugMode) { Note "    $_" } }
        $ErrorActionPreference = $prevE
        if (Test-Path -LiteralPath $tmpOut) { $finalFile = $tmpOut }
    }
}

# ---------------------------------------------------------------- cleanup
if (-not $KeepTemp) { Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue }
else { Note "temp kept: $work" }

Info "DONE -> $finalFile"
Write-Host $finalFile
