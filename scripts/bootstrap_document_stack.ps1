[CmdletBinding()]
param(
    [string]$RuntimeRoot,
    [switch]$InstallAnalysisFallbacks,
    [switch]$InstallOfficeComFallback
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$fallbackRoot = Join-Path $repoRoot '.tooling\python-fallbacks'

. (Join-Path $PSScriptRoot 'activate_document_stack.ps1') -RuntimeRoot $RuntimeRoot
$python = $env:SYN_STUDIOS_PYTHON
New-Item -ItemType Directory -Path $fallbackRoot -Force | Out-Null

function Has-Import {
    param([string]$Name)
    & $python -c "import $Name" 2>$null
    return $LASTEXITCODE -eq 0
}

if (-not (Has-Import 'jsonschema')) {
    & $python -m pip install --disable-pip-version-check --target $fallbackRoot -r (Join-Path $repoRoot 'requirements-dev.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Failed to install core Python fallback' }
    . (Join-Path $PSScriptRoot 'activate_document_stack.ps1') -RuntimeRoot $RuntimeRoot
}
if ($InstallAnalysisFallbacks) {
    $analysisJson = & $python (Join-Path $PSScriptRoot 'check_document_stack.py') --profile analysis --json
    $analysis = $analysisJson | ConvertFrom-Json
    $missingSpecs = @(
        $analysis.packages.PSObject.Properties |
            Where-Object { $_.Value.status -ne 'PASS' } |
            ForEach-Object { "$($_.Name)==$($_.Value.accepted)" }
    )
    if ($missingSpecs.Count -gt 0) {
        & $python -m pip install --disable-pip-version-check --target $fallbackRoot $missingSpecs
        if ($LASTEXITCODE -ne 0) { throw 'Failed to install analysis fallbacks' }
        . (Join-Path $PSScriptRoot 'activate_document_stack.ps1') -RuntimeRoot $RuntimeRoot
    }
}
if ($InstallOfficeComFallback -and $IsWindows -and -not (Has-Import 'win32com.client')) {
    & $python -m pip install --disable-pip-version-check --target $fallbackRoot -r (Join-Path $repoRoot 'requirements-windows.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Failed to install Windows Office COM fallback' }
}

. (Join-Path $PSScriptRoot 'activate_document_stack.ps1') -RuntimeRoot $RuntimeRoot
$profiles = @('core')
if ($InstallAnalysisFallbacks) { $profiles += 'analysis' }
if ($InstallOfficeComFallback) { $profiles += 'office' }
foreach ($profile in $profiles) {
    & $env:SYN_STUDIOS_PYTHON (Join-Path $PSScriptRoot 'check_document_stack.py') --profile $profile --json
    if ($LASTEXITCODE -ne 0) { throw "$profile document-stack validation failed" }
}
