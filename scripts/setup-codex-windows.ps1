[CmdletBinding()]
param(
    [switch]$Lite,
    [switch]$SkipPluginDeps,
    [switch]$StartServices,
    [switch]$SkipDockerCheck
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Step {
    param([string]$Message)
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Write-Warn {
    param([string]$Message)
    Write-Host "WARN: $Message" -ForegroundColor Yellow
}

if (-not $IsWindows) {
    throw "This setup script is intended for Windows only."
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pyprojectPath = Join-Path $repoRoot "pyproject.toml"
if (-not (Test-Path $pyprojectPath)) {
    throw "Could not find pyproject.toml at $pyprojectPath. Run this script from the repository copy."
}

Write-Step "Repository root: $repoRoot"

function Get-PythonCommand {
    $candidates = @(
        @{ Name = "py"; Args = @("-3.11") },
        @{ Name = "py"; Args = @("-3") },
        @{ Name = "python"; Args = @() }
    )

    foreach ($candidate in $candidates) {
        if (-not (Get-Command $candidate.Name -ErrorAction SilentlyContinue)) {
            continue
        }

        try {
            $versionText = & $candidate.Name @($candidate.Args + @("-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"))
            $version = [Version]("$versionText.0")
            if ($version -ge [Version]"3.11.0") {
                return $candidate
            }
        } catch {
            continue
        }
    }

    throw "Python 3.11+ is required. Install Python 3.11+ and ensure it is available as 'py' or 'python'."
}

$pythonCommand = Get-PythonCommand
Write-Step "Using Python launcher: $($pythonCommand.Name) $($pythonCommand.Args -join ' ')"

$venvPath = Join-Path $repoRoot ".venv"
$venvPython = Join-Path $venvPath "Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    Write-Step "Creating virtual environment at $venvPath"
    & $pythonCommand.Name @($pythonCommand.Args + @("-m", "venv", $venvPath))
}
else {
    Write-Step "Virtual environment already exists at $venvPath"
}

Write-Step "Upgrading pip/setuptools/wheel"
& $venvPython -m pip install --upgrade pip setuptools wheel

if ($Lite) {
    Write-Step "Installing vault-memory in editable mode with lite extras"
    & $venvPython -m pip install -e ".[lite]"
}
else {
    Write-Step "Installing vault-memory in editable mode"
    & $venvPython -m pip install -e .
}

if (-not $SkipPluginDeps) {
    $pluginDir = Join-Path $repoRoot "obsidian-plugin"
    $pluginPackage = Join-Path $pluginDir "package.json"

    if (Test-Path $pluginPackage) {
        if (Get-Command "npm" -ErrorAction SilentlyContinue) {
            Write-Step "Installing Obsidian plugin npm dependencies"
            Push-Location $pluginDir
            try {
                npm install
            } finally {
                Pop-Location
            }
        }
        else {
            Write-Warn "npm not found. Skipping obsidian-plugin dependency install."
        }
    }
}
else {
    Write-Step "Skipping Obsidian plugin dependencies (--SkipPluginDeps)"
}

if (-not $SkipDockerCheck) {
    if (Get-Command "docker" -ErrorAction SilentlyContinue) {
        Write-Step "Docker found"
        if ($StartServices) {
            Write-Step "Starting Weaviate + Postgres with docker compose"
            Push-Location $repoRoot
            try {
                docker compose up -d
            } finally {
                Pop-Location
            }
        }
        else {
            Write-Host "Docker services not started. Use: docker compose up -d" -ForegroundColor DarkGray
        }
    }
    else {
        Write-Warn "Docker CLI not found. Install Docker Desktop if you need Weaviate/Postgres locally."
    }
}
else {
    Write-Step "Skipping Docker checks (--SkipDockerCheck)"
}

Write-Step "Setup complete"
Write-Host "Next steps:" -ForegroundColor Green
Write-Host "1) Activate venv: .\.venv\Scripts\Activate.ps1"
Write-Host "2) (Optional) Start services: docker compose up -d"
Write-Host "3) Start daemon: vault-memory daemon start"
Write-Host "4) Quick test: python -m pytest tests\ -q --basetemp C:\temp\vault_memory_pytest"
