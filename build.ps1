param([Parameter(Mandatory=$true)][string]$Zig)
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    & $Zig cc -target x86_64-windows-gnu -O2 -Wall -Wextra -shared plugin.c -lws2_32 -ladvapi32 -o mumble_ru.dll
    if ($LASTEXITCODE -ne 0) { throw 'DLL build failed' }
    py -3.11 package.py
    if ($LASTEXITCODE -ne 0) { throw 'Packaging failed' }
} finally { Pop-Location }
