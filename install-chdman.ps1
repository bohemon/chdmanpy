[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$mameVersion = "0.287"
$releaseTag = "mame0287"
$architecture = if ($env:PROCESSOR_ARCHITEW6432) {
    $env:PROCESSOR_ARCHITEW6432
} else {
    $env:PROCESSOR_ARCHITECTURE
}

$package = switch ($architecture) {
    "AMD64" {
        @{
            FileName = "mame0287b_x64.exe"
            Sha256 = "68cdaf6d48213c6f3d0f7fa7f2733db46f74e400ad66db2d8a8d777430a42fb9"
        }
        break
    }
    "ARM64" {
        @{
            FileName = "mame0287b_arm64.exe"
            Sha256 = "dafc600b272a1d68e38dd79f06efc6c37d14ee42659854381771884b050dd4b0"
        }
        break
    }
    default {
        throw "Unsupported Windows architecture: $architecture. Only x64 and Arm64 are supported."
    }
}

$destination = Join-Path $PSScriptRoot "chdman.exe"
$temporaryRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("chdmanpy-" + [guid]::NewGuid())
$archivePath = Join-Path $temporaryRoot $package.FileName
$extractPath = Join-Path $temporaryRoot "mame"
$downloadUrl = "https://github.com/mamedev/mame/releases/download/$releaseTag/$($package.FileName)"

try {
    New-Item -ItemType Directory -Path $extractPath -Force | Out-Null

    Write-Host "Downloading MAME $mameVersion for $architecture..."
    Invoke-WebRequest -Uri $downloadUrl -OutFile $archivePath -UseBasicParsing

    $actualHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash
    if ($actualHash -ne $package.Sha256) {
        throw "SHA-256 mismatch for $($package.FileName). Expected $($package.Sha256), got $actualHash."
    }

    Write-Host "Extracting chdman.exe..."
    & $archivePath "-o$extractPath" -y | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "MAME package extraction failed with exit code $LASTEXITCODE."
    }

    $extractedFiles = @(Get-ChildItem -LiteralPath $extractPath -Filter "chdman.exe" -File -Recurse)
    if ($extractedFiles.Count -ne 1) {
        throw "Expected one chdman.exe in the MAME package, found $($extractedFiles.Count)."
    }

    $versionOutput = (& $extractedFiles[0].FullName -help 2>&1 | Select-Object -First 1) -join ""
    if ($versionOutput -notmatch [regex]::Escape($mameVersion)) {
        throw "The extracted chdman.exe did not report the expected version $mameVersion."
    }

    Copy-Item -LiteralPath $extractedFiles[0].FullName -Destination $destination -Force
    Write-Host "Installed chdman $mameVersion to $destination"
} finally {
    if (Test-Path -LiteralPath $temporaryRoot) {
        Remove-Item -LiteralPath $temporaryRoot -Recurse -Force
    }
}
