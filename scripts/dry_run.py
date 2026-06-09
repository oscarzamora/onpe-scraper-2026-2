"""
Dry run con data ficticia — ejercita el pipeline completo sin tocar la API de ONPE.

Genera ~20 mesas falsas (Perú + exterior), corre _flush_batch y exporters,
imprime las tablas resultantes y simula el mecanismo de resume.

Uso:
    python scripts/dry_run.py
    python scripts/dry_run.py --salida output_test
"""
from __future__ import annotations

import argparse
import random
import shutil
import sys
from pathlib import Path

# Allow running from repo root without installing the package
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from onpe_scraper.exporters import (
    upsert_agrupaciones_txt,
    upsert_locales_txt,
    upsert_mesas_data_txt,
    upsert_ubicaciones_txt,
    upsert_votos_txt,
    write_pending_mesas_txt,
)
from onpe_scraper.models import (
    AgrupacionData,
    MesaData,
    MesaResult,
    UbicacionData,
    VotoData,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

ID_ELECCION = 99  # ficticio

AGRUPACIONES = [
    AgrupacionData(partido_id=1, codigo_op="P01", nombre="Partido Azul"),
    AgrupacionData(partido_id=2, codigo_op="P02", nombre="Partido Rojo"),
]

UBICACIONES = [
    # Peru
    UbicacionData("150101", "peru", "LIMA", "LIMA", "LIMA", "", "", ""),
    UbicacionData("150110", "peru", "LIMA", "LIMA", "SAN BORJA", "", "", ""),
    UbicacionData("040101", "peru", "AREQUIPA", "AREQUIPA", "AREQUIPA", "", "", ""),
    UbicacionData("130101", "peru", "LA LIBERTAD", "TRUJILLO", "TRUJILLO", "", "", ""),
    UbicacionData("250101", "peru", "UCAYALI", "CORONEL PORTILLO", "CALLERIA", "", "", ""),
    # Exterior
    UbicacionData("920101", "exterior", "", "", "", "AMERICA", "ESTADOS UNIDOS", "MIAMI"),
    UbicacionData("940201", "exterior", "", "", "", "EUROPA", "ESPANA", "MADRID"),
    UbicacionData("930101", "exterior", "", "", "", "ASIA", "JAPON", "TOKIO"),
]

LOCALES = {
    "150101": ("L001", "IE 7096 VILLA EL SALVADOR"),
    "150110": ("L002", "CE PARROQUIAL SAN BORJA"),
    "040101": ("L003", "COLEGIO NACIONAL AREQUIPA"),
    "130101": ("L004", "IE SAN JUAN"),
    "250101": ("L005", "IE FAUSTINO MALDONADO"),
    "920101": ("L006", "CONSULADO MIAMI"),
    "940201": ("L007", "CONSULADO MADRID"),
    "930101": ("L008", "CONSULADO TOKIO"),
}

random.seed(42)


def _fake_mesa(codigo_mesa: str, ubigeo: str, contabilizada: bool) -> MesaResult:
    codigo_local, nombre_local = LOCALES[ubigeo]
    electores = random.randint(200, 350)
    emitidos = int(electores * random.uniform(0.70, 0.92))
    validos = int(emitidos * random.uniform(0.93, 0.99))
    participacion = round(emitidos / electores * 100, 2)

    v1 = random.randint(int(validos * 0.40), int(validos * 0.60))
    v2 = validos - v1

    mesa_data = MesaData(
        codigo_mesa=codigo_mesa,
        id_eleccion=ID_ELECCION,
        id_ubigeo=ubigeo,
        nombre_local_votacion=nombre_local,
        codigo_local_votacion=codigo_local,
        id_ambito_geografico=1 if ubigeo[:2] <= "25" else 2,
        electores_habiles=electores,
        votos_emitidos=emitidos,
        votos_validos=validos,
        total_asistentes=emitidos,
        participacion_ciudadana=participacion,
        codigo_estado_acta="C" if contabilizada else "P",
        descripcion_estado_acta="Contabilizada" if contabilizada else "Procesada",
    )
    votos = [
        VotoData(codigo_mesa, ID_ELECCION, 1, v1,
                 round(v1 / validos * 100, 2), round(v1 / emitidos * 100, 2)),
        VotoData(codigo_mesa, ID_ELECCION, 2, v2,
                 round(v2 / validos * 100, 2), round(v2 / emitidos * 100, 2)),
    ]
    return MesaResult(
        codigo_mesa=codigo_mesa,
        id_acta=int(codigo_mesa),
        mesa_data=mesa_data,
        agrupaciones=AGRUPACIONES,
        votos=votos,
    )


def _build_fake_results() -> tuple[list[MesaResult], list[str]]:
    all_results: list[MesaResult] = []
    pending: list[str] = []
    mesa_num = 1
    for ubigeo in LOCALES:
        for i in range(2):
            code = str(mesa_num).zfill(6)
            contabilizada = (i == 0)
            result = _fake_mesa(code, ubigeo, contabilizada)
            all_results.append(result)
            if not contabilizada:
                pending.append(code)
            mesa_num += 1
    return all_results, pending


def _print_tsv(path: Path, max_rows: int = 8) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    header = lines[0].split("\t")
    col_w = max(14, max(len(h) for h in header))
    print(f"\n{'─'*4} {path.name} ({len(lines)-1} filas) {'─'*35}")
    for line in lines[:max_rows + 1]:
        cells = line.split("\t")
        print("  ".join(c[:col_w].ljust(col_w) for c in cells))
    if len(lines) > max_rows + 1:
        print(f"  ... ({len(lines) - max_rows - 1} filas más)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Dry run con data ficticia")
    parser.add_argument("--salida", default="output_test",
                        help="Carpeta de salida (se borra y recrea)")
    args = parser.parse_args()

    out = Path(args.salida)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    work = out / "work"
    work.mkdir()

    print("=" * 60)
    print("DRY RUN — pipeline completo con data ficticia")
    print("=" * 60)
    print(f"  {len(UBICACIONES)} ubigeos  |  {len(LOCALES)} locales  |  2 partidos")

    # ── Pasada 1: mitad contabilizada, mitad pendiente ────────────────────────
    print("\n--- PASADA 1 (simula primera ejecución del scraper) ---")
    upsert_ubicaciones_txt(UBICACIONES, out / "ubicaciones.txt")

    results, pending = _build_fake_results()
    print(f"  {len(results)} mesas procesadas | {len(pending)} pendientes")

    upsert_mesas_data_txt(results, out / "mesas_data.txt")
    upsert_votos_txt(results, out / "votos.txt")
    upsert_agrupaciones_txt(results, out / "agrupaciones.txt")
    upsert_locales_txt(results, out / "locales.txt")
    write_pending_mesas_txt(pending, work / "mesas_pendientes.txt")

    _print_tsv(out / "ubicaciones.txt")
    _print_tsv(out / "mesas_data.txt")
    _print_tsv(out / "votos.txt")
    _print_tsv(out / "agrupaciones.txt")
    _print_tsv(out / "locales.txt")

    print(f"\n---- work/mesas_pendientes.txt ({len(pending)} mesas pendientes) ----")
    for code in pending:
        print(f"  {code}")

    # ── Pasada 2: resume — solo las pendientes, ahora todas contabilizadas ────
    print("\n\n--- PASADA 2 (resume automático desde mesas_pendientes.txt) ---")
    resumed = []
    for code in pending:
        for r in results:
            if r.codigo_mesa == code and r.mesa_data:
                r.mesa_data.codigo_estado_acta = "C"
                r.mesa_data.descripcion_estado_acta = "Contabilizada"
                resumed.append(r)
                break

    print(f"  {len(resumed)} mesas re-consultadas y contabilizadas")
    upsert_mesas_data_txt(resumed, out / "mesas_data.txt")
    upsert_votos_txt(resumed, out / "votos.txt")
    upsert_locales_txt(resumed, out / "locales.txt")
    write_pending_mesas_txt([], work / "mesas_pendientes.txt")

    _print_tsv(out / "mesas_data.txt")

    pending_content = (work / "mesas_pendientes.txt").read_text().strip()
    print(f"\n  work/mesas_pendientes.txt: "
          f"{'(vacío) → próximo run se saltea ✅' if not pending_content else pending_content}")

    print(f"\n{'=' * 60}")
    print(f"✅  Dry run completo. Archivos en: {out.resolve()}")
    print(f"    {', '.join(p.name for p in sorted(out.glob('*.txt')))}")


if __name__ == "__main__":
    main()
