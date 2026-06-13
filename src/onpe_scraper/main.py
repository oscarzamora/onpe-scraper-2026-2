from __future__ import annotations

import argparse
import csv
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .client import OnpeClient
from .exporters import (
    append_candidates_txt,
    load_pending_mesas_txt,
    upsert_agrupaciones_txt,
    upsert_locales_txt,
    upsert_mesas_data_txt,
    upsert_ubicaciones_txt,
    upsert_votos_txt,
    write_pending_mesas_txt,
    write_snapshot_json,
)
from .models import MesaResult
from .pdfs import (
    download_acta_archivos,
    migrate_flat_acta_tree,
    load_acta_download_keys,
)
from .resumen_layer import run_resumen_geo


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extraccion base ONPE segunda vuelta 2026 desde API interna"
    )
    parser.add_argument(
        "--modo",
        default="resumen",
        choices=["resumen", "mesas", "resumen-geo", "pdfs"],
        help="resumen: totales y candidatos (default). mesas: extraccion autonoma por mesa. resumen-geo: capa de resumen nacional/departamentos. pdfs: descarga de actas PDF.",
    )

    # --- resumen mode args ---
    parser.add_argument("--id-eleccion", type=int, default=None)
    parser.add_argument(
        "--tipo-filtro",
        default="eleccion",
        help="eleccion, ambito_geografico, ubigeo_nivel_01, ubigeo_nivel_02, ubigeo_nivel_03",
    )
    parser.add_argument("--id-ambito-geografico", type=int, default=None)
    parser.add_argument("--ubigeo", default=None)

    # --- mesas mode args ---
    parser.add_argument(
        "--redescubrir",
        action="store_true",
        help="Ignorar mesas_pendientes.txt y re-descubrir desde assets/data/mesas.json",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=5,
        help="Hilos paralelos para consultas de mesa (maximo: 5)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Mesas por lote antes de escribir a disco",
    )
    parser.add_argument("--timeout", type=int, default=20, help="Segundos por peticion")
    parser.add_argument(
        "--tiempo-max",
        type=int,
        default=0,
        help="Detener scraping despues de N minutos (0 = sin limite). Util para GitHub Actions.",
    )

    parser.add_argument(
        "--no-smart-order",
        action="store_true",
        help="Disable smart ordering (don't prioritize mesas from active districts).",
    )
    parser.add_argument(
        "--resumen-full",
        action="store_true",
        help="Forzar full build del resumen (ignora estado incremental previo).",
    )
    parser.add_argument(
        "--resumen-dir",
        default="resumen",
        help="Carpeta de salida para archivos de resumen (default: resumen/).",
    )
    parser.add_argument("--intervalo-segundos", type=int, default=0)
    parser.add_argument("--salida", default="output")
    parser.add_argument("--trabajo", default="work", help="Carpeta para archivos intermedios (pendientes, snapshots)")
    parser.add_argument(
        "--descargar-pdfs",
        action="store_true",
        help="En modo mesas, descargar tambien los PDFs de cada acta procesada.",
    )
    parser.add_argument(
        "--mesas-fuente",
        default="output/mesas_data.txt",
        help="Archivo TSV de mesas ya procesadas para el modo pdfs (default: output/mesas_data.txt).",
    )
    parser.add_argument(
        "--actas-dir",
        default="acta",
        help="Carpeta destino para PDFs de actas (default: acta/).",
    )
    parser.add_argument("--verbose", action="store_true")

    # --- reconciliation args ---
    parser.add_argument(
        "--reconciliar",
        action="store_true",
        help=(
            "Comparar C_onpe vs C_local tras el scrape normal y re-consultar mesas "
            "que ONPE marca como C pero que localmente no lo son."
        ),
    )
    parser.add_argument(
        "--max-reconciliacion-mesas",
        type=int,
        default=500,
        help="Cap de mesas a re-consultar por ciclo de reconciliacion (default: 500).",
    )
    parser.add_argument(
        "--max-paginas-reconciliacion",
        type=int,
        default=50,
        help=(
            "Paginas maximas de /actas por ambito geografico en reconciliacion "
            "(0 = sin limite; default: 50 = ~5000 mesas por ambito)."
        ),
    )
    return parser


def build_extra_filters(args: argparse.Namespace) -> dict[str, Any]:
    filters: dict[str, Any] = {}
    if args.tipo_filtro == "ambito_geografico" and args.id_ambito_geografico is not None:
        filters["idAmbitoGeografico"] = args.id_ambito_geografico
    if args.tipo_filtro.startswith("ubigeo_nivel_"):
        if not args.ubigeo:
            raise ValueError("Para filtros ubigeo_nivel_* debes pasar --ubigeo")
        filters["ubigeo"] = args.ubigeo
    return filters


def print_summary(snapshot: dict[str, Any]) -> None:
    process = snapshot.get("activeProcess", {})
    totals = snapshot.get("totales", {})
    candidates = snapshot.get("candidatos", [])

    print(f"Proceso: {process.get('nombre')}")
    print(f"Id eleccion: {snapshot.get('idEleccion')}")
    print(f"Tipo filtro: {snapshot.get('tipoFiltro')}")
    if snapshot.get("filtros"):
        print(f"Filtros: {json.dumps(snapshot['filtros'], ensure_ascii=False)}")
    print(f"Actas contabilizadas: {totals.get('actasContabilizadas')}")
    print(f"Total actas: {totals.get('totalActas')}")
    print(f"Participacion ciudadana: {totals.get('participacionCiudadana')}")
    print("Candidatos:")
    for c in candidates:
        print(
            f"- {c.get('nombreCandidato')} ({c.get('nombreAgrupacionPolitica')}): "
            f"{c.get('porcentajeVotosValidos')}%"
        )


def run_resumen(client: OnpeClient, args: argparse.Namespace, output_dir: Path, work_dir: Path) -> None:
    filters = build_extra_filters(args)
    snapshot = client.get_snapshot(
        election_id=args.id_eleccion,
        tipo_filtro=args.tipo_filtro,
        **filters,
    )

    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = work_dir / f"snapshot_{now}.json"
    txt_path = output_dir / "candidatos_historial.txt"

    write_snapshot_json(snapshot, json_path)
    append_candidates_txt(snapshot, txt_path)

    print_summary(snapshot)
    print(f"Snapshot guardado en: {json_path}")
    print(f"Historial actualizado en: {txt_path}")


def _flush_batch(
    batch_results: list[MesaResult],
    output_dir: Path,
) -> None:
    if not batch_results:
        return
    upsert_mesas_data_txt(batch_results, output_dir / "mesas_data.txt")
    upsert_votos_txt(batch_results, output_dir / "votos.txt")
    upsert_agrupaciones_txt(batch_results, output_dir / "agrupaciones.txt")
    upsert_locales_txt(batch_results, output_dir / "locales.txt")


def _run_reconciliacion(
    client: OnpeClient,
    id_eleccion: int,
    output_dir: Path,
    work_dir: Path,
    max_reconciliacion_mesas: int = 200,
    max_paginas_reconciliacion: int = 50,
    descargar_pdfs: bool = False,
    actas_dir: Path | None = None,
    verbose: bool = False,
) -> dict[str, int]:
    """
    Detecta y cierra el gap entre C_onpe (resumen API) y C_local (mesas_data.txt).

    Flujo:
    1. C_onpe  <- totales.actasContabilizadas
    2. C_local <- contar filas en mesas_data.txt con estado Contabilizada
    3. gap = C_onpe - C_local
    4. Si gap > 0: paginar /actas para obtener codigos C en ONPE
    5. gap_mesas = C_onpe_codigos - C_local_codigos
    6. Re-consultar hasta max_reconciliacion_mesas de esas mesas y hacer upsert
    7. Escribir work/reconciliacion_estado.txt
    """
    stats: dict[str, int] = {
        "c_onpe": 0, "c_local": 0, "gap": 0,
        "pendientes_onpe": 0,
        "gap_mesas_detectadas": 0, "reconciliadas": 0, "errores": 0,
    }

    # 1. C_onpe desde summary (1 request rápido)
    # 'contabilizadas' = count de actas C; 'enviadasJee' = "Para envío al JEE" (también done)
    # 'actasContabilizadas' es un porcentaje (e.g. 98.27), NO el conteo.
    try:
        totals = client.get_totals(id_eleccion, tipo_filtro="eleccion")
        stats["c_onpe"] = int(totals.get("contabilizadas") or 0) + int(totals.get("enviadasJee") or 0)
        stats["pendientes_onpe"] = int(totals.get("pendientesJee") or 0)
    except Exception as exc:
        print(f"  [reconciliacion] No se pudo obtener totales: {exc}")
        return stats

    # 2. C_local desde archivo — distinguir C puro vs E (Para envio al JEE)
    mesas_data_path = output_dir / "mesas_data.txt"
    local_c_only: set[str] = set()
    local_e_mesas: set[str] = set()
    if mesas_data_path.exists():
        with mesas_data_path.open("r", encoding="utf-8") as _f:
            for _row in csv.DictReader(_f, delimiter="\t"):
                estado = (_row.get("descripcion_estado_acta") or "").casefold()
                if estado == "contabilizada":
                    local_c_only.add(_row["codigo_mesa"])
                elif "env" in estado:
                    local_e_mesas.add(_row["codigo_mesa"])
    local_done = local_c_only | local_e_mesas
    stats["c_local"] = len(local_c_only)
    stats["e_local"] = len(local_e_mesas)
    c_onpe_pure = stats["c_onpe"] - int(totals.get("enviadasJee") or 0)
    stats["c_onpe_pure"] = c_onpe_pure
    stats["e_c_drift"] = max(0, c_onpe_pure - len(local_c_only))
    stats["gap"] = max(0, stats["c_onpe"] - len(local_done))

    print(
        f"  [reconciliacion] C_onpe={c_onpe_pure} E_onpe={totals.get('enviadasJee')} "
        f"C_local={stats['c_local']} E_local={stats['e_local']} "
        f"E->C_drift={stats['e_c_drift']} gap={stats['gap']} "
        f"pendientes_onpe={stats['pendientes_onpe']}"
    )

    # Re-query E mesas solo si hay drift detectado o es la primera vez (cada 3 ciclos como mínimo)
    # Si drift=0 y gap=0 no re-consultamos E para evitar llamadas innecesarias
    e_to_recheck: list[str] = []
    if stats["e_c_drift"] > 0 or stats["gap"] > 0:
        e_to_recheck = list(local_e_mesas)[:max_reconciliacion_mesas]
    if e_to_recheck:
        print(f"  [reconciliacion] Re-consultando {len(e_to_recheck)} mesas E (drift={stats['e_c_drift']})")

    if stats["gap"] == 0 and not e_to_recheck:
        _write_reconciliacion_estado(work_dir, stats)
        return stats

    # 3. Para gap>0: paginar /actas para encontrar mesas C que nos faltan
    extra_gap_mesas: list[str] = []
    if stats["gap"] > 0:
        try:
            onpe_c_codes = client.get_contabilized_mesas(
                id_eleccion,
                include_observadas=True,
                max_pages_per_ambito=max_paginas_reconciliacion,
            )
            onpe_c_set = set(onpe_c_codes)
            extra_gap_mesas = [m for m in onpe_c_set if m not in local_done]
            stats["gap_mesas_detectadas"] = len(extra_gap_mesas)
            print(f"  [reconciliacion] /actas C/O: {len(onpe_c_set)} | gap_mesas nuevas: {len(extra_gap_mesas)}")
        except Exception as exc:
            print(f"  [reconciliacion] Error paginando /actas: {exc}")

    # 4. Re-consultar: E->C candidates primero, luego gap_mesas nuevas
    to_query = (e_to_recheck + extra_gap_mesas)[:max_reconciliacion_mesas]
    if not to_query:
        _write_reconciliacion_estado(work_dir, stats)
        return stats
    # 5. Re-consultar en paralelo (mismo pool size que main loop)
    actas_track_path = work_dir / "actas_descargadas.tsv"
    actas_downloaded_keys: set[str] = set()
    if descargar_pdfs and actas_dir is not None:
        from .pdfs import load_acta_download_keys
        actas_downloaded_keys = load_acta_download_keys(actas_track_path)

    batch_results: list[MesaResult] = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(client.get_mesa_acta, m, id_eleccion): m for m in to_query}
        for future in as_completed(futures):
            codigo_mesa = futures[future]
            try:
                result = future.result(timeout=30)
                if result is not None:
                    batch_results.append(result)
                    if descargar_pdfs and actas_dir is not None and result.mesa_data is not None:
                        estado = (result.mesa_data.descripcion_estado_acta or "").casefold()
                        if estado in ("contabilizada", "para envío al jee"):
                            try:
                                from .pdfs import download_acta_archivos
                                archivos = client.get_acta_archivos_by_id_acta(
                                    result.id_acta, result.codigo_mesa, result.mesa_data.id_eleccion
                                )
                                download_acta_archivos(
                                    client, archivos, actas_dir,
                                    index_file=actas_track_path,
                                    downloaded_keys=actas_downloaded_keys,
                                    skip_existing=True,
                                )
                            except Exception:
                                pass
                    stats["reconciliadas"] += 1
                    if verbose:
                        estado = result.mesa_data.descripcion_estado_acta if result.mesa_data else "?"
                        print(f"    reconciliada {codigo_mesa}: {estado}")
            except Exception as exc:
                stats["errores"] += 1
                if verbose:
                    print(f"    error reconciliacion {codigo_mesa}: {exc}")

    if batch_results:
        _flush_batch(batch_results, output_dir)

    print(
        f"  [reconciliacion] reconciliadas={stats['reconciliadas']} "
        f"errores={stats['errores']} (de {len(to_query)} consultadas)"
    )
    _write_reconciliacion_estado(work_dir, stats)
    return stats


def _write_reconciliacion_estado(work_dir: Path, stats: dict[str, int]) -> None:
    path = work_dir / "reconciliacion_estado.txt"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        f"timestamp\t{ts}",
        f"c_onpe\t{stats.get('c_onpe_pure', stats['c_onpe'])}",
        f"e_onpe\t{stats['c_onpe'] - stats.get('c_onpe_pure', stats['c_onpe'])}",
        f"c_local\t{stats['c_local']}",
        f"e_local\t{stats.get('e_local', 0)}",
        f"e_c_drift\t{stats.get('e_c_drift', 0)}",
        f"gap\t{stats['gap']}",
        f"pendientes_onpe\t{stats.get('pendientes_onpe', 0)}",
        f"gap_mesas_detectadas\t{stats['gap_mesas_detectadas']}",
        f"reconciliadas\t{stats['reconciliadas']}",
        f"errores\t{stats['errores']}",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")



def run_mesas(client: OnpeClient, args: argparse.Namespace, output_dir: Path, work_dir: Path) -> None:
    # 1. Detect election
    id_eleccion = args.id_eleccion or client.get_active_presidential_election_id()
    print(f"idEleccion: {id_eleccion}")

    # 2. Fetch and write full geographic hierarchy once per run
    print("Descargando jerarquía geográfica...")
    ubicaciones = client.get_ubicaciones(id_eleccion)
    upsert_ubicaciones_txt(ubicaciones, output_dir / "ubicaciones.txt")
    print(f"  {len(ubicaciones)} ubigeos escritos en ubicaciones.txt")

    # 3. Determine which mesas to process using delta approach:
    #    ONPE's /actas gives us exactly which mesas are C/O; we only scrape what's new.
    pending_path = work_dir / "mesas_pendientes.txt"

    # Delta detection via /actas paging is handled by --reconciliar.
    # Keep contabilized_from_onpe=[] so the loop falls back to mesas_pendientes.txt
    # (avoids paginating 900+ pages on every cycle).
    contabilized_from_onpe: list[str] = []
    print("Consultando /actas para obtener mesas contabilizadas...")
    print(f"  {len(contabilized_from_onpe)} mesas C/O desde /actas")

    if contabilized_from_onpe:
        # Load what we already have scraped as Contabilizada
        already_done: set[str] = set()
        mesas_data_path = output_dir / "mesas_data.txt"
        if mesas_data_path.exists():
            with mesas_data_path.open("r", encoding="utf-8") as _f:
                for _row in csv.DictReader(_f, delimiter="\t"):
                    if _row.get("descripcion_estado_acta", "").casefold() == "contabilizada":
                        already_done.add(_row["codigo_mesa"])

        # Delta = contabilized by ONPE but not yet in our data
        delta = [m for m in contabilized_from_onpe if m not in already_done]
        # Also retry any previously errored mesas still in pending file
        errored: list[str] = []
        if pending_path.exists():
            pending_set = set(load_pending_mesas_txt(pending_path))
            onpe_set = set(contabilized_from_onpe)
            errored = [m for m in pending_set if m in onpe_set and m not in already_done and m not in set(delta)]
        mesas = delta + errored
        print(
            f"  Delta: {len(delta)} nuevas C/O + {len(errored)} reintentos con error "
            f"(ya completadas: {len(already_done)})"
        )
    elif not args.redescubrir and pending_path.exists():
        mesas = load_pending_mesas_txt(pending_path)
        print(f"Reanudando desde mesas_pendientes.txt: {len(mesas)} mesas")
    else:
        mesas = client.get_all_mesas(election_id=id_eleccion)
        print(f"Mesas descubiertas desde mesas.json: {len(mesas)}")

    max_workers = max(1, min(args.max_workers, 32))
    batch_size = max(1, args.batch_size)
    tiempo_max_s = getattr(args, "tiempo_max", 0) * 60
    start_time = time.time()
    descargar_pdfs = getattr(args, "descargar_pdfs", False)
    actas_dir = Path(args.actas_dir)
    actas_track_path = work_dir / "actas_descargadas.tsv"
    actas_downloaded_keys = load_acta_download_keys(actas_track_path) if descargar_pdfs else set()

    # Smart ordering only needed when falling back to pending list (no /actas delta)
    if not contabilized_from_onpe and not getattr(args, "no_smart_order", False):
        active_ubigeos = client.get_active_ubigeos(id_eleccion)
        known_ubigeo: dict[str, str] = {}
        mesas_data_path = output_dir / "mesas_data.txt"
        if mesas_data_path.exists():
            with mesas_data_path.open("r", encoding="utf-8") as _f:
                for _row in csv.DictReader(_f, delimiter="\t"):
                    known_ubigeo[_row["codigo_mesa"]] = _row["id_ubigeo"]
        active_pending   = [m for m in mesas if known_ubigeo.get(m, "") in active_ubigeos]
        unknown_pending  = [m for m in mesas if m not in known_ubigeo]
        inactive_pending = [m for m in mesas if m in known_ubigeo and known_ubigeo[m] not in active_ubigeos]
        mesas = active_pending + unknown_pending + inactive_pending
        print(
            f"  Orden inteligente — distritos activos: {len(active_ubigeos)} | "
            f"activos: {len(active_pending)} | desconocidos: {len(unknown_pending)} | "
            f"inactivos: {len(inactive_pending)}"
        )

    processed = 0
    errors = 0
    sin_datos = 0  # mesas that returned no data (not yet published by ONPE)
    pending_after: list[str] = []
    batch_results: list[MesaResult] = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit in chunks so we don't queue all 92k futures at once
        for chunk_start in range(0, len(mesas), batch_size):
            chunk = mesas[chunk_start : chunk_start + batch_size]
            futures = {
                executor.submit(client.get_mesa_acta, code, id_eleccion): code
                for code in chunk
            }
            for future in as_completed(futures):
                codigo_mesa = futures[future]
                try:
                    result = future.result()
                    if result is None:
                        # No data yet for this mesa — keep pending, not an error
                        sin_datos += 1
                        pending_after.append(codigo_mesa)
                    else:
                        batch_results.append(result)
                        estado = (result.mesa_data.descripcion_estado_acta if result.mesa_data else "")
                        # "Para envío al JEE" = votos ya capturados, en camino a C → tratar como done
                        _ESTADOS_DONE = {"contabilizada", "para envío al jee"}
                        is_done = estado.casefold() in _ESTADOS_DONE
                        # Only download PDFs when the mesa is done (C or E) — avoids extra API calls for P mesas
                        if descargar_pdfs and is_done and result.mesa_data is not None:
                            try:
                                archivos = client.get_acta_archivos_by_id_acta(
                                    result.id_acta,
                                    result.codigo_mesa,
                                    result.mesa_data.id_eleccion,
                                )
                                downloads = download_acta_archivos(
                                    client,
                                    archivos,
                                    actas_dir,
                                    index_file=actas_track_path,
                                    downloaded_keys=actas_downloaded_keys,
                                    skip_existing=True,
                                )
                                if args.verbose:
                                    descargados = sum(1 for d in downloads if d.status == "downloaded")
                                    omitidos = sum(1 for d in downloads if d.status == "skipped_existing")
                                    fallidos = sum(1 for d in downloads if d.status == "failed")
                                    print(
                                        f"  PDFs {codigo_mesa}: descargados={descargados} "
                                        f"omitidos={omitidos} fallidos={fallidos}"
                                    )
                            except Exception as pdf_exc:
                                if args.verbose:
                                    print(f"  PDF error {codigo_mesa}: {pdf_exc}")
                        if not is_done:
                            pending_after.append(codigo_mesa)
                    processed += 1
                except Exception as exc:
                    errors += 1
                    pending_after.append(codigo_mesa)
                    if args.verbose:
                        cause = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
                        detail = f" (causa: {cause})" if cause else ""
                        print(f"  Error {codigo_mesa}: {exc}{detail}")

            # Write batch to disk and clear buffer
            _flush_batch(batch_results, output_dir)
            batch_results = []

            done = min(chunk_start + batch_size, len(mesas))
            print(
                f"  {done}/{len(mesas)} mesas | "
                f"errores={errors} | sin_datos={sin_datos} | pendientes={len(pending_after)}"
            )

            if tiempo_max_s > 0 and (time.time() - start_time) >= tiempo_max_s:
                # Move unprocessed mesas to the front so next run advances the sweep.
                # Without this, short time windows can keep revisiting the same prefix.
                remaining = mesas[chunk_start + batch_size :]
                pending_after = remaining + pending_after
                print(f"  Tiempo maximo alcanzado ({args.tiempo_max} min). "
                      f"{len(remaining)} mesas sin procesar quedan pendientes.")
                break

    write_pending_mesas_txt(pending_after, pending_path)
    print(
        f"\nCompletado: procesadas={processed} errores={errors} "
        f"sin_datos={sin_datos} pendientes={len(pending_after)}"
    )
    print(f"Salidas en: {output_dir}")

    # Reconciliation step: detect and close gap between C_onpe and C_local
    if getattr(args, "reconciliar", False):
        _run_reconciliacion(
            client=client,
            id_eleccion=id_eleccion,
            output_dir=output_dir,
            work_dir=work_dir,
            max_reconciliacion_mesas=getattr(args, "max_reconciliacion_mesas", 200),
            max_paginas_reconciliacion=getattr(args, "max_paginas_reconciliacion", 50),
            descargar_pdfs=getattr(args, "descargar_pdfs", False),
            actas_dir=Path(args.actas_dir) if getattr(args, "descargar_pdfs", False) else None,
            verbose=getattr(args, "verbose", False),
        )


def _load_mesas_source(mesas_file: Path) -> list[tuple[str, int]]:
    if not mesas_file.exists():
        raise FileNotFoundError(f"No existe el archivo de mesas fuente: {mesas_file}")
    with mesas_file.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        if not reader.fieldnames or "codigo_mesa" not in reader.fieldnames:
            raise ValueError(f"El archivo {mesas_file} no tiene la columna codigo_mesa")
        rows: list[tuple[str, int]] = []
        for row in reader:
            codigo_mesa = str(row.get("codigo_mesa") or "").strip()
            if not codigo_mesa:
                continue
            id_eleccion = int(row.get("id_eleccion") or 10)
            rows.append((codigo_mesa.zfill(6), id_eleccion))
    return rows


def run_pdfs(client: OnpeClient, args: argparse.Namespace, output_dir: Path, work_dir: Path) -> None:
    mesas_file = Path(args.mesas_fuente)
    actas_dir = Path(args.actas_dir)
    migrate_flat_acta_tree(actas_dir)
    actas_track_path = work_dir / "actas_descargadas.tsv"
    downloaded_keys = load_acta_download_keys(actas_track_path)

    rows = _load_mesas_source(mesas_file)
    print(f"Mesas fuente: {mesas_file} ({len(rows)} filas)")
    print(f"Salida PDFs: {actas_dir}")

    processed = 0
    downloaded = 0
    skipped = 0
    failed = 0
    batch_size = max(1, args.batch_size)
    max_workers = max(1, min(args.max_workers, 5))
    tiempo_max_s = getattr(args, "tiempo_max", 0) * 60
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for chunk_start in range(0, len(rows), batch_size):
            chunk = rows[chunk_start : chunk_start + batch_size]
            futures = {
                executor.submit(client.get_acta_archivos, codigo_mesa, id_eleccion): (codigo_mesa, id_eleccion)
                for codigo_mesa, id_eleccion in chunk
            }
            for future in as_completed(futures):
                codigo_mesa, id_eleccion = futures[future]
                try:
                    archivos = future.result()
                    downloads = download_acta_archivos(
                        client,
                        archivos,
                        actas_dir,
                        index_file=actas_track_path,
                        downloaded_keys=downloaded_keys,
                        skip_existing=True,
                    )
                    processed += 1
                    downloaded += sum(1 for d in downloads if d.status == "downloaded")
                    skipped += sum(1 for d in downloads if d.status == "skipped_existing")
                    failed += sum(1 for d in downloads if d.status == "failed")
                    if args.verbose:
                        print(
                            f"  {codigo_mesa}: archivos={len(downloads)} "
                            f"descargados={sum(1 for d in downloads if d.status == 'downloaded')} "
                            f"omitidos={sum(1 for d in downloads if d.status == 'skipped_existing')} "
                            f"fallidos={sum(1 for d in downloads if d.status == 'failed')}"
                        )
                except Exception as exc:
                    failed += 1
                    if args.verbose:
                        print(f"  Error {codigo_mesa}: {exc}")

            done = min(chunk_start + batch_size, len(rows))
            print(
                f"  {done}/{len(rows)} mesas | descargados={downloaded} "
                f"omitidos={skipped} fallidos={failed}"
            )

            if tiempo_max_s > 0 and (time.time() - start_time) >= tiempo_max_s:
                print(
                    f"  Tiempo maximo alcanzado ({args.tiempo_max} min). "
                    f"Se detuvo en {done}/{len(rows)} mesas."
                )
                break

    print(
        f"\nCompletado PDFs: mesas={processed} descargados={downloaded} "
        f"omitidos={skipped} fallidos={failed}"
    )


def run_resumen_geo_mode(
    client: OnpeClient, args: argparse.Namespace, output_dir: Path, work_dir: Path
) -> None:
    id_eleccion = args.id_eleccion or client.get_active_presidential_election_id()
    resumen_dir = Path(args.resumen_dir)
    force_full = getattr(args, "resumen_full", False)
    run_resumen_geo(client, id_eleccion, output_dir, resumen_dir, work_dir, force_full=force_full)


def _run_once(client: OnpeClient, args: argparse.Namespace, output_dir: Path, work_dir: Path) -> None:
    if args.modo == "mesas":
        run_mesas(client, args, output_dir, work_dir)
    elif args.modo == "resumen-geo":
        run_resumen_geo_mode(client, args, output_dir, work_dir)
    elif args.modo == "pdfs":
        run_pdfs(client, args, output_dir, work_dir)
    else:
        run_resumen(client, args, output_dir, work_dir)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    output_dir = Path(args.salida)
    output_dir.mkdir(parents=True, exist_ok=True)

    work_dir = Path(args.trabajo)
    work_dir.mkdir(parents=True, exist_ok=True)

    client = OnpeClient(timeout_seconds=args.timeout if hasattr(args, "timeout") else 20)

    if args.intervalo_segundos <= 0:
        _run_once(client, args, output_dir, work_dir)
        return

    print(f"Iniciando modo continuo cada {args.intervalo_segundos} segundos")
    while True:
        try:
            _run_once(client, args, output_dir, work_dir)
        except Exception as exc:
            print(f"Error durante extraccion: {exc}")
            if args.verbose:
                raise
        time.sleep(args.intervalo_segundos)


if __name__ == "__main__":
    main()
