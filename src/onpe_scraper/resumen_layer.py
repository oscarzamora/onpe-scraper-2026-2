"""
Resumen layer — capa de agregación y exportación oficial de resumen electoral.

Estrategia híbrida:
- Nacional: directo de ONPE API (/eleccion-presidencial/participantes-ubicacion-geografica)
- Departamentos/provincias: bottom-up desde output/mesas_data.txt + output/votos.txt
- Cobertura deps: /resumen-general/mapa-calor nivel_01
- Participación deps: /participacion-ciudadana/ubigeos-total

Full load la primera vez (no existe work/resumen_state.txt).
Delta en runs siguientes (recomputa si cambian los counts).
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .client import OnpeClient

# ---------------------------------------------------------------------- #
# Field definitions                                                        #
# ---------------------------------------------------------------------- #

_NACIONAL_FIELDS = [
    "id_eleccion",
    "id_ambito_geografico",
    "partido_id",
    "nombre_candidato",
    "nombre_agrupacion_politica",
    "votos_validos",
    "pct_votos_validos",
    "pct_votos_emitidos",
    "actas_contabilizadas_pct",
    "contabilizadas",
    "total_actas",
    "participacion_ciudadana",
    "fecha_actualizacion",
    "fuente",
]

_GEO_FIELDS = [
    "id_eleccion",
    "ubigeo",
    "partido_id",
    "nombre_candidato",
    "nombre_agrupacion_politica",
    "votos_validos",
    "pct_votos_validos",
    "pct_votos_emitidos",
    "total_votos_validos_geo",
    "total_votos_emitidos_geo",
    "fuente",
]

_COBERTURA_FIELDS = [
    "id_eleccion",
    "ubigeo",
    "nombre_departamento",
    "actas_contabilizadas",
    "pct_actas_contabilizadas",
    "fuente",
]

_PARTICIPACION_FIELDS = [
    "id_eleccion",
    "ubigeo",
    "nombre_departamento",
    "pct_asistentes",
    "pct_ausentes",
    "fuente",
]


# ---------------------------------------------------------------------- #
# Internal I/O helpers                                                     #
# ---------------------------------------------------------------------- #

def _read_txt(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def _write_txt(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------- #
# State management (full vs delta)                                         #
# ---------------------------------------------------------------------- #

def _load_state(state_path: Path) -> dict[str, str]:
    if not state_path.exists():
        return {}
    out: dict[str, str] = {}
    with state_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def _save_state(
    state_path: Path,
    mode: str,
    id_eleccion: int,
    mesas_count: int,
    votos_count: int,
) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with state_path.open("w", encoding="utf-8") as f:
        f.write(f"id_eleccion={id_eleccion}\n")
        f.write(f"last_run_utc={now}\n")
        f.write(f"mode={mode}\n")
        f.write(f"mesas_count={mesas_count}\n")
        f.write(f"votos_count={votos_count}\n")


def _detect_mode(state_path: Path, mesas_count: int, votos_count: int, force_full: bool) -> str:
    if force_full or not state_path.exists():
        return "full"
    state = _load_state(state_path)
    if (
        state.get("mesas_count") == str(mesas_count)
        and state.get("votos_count") == str(votos_count)
    ):
        return "delta_skip"  # nothing changed — skip geo rebuild
    return "delta"


# ---------------------------------------------------------------------- #
# Nacional (from ONPE API)                                                 #
# ---------------------------------------------------------------------- #

def build_resumen_nacional(
    client: OnpeClient,
    id_eleccion: int,
    resumen_dir: Path,
    id_ambito_geografico: int = 1,
) -> int:
    """Fetch national candidate totals directly from ONPE and write resumen_nacional.txt."""
    try:
        candidates = client.get_candidates(
            election_id=id_eleccion,
            tipo_filtro="eleccion",
        )
        totals = client.get_totals(
            election_id=id_eleccion,
            tipo_filtro="eleccion",
        )
    except Exception as exc:
        print(f"  [resumen] Nacional ONPE error: {exc}")
        return 0

    fecha_ms = totals.get("fechaActualizacion")
    fecha_str = ""
    if fecha_ms:
        try:
            fecha_str = datetime.fromtimestamp(int(fecha_ms) / 1000, tz=timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
        except Exception:
            fecha_str = str(fecha_ms)

    rows: list[dict] = []
    for c in candidates:
        partido_id = c.get("adAgrupacionPolitica") or c.get("idAgrupacionPolitica") or ""
        rows.append({
            "id_eleccion": str(id_eleccion),
            "id_ambito_geografico": str(id_ambito_geografico),
            "partido_id": str(partido_id),
            "nombre_candidato": c.get("nombreCandidato", ""),
            "nombre_agrupacion_politica": c.get("nombreAgrupacionPolitica", ""),
            "votos_validos": str(c.get("totalVotosValidos", "")),
            "pct_votos_validos": str(c.get("porcentajeVotosValidos", "")),
            "pct_votos_emitidos": str(c.get("porcentajeVotosEmitidos", "")),
            "actas_contabilizadas_pct": str(totals.get("actasContabilizadas", "")),
            "contabilizadas": str(totals.get("contabilizadas", "")),
            "total_actas": str(totals.get("totalActas", "")),
            "participacion_ciudadana": str(totals.get("participacionCiudadana", "")),
            "fecha_actualizacion": fecha_str,
            "fuente": "onpe_api",
        })

    _write_txt(resumen_dir / "resumen_nacional.txt", _NACIONAL_FIELDS, rows)
    return len(rows)


# ---------------------------------------------------------------------- #
# Departamentos / Provincias (bottom-up from local output/ files)          #
# ---------------------------------------------------------------------- #

def _build_geo_resumen(
    mesas_rows: list[dict[str, str]],
    votos_rows: list[dict[str, str]],
    agrupaciones: dict[str, dict[str, str]],
    id_eleccion: int,
    nivel: str,  # "departamento" | "provincia"
) -> list[dict]:
    """Aggregate votos by geographic level from local scraped data."""
    id_e = str(id_eleccion)

    # mesa -> ubigeo (only our election)
    mesa_ubigeo: dict[str, str] = {
        r["codigo_mesa"]: r["id_ubigeo"]
        for r in mesas_rows
        if r.get("id_eleccion") == id_e and r.get("id_ubigeo")
    }

    def geo_key(ubigeo: str) -> str:
        if nivel == "departamento":
            return ubigeo[:2] + "0000"
        else:  # provincia
            return ubigeo[:4] + "00"

    # Accumulate: (geo_ubigeo, partido_id) -> {votos_validos, total_votos_validos, total_votos_emitidos}
    sums: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {"votos_validos": 0, "total_votos_validos_geo": 0, "total_votos_emitidos_geo": 0}
    )

    for v in votos_rows:
        if v.get("id_eleccion") != id_e:
            continue
        ubigeo = mesa_ubigeo.get(v.get("codigo_mesa", ""), "")
        if not ubigeo or ubigeo == "000000":
            continue
        geo = geo_key(ubigeo)
        if geo[:2] in ("00",):  # skip national placeholder
            continue
        partido_id = v.get("partido_id", "")
        key = (geo, partido_id)
        votos = int(v.get("votos") or 0)
        sums[key]["votos_validos"] += votos

    # Also compute geo-level totals (sum across all parties for each geo)
    geo_total_validos: dict[str, int] = defaultdict(int)
    geo_total_emitidos: dict[str, int] = defaultdict(int)
    for r in mesas_rows:
        if r.get("id_eleccion") != id_e:
            continue
        ubigeo = r.get("id_ubigeo", "")
        if not ubigeo or ubigeo == "000000":
            continue
        geo = geo_key(ubigeo)
        geo_total_validos[geo] += int(r.get("votos_validos") or 0)
        geo_total_emitidos[geo] += int(r.get("votos_emitidos") or 0)

    rows: list[dict] = []
    for (geo, partido_id), acc in sorted(sums.items()):
        total_validos = geo_total_validos.get(geo, 0)
        total_emitidos = geo_total_emitidos.get(geo, 0)
        pct_validos = round(acc["votos_validos"] / total_validos * 100, 3) if total_validos else 0.0
        pct_emitidos = round(acc["votos_validos"] / total_emitidos * 100, 3) if total_emitidos else 0.0
        ag = agrupaciones.get(partido_id, {})
        rows.append({
            "id_eleccion": id_e,
            "ubigeo": geo,
            "partido_id": partido_id,
            "nombre_candidato": ag.get("nombre", ""),
            "nombre_agrupacion_politica": ag.get("nombre", ""),
            "votos_validos": str(acc["votos_validos"]),
            "pct_votos_validos": str(pct_validos),
            "pct_votos_emitidos": str(pct_emitidos),
            "total_votos_validos_geo": str(total_validos),
            "total_votos_emitidos_geo": str(total_emitidos),
            "fuente": "local_agregado",
        })
    return rows


def build_resumen_departamentos(
    id_eleccion: int,
    output_dir: Path,
    resumen_dir: Path,
) -> int:
    mesas_rows = _read_txt(output_dir / "mesas_data.txt")
    votos_rows = _read_txt(output_dir / "votos.txt")
    ag_rows = _read_txt(output_dir / "agrupaciones.txt")
    agrupaciones = {r["partido_id"]: r for r in ag_rows}

    rows = _build_geo_resumen(mesas_rows, votos_rows, agrupaciones, id_eleccion, "departamento")
    _write_txt(resumen_dir / "resumen_departamentos.txt", _GEO_FIELDS, rows)
    return len(rows)


def build_resumen_provincias(
    id_eleccion: int,
    output_dir: Path,
    resumen_dir: Path,
) -> int:
    mesas_rows = _read_txt(output_dir / "mesas_data.txt")
    votos_rows = _read_txt(output_dir / "votos.txt")
    ag_rows = _read_txt(output_dir / "agrupaciones.txt")
    agrupaciones = {r["partido_id"]: r for r in ag_rows}

    rows = _build_geo_resumen(mesas_rows, votos_rows, agrupaciones, id_eleccion, "provincia")
    _write_txt(resumen_dir / "resumen_provincias.txt", _GEO_FIELDS, rows)
    return len(rows)


# ---------------------------------------------------------------------- #
# Cobertura departamentos (from ONPE mapa-calor nivel_01)                  #
# ---------------------------------------------------------------------- #

def build_resumen_cobertura_departamentos(
    client: OnpeClient,
    id_eleccion: int,
    resumen_dir: Path,
    ubicaciones: dict[str, str] | None = None,
) -> int:
    try:
        data = client.get_mapa_calor(id_eleccion, tipo_filtro="ubigeo_nivel_01")
    except Exception as exc:
        print(f"  [resumen] Cobertura deps error: {exc}")
        return 0

    # Aggregate by ubigeoNivel01 (department) from province-level rows
    from collections import defaultdict
    dep_actas: dict[str, int] = defaultdict(int)
    dep_total: dict[str, int] = defaultdict(int)

    for item in data:
        dep_raw = item.get("ubigeoNivel01")
        if dep_raw is None:
            continue
        dep_ubigeo = str(int(dep_raw)).zfill(6)
        dep_actas[dep_ubigeo] += int(item.get("actasContabilizadas") or 0)
        # Need total actas per dept — use pct to back-calculate
        pct = float(item.get("porcentajeActasContabilizadas") or 0)
        actas_c = int(item.get("actasContabilizadas") or 0)
        if pct > 0:
            total_est = round(actas_c / (pct / 100))
            dep_total[dep_ubigeo] += total_est

    rows: list[dict] = []
    for dep_ubigeo in sorted(dep_actas):
        actas_c = dep_actas[dep_ubigeo]
        total_est = dep_total.get(dep_ubigeo, 0)
        pct = round(actas_c / total_est * 100, 3) if total_est else 0.0
        nombre = ubicaciones.get(dep_ubigeo, "") if ubicaciones else ""
        rows.append({
            "id_eleccion": str(id_eleccion),
            "ubigeo": dep_ubigeo,
            "nombre_departamento": nombre,
            "actas_contabilizadas": str(actas_c),
            "pct_actas_contabilizadas": str(pct),
            "fuente": "onpe_api",
        })

    _write_txt(resumen_dir / "resumen_cobertura_departamentos.txt", _COBERTURA_FIELDS, rows)
    return len(rows)


# ---------------------------------------------------------------------- #
# Participación departamentos (from ONPE /participacion-ciudadana)         #
# ---------------------------------------------------------------------- #

def build_resumen_participacion_departamentos(
    client: OnpeClient,
    id_eleccion: int,
    resumen_dir: Path,
    ubicaciones: dict[str, str] | None = None,
) -> int:
    try:
        data = client.get_participacion_ubigeos(id_eleccion)
    except Exception as exc:
        print(f"  [resumen] Participación deps error: {exc}")
        return 0

    if not data:
        return 0

    rows: list[dict] = []
    for item in data:
        ubigeo_raw = item.get("ubigeo") or item.get("idUbigeo") or ""
        ubigeo = str(int(ubigeo_raw)).zfill(6) if ubigeo_raw else ""
        if not ubigeo:
            continue
        nombre = item.get("nombre") or item.get("descripcion") or (
            ubicaciones.get(ubigeo, "") if ubicaciones else ""
        )
        rows.append({
            "id_eleccion": str(id_eleccion),
            "ubigeo": ubigeo,
            "nombre_departamento": nombre,
            "pct_asistentes": str(item.get("porcentajeAsistentes") or item.get("pctAsistentes", "")),
            "pct_ausentes": str(item.get("porcentajeAusentes") or item.get("pctAusentes", "")),
            "fuente": "onpe_api",
        })

    _write_txt(resumen_dir / "resumen_participacion_departamentos.txt", _PARTICIPACION_FIELDS, rows)
    return len(rows)


# ---------------------------------------------------------------------- #
# Orchestrator                                                             #
# ---------------------------------------------------------------------- #

def run_resumen_geo(
    client: OnpeClient,
    id_eleccion: int,
    output_dir: Path,
    resumen_dir: Path,
    work_dir: Path,
    force_full: bool = False,
) -> None:
    """
    Build / refresh all resumen files.

    First call (no state file): full build of all 5 outputs.
    Subsequent calls: delta — rebuild geo aggregations only if output/ changed,
    always refresh national from ONPE API.
    """
    resumen_dir.mkdir(parents=True, exist_ok=True)
    state_path = work_dir / "resumen_state.txt"

    mesas_rows = _read_txt(output_dir / "mesas_data.txt")
    votos_rows = _read_txt(output_dir / "votos.txt")
    mesas_count = len(mesas_rows)
    votos_count = len(votos_rows)

    # Build dep ubigeo -> department name from ubicaciones (010101 -> "AMAZONAS")
    dep_nombres: dict[str, str] = {}
    for ub in _read_txt(output_dir / "ubicaciones.txt"):
        ubigeo = ub.get("ubigeo", "")
        dep = ub.get("departamento", "")
        if ubigeo and dep:
            dep_key = ubigeo[:2] + "0000"
            if dep_key not in dep_nombres:
                dep_nombres[dep_key] = dep

    mode = _detect_mode(state_path, mesas_count, votos_count, force_full)
    print(f"\n[resumen] Modo: {mode} | mesas={mesas_count} votos={votos_count}")

    # 1. Nacional — always from ONPE (fast, 2 API calls)
    n = build_resumen_nacional(client, id_eleccion, resumen_dir)
    print(f"  resumen_nacional.txt -> {n} candidatos")

    # 2. Geo aggregations — rebuild unless nothing changed
    if mode != "delta_skip":
        nd = build_resumen_departamentos(id_eleccion, output_dir, resumen_dir)
        print(f"  resumen_departamentos.txt -> {nd} filas")

        np_ = build_resumen_provincias(id_eleccion, output_dir, resumen_dir)
        print(f"  resumen_provincias.txt -> {np_} filas")
    else:
        print("  resumen_departamentos/provincias sin cambios — saltando rebuild")

    # 3. Cobertura — always from ONPE (1 call)
    nc = build_resumen_cobertura_departamentos(client, id_eleccion, resumen_dir, ubicaciones=dep_nombres)
    print(f"  resumen_cobertura_departamentos.txt -> {nc} filas")

    # 4. Participación — always from ONPE (1 call, fails gracefully)
    npart = build_resumen_participacion_departamentos(client, id_eleccion, resumen_dir)
    print(f"  resumen_participacion_departamentos.txt -> {npart} filas")

    _save_state(state_path, mode if mode != "delta_skip" else "delta", id_eleccion, mesas_count, votos_count)
    print(f"[resumen] Completado. Salidas en: {resumen_dir}")
