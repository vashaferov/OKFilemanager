# OziAPI bridge: loads the 32-bit OziAPI.dll inside the 32-bit Windows
# PowerShell and drives the already running OziExplorer.
#
# Why: OziAPI.dll (like OziExplorer itself) is a 32-bit binary. The
# application runs on 64-bit Python, which cannot load a 32-bit DLL
# (WinError 193). The 32-bit PowerShell that ships with Windows can,
# so it hosts the DLL and talks to us over stdin/stdout.
#
# NOTE: this file must stay ASCII only. Windows PowerShell reads .ps1
# files using the ANSI code page, so non-ASCII text would break it.
#
# Protocol, one command per line on stdin:
#     <action><TAB><path><TAB><track num>
#     action = wp | track | map | find | ping | quit
#     addwpts<TAB><json array of waypoints>   create points one by one
# Answers, one line on stdout:
#     OZI_BRIDGE_READY=<api version>     once, after loading the DLL
#     OZI_RESULT found=<n> rc=<n> [created=<n> failed=<n>]
#     OZI_PONG                           for action "ping"
# After loading a track the bridge turns the track display on and
# redraws the map: except for track 1 OziExplorer keeps tracks hidden,
# so a track loaded into another number would not be visible.
param(
    [Parameter(Mandatory = $true)][string]$Dll
)

try {
    [Console]::InputEncoding = New-Object System.Text.UTF8Encoding $false
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
} catch { }

function Send([string]$text) {
    [Console]::Out.WriteLine($text)
    [Console]::Out.Flush()
}

if (-not (Test-Path -LiteralPath $Dll)) {
    Send('OZI_BRIDGE_ERROR=dll not found')
    exit 2
}
$dllPath = (Resolve-Path -LiteralPath $Dll).Path

# Calling convention and the "var ...: pansichar" parameters (char**) are
# taken from the official OziAPI documentation (version 1.09).
$src = @"
using System;
using System.Runtime.InteropServices;
public static class OziApi {
    private const string D = @"$dllPath";
    [DllImport(D, CallingConvention = CallingConvention.StdCall)]
    public static extern int oziAPIsetTimeout(int ms);
    [DllImport(D, CallingConvention = CallingConvention.StdCall)]
    public static extern int oziFindOzi();
    [DllImport(D, CallingConvention = CallingConvention.StdCall)]
    public static extern int oziGetApiVersion(IntPtr version,
                                               IntPtr dataLength);
    [DllImport(D, CallingConvention = CallingConvention.StdCall)]
    public static extern int oziLoadWPfile(IntPtr fileName);
    [DllImport(D, CallingConvention = CallingConvention.StdCall)]
    public static extern int oziLoadTrackFile(int trackNum, IntPtr fileName);
    [DllImport(D, CallingConvention = CallingConvention.StdCall)]
    public static extern int oziLoadMap(IntPtr mapName);
    [DllImport(D, CallingConvention = CallingConvention.StdCall)]
    public static extern int oziShowTrack(int trackNum);
    [DllImport(D, CallingConvention = CallingConvention.StdCall)]
    public static extern int oziShowAllTracks();
    [DllImport(D, CallingConvention = CallingConvention.StdCall)]
    public static extern int oziRefreshMap();
    [DllImport(D, CallingConvention = CallingConvention.StdCall)]
    public static extern int oziCreateWPex(IntPtr name, int symbol,
        double lat, double lon, double altitude, double wpDate,
        int mapDisplayFormat, int pointerDirection, int garminDisplayFormat,
        int foreColor, int backColor, int proximityDistance,
        IntPtr description, int fontSize, int fontStyle, int symbolSize,
        int proximitySymbolPos, double proximityTime, int proximityRoute,
        IntPtr fileAttachment, IntPtr proximityFileAttachment,
        IntPtr proximitySymbolName);
    [DllImport(D, CallingConvention = CallingConvention.StdCall)]
    public static extern int oziCloseApi();
}
"@

try {
    Add-Type -TypeDefinition $src -ErrorAction Stop
} catch {
    Send('OZI_BRIDGE_ERROR=' + $_.Exception.GetType().Name)
    exit 3
}

$M = [System.Runtime.InteropServices.Marshal]

# (var s: pansichar) is a pointer to the caller's pointer, so we hand the
# DLL the address of a slot that holds a pointer to the ANSI string.
#
# The allocations are deliberately not freed: the DLL may still be using
# the string when the call returns after a timeout, and the leak is a few
# dozen bytes per loaded file.
function New-StrSlot([string]$s) {
    $p = $M::StringToHGlobalAnsi($s)
    $slot = $M::AllocHGlobal([IntPtr]::Size)
    $M::WriteIntPtr($slot, $p)
    return $slot
}

function Get-ApiVersion {
    try {
        $slot = $M::AllocHGlobal([IntPtr]::Size)
        $M::WriteIntPtr($slot, [IntPtr]::Zero)
        $len = $M::AllocHGlobal(4)
        $M::WriteInt32($len, 0)
        $null = [OziApi]::oziGetApiVersion($slot, $len)
        $p = $M::ReadIntPtr($slot)
        if ($p -ne [IntPtr]::Zero) { return $M::PtrToStringAnsi($p) }
    } catch { }
    return '?'
}

Send('OZI_BRIDGE_READY=' + (Get-ApiVersion))

# Creates waypoints one by one: oziLoadWPfile would replace the set
# already loaded into OziExplorer, while oziCreateWPex appends to it.
# Returns a hashtable with the counters (rc > 0 means the point was
# created - the function returns its internal number).
function Add-Waypoints([string]$json) {
    $created = 0
    $failed = 0
    # foreach, not @($json | ConvertFrom-Json): PowerShell 5.1 writes the
    # deserialized array as a single object, so @() would wrap all points
    # into one element and every field access ([int]$p.symbol and so on)
    # would fail - a multi point file would add nothing at all.
    foreach ($p in ($json | ConvertFrom-Json)) {
        try {
            $rcWp = [OziApi]::oziCreateWPex(
                (New-StrSlot ([string]$p.name)),
                [int]$p.symbol, [double]$p.lat, [double]$p.lon,
                [double]$p.altitude, [double]$p.date,
                [int]$p.mapDisplayFormat, [int]$p.pointerDirection,
                [int]$p.garminDisplayFormat, [int]$p.foreColor,
                [int]$p.backColor, [int]$p.proximityDistance,
                (New-StrSlot ([string]$p.description)),
                [int]$p.fontSize, [int]$p.fontStyle, [int]$p.symbolSize,
                [int]$p.proximitySymbolPos, [double]$p.proximityTime,
                [int]$p.proximityRoute,
                (New-StrSlot ''), (New-StrSlot ''), (New-StrSlot ''))
        } catch {
            $rcWp = -3
        }
        if ($rcWp -gt 0) { $created++ } else { $failed++ }
    }
    return @{ created = $created; failed = $failed }
}

while ($true) {
    $line = [Console]::In.ReadLine()
    if ($null -eq $line) { break }
    $line = $line.Trim()
    if ($line -eq '') { continue }
    if ($line -eq 'quit') { break }

    $parts = $line.Split("`t")
    $action = $parts[0].Trim().ToLower()
    if ($action -eq 'ping') {
        Send('OZI_PONG')
        continue
    }

    $path = ''
    $track = 1
    if ($parts.Length -gt 1) { $path = $parts[1] }
    if ($parts.Length -gt 2) { [void][int]::TryParse($parts[2], [ref]$track) }

    $found = -1
    $rc = -1
    $created = 0
    $failed = 0
    try {
        $null = [OziApi]::oziAPIsetTimeout(5000)
        $found = [OziApi]::oziFindOzi()
        if ($found -eq 0) {
            switch ($action) {
                'wp' {
                    $rc = [OziApi]::oziLoadWPfile((New-StrSlot $path))
                    # Redraw, otherwise the new points may not show up on
                    # the map until the next action.
                    if ($rc -eq 0) { $null = [OziApi]::oziRefreshMap() }
                }
                'track' {
                    $rc = [OziApi]::oziLoadTrackFile($track,
                                                     (New-StrSlot $path))
                    if ($rc -eq 0) {
                        # Except for track 1, OziExplorer keeps every track
                        # hidden, so a loaded track would not be visible.
                        $null = [OziApi]::oziShowAllTracks()
                        $null = [OziApi]::oziShowTrack($track)
                        $null = [OziApi]::oziRefreshMap()
                    }
                }
                'map' { $rc = [OziApi]::oziLoadMap((New-StrSlot $path)) }
                'addwpts' {
                    $counts = Add-Waypoints $path
                    $created = $counts['created']
                    $failed = $counts['failed']
                    if ($created -gt 0) {
                        $null = [OziApi]::oziRefreshMap()
                        $rc = 0
                    } else {
                        $rc = -4
                    }
                }
                'find' { $rc = 0 }
                default { $rc = -2 }
            }
        }
    } catch {
        $rc = -3
    }
    $extra = ''
    if ($action -eq 'addwpts') {
        $extra = ' created={0} failed={1}' -f $created, $failed
    }
    Send(('OZI_RESULT found={0} rc={1}{2}' -f $found, $rc, $extra))
}

try { $null = [OziApi]::oziCloseApi() } catch { }
