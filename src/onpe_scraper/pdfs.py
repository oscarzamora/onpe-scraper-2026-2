from __future__ import annotations

import csv
import hashlib
import time
from pathlib import Path
from typing import Iterable

import requests

from .client import OnpeClient
from .models import ActaArchivoData, ActaPdfDownload


_TRACK_FIELDS = [
    "codigo_mesa",
    "id_eleccion",
    "id_acta",
    "archivo_id",
    "orden",
    "tipo",
    "nombre",
    "descripcion",
    "output_path",
    "bytes_written",
    "sha256",
    "status",
    "error",
]


def acta_bucket(codigo_mesa: str) -> str:
    return codigo_mesa.zfill(6)[:2]


def acta_pdf_path(output_dir: str | Path, codigo_mesa: str, orden: int) -> Path:
    base = Path(output_dir)
    return base / acta_bucket(codigo_mesa) / f"{codigo_mesa}-{orden}.pdf"


def migrate_flat_acta_tree(output_dir: str | Path) -> None:
    base = Path(output_dir)
    if not base.exists():
        return
    for path in base.glob("*.pdf"):
        stem = path.stem
        if "-" not in stem:
            continue
        codigo_mesa, _, suffix = stem.partition("-")
        if not codigo_mesa.isdigit():
            continue
        target = acta_pdf_path(base, codigo_mesa, int(suffix) if suffix.isdigit() else 0)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            path.unlink(missing_ok=True)
        else:
            path.replace(target)


def load_acta_download_keys(index_file: str | Path) -> set[tuple[str, str, str]]:
    path = Path(index_file)
    keys: set[tuple[str, str, str]] = set()
    if not path.exists():
        return keys
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            key = (
                str(row.get("codigo_mesa") or ""),
                str(row.get("id_eleccion") or ""),
                str(row.get("archivo_id") or ""),
            )
            if all(key):
                keys.add(key)
    return keys


def append_acta_download_record(index_file: str | Path, download: ActaPdfDownload) -> Path:
    path = Path(index_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = path.exists()
    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_TRACK_FIELDS, delimiter="\t")
        if not file_exists:
            writer.writeheader()
        writer.writerow(
            {
                "codigo_mesa": download.codigo_mesa,
                "id_eleccion": download.id_eleccion,
                "id_acta": download.id_acta,
                "archivo_id": download.archivo_id,
                "orden": download.orden,
                "tipo": download.tipo,
                "nombre": download.nombre,
                "descripcion": download.descripcion,
                "output_path": download.output_path,
                "bytes_written": download.bytes_written,
                "sha256": download.sha256,
                "status": download.status,
                "error": download.error or "",
            }
        )
    return path


def _write_pdf_from_url(signed_url: str, dst: Path, timeout_seconds: int) -> tuple[int, str]:
    tmp = dst.with_suffix(".pdf.tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    sha = hashlib.sha256()
    bytes_written = 0

    with requests.get(signed_url, stream=True, timeout=timeout_seconds) as response:
        response.raise_for_status()
        with tmp.open("wb") as f:
            for chunk in response.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                f.write(chunk)
                sha.update(chunk)
                bytes_written += len(chunk)

    if bytes_written < 1024:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"PDF sospechosamente pequeno: {bytes_written} bytes")
    if not tmp.read_bytes().startswith(b"%PDF"):
        tmp.unlink(missing_ok=True)
        raise RuntimeError("La respuesta descargada no parece un PDF valido")

    tmp.replace(dst)
    return bytes_written, sha.hexdigest()


def download_acta_archivos(
    client: OnpeClient,
    archivos: Iterable[ActaArchivoData],
    output_dir: str | Path,
    *,
    index_file: str | Path | None = None,
    downloaded_keys: set[tuple[str, str, str]] | None = None,
    skip_existing: bool = True,
) -> list[ActaPdfDownload]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    if downloaded_keys is not None:
        index_keys = downloaded_keys
    else:
        index_keys = load_acta_download_keys(index_file) if index_file is not None else set()
    downloads: list[ActaPdfDownload] = []

    for archivo in archivos:
        dst = acta_pdf_path(output_path, archivo.codigo_mesa, archivo.orden)
        key = (archivo.codigo_mesa, str(archivo.id_eleccion), archivo.archivo_id)

        if skip_existing and dst.exists() and dst.stat().st_size > 0:
            try:
                sha = hashlib.sha256(dst.read_bytes()).hexdigest()
            except Exception:
                sha = ""
            download = ActaPdfDownload(
                codigo_mesa=archivo.codigo_mesa,
                id_eleccion=archivo.id_eleccion,
                id_acta=archivo.id_acta,
                archivo_id=archivo.archivo_id,
                orden=archivo.orden,
                tipo=archivo.tipo,
                nombre=archivo.nombre,
                descripcion=archivo.descripcion,
                output_path=str(dst),
                bytes_written=dst.stat().st_size,
                sha256=sha,
                status="skipped_existing",
            )
            downloads.append(download)
            if index_file is not None and key not in index_keys:
                append_acta_download_record(index_file, download)
                index_keys.add(key)
            continue

        try:
            signed_url = client.get_acta_signed_url(archivo.archivo_id)
            bytes_written, sha256 = _write_pdf_from_url(
                signed_url,
                dst,
                client.timeout_seconds,
            )
            download = ActaPdfDownload(
                codigo_mesa=archivo.codigo_mesa,
                id_eleccion=archivo.id_eleccion,
                id_acta=archivo.id_acta,
                archivo_id=archivo.archivo_id,
                orden=archivo.orden,
                tipo=archivo.tipo,
                nombre=archivo.nombre,
                descripcion=archivo.descripcion,
                output_path=str(dst),
                bytes_written=bytes_written,
                sha256=sha256,
                status="downloaded",
            )
        except Exception as exc:
            download = ActaPdfDownload(
                codigo_mesa=archivo.codigo_mesa,
                id_eleccion=archivo.id_eleccion,
                id_acta=archivo.id_acta,
                archivo_id=archivo.archivo_id,
                orden=archivo.orden,
                tipo=archivo.tipo,
                nombre=archivo.nombre,
                descripcion=archivo.descripcion,
                output_path=str(dst),
                bytes_written=0,
                sha256="",
                status="failed",
                error=str(exc),
            )
        downloads.append(download)
        if index_file is not None and download.status != "failed":
            if key not in index_keys:
                append_acta_download_record(index_file, download)
                index_keys.add(key)
    return downloads
