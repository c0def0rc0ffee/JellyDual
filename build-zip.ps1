# SUPERSEDED on 12/09/2026 by build-zip.sh in this folder (Linux build). Kept for reference only, do not run.
# Builds two versioned zips straight from the project root, plus the local
# runnable copy:
#   JellyDualPlay Dist\service.jellyfin.dualplay-v<version>.zip      - the Kodi-installable addon
#   JellyDualPlay Git\service.jellyfin.dualplay-v<version>-src.zip   - GitHub-bound source
#                                                                      (addon + repo housekeeping)
#   JellyDualPlay App\                                               - mirror of the Dist zip contents
#
# This is an application project, not a web project, so point 5's local run
# copy is "JellyDualPlay App" and there is no WAMP mirror.
#
# Run from the repo root:  powershell -File build-zip.ps1
$ErrorActionPreference = 'Stop'

$root    = Split-Path -Parent $MyInvocation.MyCommand.Path
$version = (Get-Content (Join-Path $root 'VERSION') -Raw).Trim()
$distDir = Join-Path $root 'JellyDualPlay Dist'
$gitDir  = Join-Path $root 'JellyDualPlay Git'
$appDir  = Join-Path $root 'JellyDualPlay App'
$stage   = Join-Path $env:TEMP 'jdp-build'

# Kodi identifies the addon by its id, so the zips are named after that rather
# than the folder name.
$product = 'service.jellyfin.dualplay'
$addonXml = Join-Path $root "$product\addon.xml"

# Files that live in the repo but are never part of the installable addon.
$repoOnlyFiles = @('README.md', 'CHANGELOG.md', 'SETUP.md', 'VERSION',
                   '.gitignore', '.gitattributes', 'build-zip.ps1', '*.example.*')

# Excluded from BOTH zips. There are no secret files in this project, so the
# follower's credentials are typed into Kodi's addon settings and live in Kodi
# userdata, never here. Add any future secret to this list by name.
$secretFiles = @('*.tmp', '*.zip', '*.7z')
$excludeDirs = @('JellyDualPlay Dist', 'JellyDualPlay Git', 'JellyDualPlay App',
                 '$RECYCLE.BIN', '__pycache__')
# Plus every dot folder in the root (version control metadata, editor and
# tooling state), so nothing local can leak into a built zip.
$excludeDirs += @(Get-ChildItem -Path $root -Directory -Force -Filter '.*' |
                  Where-Object { $_.Name -ne '.github' } |
                  Select-Object -ExpandProperty FullName)

# Off-device test suite: belongs in the repo, not in the installable addon.
$repoOnlyDirs = @('tests')

foreach ($d in @($distDir, $gitDir)) {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d | Out-Null }
}

# Stamp the VERSION number into addon.xml so Kodi's version can never drift
# from the zip filename.
$xml = Get-Content $addonXml -Raw
$stamped = $xml -replace '(<addon\s[^>]*?\bversion=")[^"]*(")', ('${1}' + $version + '${2}')
if ($stamped -ne $xml) {
    [System.IO.File]::WriteAllText($addonXml, $stamped, (New-Object System.Text.UTF8Encoding $false))
    Write-Output "Stamped v$version into addon.xml"
}

if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }

function Stage-And-Zip {
    # NB: param must not be named ExcludeDirs, because PS variable names are
    # case-insensitive, so it would shadow the script-level $excludeDirs.
    param([string]$StageName, [string[]]$ExcludeFiles, [string[]]$ExtraExcludeDirs, [string]$ZipPath)

    $target = Join-Path $stage $StageName
    $xd = $script:excludeDirs + $ExtraExcludeDirs
    robocopy $root $target /E /XF @ExcludeFiles /XD @xd /NFL /NDL /NJH | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed staging $StageName (exit $LASTEXITCODE)" }

    if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
    # ZipFile rather than Compress-Archive: Windows PowerShell 5.1's
    # Compress-Archive writes sub-folder entries with backslash separators,
    # which Kodi's zip reader will not install. CreateFromDirectory always
    # writes forward slashes.
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::CreateFromDirectory(
        $target, $ZipPath, [System.IO.Compression.CompressionLevel]::Optimal, $false)

    $size = [math]::Round((Get-Item $ZipPath).Length / 1KB, 1)
    Write-Output "Built: $ZipPath ($size KB)"
}

# Dist zip: the addon folder only, installable as-is via
# Settings > Add-ons > Install from zip file.
Stage-And-Zip -StageName 'deploy' `
    -ExcludeFiles ($secretFiles + $repoOnlyFiles) `
    -ExtraExcludeDirs $repoOnlyDirs `
    -ZipPath (Join-Path $distDir "$product-v$version.zip")

# Local runnable copy: an exact mirror of the Dist zip contents.
# /MIR removes anything not in the stage, so never hand-edit this folder,
# fix the source and rebuild.
robocopy (Join-Path $stage 'deploy') $appDir /MIR /NFL /NDL /NJH | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed mirroring $appDir (exit $LASTEXITCODE)" }
Write-Output "Mirrored: $appDir"

# Source zip: addon + repo housekeeping.
Stage-And-Zip -StageName 'src' `
    -ExcludeFiles $secretFiles `
    -ExtraExcludeDirs @() `
    -ZipPath (Join-Path $gitDir "$product-v$version-src.zip")

Remove-Item $stage -Recurse -Force
