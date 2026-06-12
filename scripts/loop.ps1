<#
.SYNOPSIS
    Loop de scraping ONPE segunda vuelta 2026.
    Corre un ciclo (mesas + reconciliacion + resumen-geo + git push) y duerme N minutos.

.DESCRIPTION
    Replica exactamente lo que corre Copilot CLI de forma autónoma.
    Puede arrancarse, detenerse (Ctrl+C) y reanudarse sin perder progreso:
    la próxima corrida retoma desde work/mesas_pendientes.txt.

.PARAMETER IdEleccion
    ID de elección ONPE. Default: 10 (segunda vuelta 2026).

.PARAMETER IntervaloMinutos
    Minutos de espera entre ciclos. Default: 20.

.PARAMETER MaxWorkers
    Número de workers paralelos para scraping. Default: 5.

.PARAMETER BatchSize
    Mesas por lote antes de hacer flush a disco. Default: 200.

.PARAMETER DescargarPdfs
    Switch. Si se pasa, descarga los PDFs de las actas contabilizadas.
    OFF por defecto.

.PARAMETER ActasDir
    Directorio donde guardar los PDFs. Default: actas.

.PARAMETER Reconciliar
    Switch. Si se pasa, activa la reconciliación E->C al final de cada ciclo.
    ON por defecto.

.PARAMETER MaxPaginasReconciliacion
    Páginas máximas a paginar en /actas durante reconciliación. Default: 50.

.PARAMETER CicloInicial
    Número de ciclo para el log. Solo cosmético. Default: 1.

.PARAMETER SoloCiclos
    Número de ciclos a correr antes de salir. 0 = infinito (default).

.EXAMPLE
    # Arranque básico (igual a lo que corre Copilot)
    .\scripts\loop.ps1

.EXAMPLE
    # Con descarga de PDFs, intervalo 30 min
    .\scripts\loop.ps1 -DescargarPdfs -IntervaloMinutos 30

.EXAMPLE
    # Solo 1 ciclo (útil para probar / correr manualmente)
    .\scripts\loop.ps1 -SoloCiclos 1

.EXAMPLE
    # Sin reconciliacion, sin PDFs, intervalo corto
    .\scripts\loop.ps1 -Reconciliar:$false -IntervaloMinutos 5
#>
param(
    [int]    $IdEleccion               = 10,
    [int]    $IntervaloMinutos         = 20,
    [int]    $MaxWorkers               = 5,
    [int]    $BatchSize                = 200,
    [switch] $DescargarPdfs,
    [string] $ActasDir                 = "actas",
    [switch] $Reconciliar              = $true,
    [int]    $MaxPaginasReconciliacion = 50,
    [int]    $CicloInicial             = 1,
    [int]    $SoloCiclos               = 0
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

# ── Directorio raíz del repo (el script vive en scripts/) ──────────────────────
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

# ── Activa el virtualenv si existe ─────────────────────────────────────────────
$VenvActivate = Join-Path $RepoRoot ".venv\Scripts\Activate.ps1"
if (Test-Path $VenvActivate) {
    . $VenvActivate
} else {
    Write-Warning "No se encontro .venv — asegurate de correr: python -m venv .venv && pip install -r requirements.txt"
}

# ── Construye los argumentos base ──────────────────────────────────────────────
$MesasArgs = @(
    "-m", "src.onpe_scraper.main",
    "--modo", "mesas",
    "--id-eleccion", $IdEleccion,
    "--tiempo-max", "9",
    "--max-workers", $MaxWorkers,
    "--batch-size", $BatchSize
)

if ($DescargarPdfs) {
    $MesasArgs += "--descargar-pdfs"
    $MesasArgs += "--actas-dir"
    $MesasArgs += $ActasDir
}

if ($Reconciliar) {
    $MesasArgs += "--reconciliar"
    $MesasArgs += "--max-paginas-reconciliacion"
    $MesasArgs += $MaxPaginasReconciliacion
}

$ResumenArgs = @(
    "-m", "src.onpe_scraper.main",
    "--modo", "resumen-geo",
    "--id-eleccion", $IdEleccion
)

# ── Loop principal ─────────────────────────────────────────────────────────────
$cycle = $CicloInicial
while ($true) {
    $ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    Write-Host ""
    Write-Host "=== CICLO $cycle  $ts ===" -ForegroundColor Cyan

    # 1. Scraping de mesas + reconciliacion
    Write-Host "[1/4] Scraping mesas..." -ForegroundColor Yellow
    python @MesasArgs
    if ($LASTEXITCODE -ne 0) { Write-Warning "  mesas salio con codigo $LASTEXITCODE" }

    # 2. Resumen geo
    Write-Host "[2/4] Resumen geo..." -ForegroundColor Yellow
    python @ResumenArgs
    if ($LASTEXITCODE -ne 0) { Write-Warning "  resumen-geo salio con codigo $LASTEXITCODE" }

    # 3. Git commit + push
    Write-Host "[3/4] Git commit + push..." -ForegroundColor Yellow
    git add output/ resumen/ work/reconciliacion_estado.txt 2>$null
    $pendientes = (
        Select-String -Path "output\mesas_data.txt" -Pattern "`tpendiente`t" -CaseSensitive:$false -Quiet
    ) ? (Select-String -Path "output\mesas_data.txt" -Pattern "`tpendiente`t" -CaseSensitive:$false | Measure-Object).Count : 0
    $tsCommit = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mmZ")
    git commit -m "data: $tsCommit — pendientes: $pendientes mesas" --allow-empty
    git push
    if ($LASTEXITCODE -ne 0) { Write-Warning "  git push salio con codigo $LASTEXITCODE" }

    $cycle++

    # 4. Salir si alcanzamos SoloCiclos
    if ($SoloCiclos -gt 0 -and ($cycle - $CicloInicial) -ge $SoloCiclos) {
        Write-Host "[4/4] SoloCiclos=$SoloCiclos alcanzado. Saliendo." -ForegroundColor Green
        break
    }

    # 5. Sleep
    Write-Host "[4/4] Esperando $IntervaloMinutos min..." -ForegroundColor DarkGray
    Start-Sleep -Seconds ($IntervaloMinutos * 60)
}

Write-Host "Loop terminado." -ForegroundColor Green
