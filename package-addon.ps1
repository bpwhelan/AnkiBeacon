param(
    [string]$OutputPath
)

$ErrorActionPreference = "Stop"

$AddonDir = $PSScriptRoot
if (-not $AddonDir) {
    $AddonDir = (Get-Location).Path
}

$ManifestPath = Join-Path $AddonDir "manifest.json"
$ArchiveName = "addon.ankiaddon"

if (Test-Path -LiteralPath $ManifestPath) {
    $manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
    if ($manifest.name) {
        $safeName = $manifest.name -replace '[\\/:*?"<>|]', "_"
        $ArchiveName = "$safeName.ankiaddon"
    }
}

if (-not $OutputPath) {
    $OutputPath = Join-Path (Split-Path $AddonDir -Parent) $ArchiveName
}

$OutputPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputPath)
$ScriptPath = $PSCommandPath

if (Test-Path -LiteralPath $OutputPath) {
    Remove-Item -LiteralPath $OutputPath -Force
}

Add-Type -AssemblyName System.IO.Compression.FileSystem

$files = Get-ChildItem -LiteralPath $AddonDir -Recurse -File -Force | Where-Object {
    $fullName = $_.FullName
    $relative = [System.IO.Path]::GetRelativePath($AddonDir, $fullName)
    $parts = $relative -split '[\\/]'

    $fullName -ne $ScriptPath `
        -and $fullName -ne $OutputPath `
        -and $_.Name -ne "meta.json" `
        -and $_.Extension -ne ".ankiaddon" `
        -and $parts -notcontains "__pycache__" `
        -and $parts -notcontains ".git"
}

$zip = [System.IO.Compression.ZipFile]::Open($OutputPath, [System.IO.Compression.ZipArchiveMode]::Create)
try {
    foreach ($file in $files) {
        $entryName = [System.IO.Path]::GetRelativePath($AddonDir, $file.FullName) -replace '\\', '/'
        [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
            $zip,
            $file.FullName,
            $entryName,
            [System.IO.Compression.CompressionLevel]::Optimal
        ) | Out-Null
    }
}
finally {
    $zip.Dispose()
}

Write-Host "Created $OutputPath"
Write-Host ""
Write-Host "Archive contents:"
tar -tf $OutputPath
