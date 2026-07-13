$ErrorActionPreference = "Stop"

$workspace = "F:\Programming\Minlor\LumiSync"
$etl = Join-Path $workspace "govee-ui-capture.etl"
$pcap = Join-Path $workspace "govee-ui-capture.pcapng"
$log = Join-Path $workspace "govee-ui-capture.log"

function Log($msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Add-Content -LiteralPath $log -Value $line
    Write-Output $line
}

Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class Win32Mouse {
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint dwFlags, uint dx, uint dy, uint dwData, UIntPtr dwExtraInfo);
  public const uint LEFTDOWN = 0x0002;
  public const uint LEFTUP = 0x0004;
}
"@

function Get-GoveeWindow {
    $root = [System.Windows.Automation.AutomationElement]::RootElement
    $cond = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::NameProperty,
        "Govee Desktop"
    )
    return $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
}

function Find-ElementById([string]$automationId) {
    $win = Get-GoveeWindow
    if (-not $win) { return $null }
    $cond = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::AutomationIdProperty,
        $automationId
    )
    return $win.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cond)
}

function Find-ElementByIdPrefix([string]$prefix) {
    $win = Get-GoveeWindow
    if (-not $win) { return $null }
    $cond = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::AutomationIdProperty,
        ""
    )
    $all = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
    for ($i = 0; $i -lt $all.Count; $i++) {
        $item = $all.Item($i)
        if ($item.Current.AutomationId -like ($prefix + "*")) {
            return $item
        }
    }
    return $null
}

function Find-ElementByName([string]$name) {
    $win = Get-GoveeWindow
    if (-not $win) { return $null }
    $cond = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::NameProperty,
        $name
    )
    return $win.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cond)
}

function Wait-ElementById([string]$automationId, [int]$timeoutMs = 5000) {
    $deadline = [DateTime]::UtcNow.AddMilliseconds($timeoutMs)
    do {
        $el = Find-ElementById $automationId
        if ($el) { return $el }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)
    return $null
}

function Wait-ElementByIdPrefix([string]$prefix, [int]$timeoutMs = 5000) {
    $deadline = [DateTime]::UtcNow.AddMilliseconds($timeoutMs)
    do {
        $el = Find-ElementByIdPrefix $prefix
        if ($el) { return $el }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)
    return $null
}

function Click-RectCenter($rect) {
    $x = [int]($rect.Left + ($rect.Width / 2))
    $y = [int]($rect.Top + ($rect.Height / 2))
    [Win32Mouse]::SetCursorPos($x, $y) | Out-Null
    Start-Sleep -Milliseconds 120
    [Win32Mouse]::mouse_event([Win32Mouse]::LEFTDOWN, 0, 0, 0, [UIntPtr]::Zero)
    Start-Sleep -Milliseconds 80
    [Win32Mouse]::mouse_event([Win32Mouse]::LEFTUP, 0, 0, 0, [UIntPtr]::Zero)
}

function Click-ElementById([string]$automationId, [string]$label) {
    $el = Wait-ElementById $automationId 4500
    if (-not $el) {
        Log("Missing element: $label ($automationId)")
        return $false
    }
    $rect = $el.Current.BoundingRectangle
    Log("Clicking $label at $([int]$rect.Left),$([int]$rect.Top),$([int]$rect.Width),$([int]$rect.Height)")
    Click-RectCenter $rect
    Start-Sleep -Milliseconds 1800
    return $true
}

function Click-ElementByName([string]$name, [string]$label) {
    $el = Find-ElementByName $name
    if (-not $el) {
        Log("Missing named element: $label ($name)")
        return $false
    }
    $rect = $el.Current.BoundingRectangle
    Log("Clicking $label at $([int]$rect.Left),$([int]$rect.Top),$([int]$rect.Width),$([int]$rect.Height)")
    Click-RectCenter $rect
    Start-Sleep -Milliseconds 2800
    return $true
}

function Click-ElementByIdPrefix([string]$prefix, [string]$label) {
    $el = Wait-ElementByIdPrefix $prefix 6000
    if (-not $el) {
        Log("Missing prefixed element: $label ($prefix*)")
        return $false
    }
    $rect = $el.Current.BoundingRectangle
    Log("Clicking $label at $([int]$rect.Left),$([int]$rect.Top),$([int]$rect.Width),$([int]$rect.Height)")
    Click-RectCenter $rect
    Start-Sleep -Milliseconds 3500
    return $true
}

function Set-SliderFraction([string]$automationId, [double]$fraction, [string]$label) {
    $el = Wait-ElementById $automationId 4500
    if (-not $el) {
        Log("Missing slider: $label ($automationId)")
        return $false
    }
    $rect = $el.Current.BoundingRectangle
    $fraction = [Math]::Max(0.0, [Math]::Min(1.0, $fraction))
    $x = [int]($rect.Left + ($rect.Width * $fraction))
    $y = [int]($rect.Top + ($rect.Height / 2))
    Log("Setting $label to fraction $fraction at $x,$y")
    [Win32Mouse]::SetCursorPos($x, $y) | Out-Null
    Start-Sleep -Milliseconds 120
    [Win32Mouse]::mouse_event([Win32Mouse]::LEFTDOWN, 0, 0, 0, [UIntPtr]::Zero)
    Start-Sleep -Milliseconds 80
    [Win32Mouse]::mouse_event([Win32Mouse]::LEFTUP, 0, 0, 0, [UIntPtr]::Zero)
    Start-Sleep -Milliseconds 1800
    return $true
}

function Set-SliderFractionByPrefix([string]$prefix, [double]$fraction, [string]$label) {
    $el = Wait-ElementByIdPrefix $prefix 6000
    if (-not $el) {
        Log("Missing prefixed slider: $label ($prefix*)")
        return $false
    }
    $rect = $el.Current.BoundingRectangle
    $fraction = [Math]::Max(0.0, [Math]::Min(1.0, $fraction))
    $x = [int]($rect.Left + ($rect.Width * $fraction))
    $y = [int]($rect.Top + ($rect.Height / 2))
    Log("Setting $label to fraction $fraction at $x,$y")
    [Win32Mouse]::SetCursorPos($x, $y) | Out-Null
    Start-Sleep -Milliseconds 150
    [Win32Mouse]::mouse_event([Win32Mouse]::LEFTDOWN, 0, 0, 0, [UIntPtr]::Zero)
    Start-Sleep -Milliseconds 90
    [Win32Mouse]::mouse_event([Win32Mouse]::LEFTUP, 0, 0, 0, [UIntPtr]::Zero)
    Start-Sleep -Milliseconds 2200
    return $true
}

Remove-Item -LiteralPath $etl, $pcap, $log -Force -ErrorAction SilentlyContinue
Log("Starting UI capture run")

pktmon stop | Out-Null
pktmon filter remove | Out-Null
pktmon filter add Lan4001 -t UDP -p 4001 | Out-Null
pktmon filter add Lan4002 -t UDP -p 4002 | Out-Null
pktmon filter add Lan4003 -t UDP -p 4003 | Out-Null
pktmon filter add Mqtt8883 -t TCP -p 8883 | Out-Null
foreach ($ip in @(
    "34.232.112.160",
    "32.195.91.18",
    "100.30.89.201",
    "18.208.10.99",
    "100.51.94.144",
    "52.73.237.8",
    "3.217.239.138",
    "18.213.92.127",
    "54.83.203.128",
    "54.88.245.74",
    "52.23.8.170",
    "3.222.126.108",
    "52.14.84.1",
    "3.20.164.229",
    "54.221.64.69",
    "52.7.129.141"
)) {
    pktmon filter add ("Cloud443-" + $ip.Replace(".", "-")) -i $ip -t TCP -p 443 | Out-Null
}
pktmon start --capture --pkt-size 0 --file-name $etl | Out-Null

try {
    Start-Sleep -Seconds 2
    $win = Get-GoveeWindow
    if (-not $win) {
        throw "Govee Desktop window not found"
    }

    Log("Govee window detected")

    Click-ElementByName "Device List" "Device List nav" | Out-Null
    Click-ElementById "titColor" "Color tab" | Out-Null
    Click-ElementById "imgCardSwitch8913D4ADFCAD3532" "Power toggle" | Out-Null
    Set-SliderFraction "sdBrightness" 0.30 "Device brightness slider" | Out-Null
    Set-SliderFraction "sdBrightness" 0.82 "Device brightness slider" | Out-Null
    Click-ElementById "titScenic" "Scene tab" | Out-Null
    Click-ElementById "titDiy" "My DIY tab" | Out-Null
    Click-ElementById "titSnapshot" "Snapshot tab" | Out-Null
    Click-ElementById "titColor" "Color tab" | Out-Null

    Click-ElementByName "Movie-Watching/Gaming DreamView" "Movie/Gaming DreamView nav" | Out-Null
    Click-ElementByIdPrefix "imgDreamviewSwitch" "DreamView switch" | Out-Null
    Set-SliderFractionByPrefix "sldDreamviewBrightness" 0.35 "DreamView brightness slider" | Out-Null
    Set-SliderFractionByPrefix "sldDreamviewBrightness" 0.78 "DreamView brightness slider" | Out-Null

    Click-ElementByName "Music Dreamview" "Music Dreamview nav" | Out-Null
    Click-ElementByIdPrefix "imgMusicDreamviewSwitch" "Music Dreamview switch" | Out-Null
    Set-SliderFractionByPrefix "sldMusicDreamviewBrightness" 0.40 "Music Dreamview brightness slider" | Out-Null
    Set-SliderFractionByPrefix "sldMusicDreamviewBrightness" 0.84 "Music Dreamview brightness slider" | Out-Null

    Click-ElementByName "Razer" "Razer nav" | Out-Null
    Click-ElementByIdPrefix "imgRazerSwitch" "Razer switch" | Out-Null
    Set-SliderFractionByPrefix "sldRazerBrightness" 0.45 "Razer brightness slider" | Out-Null
    Set-SliderFractionByPrefix "sldRazerBrightness" 0.88 "Razer brightness slider" | Out-Null

    Click-ElementByName "Tap-to-Run" "Tap-to-Run nav" | Out-Null
    Click-ElementByName "Community" "Community nav" | Out-Null
    Click-ElementByName "Device List" "Device List nav" | Out-Null
    Start-Sleep -Seconds 3
}
finally {
    pktmon stop | Out-Null
    pktmon etl2pcap $etl --out $pcap | Out-Null
    pktmon filter remove | Out-Null
    Log("Finished UI capture run")
}
