[CmdletBinding()]
param(
    [string]$RuntimeRoot,
    [string]$PythonPath,
    [string]$NodePath,
    [string]$NodeModulesPath,
    [string]$PopplerPath,
    [string]$SofficePath,
    [string]$ExcelPath,
    [string]$WordPath,
    [string]$PowerPointPath
)

$ErrorActionPreference = 'Stop'

function First-ExistingFile {
    param([string[]]$Candidates)
    foreach ($candidate in $Candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return [System.IO.Path]::GetFullPath($candidate)
        }
    }
    return ''
}

function Command-Path {
    param([string]$Name)
    $command = Get-Command $Name -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command) { return $command.Source }
    return ''
}

function App-Path {
    param([string]$Name)
    $keys = @(
        "Registry::HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\App Paths\$Name",
        "Registry::HKEY_LOCAL_MACHINE\Software\Microsoft\Windows\CurrentVersion\App Paths\$Name",
        "Registry::HKEY_LOCAL_MACHINE\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\$Name"
    )
    foreach ($key in $keys) {
        if (Test-Path -LiteralPath $key) {
            $value = (Get-Item -LiteralPath $key).GetValue('')
            if ($value) { return $value }
        }
    }
    return ''
}

if (-not $RuntimeRoot) {
    $RuntimeRoot = if ($env:CODEX_PRIMARY_RUNTIME_DEPENDENCIES) {
        $env:CODEX_PRIMARY_RUNTIME_DEPENDENCIES
    } else {
        Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies'
    }
}
$managedRoot = if (Test-Path -LiteralPath $RuntimeRoot -PathType Container) {
    [System.IO.Path]::GetFullPath($RuntimeRoot)
} else { '' }

$managedPython = if ($managedRoot) { Join-Path $managedRoot 'python\python.exe' } else { '' }
$python = First-ExistingFile @($PythonPath, $managedPython, (Command-Path 'python'))
$node = First-ExistingFile @($NodePath, $(if ($managedRoot) { Join-Path $managedRoot 'node\bin\node.exe' }), (Command-Path 'node'))
$nodeModules = if ($NodeModulesPath -and (Test-Path -LiteralPath $NodeModulesPath -PathType Container)) {
    [System.IO.Path]::GetFullPath($NodeModulesPath)
} elseif ($managedRoot -and (Test-Path -LiteralPath (Join-Path $managedRoot 'node\node_modules') -PathType Container)) {
    Join-Path $managedRoot 'node\node_modules'
} else { '' }
$pnpm = First-ExistingFile @($(if ($managedRoot) { Join-Path $managedRoot 'bin\fallback\pnpm.cmd' }), (Command-Path 'pnpm'))
$git = First-ExistingFile @($(if ($managedRoot) { Join-Path $managedRoot 'native\git\cmd\git.exe' }), (Command-Path 'git'))

$managedPoppler = if ($managedRoot) { Join-Path $managedRoot 'native\poppler\Library\bin' } else { '' }
$pathPdfInfo = Command-Path 'pdfinfo'
$pathPoppler = if ($pathPdfInfo) { Split-Path -Parent $pathPdfInfo } else { '' }
$poppler = @($PopplerPath, $managedPoppler, $pathPoppler) |
    Where-Object { $_ -and (Test-Path -LiteralPath (Join-Path $_ 'pdfinfo.exe') -PathType Leaf) -and (Test-Path -LiteralPath (Join-Path $_ 'pdftoppm.exe') -PathType Leaf) } |
    Select-Object -First 1

$soffice = First-ExistingFile @(
    $SofficePath,
    (Command-Path 'soffice.com'),
    (App-Path 'soffice.com'),
    (Command-Path 'soffice.exe'),
    (App-Path 'soffice.exe'),
    (Join-Path $env:ProgramFiles 'LibreOffice\program\soffice.com'),
    (Join-Path $env:ProgramFiles 'LibreOffice\program\soffice.exe')
)

if (-not $python) { throw 'No usable Python interpreter found' }
$repoRoot = Split-Path -Parent $PSScriptRoot
$fallback = Join-Path $repoRoot '.tooling\python-fallbacks'
$officeRoots = @(
    (Join-Path $env:ProgramFiles 'Microsoft Office\root\Office16'),
    $(if (${env:ProgramFiles(x86)}) { Join-Path ${env:ProgramFiles(x86)} 'Microsoft Office\root\Office16' })
)

$env:SYN_STUDIOS_RUNTIME_ROOT = $managedRoot
$env:SYN_STUDIOS_PYTHON = $python
$env:SYN_STUDIOS_MANAGED_PYTHON = $managedPython
$env:SYN_STUDIOS_PYTHON_FALLBACKS = $fallback
$env:SYN_STUDIOS_NODE = $node
$env:SYN_STUDIOS_NODE_MODULES = $nodeModules
$env:SYN_STUDIOS_PNPM = $pnpm
$env:SYN_STUDIOS_GIT = $git
$env:SYN_STUDIOS_POPPLER_BIN = $poppler
$env:SYN_STUDIOS_SOFFICE = $soffice
$env:SYN_STUDIOS_EXCEL = First-ExistingFile @($ExcelPath, (App-Path 'EXCEL.EXE'), ($officeRoots | ForEach-Object { Join-Path $_ 'EXCEL.EXE' }), (Command-Path 'EXCEL.EXE'))
$env:SYN_STUDIOS_WORD = First-ExistingFile @($WordPath, (App-Path 'WINWORD.EXE'), ($officeRoots | ForEach-Object { Join-Path $_ 'WINWORD.EXE' }), (Command-Path 'WINWORD.EXE'))
$env:SYN_STUDIOS_POWERPOINT = First-ExistingFile @($PowerPointPath, (App-Path 'POWERPNT.EXE'), ($officeRoots | ForEach-Object { Join-Path $_ 'POWERPNT.EXE' }), (Command-Path 'POWERPNT.EXE'))
if ($nodeModules) { $env:NODE_PATH = $nodeModules }
if (Test-Path -LiteralPath $fallback -PathType Container) {
    $fallbackPaths = @($fallback, (Join-Path $fallback 'win32'), (Join-Path $fallback 'win32\lib'), (Join-Path $fallback 'pythonwin')) |
        Where-Object { Test-Path -LiteralPath $_ -PathType Container }
    $prefix = $fallbackPaths -join ';'
    $env:PYTHONPATH = if ($env:PYTHONPATH) { "$prefix;$($env:PYTHONPATH)" } else { $prefix }
    $pywin32System = Join-Path $fallback 'pywin32_system32'
    if (Test-Path -LiteralPath $pywin32System -PathType Container) {
        $env:PATH = "$pywin32System;$($env:PATH)"
    }
}

Write-Host 'Syn Studios document stack activated.'
Write-Host "  Python:      $python"
Write-Host "  Node:        $node"
Write-Host "  Poppler:     $poppler"
Write-Host "  LibreOffice: $soffice"
Write-Host "Run: & `$env:SYN_STUDIOS_PYTHON .\scripts\check_document_stack.py --profile all --json"
