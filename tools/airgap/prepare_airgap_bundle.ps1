# ==============================================================================
# Script: prepare_airgap_bundle.ps1
# Runs on: Windows PowerShell (Jump Server with Internet Access)
# ==============================================================================

param(
    [string[]]$Models = @("qwen2.5:3b")
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Get-Item $PSScriptRoot).Parent.Parent.FullName
$ScriptPy = Join-Path $ProjectRoot "tools\airgap\prepare_airgap_bundle.py"

Write-Host "Executing Cross-Platform Bundle Builder via Python..." -ForegroundColor Cyan
python $ScriptPy $Models
