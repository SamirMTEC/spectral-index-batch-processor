param([switch]$SkipWeb)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../../..')).Path
$build = Join-Path $PSScriptRoot 'build'
$package = Join-Path $build 'webview2.nupkg'
$version = '1.0.4314.35'
New-Item -ItemType Directory -Force -Path $build | Out-Null

if (-not $SkipWeb) {
    Push-Location $root
    try {
        npm run build --workspace @spectral-index/web -- --mode desktop
        if ($LASTEXITCODE -ne 0) { throw 'Falha ao compilar a interface desktop.' }
    } finally { Pop-Location }
}
if (-not (Test-Path (Join-Path $root 'apps/web/dist/index.html'))) { throw 'Interface desktop não encontrada.' }
if (-not (Test-Path (Join-Path $root 'apps/desktop/offline-images.tar.gz'))) { throw 'Imagens Docker offline não encontradas.' }

if (-not (Test-Path $package)) {
    Invoke-WebRequest "https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/$version/microsoft.web.webview2.$version.nupkg" -OutFile $package
}
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [IO.Compression.ZipFile]::OpenRead($package)
try {
    foreach ($entry in $zip.Entries) {
        if ($entry.FullName -in @(
            'lib/net462/Microsoft.Web.WebView2.Core.dll',
            'lib/net462/Microsoft.Web.WebView2.WinForms.dll',
            'runtimes/win-x64/native/WebView2Loader.dll',
            'LICENSE.txt', 'NOTICE.txt'
        )) {
            [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, (Join-Path $build ([IO.Path]::GetFileName($entry.FullName))), $true)
        }
    }
} finally { $zip.Dispose() }

$compiler = 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path $compiler)) { throw 'Compilador .NET Framework do Windows não encontrado.' }
& $compiler /nologo /target:winexe /platform:x64 "/out:$(Join-Path $build 'SpectralIndexDesktop.exe')" /r:System.Web.Extensions.dll "/r:$(Join-Path $build 'Microsoft.Web.WebView2.Core.dll')" "/r:$(Join-Path $build 'Microsoft.Web.WebView2.WinForms.dll')" (Join-Path $PSScriptRoot 'SpectralIndexDesktop.cs')
if ($LASTEXITCODE -ne 0) { throw 'Falha ao compilar a janela nativa.' }

$nsis = Get-ChildItem (Join-Path $env:LOCALAPPDATA 'electron-builder/Cache/nsis-3.0.4.1') -Filter makensis.exe -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
if (-not $nsis) { $nsis = Get-Command makensis.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source }
if (-not $nsis) { throw 'Compilador NSIS não encontrado no cache. Instale o NSIS 3 e adicione makensis.exe ao PATH.' }
Push-Location $PSScriptRoot
try {
    & $nsis installer.nsi
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao criar o instalador.' }
} finally { Pop-Location }
