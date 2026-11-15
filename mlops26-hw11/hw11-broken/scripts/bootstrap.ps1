$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot/..
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
function Invoke-Checked {
    param([string] $Program, [string[]] $Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program exited with $LASTEXITCODE" }
}
if (-not (Test-Path .venv)) { throw 'Run uv sync yourself before bootstrap.' }
Invoke-Checked uv @('run', '--no-sync', 'python', '-m', 'scripts.sync_chart')
Invoke-Checked helm @('lint', 'chart')
Invoke-Checked docker @('build', '-t', 'mlops-hw11:dev3', '.')
$clusters = & kind get clusters
if ($LASTEXITCODE -ne 0) { throw 'Cannot list kind clusters' }
if ($clusters -notcontains 'mlops-hw11') {
    Invoke-Checked kind @('create', 'cluster', '--config', 'kind.yaml', '--wait', '180s')
}
Invoke-Checked kind @('load', 'docker-image', 'mlops-hw11:dev3', '--name', 'mlops-hw11')
Invoke-Checked kubectl @('--context', 'kind-mlops-hw11', 'apply', '-f', 'monitoring/stack.yaml')
Invoke-Checked kubectl @('--context', 'kind-mlops-hw11', 'apply', '-f', 'monitoring/metrics-server.yaml')
Invoke-Checked helm @('upgrade', '--install', 'hw11', 'chart', '--kube-context', 'kind-mlops-hw11',
                     '-n', 'hw11', '--reset-values', '--wait', '--timeout', '180s')
Invoke-Checked kubectl @('--context', 'kind-mlops-hw11', 'apply', '-f', 'clients.yaml')
Invoke-Checked kubectl @('--context', 'kind-mlops-hw11', '-n', 'hw11', 'wait',
                        '--for=condition=Ready', 'pod', '-l', 'app=hw11-client', '--timeout=180s')
