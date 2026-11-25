param(
    [switch]$SkipImages
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$archive = Join-Path $PSScriptRoot 'offline-images.tar.gz'
$temporary = Join-Path $root '.local/desktop-build'
New-Item -ItemType Directory -Force -Path $temporary | Out-Null
$envFile = Join-Path $temporary 'build.env'

@'
POSTGRES_DB=spectralindex
POSTGRES_USER=spectralindex
POSTGRES_PASSWORD=desktop-build-only
MINIO_ROOT_USER=spectraldesktop
MINIO_ROOT_PASSWORD=desktop-build-only
DESKTOP_WEB_ORIGIN=http://127.0.0.1:58000
DESKTOP_STORAGE_URL=http://127.0.0.1:58003
'@ | Set-Content -LiteralPath $envFile -Encoding ascii

Push-Location $root
try {
    npm run build --workspace @spectral-index/web -- --mode desktop
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao compilar o frontend desktop.' }

    if (-not $SkipImages) {
        docker build -f apps/desktop/desktop.Dockerfile -t spectral-index-desktop:0.6.0 .
        if ($LASTEXITCODE -ne 0) { throw 'Falha ao compilar as imagens Docker.' }

        $tarFile = Join-Path $temporary 'images.tar'
        docker image save -o $tarFile spectral-index-desktop:0.6.0 postgres:17-alpine redis:8-alpine minio/minio:RELEASE.2025-09-07T16-13-09Z
        if ($LASTEXITCODE -ne 0) { throw 'Falha ao exportar as imagens Docker.' }
        try {
            $inputStream = [System.IO.File]::OpenRead($tarFile)
            try {
                $outputStream = [System.IO.File]::Create($archive)
                try {
                    $gzip = [System.IO.Compression.GZipStream]::new($outputStream, [System.IO.Compression.CompressionLevel]::Optimal)
                    try { $inputStream.CopyTo($gzip) } finally { $gzip.Dispose() }
                } finally { $outputStream.Dispose() }
            } finally { $inputStream.Dispose() }
        } finally { Remove-Item -LiteralPath $tarFile -ErrorAction SilentlyContinue }
    }

    if (-not (Test-Path -LiteralPath $archive)) { throw 'Arquivo de imagens offline não encontrado.' }
    npm install --prefix apps/desktop --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao instalar dependências do Electron.' }
    npm run installer --prefix apps/desktop
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao criar o instalador.' }
} finally {
    Pop-Location
}
