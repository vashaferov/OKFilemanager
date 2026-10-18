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
# Answers, one line on stdout:
#     OZI_BRIDGE_READY=<api version>     once, after loading the DLL
#     OZI_RESULT found=<n> rc=<n>        per command (0 = OziExplorer found)
#     OZI_PONG                           for action "ping"
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
    try {
        $null = [OziApi]::oziAPIsetTimeout(5000)
        $found = [OziApi]::oziFindOzi()
        if ($found -eq 0) {
            switch ($action) {
                'wp' { $rc = [OziApi]::oziLoadWPfile((New-StrSlot $path)) }
                'track' {
                    $rc = [OziApi]::oziLoadTrackFile($track,
                                                     (New-StrSlot $path))
                }
                'map' { $rc = [OziApi]::oziLoadMap((New-StrSlot $path)) }
                'find' { $rc = 0 }
                default { $rc = -2 }
            }
        }
    } catch {
        $rc = -3
    }
    Send(('OZI_RESULT found={0} rc={1}' -f $found, $rc))
}

try { $null = [OziApi]::oziCloseApi() } catch { }
