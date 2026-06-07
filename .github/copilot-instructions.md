# Copilot Instructions

## Running the scraper

```powershell
# Setup
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Resumen mode (totals + candidates, auto-detects active election)
python -m src.onpe_scraper.main

# Mesa mode — auto-discovers all mesas from ONPE and scrapes per-mesa data
python -m src.onpe_scraper.main --modo mesas --redescubrir

# Mesa mode — resume from last run (only re-queries pending/uncounted mesas)
python -m src.onpe_scraper.main --modo mesas

# Continuous audit loop (re-queries pending every 60s)
python -m src.onpe_scraper.main --modo mesas --intervalo-segundos 60

# Override election ID (default: auto-detected from active process)
python -m src.onpe_scraper.main --modo mesas --id-eleccion 10 --redescubrir
```

## Architecture

Four modules under `src/onpe_scraper/`:

- **`models.py`** — Dataclasses: `MesaData`, `AgrupacionData`, `VotoData`, `MesaResult`. All use `slots=True`. `id_eleccion` is present on all row-level models to support multi-election keys.
- **`client.py`** — `OnpeClient` dataclass. All HTTP uses `curl_cffi` with `impersonate="chrome124"` (required by ONPE for both summary and mesa endpoints). Thread-local `curl_cffi.Session` is used for parallel mesa scraping. Key methods:
  - `get_snapshot()` — aggregate summary (resumen mode)
  - `get_all_mesas()` — discovers 6-digit mesa codes from `assets/data/mesas.json` when available, and falls back to `totalActas` range (`000001..N`) when the asset is unavailable/placeholder
  - `get_mesa_acta(codigo_mesa, id_eleccion)` — fetches `/actas/buscar/mesa`, retries up to `max_retries` with exponential backoff, returns `MesaResult | None`
- **`exporters.py`** — Output functions:
  - `write_snapshot_json` / `append_candidates_csv` — resumen mode outputs
  - `upsert_mesas_data_txt`, `upsert_votos_txt`, `upsert_agrupaciones_txt` — load-merge-rewrite upsert with composite keys (include `id_eleccion`)
  - `write_pending_mesas_txt` / `load_pending_mesas_txt` — incremental resume file
- **`main.py`** — CLI via `argparse`. `--modo resumen` (default) or `--modo mesas`. Mesa mode processes in batches via `ThreadPoolExecutor` (max 5 workers), flushes TXT after each batch, and rewrites `mesas_pendientes.txt` with non-Contabilizada mesas for the next run.

Output files go to `output/` (gitignored).

## Data model (mesa mode)

```
mesas_data.txt (tab-delimited, upsert key: id_eleccion + codigo_mesa)
  codigo_mesa, id_eleccion, id_ubigeo, nombre_local_votacion,
  codigo_local_votacion, id_ambito_geografico,
  electores_habiles, votos_emitidos, votos_validos,
  total_asistentes, participacion_ciudadana,
  codigo_estado_acta, descripcion_estado_acta

votos.txt (tab-delimited, upsert key: id_eleccion + codigo_mesa + partido_id)
  codigo_mesa, id_eleccion, partido_id,
  votos, pct_votos_validos, pct_votos_emitidos

agrupaciones.txt (tab-delimited, upsert key: partido_id — global catalog)
  partido_id, codigo_op, nombre

mesas_pendientes.txt (one mesa code per line — resume file)
  → deleted/empty when all mesas are Contabilizada
```

## Key conventions

- `from __future__ import annotations` is used in every module.
- All HTTP goes through `curl_cffi` with `impersonate="chrome124"` — ONPE returns the Angular SPA HTML without this.
- Spanish names are used for variables/fields that mirror API response keys (e.g., `idEleccion`, `nombreCandidato`, `tipoFiltro`). Python-side arguments use snake_case (e.g., `id_eleccion`).
- `ensure_ascii=False` is always used in `json.dump` and CSV/TXT writes to preserve Spanish characters.
- `tipoFiltro` valid values: `eleccion`, `ambito_geografico`, `ubigeo_nivel_01`, `ubigeo_nivel_02`, `ubigeo_nivel_03`.
- Extra API filter params (`idAmbitoGeografico`, `ubigeo`) are forwarded via `**extra_filters` kwargs through `get_snapshot()` → `get_totals()` / `get_candidates()`.
- Segunda vuelta usa `https://resultadosegundavuelta.onpe.gob.pe/presentacion-backend`; si `assets/data/mesas.json` no está utilizable, discovery cae a rango `000001..totalActas`.
- `mesas_pendientes.txt` is the resume file for incremental runs. Delete it or pass `--redescubrir` to start fresh.
- If the ONPE API changes its payload shape, `client.py` is the only file to update.
