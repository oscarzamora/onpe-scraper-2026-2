"""Probe aislado para validar si los runners de GitHub Actions pueden
extraer data directamente desde la API de ONPE.

NO toca ningún archivo de `output/`, `work/` o `acta/`.
NO realiza commits.
Solo imprime al stdout (visible en el log de Actions).

Uso local (opcional):
    python scripts/probe_github_actions.py

Salida:
    - Status HTTP, content-type, content-length, primeros 400 chars del body
    - Compara `requests` (plain) vs `curl_cffi` con `impersonate="chrome124"`
    - Reporta IP pública del runner y región aproximada
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any

BASE = "https://resultadosegundavuelta.onpe.gob.pe"
TIMEOUT = 20

ENDPOINTS = [
    ("proceso-electoral-activo", f"{BASE}/presentacion-backend/proceso/proceso-electoral-activo"),
    ("acta-mesa-040100",         f"{BASE}/presentacion-backend/actas/buscar/mesa?codigoMesa=040100&idEleccion=10"),
    ("ubigeos",                  f"{BASE}/presentacion-backend/ubigeos/dep-prov-distritos?idEleccion=10"),
]


def _print_section(title: str) -> None:
    bar = "=" * 72
    print(f"\n{bar}\n{title}\n{bar}", flush=True)


def _summarize_response(label: str, status: int, headers: dict[str, Any], body: bytes | str) -> None:
    if isinstance(body, bytes):
        try:
            text = body.decode("utf-8", errors="replace")
        except Exception:
            text = repr(body[:400])
    else:
        text = body
    ct = headers.get("content-type") or headers.get("Content-Type") or "?"
    cl = headers.get("content-length") or headers.get("Content-Length") or len(text)
    snippet = text[:400].replace("\n", " ")
    print(f"[{label}] status={status} content-type={ct} length={cl}", flush=True)
    print(f"[{label}] body[:400]= {snippet}", flush=True)
    # Heurística rápida: ¿parece JSON real o el SPA Angular?
    looks_like_spa = "<html" in text.lower()[:200] or "<!doctype html" in text.lower()[:200]
    looks_like_json = text.lstrip().startswith("{") or text.lstrip().startswith("[")
    verdict = "JSON (OK)" if looks_like_json else ("SPA Angular (BLOQUEADO)" if looks_like_spa else "OTRO")
    print(f"[{label}] verdict= {verdict}", flush=True)


def probe_public_ip() -> None:
    """Reporta IP pública y región del runner (útil para diagnóstico)."""
    _print_section("CONTEXTO DEL RUNNER")
    try:
        import requests
        r = requests.get("https://ifconfig.co/json", timeout=TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            print(json.dumps({
                "ip": data.get("ip"),
                "country": data.get("country"),
                "country_iso": data.get("country_iso"),
                "region": data.get("region_name"),
                "city": data.get("city"),
                "asn_org": data.get("asn_org"),
            }, ensure_ascii=False, indent=2), flush=True)
        else:
            print(f"ifconfig.co status={r.status_code}", flush=True)
    except Exception as exc:  # pragma: no cover
        print(f"No se pudo obtener IP pública: {exc!r}", flush=True)


def probe_with_requests() -> None:
    """Baseline: `requests` plain (sin fingerprinting). Esperamos SPA o error."""
    _print_section("PROBE 1 — requests (plain, sin Chrome impersonation)")
    try:
        import requests
    except ImportError:
        print("requests no instalado", flush=True)
        return

    headers_browser = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "es-PE,es;q=0.9",
        "Referer": "https://resultadosegundavuelta.onpe.gob.pe/main/resumen",
    }

    for label, url in ENDPOINTS:
        try:
            t0 = time.perf_counter()
            r = requests.get(url, headers=headers_browser, timeout=TIMEOUT)
            ms = (time.perf_counter() - t0) * 1000
            print(f"\n--- requests: {label} ({ms:.0f} ms) ---", flush=True)
            _summarize_response(label, r.status_code, dict(r.headers), r.content[:2000])
        except Exception as exc:
            print(f"[{label}] EXCEPTION: {exc!r}", flush=True)


def probe_with_curl_cffi() -> None:
    """Prueba real: el método que usa el scraper en producción."""
    _print_section("PROBE 2 — curl_cffi con impersonate='chrome124' (método del scraper)")
    try:
        from curl_cffi import requests as cffi_requests
    except ImportError:
        print("curl_cffi NO instalado en este entorno", flush=True)
        return

    for label, url in ENDPOINTS:
        try:
            t0 = time.perf_counter()
            r = cffi_requests.get(url, impersonate="chrome124", timeout=TIMEOUT)
            ms = (time.perf_counter() - t0) * 1000
            print(f"\n--- curl_cffi: {label} ({ms:.0f} ms) ---", flush=True)
            _summarize_response(label, r.status_code, dict(r.headers), r.content[:2000])
        except Exception as exc:
            print(f"[{label}] EXCEPTION: {exc!r}", flush=True)


def probe_alternative_impersonations() -> None:
    """Si chrome124 falla desde el runner, probar otros perfiles."""
    _print_section("PROBE 3 — curl_cffi con perfiles alternativos (solo si chrome124 falla)")
    try:
        from curl_cffi import requests as cffi_requests
    except ImportError:
        print("curl_cffi NO instalado", flush=True)
        return

    profiles = ["chrome131", "chrome120", "chrome116", "safari17_2_ios", "firefox133"]
    url = ENDPOINTS[0][1]  # endpoint más liviano

    for profile in profiles:
        try:
            t0 = time.perf_counter()
            r = cffi_requests.get(url, impersonate=profile, timeout=TIMEOUT)
            ms = (time.perf_counter() - t0) * 1000
            body = r.text[:200].replace("\n", " ")
            looks_json = r.text.lstrip().startswith("{") or r.text.lstrip().startswith("[")
            print(f"[{profile}] status={r.status_code} ms={ms:.0f} json={looks_json} body[:200]={body}", flush=True)
        except Exception as exc:
            print(f"[{profile}] EXCEPTION: {exc!r}", flush=True)


def main() -> int:
    print("ONPE GitHub Actions probe — solo lectura, no toca output/", flush=True)
    print(f"python: {sys.version}", flush=True)

    probe_public_ip()
    probe_with_requests()
    probe_with_curl_cffi()
    probe_alternative_impersonations()

    _print_section("FIN DEL PROBE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
