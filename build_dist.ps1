#Requires -Version 5.1
<#
.SYNOPSIS
    Build a self-contained distributable of Singnova Karaoke.

.DESCRIPTION
    Produces a folder (and optional zip) that end-users can run without
    installing anything. Double-click the exe, all 18k songs are available,
    YouTube content downloads on the fly.

    Pre-requisites on the BUILD machine (not required on end-user machines):
      - Unity 6000.1.x  (auto-detected via ProjectVersion.txt)
      - Python 3.10+    (in PATH)
      - .NET SDK 8+     (in PATH or auto-installed by build.ps1)
      - yt-dlp          (in PATH, or will be downloaded automatically)
      - ffmpeg           (in PATH, e.g. via choco install ffmpeg)
      - PyInstaller     (auto-installed if missing)
      - songs-pipeline/config.json with valid USDB credentials
      - songs-pipeline/songs.db    (pre-built catalog)
      - songs-pipeline/downloads/  (pre-generated stubs)

.PARAMETER SkipUnityBuild
    Skip the Unity build step (use existing Build/ output).

.PARAMETER SkipPyInstaller
    Skip PyInstaller compilation (use existing .exe files).

.PARAMETER Zip
    Zip the output folder after build.

.EXAMPLE
    .\build_dist.ps1
    .\build_dist.ps1 -SkipUnityBuild -Zip
#>
[CmdletBinding()]
param(
    [switch]$SkipUnityBuild,
    [switch]$SkipPyInstaller,
    [switch]$Zip
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"
$RepoRoot = $PSScriptRoot

function Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "  OK: $msg"   -ForegroundColor Green }
function Warn($msg) { Write-Host "  WARN: $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Error "FAILED: $msg"; exit 1 }

# ── 1. Unity build ───────────────────────────────────────────────────────────
if (-not $SkipUnityBuild) {
    Step "Building Unity game (Windows 64)"
    & "$RepoRoot\build.ps1" BuildMainGameWindows64
    if ($LASTEXITCODE -ne 0) { Fail "Unity build failed. Check Build/*.log" }
    Ok "Unity build complete"
} else {
    Warn "Skipping Unity build"
}

# ── 2. Find build output folder ───────────────────────────────────────────────
Step "Locating build output"
$buildBase = "$RepoRoot\Build"
$distDir = Get-ChildItem $buildBase -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match "Win64" } |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1 -ExpandProperty FullName

if (-not $distDir) { Fail "No Win64 build folder found under $buildBase" }
Ok "Dist folder: $distDir"

# ── 3. PyInstaller ────────────────────────────────────────────────────────────
$pipelineDir = "$RepoRoot\songs-pipeline"

if (-not $SkipPyInstaller) {
    Step "Installing PyInstaller"
    & python -m pip install pyinstaller --quiet --upgrade
    if ($LASTEXITCODE -ne 0) { Fail "pip install pyinstaller failed" }

    foreach ($script in @("api_server", "fetch_song")) {
        Step "Compiling $script.exe"
        & python -m PyInstaller `
            --onefile `
            --name $script `
            --distpath "$distDir" `
            --workpath "$RepoRoot\.nuke\pyinstaller-work\$script" `
            --specpath "$RepoRoot\.nuke\pyinstaller-specs" `
            "$pipelineDir\scripts\$script.py"
        if ($LASTEXITCODE -ne 0) { Fail "PyInstaller failed for $script.py" }
        Ok "$script.exe → $distDir"
    }
} else {
    Warn "Skipping PyInstaller"
}

# ── 4. songs.db ───────────────────────────────────────────────────────────────
Step "Copying songs database"
$dbSrc = "$pipelineDir\songs.db"
if (Test-Path $dbSrc) {
    Copy-Item $dbSrc "$distDir\songs.db" -Force
    Ok "songs.db copied ($([math]::Round((Get-Item $dbSrc).Length / 1MB, 1)) MB)"
} else {
    Fail "songs.db not found at $dbSrc — run bulk_index.py first"
}

# ── 5. config.json (USDB credentials for on-demand TXT download) ──────────────
Step "Copying config"
$cfgSrc = "$pipelineDir\config.json"
if (Test-Path $cfgSrc) {
    Copy-Item $cfgSrc "$distDir\config.json" -Force
    Ok "config.json copied (contains USDB credentials)"
} else {
    Warn "config.json not found — on-demand downloads will fail. Create $cfgSrc first."
}

# ── 6. Stubs (downloads/) ────────────────────────────────────────────────────
Step "Copying song stubs"
$stubsSrc = "$pipelineDir\downloads"
$stubsDst = "$distDir\downloads"

if (Test-Path $stubsSrc) {
    $stubCount = (Get-ChildItem $stubsSrc -Recurse -File).Count
    Write-Host "  Copying $stubCount files (this may take a minute)..."
    & robocopy $stubsSrc $stubsDst /E /NFL /NDL /NJH /NJS /NC /NS /NP | Out-Null
    Ok "Stubs copied"
} else {
    Warn "downloads/ not found. Run: python songs-pipeline/scripts/generate_stubs.py"
}

# ── 7. yt-dlp.exe ────────────────────────────────────────────────────────────
Step "Bundling yt-dlp"
$ytdlpDst = "$distDir\yt-dlp.exe"
$ytdlpSrc = (Get-Command yt-dlp -ErrorAction SilentlyContinue)?.Source
if ($ytdlpSrc) {
    Copy-Item $ytdlpSrc $ytdlpDst -Force
    Ok "yt-dlp copied from $ytdlpSrc"
} else {
    Write-Host "  yt-dlp not in PATH — downloading latest release..."
    $ytdlpUrl = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
    Invoke-WebRequest -Uri $ytdlpUrl -OutFile $ytdlpDst -UseBasicParsing
    Ok "yt-dlp downloaded"
}

# ── 8. ffmpeg.exe ─────────────────────────────────────────────────────────────
Step "Bundling ffmpeg"
$ffmpegDst = "$distDir\ffmpeg.exe"
$ffmpegSrc = (Get-Command ffmpeg -ErrorAction SilentlyContinue)?.Source
if ($ffmpegSrc) {
    Copy-Item $ffmpegSrc $ffmpegDst -Force
    Ok "ffmpeg copied from $ffmpegSrc"
} else {
    Warn "ffmpeg not found in PATH. Install via: choco install ffmpeg"
    Warn "Without ffmpeg, audio extraction from video will fail."
    Warn "Manually copy ffmpeg.exe to: $distDir"
}

# ── 9. Optional zip ──────────────────────────────────────────────────────────
if ($Zip) {
    Step "Creating zip archive"
    $zipPath = "$distDir.zip"
    if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::CreateFromDirectory($distDir, $zipPath)
    $zipMB = [math]::Round((Get-Item $zipPath).Length / 1MB, 0)
    Ok "Archive: $zipPath ($zipMB MB)"
}

# ── Done ─────────────────────────────────────────────────────────────────────
Step "Build complete"
Write-Host ""
Write-Host "  Distribution: $distDir" -ForegroundColor White
Write-Host ""
Write-Host "  End-user instructions:" -ForegroundColor White
Write-Host "    1. Copy the folder to any Windows PC"
Write-Host "    2. Double-click the game .exe"
Write-Host "    3. All 18k songs listed immediately"
Write-Host "    4. Clicking an undownloaded song fetches video+audio automatically"
Write-Host ""
Get-ChildItem $distDir -File | Format-Table Name, @{L="Size MB";E={[math]::Round($_.Length/1MB,1)}}
