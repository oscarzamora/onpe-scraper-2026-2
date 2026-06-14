from __future__ import annotations

import random
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from json import JSONDecodeError
from urllib.parse import urlparse
from typing import Any

from curl_cffi import requests as curl_requests

from .models import (
    ActaArchivoData,
    AgrupacionData,
    MesaData,
    MesaResult,
    UbicacionData,
    VotoData,
)


BASE_URL = "https://resultadosegundavuelta.onpe.gob.pe/presentacion-backend"
ASSETS_BASE_URL = "https://resultadosegundavuelta.onpe.gob.pe"
_MIN_MESAS_SANITY = 100
_ACTAS_PAGE_SIZE = 100


@dataclass
class OnpeClient:
    base_url: str = BASE_URL
    timeout_seconds: int = 20
    max_retries: int = 5
    _thread_local: threading.local = field(
        default_factory=threading.local, init=False, repr=False, compare=False
    )

    @property
    def _frontend_referer(self) -> str:
        parsed = urlparse(self.base_url)
        return f"{parsed.scheme}://{parsed.netloc}/main/resumen"

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _request_json_get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self._get_curl_session().get(
            url,
            params=params,
            impersonate="chrome124",
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except JSONDecodeError as exc:
            snippet = response.text[:180].replace("\n", " ")
            raise RuntimeError(
                f"Respuesta no-JSON desde {url}. status={response.status_code}. "
                f"body~={snippet}"
            ) from exc
        if not isinstance(payload, dict):
            raise ValueError(f"Respuesta JSON inesperada ({type(payload)}): {payload}")
        return payload

    def _get_data(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """curl_cffi GET for aggregate endpoints (Chrome impersonation required by ONPE)."""
        payload = self._request_json_get(f"{self.base_url}{path}", params=params)
        if "data" not in payload:
            raise ValueError(f"Respuesta inesperada sin 'data': {payload}")
        return payload["data"]

    def _build_curl_session(self) -> curl_requests.Session:
        session = curl_requests.Session()
        session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json, text/plain, */*",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": self._frontend_referer,
                "Accept-Language": "es-PE,es;q=0.9",
            }
        )
        return session

    def _get_curl_session(self) -> curl_requests.Session:
        """Thread-local curl_cffi session (reused across requests in the same thread)."""
        session = getattr(self._thread_local, "session", None)
        if session is None:
            session = self._build_curl_session()
            self._thread_local.session = session
        return session

    def _get_mesa_record(self, codigo_mesa: str, id_eleccion: int) -> dict[str, Any] | None:
        codigo_mesa = codigo_mesa.zfill(6)
        url = f"{self.base_url}/actas/buscar/mesa"
        last_exc: Exception | None = None

        for attempt in range(self.max_retries):
            try:
                response = self._get_curl_session().get(
                    url,
                    params={"codigoMesa": codigo_mesa, "idEleccion": id_eleccion},
                    impersonate="chrome124",
                    timeout=self.timeout_seconds,
                )
                if response.status_code == 204:
                    return None
                response.raise_for_status()
                if not response.text.strip():
                    return None
                payload = response.json()
                data = payload.get("data")
                if not isinstance(data, list):
                    return None

                matching = [
                    a for a in data if isinstance(a, dict) and a.get("idEleccion") == id_eleccion
                ]
                if not matching:
                    return None
                if len(matching) > 1:
                    warnings.warn(
                        f"Mesa {codigo_mesa}: {len(matching)} actas para idEleccion={id_eleccion}, "
                        "usando la primera.",
                        stacklevel=2,
                    )
                return matching[0]
            except Exception as exc:
                last_exc = exc
                if attempt < self.max_retries - 1:
                    time.sleep(0.5 * (2**attempt) + random.uniform(0.0, 0.5))

        raise RuntimeError(
            f"Mesa {codigo_mesa}: {self.max_retries} intentos fallidos"
        ) from last_exc

    @staticmethod
    def _sanitize_text(value: Any) -> str:
        return " ".join(str(value or "").strip().split())

    @staticmethod
    def _sanitize_code(value: Any, width: int = 0) -> str:
        raw = "".join(ch for ch in str(value or "").strip() if ch.isdigit())
        if width > 0:
            return raw.zfill(width)
        return raw

    @staticmethod
    def _to_int(value: Any, default: int = 0) -> int:
        if value is None:
            return default
        try:
            if isinstance(value, str):
                cleaned = value.strip().replace(",", "")
                if not cleaned:
                    return default
                return int(float(cleaned))
            return int(value)
        except Exception:
            return default

    @staticmethod
    def _to_float(value: Any, default: float = 0.0) -> float:
        if value is None:
            return default
        try:
            if isinstance(value, str):
                cleaned = value.strip().replace("%", "").replace(",", ".")
                if not cleaned:
                    return default
                return float(cleaned)
            return float(value)
        except Exception:
            return default

    # ------------------------------------------------------------------ #
    # Summary / aggregate endpoints (use plain requests)                  #
    # ------------------------------------------------------------------ #

    def get_active_process(self) -> dict[str, Any]:
        return self._get_data("/proceso/proceso-electoral-activo")

    def get_active_presidential_election_id(self) -> int:
        process = self.get_active_process()
        election_id = process.get("idEleccionPrincipal")
        if election_id is None:
            raise ValueError(f"No se encontro 'idEleccionPrincipal' en: {process}")
        return int(election_id)

    def get_totals(
        self,
        election_id: int,
        tipo_filtro: str = "eleccion",
        **extra_filters: Any,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "idEleccion": election_id,
            "tipoFiltro": tipo_filtro,
            **extra_filters,
        }
        return self._get_data("/resumen-general/totales", params=params)

    def get_candidates(
        self,
        election_id: int,
        tipo_filtro: str = "eleccion",
        **extra_filters: Any,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "idEleccion": election_id,
            "tipoFiltro": tipo_filtro,
            **extra_filters,
        }
        data = self._get_data(
            "/eleccion-presidencial/participantes-ubicacion-geografica-nombre",
            params=params,
        )
        if not isinstance(data, list):
            raise ValueError(f"Se esperaba lista de candidatos, se obtuvo: {type(data)}")
        return data

    def get_snapshot(
        self,
        election_id: int | None = None,
        tipo_filtro: str = "eleccion",
        **extra_filters: Any,
    ) -> dict[str, Any]:
        if election_id is None:
            election_id = self.get_active_presidential_election_id()

        active_process = self.get_active_process()
        totals = self.get_totals(election_id, tipo_filtro=tipo_filtro, **extra_filters)
        candidates = self.get_candidates(election_id, tipo_filtro=tipo_filtro, **extra_filters)

        return {
            "activeProcess": active_process,
            "idEleccion": election_id,
            "tipoFiltro": tipo_filtro,
            "filtros": extra_filters,
            "totales": totals,
            "candidatos": candidates,
        }

    # ------------------------------------------------------------------ #
    # Mesa-level endpoints (require curl_cffi Chrome impersonation)       #
    # ------------------------------------------------------------------ #

    def get_all_mesas(
        self,
        election_id: int | None = None,
        min_real_mesas: int = _MIN_MESAS_SANITY,
        strict: bool = True,
    ) -> list[str]:
        """
        Return deduplicated, normalized 6-digit mesa codes.

        Strategy:
        1) Try static asset /assets/data/mesas.json.
        2) If asset is missing/invalid, fallback to totalActas and generate [000001..N].

        Placeholder/demo records like 0000/000000 are ignored.
        If fewer than min_real_mesas are found, strict mode raises RuntimeError.
        """
        url = f"{ASSETS_BASE_URL}/assets/data/mesas.json"
        codes: list[str] = []
        try:
            response = self._get_curl_session().get(url, impersonate="chrome124", timeout=self.timeout_seconds)
            response.raise_for_status()
            data = response.json()
            if isinstance(data, list):
                seen: set[str] = set()
                for record in data:
                    raw = str(record.get("NUM_MESA", "")).strip()
                    if not raw or not raw.isdigit():
                        continue
                    code = raw.zfill(6)
                    if code == "000000":
                        continue
                    if code not in seen:
                        seen.add(code)
                        codes.append(code)
        except Exception:
            # Some ONPE deployments route unknown assets to SPA HTML (200 + non-JSON).
            codes = []

        if len(codes) >= min_real_mesas:
            return codes

        if election_id is None:
            election_id = self.get_active_presidential_election_id()
        totals = self.get_totals(election_id=election_id, tipo_filtro="eleccion")
        total_actas = int(totals.get("totalActas") or 0)
        if total_actas > 0:
            warnings.warn(
                "No se pudo usar assets/data/mesas.json; usando rango 000001..totalActas para discovery.",
                stacklevel=2,
            )
            codes = [str(i).zfill(6) for i in range(1, total_actas + 1)]

        if len(codes) < min_real_mesas:
            message = (
                f"mesas.json devolvio solo {len(codes)} mesas reales "
                f"(minimo esperado: {min_real_mesas}). "
                "ONPE aun no publica el padron real de mesas para scraping por mesa."
            )
            if strict:
                raise RuntimeError(message)
            warnings.warn(message, stacklevel=2)

        return codes

    def get_ubicaciones(self, id_eleccion: int) -> list[UbicacionData]:
        """
        Fetch the full geographic hierarchy (Peru + exterior) from dep-prov-distritos.

        Peru   (ubigeo prefix 01-25): nombre = DEPARTAMENTO \\ PROVINCIA \\ DISTRITO
        Exterior (prefix 91-95):      nombre = CONTINENTE \\ PAIS \\ CIUDAD
        Nacional (prefix 00):         skipped (aggregate placeholder).
        """
        data = self._get_data("/ubigeos/dep-prov-distritos", params={"idEleccion": id_eleccion})
        if not isinstance(data, list):
            raise ValueError("dep-prov-distritos: se esperaba lista")

        seen: set[str] = set()
        ubicaciones: list[UbicacionData] = []

        for row in data:
            ubigeo = str(row.get("ubigeo") or "").zfill(6)
            if not ubigeo or ubigeo == "000000" or ubigeo in seen:
                continue
            seen.add(ubigeo)

            parts = [p.strip() for p in str(row.get("nombre") or "").split("\\")]
            while len(parts) < 3:
                parts.append("")

            prefix = ubigeo[:2]
            if prefix in ("91", "92", "93", "94", "95"):
                ubicaciones.append(UbicacionData(
                    ubigeo=ubigeo, ambito="exterior",
                    departamento="", provincia="", distrito="",
                    continente=parts[0], pais=parts[1], ciudad=parts[2],
                ))
            else:
                ubicaciones.append(UbicacionData(
                    ubigeo=ubigeo, ambito="peru",
                    departamento=parts[0], provincia=parts[1], distrito=parts[2],
                    continente="", pais="", ciudad="",
                ))

        return ubicaciones

    @staticmethod
    def _parse_acta(acta: dict[str, Any], codigo_mesa: str) -> MesaResult:
        id_acta = OnpeClient._to_int(acta.get("id"), 0)
        id_eleccion = OnpeClient._to_int(acta.get("idEleccion"), 0)
        mesa_data = MesaData(
            codigo_mesa=OnpeClient._sanitize_code(codigo_mesa, 6),
            id_eleccion=id_eleccion,
            id_ubigeo=OnpeClient._sanitize_code(acta.get("idUbigeo"), 6),
            nombre_local_votacion=OnpeClient._sanitize_text(acta.get("nombreLocalVotacion")),
            codigo_local_votacion=OnpeClient._sanitize_code(acta.get("codigoLocalVotacion"), 4),
            id_ambito_geografico=OnpeClient._to_int(acta.get("idAmbitoGeografico"), 0),
            electores_habiles=OnpeClient._to_int(acta.get("totalElectoresHabiles"), 0),
            votos_emitidos=OnpeClient._to_int(acta.get("totalVotosEmitidos"), 0),
            votos_validos=OnpeClient._to_int(acta.get("totalVotosValidos"), 0),
            total_asistentes=OnpeClient._to_int(acta.get("totalAsistentes"), 0),
            participacion_ciudadana=OnpeClient._to_float(acta.get("porcentajeParticipacionCiudadana"), 0.0),
            codigo_estado_acta=OnpeClient._sanitize_text(acta.get("codigoEstadoActa")),
            descripcion_estado_acta=OnpeClient._sanitize_text(acta.get("descripcionEstadoActa")),
        )
        agrupaciones: list[AgrupacionData] = []
        votos: list[VotoData] = []
        for item in acta.get("detalle") or []:
            partido_id = OnpeClient._to_int(item.get("adAgrupacionPolitica"), 0)
            if not partido_id:
                continue
            agrupaciones.append(
                AgrupacionData(
                    partido_id=partido_id,
                    codigo_op=OnpeClient._sanitize_text(item.get("adCodigo")),
                    nombre=OnpeClient._sanitize_text(item.get("adDescripcion")),
                )
            )
            votos.append(
                VotoData(
                    codigo_mesa=mesa_data.codigo_mesa,
                    id_eleccion=id_eleccion,
                    partido_id=partido_id,
                    votos=OnpeClient._to_int(item.get("adVotos"), 0),
                    pct_votos_validos=OnpeClient._to_float(item.get("adPorcentajeVotosValidos"), 0.0),
                    pct_votos_emitidos=OnpeClient._to_float(item.get("adPorcentajeVotosEmitidos"), 0.0),
                )
            )
        return MesaResult(
            codigo_mesa=mesa_data.codigo_mesa,
            id_acta=id_acta,
            mesa_data=mesa_data,
            agrupaciones=agrupaciones,
            votos=votos,
        )

    def get_mapa_calor(
        self,
        id_eleccion: int,
        tipo_filtro: str = "ubigeo_nivel_02",
    ) -> list[dict[str, Any]]:
        """Fetch heat-map data at the requested geographic level (nivel_01=dept, nivel_02=dist)."""
        try:
            data = self._get_data(
                "/resumen-general/mapa-calor",
                params={"idEleccion": id_eleccion, "tipoFiltro": tipo_filtro},
            )
        except Exception:
            return []
        return data if isinstance(data, list) else []

    def get_participacion_ubigeos(self, id_eleccion: int) -> list[dict[str, Any]]:
        """Fetch voter participation totals by department from ONPE."""
        try:
            data = self._get_data(
                "/participacion-ciudadana/ubigeos-total",
                params={"idEleccion": id_eleccion},
            )
        except Exception:
            return []
        return data if isinstance(data, list) else []

    def get_active_ubigeos(self, id_eleccion: int, min_actas: int = 1) -> set[str]:
        """
        Return 6-digit ubigeo codes of all districts with at least min_actas published.

        Uses /resumen-general/mapa-calor at district (nivel_02) granularity, which
        returns all ~2102 districts nationwide with their actasContabilizadas count.
        This lets callers skip mesas in districts where ONPE has not yet published any acta.
        """
        try:
            data = self._get_data(
                "/resumen-general/mapa-calor",
                params={"idEleccion": id_eleccion, "tipoFiltro": "ubigeo_nivel_02"},
            )
        except Exception:
            return set()
        if not isinstance(data, list):
            return set()
        active: set[str] = set()
        for item in data:
            if int(item.get("actasContabilizadas") or 0) >= min_actas:
                ubigeo3 = item.get("ubigeoNivel03")
                if ubigeo3 is not None:
                    active.add(str(int(ubigeo3)).zfill(6))
        return active

    def _get_actas_page(
        self,
        *,
        id_ambito_geografico: int,
        pagina: int,
        tamanio: int = _ACTAS_PAGE_SIZE,
        id_ubigeo: str | None = None,
        codigo_local_votacion: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "idAmbitoGeografico": id_ambito_geografico,
            "pagina": pagina,
            "tamanio": tamanio,
        }
        if id_ubigeo is not None:
            params["idUbigeo"] = id_ubigeo
        if codigo_local_votacion is not None:
            params["codigoLocalVotacion"] = codigo_local_votacion

        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                response = self._get_curl_session().get(
                    f"{self.base_url}/actas",
                    params=params,
                    impersonate="chrome124",
                    timeout=self.timeout_seconds,
                )
                if response.status_code == 204:
                    return {
                        "paginaActual": pagina, "totalRegistros": 0,
                        "totalPaginas": 0, "content": [],
                        "contabilizada": 0, "observada": 0, "pendiente": 0,
                    }
                response.raise_for_status()
                if not response.text.strip():
                    return {
                        "paginaActual": pagina, "totalRegistros": 0,
                        "totalPaginas": 0, "content": [],
                        "contabilizada": 0, "observada": 0, "pendiente": 0,
                    }
                payload = response.json()
                data = payload.get("data")
                if not isinstance(data, dict):
                    raise ValueError(f"/actas devolvio payload inesperado: {payload}")
                return data
            except Exception as exc:
                last_exc = exc
                if attempt < self.max_retries - 1:
                    time.sleep(0.3 * (2**attempt) + random.uniform(0.0, 0.3))
        raise RuntimeError(f"/actas pagina={pagina}: {self.max_retries} intentos fallidos") from last_exc

    def get_contabilized_mesas(
        self,
        id_eleccion: int,
        include_observadas: bool = True,
        max_pages_per_ambito: int = 0,
    ) -> list[str]:
        """
        Paginate /actas (idAmbitoGeografico=1 Peru, 2 Exterior) to collect C/O mesa codes.

        max_pages_per_ambito=0 means paginate all pages for each ambito.
        Set a positive value to cap pages (e.g. 50 = first 5000 mesas per ambito).
        """
        _DONE = {"contabilizada"}
        if include_observadas:
            _DONE |= {"observada", "para envío al jee"}

        codes: list[str] = []
        for id_ambito in (1, 2):
            pagina = 1
            while True:
                try:
                    data = self._get_actas_page(
                        id_ambito_geografico=id_ambito,
                        pagina=pagina,
                    )
                except Exception:
                    break
                content = data.get("content") or []
                for item in content:
                    estado = str(item.get("descripcionEstadoActa") or "").casefold()
                    if estado in _DONE:
                        mesa = str(item.get("codigoMesa") or "").strip().zfill(6)
                        if mesa and mesa != "000000":
                            codes.append(mesa)
                total_paginas = int(data.get("totalPaginas") or 0)
                if pagina >= total_paginas:
                    break
                if max_pages_per_ambito > 0 and pagina >= max_pages_per_ambito:
                    break
                pagina += 1
        return codes

    def get_mesa_acta(self, codigo_mesa: str, id_eleccion: int) -> MesaResult | None:
        """
        Fetch the acta for a specific mesa and election ID.

        Returns None when the mesa has no data (HTTP 204 or empty body — ONPE returns
        200 with empty body for mesas not yet published, which is NOT an error).
        Retries up to max_retries times with exponential backoff on transient errors.
        Raises RuntimeError after all retries are exhausted.
        """
        record = self._get_mesa_record(codigo_mesa, id_eleccion)
        if record is None:
            return None
        return self._parse_acta(record, codigo_mesa)

    def get_acta_archivos(
        self,
        codigo_mesa: str,
        id_eleccion: int,
    ) -> list[ActaArchivoData]:
        """Fetch the list of PDF archivos for a mesa and election."""
        record = self._get_mesa_record(codigo_mesa, id_eleccion)
        if record is None:
            return []
        id_acta = int(record.get("id") or 0)
        if id_acta <= 0:
            return []
        return self.get_acta_archivos_by_id_acta(id_acta, codigo_mesa, id_eleccion)

    def get_acta_archivos_by_id_acta(
        self,
        id_acta: int,
        codigo_mesa: str,
        id_eleccion: int,
    ) -> list[ActaArchivoData]:
        """Fetch the PDF archivos from /actas/{idActa}."""
        payload = self._request_json_get(f"{self.base_url}/actas/{id_acta}")
        data = payload.get("data")
        if not isinstance(data, dict):
            return []

        archivos = data.get("archivos")
        if not isinstance(archivos, list):
            return []

        results: list[ActaArchivoData] = []
        for orden, item in enumerate(archivos, start=1):
            if not isinstance(item, dict):
                continue
            archivo_id = self._sanitize_text(item.get("id"))
            if not archivo_id:
                continue
            results.append(
                ActaArchivoData(
                    codigo_mesa=self._sanitize_code(codigo_mesa, 6),
                    id_eleccion=id_eleccion,
                    id_acta=id_acta,
                    archivo_id=archivo_id,
                    orden=orden,
                    tipo=self._to_int(item.get("tipo"), 0),
                    nombre=self._sanitize_text(item.get("nombre")),
                    descripcion=self._sanitize_text(item.get("descripcion")),
                    daud_fecha_creacion=self._to_int(item.get("daudFechaCreacion"), 0),
                )
            )
        return results

    def get_acta_signed_url(self, archivo_id: str) -> str:
        """Fetch the signed S3 URL for one PDF archivo."""
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                payload = self._request_json_get(
                    f"{self.base_url}/actas/file",
                    params={"id": archivo_id},
                )
                data = payload.get("data")
                if not isinstance(data, str) or not data.startswith("https://"):
                    raise ValueError(f"URL firmada invalida para archivo {archivo_id}: {data!r}")
                return data
            except Exception as exc:
                last_exc = exc
                if attempt < self.max_retries - 1:
                    time.sleep(0.5 * (2**attempt) + random.uniform(0.0, 0.5))
        raise RuntimeError(
            f"archivo {archivo_id}: {self.max_retries} intentos fallidos"
        ) from last_exc
