<#
    SurroundUpmix-GUI.ps1   (dark, full options)
    Song file  -> Demucs + lead/backing split -> surround upmix
    Stems folder -> upmix only
    Double-click SurroundUpmix.bat, or right-click -> Run with PowerShell.
#>
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()
try {
    Add-Type -Namespace Win32 -Name Dwm -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("dwmapi.dll")]
public static extern int DwmSetWindowAttribute(System.IntPtr hwnd, int attr, ref int val, int size);
'@ -ErrorAction SilentlyContinue
} catch {}

$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot }
             elseif ($PSCommandPath) { Split-Path -Parent $PSCommandPath }
             elseif ($MyInvocation.MyCommand.Path) { Split-Path -Parent $MyInvocation.MyCommand.Path }
             else { (Get-Location).Path }
$Engine     = Join-Path $ScriptDir 'SurroundUpmix.ps1'
$EngineAll  = Join-Path $ScriptDir 'SurroundUpmix-AllInOne.ps1'
$HaveFFmpeg = Test-Path -LiteralPath (Join-Path $ScriptDir 'bin\ffmpeg\ffmpeg.exe')
$HaveSplit  = Test-Path -LiteralPath (Join-Path $ScriptDir 'bin\splitter_venv\Scripts\python.exe')
if (-not (Test-Path -LiteralPath $Engine)) {
    [System.Windows.Forms.MessageBox]::Show("SurroundUpmix.ps1 not found next to this GUI.","SurroundUpmix") | Out-Null; return
}

# ---------------------------------------------------------------- dark palette
$C_Bg=[System.Drawing.Color]::FromArgb(32,33,36); $C_Panel=[System.Drawing.Color]::FromArgb(45,46,50)
$C_Input=[System.Drawing.Color]::FromArgb(52,53,58); $C_Fg=[System.Drawing.Color]::FromArgb(224,224,226)
$C_Dim=[System.Drawing.Color]::FromArgb(150,152,156); $C_Accent=[System.Drawing.Color]::FromArgb(0,122,204)
$C_AccentHi=[System.Drawing.Color]::FromArgb(28,151,234); $C_Border=[System.Drawing.Color]::FromArgb(64,65,70)
$C_Ok=[System.Drawing.Color]::FromArgb(120,205,130); $C_Err=[System.Drawing.Color]::FromArgb(232,120,110)
$C_Head=[System.Drawing.Color]::FromArgb(120,170,225)

# ---------------------------------------------------------------- helpers
function New-Label($t,$x,$y,$w=110,$h=20){ $l=New-Object System.Windows.Forms.Label;$l.Text=$t;$l.Location="$x,$y";$l.Size="$w,$h";$l.ForeColor=$C_Fg;$l.BackColor=[System.Drawing.Color]::Transparent;return $l }
function New-Head($t,$x,$y){ $l=New-Object System.Windows.Forms.Label;$l.Text=$t;$l.Location="$x,$y";$l.Size='300,16';$l.ForeColor=$C_Head;$l.BackColor=[System.Drawing.Color]::Transparent;$l.Font=New-Object System.Drawing.Font('Segoe UI',8,[System.Drawing.FontStyle]::Bold);return $l }
function New-Combo($x,$y,$items,$sel,$w=120){
    $c=New-Object System.Windows.Forms.ComboBox;$c.Location="$x,$y";$c.Size="$w,24";$c.DropDownStyle='DropDownList';$c.FlatStyle='Flat'
    $c.BackColor=$C_Input;$c.ForeColor=$C_Fg;$c.DrawMode='OwnerDrawFixed';[void]$c.Items.AddRange($items);$c.SelectedItem=$sel
    $c.Add_DrawItem({ param($s,$e)
        $seld=($e.State -band [System.Windows.Forms.DrawItemState]::Selected)-ne 0
        $bg=if($seld){$C_Accent}elseif($s.Enabled){$C_Input}else{$C_Panel}
        $fgB=New-Object System.Drawing.SolidBrush($(if($s.Enabled){$C_Fg}else{$C_Dim}));$bgB=New-Object System.Drawing.SolidBrush($bg)
        $e.Graphics.FillRectangle($bgB,$e.Bounds)
        if($e.Index -ge 0){ $e.Graphics.DrawString($s.Items[$e.Index].ToString(),$s.Font,$fgB,$e.Bounds.X+2,$e.Bounds.Y+2) }
        $bgB.Dispose();$fgB.Dispose() })
    return $c }
function Style-Button($b,[switch]$Accent){ $b.FlatStyle='Flat';$b.ForeColor=$C_Fg;$b.FlatAppearance.BorderColor=$C_Border
    if($Accent){$b.BackColor=$C_Accent;$b.FlatAppearance.BorderColor=$C_Accent;$b.FlatAppearance.MouseOverBackColor=$C_AccentHi}else{$b.BackColor=$C_Panel;$b.FlatAppearance.MouseOverBackColor=$C_Input};return $b }
function Style-Text($t){ $t.BackColor=$C_Input;$t.ForeColor=$C_Fg;$t.BorderStyle='FixedSingle';return $t }
function Style-Check($c){ $c.ForeColor=$C_Fg;$c.BackColor=[System.Drawing.Color]::Transparent;return $c }

function Test-DemucsSpec($parts){ try{ $rest=if($parts.Count-gt1){$parts[1..($parts.Count-1)]}else{@()}; & $parts[0] @rest -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('demucs') else 1)" 2>$null | Out-Null; return ($LASTEXITCODE-eq0) }catch{ return $false } }
function Detect-Demucs{ if(Get-Command demucs -ErrorAction SilentlyContinue){return 'demucs'}
    $cs=New-Object System.Collections.ArrayList;[void]$cs.Add(@('py','-3.10'));[void]$cs.Add(@('py'));[void]$cs.Add(@('python'))
    foreach($c in $cs){ if(Test-DemucsSpec $c){ return (($c -join ' ')+' -m demucs') } }; return $null }
function Detect-Stems($folder){ $f=@(); foreach($n in 'bass','drums','vocals','other','guitar','piano'){ if((Test-Path -LiteralPath (Join-Path $folder "$n.flac")) -or (Test-Path -LiteralPath (Join-Path $folder "$n.wav"))){$f+=$n} }; return $f }

# ---------------------------------------------------------------- form
$form=New-Object System.Windows.Forms.Form
$form.Text='SurroundUpmix  -  song -> 5.1 / 7.1 / 7.1.2'
$form.Size='680,900';$form.StartPosition='CenterScreen';$form.Font=New-Object System.Drawing.Font('Segoe UI',9)
$form.FormBorderStyle='FixedDialog';$form.MaximizeBox=$false;$form.BackColor=$C_Bg;$form.ForeColor=$C_Fg

$hint=New-Label "Song file -> Demucs + lead/backing split, then upmix.   Stems folder -> upmix only." 15 8 640 18
$hint.ForeColor=$C_Dim;$form.Controls.Add($hint)

# --- input ---
$form.Controls.Add((New-Label 'Input:' 15 36 45))
$panelIn=New-Object System.Windows.Forms.Panel;$panelIn.Location='63,34';$panelIn.Size='240,26';$panelIn.BackColor=$C_Bg
$rbSong=Style-Check (New-Object System.Windows.Forms.RadioButton);$rbSong.Text='Song file';$rbSong.Location='2,1';$rbSong.Size='95,24';$rbSong.Checked=$true
$rbStems=Style-Check (New-Object System.Windows.Forms.RadioButton);$rbStems.Text='Stems folder';$rbStems.Location='100,1';$rbStems.Size='120,24'
$panelIn.Controls.Add($rbSong);$panelIn.Controls.Add($rbStems);$form.Controls.Add($panelIn)

$lblPath=New-Label 'Song file:' 15 64 90;$form.Controls.Add($lblPath)
$txtInput=Style-Text (New-Object System.Windows.Forms.TextBox);$txtInput.Location='110,62';$txtInput.Size='418,24';$form.Controls.Add($txtInput)
$btnBrowse=New-Object System.Windows.Forms.Button;$btnBrowse.Text='Browse...';$btnBrowse.Location='534,61';$btnBrowse.Size='100,26';Style-Button $btnBrowse|Out-Null;$form.Controls.Add($btnBrowse)
$lblDetect=New-Label '' 110 88 524 18;$lblDetect.ForeColor=$C_Dim;$form.Controls.Add($lblDetect)

# --- output ---
$form.Controls.Add((New-Label 'Output:' 15 118 50))
$panelFmt=New-Object System.Windows.Forms.Panel;$panelFmt.Location='63,116';$panelFmt.Size='168,26';$panelFmt.BackColor=$C_Bg
$rb51=Style-Check (New-Object System.Windows.Forms.RadioButton);$rb51.Text='5.1';$rb51.Location='3,2';$rb51.Size='48,24';$rb51.Checked=$true
$rb71=Style-Check (New-Object System.Windows.Forms.RadioButton);$rb71.Text='7.1';$rb71.Location='51,2';$rb71.Size='48,24'
$rb712=Style-Check (New-Object System.Windows.Forms.RadioButton);$rb712.Text='7.1.2';$rb712.Location='99,2';$rb712.Size='62,24'
$panelFmt.Controls.Add($rb51);$panelFmt.Controls.Add($rb71);$panelFmt.Controls.Add($rb712);$form.Controls.Add($panelFmt)
$form.Controls.Add((New-Label 'Preset:' 240 118 45))
$cboPreset=New-Combo 288 116 @('None','Music','Movie','Anime','PLIIx','Focus','Immersive','Envelop','Concert','WideStage') 'Immersive' 130;$form.Controls.Add($cboPreset)
$form.Controls.Add((New-Label 'Name (optional):' 430 118 100))
$txtName=Style-Text (New-Object System.Windows.Forms.TextBox);$txtName.Location='530,116';$txtName.Size='104,24';$form.Controls.Add($txtName)

$form.Controls.Add((New-Label 'Output folder:' 15 150 90))
$txtOut=Style-Text (New-Object System.Windows.Forms.TextBox);$txtOut.Location='110,148';$txtOut.Size='418,24';$form.Controls.Add($txtOut)
$btnOutBrowse=New-Object System.Windows.Forms.Button;$btnOutBrowse.Text='Browse...';$btnOutBrowse.Location='534,147';$btnOutBrowse.Size='100,26';Style-Button $btnOutBrowse|Out-Null;$form.Controls.Add($btnOutBrowse)
$lblOutHint=New-Label '(empty = a Final_5.1 / Final_7.1 / Final_7.1.2 folder next to the input)' 110 174 520 16;$lblOutHint.ForeColor=$C_Dim;$form.Controls.Add($lblOutHint)

# --- separation ---
$form.Controls.Add((New-Head 'SEPARATION (Demucs)' 15 202))
$lblModel=New-Label 'Model:' 15 224 45;$form.Controls.Add($lblModel)
$cboModel=New-Combo 63 222 @('htdemucs_ft','htdemucs','htdemucs_6s') 'htdemucs_ft' 120;$form.Controls.Add($cboModel)
$lblDev=New-Label 'Device:' 195 224 50;$form.Controls.Add($lblDev)
$cboDevice=New-Combo 248 222 @('auto','cuda','cpu') 'auto' 80;$form.Controls.Add($cboDevice)
$chkSplit=Style-Check (New-Object System.Windows.Forms.CheckBox);$chkSplit.Text='Split lead/backing vox';$chkSplit.Location='345,224';$chkSplit.Size='185,24';$chkSplit.Checked=$true
if(-not $HaveSplit){$chkSplit.Enabled=$false;$chkSplit.Checked=$false};$form.Controls.Add($chkSplit)
$lblDemucs=New-Label '' 15 250 620 18;$form.Controls.Add($lblDemucs)

# --- vocals & backing ---
$form.Controls.Add((New-Head 'VOCALS & BACKING' 15 278))
$form.Controls.Add((New-Label 'Vocal:' 15 300 45))
$cboVocalMode=New-Combo 63 298 @('auto','forward','spread') 'auto' 95;$form.Controls.Add($cboVocalMode)
$form.Controls.Add((New-Label 'Backing:' 175 300 55))
$cboBackingMode=New-Combo 233 298 @('blend','choir','halo','rear') 'blend' 100;$form.Controls.Add($cboBackingMode)
$form.Controls.Add((New-Label 'Backing lvl:' 350 300 70))
$cboBackingGain=New-Combo 424 298 @('auto','0','-3','-6','-9','-12') 'auto' 90;$form.Controls.Add($cboBackingGain)

# --- balance ---
$form.Controls.Add((New-Head 'BALANCE' 15 330))
$form.Controls.Add((New-Label 'Rear below front:' 15 352 105))
$txtRearBelow=Style-Text (New-Object System.Windows.Forms.TextBox);$txtRearBelow.Location='122,350';$txtRearBelow.Size='45,24';$txtRearBelow.Text='16';$txtRearBelow.TextAlign='Center';$form.Controls.Add($txtRearBelow)
$form.Controls.Add((New-Label 'dB' 172 352 24))
$form.Controls.Add((New-Label 'Rear offset:' 210 352 72))
$txtRear=Style-Text (New-Object System.Windows.Forms.TextBox);$txtRear.Location='284,350';$txtRear.Size='45,24';$txtRear.Text='0';$txtRear.TextAlign='Center';$form.Controls.Add($txtRear)
$form.Controls.Add((New-Label 'dB' 334 352 24))

# --- advanced ---
$form.Controls.Add((New-Head 'ADVANCED' 15 382))
$form.Controls.Add((New-Label 'LFE Hz:' 15 404 55))
$txtLfe=Style-Text (New-Object System.Windows.Forms.TextBox);$txtLfe.Location='72,402';$txtLfe.Size='48,24';$txtLfe.Text='120';$txtLfe.TextAlign='Center';$form.Controls.Add($txtLfe)
$form.Controls.Add((New-Label 'Norm dBFS:' 135 404 72))
$txtNorm=Style-Text (New-Object System.Windows.Forms.TextBox);$txtNorm.Location='209,402';$txtNorm.Size='48,24';$txtNorm.Text='-0.1';$txtNorm.TextAlign='Center';$form.Controls.Add($txtNorm)
$form.Controls.Add((New-Label 'Resample:' 272 404 62))
$cboRate=New-Combo 336 402 @('keep','44100','48000','96000') 'keep' 82;$form.Controls.Add($cboRate)
$chkLoud=Style-Check (New-Object System.Windows.Forms.CheckBox);$chkLoud.Text='Loudness match';$chkLoud.Location='430,404';$chkLoud.Size='140,24';if(-not $HaveFFmpeg){$chkLoud.Enabled=$false};$form.Controls.Add($chkLoud)
$chkKeep=Style-Check (New-Object System.Windows.Forms.CheckBox);$chkKeep.Text='keep stems';$chkKeep.Location='15,430';$chkKeep.Size='120,24';$form.Controls.Add($chkKeep)

# --- buttons ---
$btnRun=New-Object System.Windows.Forms.Button;$btnRun.Text='Start';$btnRun.Location='15,462';$btnRun.Size='150,34';$btnRun.Font=New-Object System.Drawing.Font('Segoe UI',10,[System.Drawing.FontStyle]::Bold);Style-Button $btnRun -Accent|Out-Null;$btnRun.ForeColor=[System.Drawing.Color]::White;$form.Controls.Add($btnRun)
$btnOpen=New-Object System.Windows.Forms.Button;$btnOpen.Text='Open output folder';$btnOpen.Location='175,464';$btnOpen.Size='150,30';$btnOpen.Enabled=$false;Style-Button $btnOpen|Out-Null;$form.Controls.Add($btnOpen)
$btnDemucs=New-Object System.Windows.Forms.Button;$btnDemucs.Text='Demucs setup...';$btnDemucs.Location='484,464';$btnDemucs.Size='150,30';Style-Button $btnDemucs|Out-Null;$form.Controls.Add($btnDemucs)

# --- log ---
$log=New-Object System.Windows.Forms.TextBox;$log.Location='15,504';$log.Size='634,342';$log.Multiline=$true;$log.ScrollBars='Vertical';$log.ReadOnly=$true
$log.BackColor=[System.Drawing.Color]::FromArgb(22,23,25);$log.ForeColor=[System.Drawing.Color]::Gainsboro;$log.BorderStyle='FixedSingle';$log.Font=New-Object System.Drawing.Font('Consolas',9);$form.Controls.Add($log)

# ---------------------------------------------------------------- logic
$script:DemucsCmd=$null;$script:proc=$null;$script:outLog=$null;$script:errLog=$null;$script:lastOutFolder=$null
function Refresh-Demucs{ $script:DemucsCmd=Detect-Demucs
    if($script:DemucsCmd){$lblDemucs.Text="Demucs ready ($($script:DemucsCmd))"+$(if($HaveSplit){'  |  lead/backing splitter ready'}else{''});$lblDemucs.ForeColor=$C_Ok}
    else{$lblDemucs.Text='Demucs not installed - click "Demucs setup..."';$lblDemucs.ForeColor=$C_Err} }
function Update-View{
    $song=$rbSong.Checked;$lblPath.Text=if($song){'Song file:'}else{'Stems folder:'}
    foreach($c in @($cboModel,$cboDevice,$lblModel,$lblDev,$lblDemucs,$chkSplit,$cboVocalMode)){$c.Enabled=$song}
    if($song -and -not $HaveSplit){$chkSplit.Enabled=$false}
    $p=$txtInput.Text
    if($song){
        if($p -and (Test-Path -LiteralPath $p)){$lblDetect.Text="Song: $(Split-Path -Leaf $p)";$lblDetect.ForeColor=$C_Ok}
        else{$lblDetect.Text='Pick a stereo song file (flac/wav/mp3/m4a...).';$lblDetect.ForeColor=$C_Dim}
    } else {
        if($p -and (Test-Path -LiteralPath $p)){ $found=Detect-Stems $p
            if($found.Count){$lblDetect.Text="Found: $($found -join ', ')";$lblDetect.ForeColor=$C_Ok}else{$lblDetect.Text='No stems here (need bass/drums/vocals/other).';$lblDetect.ForeColor=$C_Err}
        } else {$lblDetect.Text='Pick a folder with bass/drums/vocals/other.';$lblDetect.ForeColor=$C_Dim}
    }
}
$rbSong.Add_CheckedChanged({ Update-View });$rbStems.Add_CheckedChanged({ Update-View });$txtInput.Add_TextChanged({ Update-View })
$btnBrowse.Add_Click({
    if($rbSong.Checked){ $d=New-Object System.Windows.Forms.OpenFileDialog;$d.Filter='Audio/Video|*.flac;*.wav;*.mp3;*.m4a;*.aac;*.ogg;*.opus;*.mkv;*.mp4;*.wma|All files|*.*';if($d.ShowDialog()-eq'OK'){$txtInput.Text=$d.FileName} }
    else{ $d=New-Object System.Windows.Forms.FolderBrowserDialog;$d.Description='Folder with bass.flac, drums.flac, ...';if($d.ShowDialog()-eq'OK'){$txtInput.Text=$d.SelectedPath} } })
$btnOutBrowse.Add_Click({ $d=New-Object System.Windows.Forms.FolderBrowserDialog;if($d.ShowDialog()-eq'OK'){$txtOut.Text=$d.SelectedPath} })
$btnDemucs.Add_Click({ Refresh-Demucs
    if($script:DemucsCmd){[System.Windows.Forms.MessageBox]::Show("Demucs is installed and ready:`n$($script:DemucsCmd)`n`nLead/backing splitter: $(if($HaveSplit){'ready'}else{'not installed'})","Demucs")|Out-Null}
    else{[System.Windows.Forms.MessageBox]::Show("Demucs is not installed. Open PowerShell and run:`n`n  py -3.10 -m pip install -U demucs`n`nFor NVIDIA GPU first:`n  py -3.10 -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121`n`nThen click 'Demucs setup...' again.","Install Demucs")|Out-Null} })

function Read-Shared($path){ if(-not $path -or -not (Test-Path -LiteralPath $path)){return ''}
    try{ $fs=[System.IO.File]::Open($path,[System.IO.FileMode]::Open,[System.IO.FileAccess]::Read,[System.IO.FileShare]::ReadWrite);$sr=New-Object System.IO.StreamReader($fs);$t=$sr.ReadToEnd();$sr.Close();$fs.Close();return $t }catch{ return '' } }
$timer=New-Object System.Windows.Forms.Timer;$timer.Interval=400
$timer.Add_Tick({
    if($script:outLog){ $text=Read-Shared $script:outLog;$e=Read-Shared $script:errLog;if($e.Trim()){$text+="`r`n"+$e}
        if($log.Text -ne $text){$log.Text=$text;$log.SelectionStart=$log.Text.Length;$log.ScrollToCaret()} }
    if($script:proc -and $script:proc.HasExited){ $timer.Stop();$code=$script:proc.ExitCode;$log.AppendText("`r`n=== finished (exit $code) ===`r`n")
        $btnRun.Enabled=$true;$btnRun.Text='Start';if($code-eq0 -and $script:lastOutFolder){$btnOpen.Enabled=$true};$script:proc=$null } })

function Get-Num($text,$default,$lo,$hi){ $v=$default;[void][double]::TryParse(($text -replace ',','.'),[Globalization.NumberStyles]::Float,[Globalization.CultureInfo]::InvariantCulture,[ref]$v);if($v -lt $lo){$v=$lo};if($v -gt $hi){$v=$hi};return $v }
function Fmt($v){ [string]::Format([Globalization.CultureInfo]::InvariantCulture,'{0:0.#}',$v) }
function Build-CommonArgs{
    $fmt=if($rb712.Checked){'7.1.2'}elseif($rb71.Checked){'7.1'}else{'5.1'}
    $rate=if($cboRate.SelectedItem -eq 'keep'){'0'}else{"$($cboRate.SelectedItem)"}
    $lfe=[int](Get-Num $txtLfe.Text 120 40 250);$norm=Get-Num $txtNorm.Text -0.1 -12 0
    $rearOff=Get-Num $txtRear.Text 0 -18 18;$rearBelow=Get-Num $txtRearBelow.Text 16 0 30
    $a=@('-OutputFormat',$fmt,'-Preset',"$($cboPreset.SelectedItem)",'-BackingMode',"$($cboBackingMode.SelectedItem)",
         '-RearBelowFront',(Fmt $rearBelow),'-RearGain',(Fmt $rearOff),'-LfeCrossover',"$lfe",'-NormLevel',(Fmt $norm),'-OutputRate',$rate)
    if($chkLoud.Checked){$a+='-LoudnessMatch'}
    if($txtName.Text.Trim()){$a+=@('-TrackLabel',"`"$($txtName.Text.Trim())`"")}
    if($txtOut.Text.Trim()){$a+=@('-OutputPath',"`"$($txtOut.Text.Trim())`"")}
    return $a }

$btnRun.Add_Click({
    $inp=$txtInput.Text;$fmt=if($rb712.Checked){'7.1.2'}elseif($rb71.Checked){'7.1'}else{'5.1'}
    if(-not $inp -or -not (Test-Path -LiteralPath $inp)){[System.Windows.Forms.MessageBox]::Show('Please choose a valid input first.','SurroundUpmix')|Out-Null;return}
    $bgSel="$($cboBackingGain.SelectedItem)"
    if($rbSong.Checked){
        Refresh-Demucs; if(-not $script:DemucsCmd){[System.Windows.Forms.MessageBox]::Show('Demucs is not installed. Click "Demucs setup...".','SurroundUpmix')|Out-Null;return}
        $a=@('-NoProfile','-ExecutionPolicy','Bypass','-File',"`"$EngineAll`"","`"$inp`"",
             '-Model',"$($cboModel.SelectedItem)",'-Device',"$($cboDevice.SelectedItem)",
             '-SplitVocals',$(if($chkSplit.Checked){'on'}else{'off'}),
             '-VocalMode',"$($cboVocalMode.SelectedItem)",'-BackingGain',$bgSel) + (Build-CommonArgs)
        if($chkKeep.Checked){$a+='-KeepStems'}
        $baseDir=Split-Path -Parent $inp
    } else {
        if((Detect-Stems $inp).Count -eq 0){[System.Windows.Forms.MessageBox]::Show('That folder has no stems.','SurroundUpmix')|Out-Null;return}
        $a=@('-NoProfile','-ExecutionPolicy','Bypass','-File',"`"$Engine`"","`"$inp`"") + (Build-CommonArgs)
        if($cboVocalMode.SelectedItem -ne 'auto'){$a+=@('-VocalMode',"$($cboVocalMode.SelectedItem)")}
        $a+=@('-BackingGain',$(if($bgSel -eq 'auto'){'-6'}else{$bgSel}))
        $baseDir=Split-Path -Parent $inp
    }
    $script:lastOutFolder=if($txtOut.Text.Trim()){$txtOut.Text.Trim()}else{Join-Path $baseDir "Final_$fmt"}
    $script:outLog=[System.IO.Path]::GetTempFileName();$script:errLog=[System.IO.Path]::GetTempFileName()
    $log.Text="Starting...`r`n";$btnRun.Enabled=$false;$btnRun.Text='Working...';$btnOpen.Enabled=$false
    $script:proc=Start-Process powershell.exe -ArgumentList $a -RedirectStandardOutput $script:outLog -RedirectStandardError $script:errLog -WindowStyle Hidden -PassThru
    $timer.Start() })
$btnOpen.Add_Click({ if($script:lastOutFolder -and (Test-Path -LiteralPath $script:lastOutFolder)){Start-Process explorer.exe $script:lastOutFolder} })

if($args.Count -ge 1 -and (Test-Path -LiteralPath $args[0])){ $txtInput.Text=$args[0];if(Test-Path -LiteralPath $args[0] -PathType Container){$rbStems.Checked=$true} }
$form.Add_Shown({ try{$v=1;[Win32.Dwm]::DwmSetWindowAttribute($form.Handle,20,[ref]$v,4)|Out-Null}catch{}; Refresh-Demucs; Update-View })
[void]$form.ShowDialog()
