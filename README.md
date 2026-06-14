# ONPE Scraper 2026 — Segunda Vuelta Presidencial

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![Data](https://img.shields.io/badge/Datos-TSV%20UTF--8-0B7A75)
![Estado](https://img.shields.io/badge/Estado-Activo-1F8B4C)
![Licencia](https://img.shields.io/badge/Licencia-MIT-111111)

> **Transparencia electoral independiente para las Elecciones Generales del Perú 2026.**
>
> Este proyecto extrae los resultados de la segunda vuelta presidencial directamente desde la API interna de ONPE — la misma fuente que alimenta el sitio oficial [resultadosegundavuelta.onpe.gob.pe](https://resultadosegundavuelta.onpe.gob.pe/main/resumen) — y los publica como datos abiertos y verificables en este repositorio, mesa por mesa, en tiempo real.

Las actualizaciones se realizan mediante corridas manuales del scraper. Puedes ejecutar el flujo localmente o desde Copilot CLI y luego publicar los cambios con `git push`.

---

## API de ONPE — Endpoints conocidos (segunda vuelta 2026)

> [!IMPORTANT]
> Todos los endpoints requieren **Chrome fingerprinting** (`curl_cffi` con `impersonate="chrome124"`). La librería estándar `requests` recibe el SPA Angular en vez del JSON. Esto fue descubierto mediante ingeniería inversa del frontend oficial.

**Base URL:** `https://resultadosegundavuelta.onpe.gob.pe`

| Endpoint | Método | Descripción |
|---|---|---|
| `/presentacion-backend/proceso/proceso-electoral-activo` | GET | Retorna el `idEleccion` del proceso activo. Auto-detecta segunda vuelta. |
| `/assets/data/mesas.json` | GET | Fuente opcional de códigos de mesa (puede no estar publicada o devolver contenido placeholder según despliegue). |
| `/presentacion-backend/ubigeos/dep-prov-distritos?idEleccion={id}` | GET | Jerarquía geográfica completa: 2 102 ubigeos únicos (Perú: dept/prov/dist; exterior: continente/país/ciudad). |
| `/presentacion-backend/actas/buscar/mesa?codigoMesa={codigo}&idEleccion={id}` | GET | Acta de una mesa: votos por partido, estado (`C`=Contabilizada), datos del local. HTTP 204 = mesa sin datos aún. |
| `/presentacion-backend/totales/...` | GET | Totales nacionales / por filtro geográfico (modo resumen). |
| `/presentacion-backend/candidatos/...` | GET | Candidatos con porcentajes de votos (modo resumen). |

**Envelope de respuesta estándar:**
```json
{ "data": <payload> }
```

**Estados del acta (`codigo_estado_acta`):**
| Código | Descripción |
|---|---|
| `C` | Contabilizada — datos finales |
| `P` | Procesada — pendiente de contabilización |
| `N` | No transmitida |

**Filtros geográficos disponibles (`tipoFiltro`):**
```
eleccion            → nacional
ambito_geografico   → 1=Perú, 2=Exterior
ubigeo_nivel_01     → departamento (ej: 15 = Lima)
ubigeo_nivel_02     → provincia    (ej: 1501 = Lima Provincia)
ubigeo_nivel_03     → distrito     (ej: 150101 = Lima Cercado)
```

> [!NOTE]
> Estos endpoints fueron identificados el 2 de junio de 2026 analizando el tráfico del frontend Angular oficial. ONPE podría modificarlos sin previo aviso. Si detectas cambios, abre un issue.

---

## Datos en vivo

Los archivos `output/*.txt` de este repositorio se actualizan automáticamente durante el escrutinio:

```
output/
  ubicaciones.txt       ← jerarquía geográfica completa (2 102 ubigeos)
  mesas_data.txt        ← una fila por mesa (estado, participación, local)
  votos.txt             ← votos por mesa × partido
  agrupaciones.txt      ← catálogo de partidos
  locales.txt           ← locales de votación con coordenadas opcionales
    locales_reasignados_segunda_vuelta_2026.txt ← mapeo de locales reasignados (origen → nuevo)
```

Cada commit de datos tiene el mensaje `data: YYYY-MM-DDTHH:MM:SSZ — pendientes: N mesas`.

---

## Usar los datos (sin instalar nada)

Los datos ya están en este repo y se actualizan solos. Descarga directa:

```bash
# Todos los archivos de una vez
git clone https://github.com/oscarzamora/onpe-scraper-2026-2.git
cd onpe-scraper-2026-2/output

# O un archivo individual (sin clonar)
curl -O https://raw.githubusercontent.com/oscarzamora/onpe-scraper-2026-2/main/output/mesas_data.txt
curl -O https://raw.githubusercontent.com/oscarzamora/onpe-scraper-2026-2/main/output/votos.txt
```

---

## Instalación

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**Requisitos:** Python 3.11+, dependencias en `requirements.txt` (`curl_cffi` es obligatorio — ONPE requiere fingerprinting de Chrome en todos sus endpoints).

---

## Uso

### Modo resumen — totales nacionales

```powershell
# Una sola extracción (detecta la elección activa automáticamente)
python -m src.onpe_scraper.main

# Bucle de auditoría cada 60 s
python -m src.onpe_scraper.main --intervalo-segundos 60

# Filtrar por departamento (ubigeo Lima = 15)
python -m src.onpe_scraper.main --tipo-filtro ubigeo_nivel_01 --ubigeo 15

# Filtrar por provincia / distrito
python -m src.onpe_scraper.main --tipo-filtro ubigeo_nivel_02 --ubigeo 1501
python -m src.onpe_scraper.main --tipo-filtro ubigeo_nivel_03 --ubigeo 150101

# Ámbito geográfico (1 = Perú, 2 = Extranjero)
python -m src.onpe_scraper.main --tipo-filtro ambito_geografico --id-ambito-geografico 2
```

### Modo mesas — scraping por mesa de votación

```powershell
# Primera ejecución: descubre mesas reales y comienza scraping
python -m src.onpe_scraper.main --modo mesas --redescubrir

# Siguientes ejecuciones: retoma solo las mesas pendientes (no contabilizadas)
python -m src.onpe_scraper.main --modo mesas

# Auditoría continua hasta 100 % contabilizado
python -m src.onpe_scraper.main --modo mesas --intervalo-segundos 120

# Opciones avanzadas
python -m src.onpe_scraper.main --modo mesas --redescubrir \
  --max-workers 5 \
  --batch-size 500 \
  --timeout 20 \
  --verbose

# Descarga de PDFs: OFF por defecto. Activar con --descargar-pdfs (opcional)
python -m src.onpe_scraper.main --modo mesas --descargar-pdfs --actas-dir acta
```

> **Nota:** La descarga de PDFs de actas es **opcional y está desactivada por defecto**.
> Añadir `--descargar-pdfs` descarga los PDFs de cada mesa que se contabilice (C/E) durante
> la misma corrida, y los guarda en `actas/<prefijo>/` (e.g. `actas/04/040100-1.pdf`).
> El índice incremental se guarda en `work/actas_descargadas.tsv`.
> Si omites el flag, el scraper funciona igual sin descargar nada.

### Modo pdfs — descarga de actas ya procesadas

```powershell
# Bulk de mesas ya contabilizadas
python -m src.onpe_scraper.main --modo pdfs --mesas-fuente work/mesas_c_contabilizadas.tsv --actas-dir acta

# Fuente alternativa: cualquier TSV con columnas codigo_mesa e id_eleccion
python -m src.onpe_scraper.main --modo pdfs --mesas-fuente output/mesas_data.txt --actas-dir acta
```

### Modo resumen-geo — totales oficiales por geografía

```powershell
# Primera ejecución: full build (genera todos los archivos resumen/)
python -m src.onpe_scraper.main --modo resumen-geo --id-eleccion 10 --resumen-full

# Ejecuciones siguientes: delta (auto-detecta si output/ cambió)
python -m src.onpe_scraper.main --modo resumen-geo --id-eleccion 10
```

Produce 4 archivos base en `resumen/` (+1 opcional según disponibilidad del endpoint):
- `resumen_nacional.txt` — totales oficiales ONPE (candidatos, % votos, cobertura nacional)
- `resumen_departamentos.txt` — votos por candidato × departamento (bottom-up de mesas locales)
- `resumen_provincias.txt` — votos por candidato × provincia (bottom-up de mesas locales)
- `resumen_cobertura_departamentos.txt` — % actas contabilizadas por departamento (ONPE API)
- `resumen_participacion_departamentos.txt` — % participación por departamento (ONPE API, opcional: se escribe si el endpoint devuelve data)



### Loop automático — `scripts/loop.ps1`

El script `scripts/loop.ps1` replica el ciclo completo que corre Copilot CLI de forma autónoma:
**mesas → reconciliación E→C → resumen-geo → git commit + push → sleep**.

```powershell
# Arranque básico (intervalo 20 min, con reconciliación, sin PDFs)
.\scripts\loop.ps1

# Solo 1 ciclo (para probar o correr manualmente una vez)
.\scripts\loop.ps1 -SoloCiclos 1

# Con descarga de PDFs de actas
.\scripts\loop.ps1 -DescargarPdfs

# Intervalo personalizado (ej. 30 min)
.\scripts\loop.ps1 -IntervaloMinutos 30

# Sin reconciliación, intervalo corto
.\scripts\loop.ps1 -Reconciliar:$false -IntervaloMinutos 5
```

**Parámetros principales:**

| Parámetro | Default | Descripción |
|---|---|---|
| `-IdEleccion` | `10` | ID de elección ONPE (segunda vuelta 2026) |
| `-IntervaloMinutos` | `20` | Minutos de espera entre ciclos |
| `-MaxWorkers` | `5` | Workers paralelos para scraping de mesas |
| `-BatchSize` | `200` | Mesas por lote antes de flush a disco |
| `-DescargarPdfs` | off | Descarga PDFs de actas contabilizadas |
| `-Reconciliar` | on | Detecta y re-consulta mesas E→C cada ciclo |
| `-MaxPaginasReconciliacion` | `50` | Páginas máximas a paginar en `/actas` |
| `-SoloCiclos` | `0` (∞) | Correr solo N ciclos y salir |
| `-CicloInicial` | `1` | Número inicial de ciclo (solo cosmético) |

El script se puede interrumpir con **Ctrl+C** en cualquier momento y retoma automáticamente desde `work/mesas_pendientes.txt` en la siguiente ejecución.

---

## Salidas

```
output/                          ← archivos analíticos por mesa (tab-delimited UTF-8)
  mesas_data.txt                 ← una fila por mesa de votación
  votos.txt                      ← votos por mesa × partido
  agrupaciones.txt               ← catálogo de agrupaciones políticas
  candidatos_historial.txt       ← serie histórica de totales por candidato
  ubicaciones.txt                ← jerarquía geográfica completa (2 102 ubigeos)
  locales.txt                    ← locales de votación con lat/lon (opcional)
    locales_reasignados_segunda_vuelta_2026.txt ← feed oficial de locales reasignados para 2da vuelta

resumen/                         ← capa de resumen oficial (tab-delimited UTF-8)
  resumen_nacional.txt           ← totales nacionales desde ONPE API (candidatos + cobertura)
  resumen_departamentos.txt      ← votos por candidato × departamento (bottom-up)
  resumen_provincias.txt         ← votos por candidato × provincia (bottom-up)
  resumen_cobertura_departamentos.txt ← % actas contabilizadas por departamento (ONPE API)
    resumen_participacion_departamentos.txt ← % participación ciudadana por departamento (opcional)

work/                            ← estado interno del scraper (no commitear)
  mesas_pendientes.txt           ← mesas aún no contabilizadas (resume file)
  resumen_state.txt              ← estado incremental del resumen (full/delta)
  snapshot_YYYYMMDDTHHMMSSZ.json ← dump crudo de la API por cada corrida

acta/                            ← PDFs descargados de ONPE
  04/040100-1.pdf                ← acta 1 de la mesa 040100
  04/040100-2.pdf                ← acta 2 de la mesa 040100
```

### Código que implementa esta capacidad

- `src/onpe_scraper/client.py` — resuelve `idActa`, extrae `archivos[]` y firma la URL del PDF.
- `src/onpe_scraper/pdfs.py` — descarga el PDF, valida `%PDF`, calcula `sha256`, migra el layout plano y escribe el índice incremental.
- `src/onpe_scraper/main.py` — expone `--modo pdfs`, `--actas-dir` y `--descargar-pdfs`.

### Esquema de tablas

#### `mesas_data.txt`
| Campo | Tipo | Descripción |
|---|---|---|
| `codigo_mesa` | str(6) | PK — código de mesa |
| `id_eleccion` | int | PK — ID de elección |
| `id_ubigeo` | str(6) | Código ubigeo del local (FK → ubicaciones) |
| `nombre_local_votacion` | str | Nombre del local |
| `codigo_local_votacion` | str | Código del local |
| `id_ambito_geografico` | int | 1 = Perú, 2 = Exterior |
| `electores_habiles` | int | Padrón |
| `votos_emitidos` | int | Total votos emitidos |
| `votos_validos` | int | Total votos válidos |
| `total_asistentes` | int | Asistencia registrada |
| `participacion_ciudadana` | float | % participación |
| `codigo_estado_acta` | str | `C` = Contabilizada |
| `descripcion_estado_acta` | str | Estado legible |

#### `votos.txt`
| Campo | Tipo | Descripción |
|---|---|---|
| `codigo_mesa` | str(6) | FK → mesas_data |
| `id_eleccion` | int | FK → mesas_data |
| `partido_id` | int | FK → agrupaciones |
| `votos` | int | Votos absolutos |
| `pct_votos_validos` | float | % sobre votos válidos |
| `pct_votos_emitidos` | float | % sobre votos emitidos |

#### `agrupaciones.txt`
| Campo | Tipo | Descripción |
|---|---|---|
| `partido_id` | int | PK |
| `codigo_op` | str | Código oficial ONPE |
| `nombre` | str | Nombre completo |

#### `candidatos_historial.txt`
Serie temporal de totales nacionales, una fila por candidato por corrida. Útil para graficar la evolución del conteo.

#### `ubicaciones.txt`
Jerarquía geográfica completa, derivada del endpoint `dep-prov-distritos` de ONPE (2 102 ubigeos únicos).

| Campo | Tipo | Descripción |
|---|---|---|
| `ubigeo` | str(6) | PK — código ubigeo de 6 dígitos |
| `ambito` | str | `peru` o `exterior` |
| `departamento` | str | Solo si `ambito = peru` |
| `provincia` | str | Solo si `ambito = peru` |
| `distrito` | str | Solo si `ambito = peru` |
| `continente` | str | Solo si `ambito = exterior` |
| `pais` | str | Solo si `ambito = exterior` |
| `ciudad` | str | Solo si `ambito = exterior` |

Prefijos: `01`–`25` = departamentos peruanos; `91`–`95` = exterior (91=África, 92=América, 93=Asia, 94=Europa, 95=Oceanía).

#### `locales.txt`
Locales de votación descubiertos durante el scraping. Las columnas `lat`/`lon` se enriquecen con `enrich_geo.py`.

| Campo | Tipo | Descripción |
|---|---|---|
| `codigo_local_votacion` | str | PK |
| `nombre_local_votacion` | str | Nombre del local |
| `ubigeo` | str(6) | FK → ubicaciones |
| `lat` | float? | Latitud (Nominatim, opcional) |
| `lon` | float? | Longitud (Nominatim, opcional) |

#### `locales_reasignados_segunda_vuelta_2026.txt`
Feed tab-delimited de locales reasignados exclusivo para la segunda vuelta presidencial 2026.
La relación principal para analítica es `nombre_local_votacion` (origen) → `nombre_local_votacion_nuevo` (destino).

| Campo | Tipo | Descripción |
|---|---|---|
| `nro` | int | Correlativo del comunicado |
| `odpe` | str | ODPE responsable |
| `dpto` | str | Departamento |
| `provincia` | str | Provincia |
| `distrito` | str | Distrito |
| `ccpp` | str | Centro poblado (si aplica) |
| `nombre_local_votacion` | str | Local original |
| `nombre_local_votacion_nuevo` | str | Local de destino reasignado |
| `motivo` | str | Motivo de la reasignación |
| `mesas_a_reasignar` | int | Número de mesas afectadas |
| `estado_parseo` | str | Calidad del parseo OCR (`OK`, `INCOMPLETO_OCR`, `OCR_REVISAR`) |

#### `resumen/resumen_nacional.txt`
Totales oficiales ONPE directamente desde la API — siempre la fuente más actualizada.

| Campo | Tipo | Descripción |
|---|---|---|
| `id_eleccion` | int | ID de elección (10 = segunda vuelta 2026) |
| `id_ambito_geografico` | int | 1 = Perú |
| `partido_id` | int? | FK → agrupaciones cuando viene poblado; puede venir vacío en ONPE para filas agregadas |
| `nombre_candidato` | str | Nombre del candidato (o "VOTOS NULOS" / "VOTOS EN BLANCO") |
| `nombre_agrupacion_politica` | str | Nombre del partido |
| `votos_validos` | int | Votos válidos totales |
| `pct_votos_validos` | float | % sobre votos válidos |
| `pct_votos_emitidos` | float | % sobre votos emitidos |
| `actas_contabilizadas_pct` | float | % de actas contabilizadas |
| `contabilizadas` | int | Número de actas contabilizadas |
| `total_actas` | int | Total de actas del padrón |
| `participacion_ciudadana` | float | % participación ciudadana |
| `fecha_actualizacion` | str (ISO) | Timestamp oficial ONPE |
| `fuente` | str | `onpe_api` |

#### `resumen/resumen_departamentos.txt` y `resumen/resumen_provincias.txt`
Agregación bottom-up desde los datos de mesas raspadas localmente. Incluye todos los departamentos / provincias con al menos una mesa contabilizada.

| Campo | Tipo | Descripción |
|---|---|---|
| `id_eleccion` | int | ID de elección |
| `ubigeo` | str(6) | Código ubigeo de departamento (`DD0000`) o provincia (`DDPP00`) |
| `partido_id` | int | FK → agrupaciones |
| `nombre_candidato` | str | Nombre del partido/candidato |
| `nombre_agrupacion_politica` | str | Nombre del partido |
| `votos_validos` | int | Votos absolutos en ese geo |
| `pct_votos_validos` | float | % sobre total válidos del geo |
| `pct_votos_emitidos` | float | % sobre total emitidos del geo |
| `total_votos_validos_geo` | int | Total votos válidos del geo (denominador) |
| `total_votos_emitidos_geo` | int | Total votos emitidos del geo (denominador) |
| `fuente` | str | `local_agregado` |

#### `resumen/resumen_cobertura_departamentos.txt`
Progreso de escrutinio por departamento. Fuente: ONPE API `/resumen-general/mapa-calor`.

| Campo | Tipo | Descripción |
|---|---|---|
| `id_eleccion` | int | ID de elección |
| `ubigeo` | str(6) | Código departamento (`DD0000`) |
| `nombre_departamento` | str | Nombre del departamento |
| `actas_contabilizadas` | int | Actas contabilizadas en el departamento |
| `pct_actas_contabilizadas` | float | % cobertura del escrutinio |
| `fuente` | str | `onpe_api` |

#### `resumen/resumen_participacion_departamentos.txt`
Participación ciudadana por departamento. Fuente: ONPE API `/participacion-ciudadana/ubigeos-total`.
Archivo opcional: puede no generarse en una corrida si el endpoint retorna vacío o no disponible.

| Campo | Tipo | Descripción |
|---|---|---|
| `id_eleccion` | int | ID de elección |
| `ubigeo` | str(6) | Código departamento (`DD0000`) |
| `nombre_departamento` | str | Nombre del departamento |
| `pct_asistentes` | float | % de electores que votaron |
| `pct_ausentes` | float | % de ausentes |
| `fuente` | str | `onpe_api` |

### Modelo relacional (`output/`)

```mermaid
erDiagram
    mesas_data {
        str   codigo_mesa        PK
        int   id_eleccion        PK
        str   id_ubigeo
        str   nombre_local_votacion
        str   codigo_local_votacion
        int   id_ambito_geografico
        int   electores_habiles
        int   votos_emitidos
        int   votos_validos
        int   total_asistentes
        float participacion_ciudadana
        str   codigo_estado_acta
        str   descripcion_estado_acta
    }

    votos {
        str   codigo_mesa       PK,FK
        int   id_eleccion       PK,FK
        int   partido_id        PK,FK
        int   votos
        float pct_votos_validos
        float pct_votos_emitidos
    }

    agrupaciones {
        int partido_id          PK
        str codigo_op
        str nombre
    }

    ubicaciones {
        str ubigeo             PK
        str ambito
        str departamento
        str provincia
        str distrito
        str continente
        str pais
        str ciudad
    }

    locales {
        str   codigo_local_votacion  PK
        str   nombre_local_votacion
        str   ubigeo                 FK
        float lat
        float lon
    }

    mesas_data ||--o{ votos : "codigo_mesa + id_eleccion"
    agrupaciones ||--o{ votos : "partido_id"
    mesas_data }o--|| locales : "codigo_local_votacion"
    locales }o--|| ubicaciones : "ubigeo"
```

`candidatos_historial.txt` se considera dataset temporal opcional (modo `resumen`),
no parte del core operacional del modo `mesas` + `resumen-geo`.

### Modelo temporal opcional (`output/candidatos_historial.txt`)

```mermaid
erDiagram
    candidatos_historial {
        str   timestampActualizacion
        str   proceso
        int   idEleccion
        str   tipoFiltro
        str   filtros
        float actasContabilizadas
        int   totalActas
        float participacionCiudadana
        str   nombreCandidato
        str   nombreAgrupacionPolitica
        int   totalVotosValidos
        float porcentajeVotosValidos
        float porcentajeVotosEmitidos
    }

    candidatos_historial }o..o{ agrupaciones : "match por nombre (analitico)"
```

### Modelo relacional (`resumen/`)

```mermaid
erDiagram
    resumen_nacional {
        int   id_eleccion
        int   id_ambito_geografico
        int   partido_id
        str   nombre_candidato
        str   nombre_agrupacion_politica
        int   votos_validos
        float pct_votos_validos
        float pct_votos_emitidos
        float actas_contabilizadas_pct
        int   contabilizadas
        int   total_actas
        float participacion_ciudadana
        str   fecha_actualizacion
        str   fuente
    }

    resumen_departamentos {
        int   id_eleccion
        str   ubigeo
        int   partido_id
        str   nombre_candidato
        str   nombre_agrupacion_politica
        int   votos_validos
        float pct_votos_validos
        float pct_votos_emitidos
        int   total_votos_validos_geo
        int   total_votos_emitidos_geo
        str   fuente
    }

    resumen_provincias {
        int   id_eleccion
        str   ubigeo
        int   partido_id
        str   nombre_candidato
        str   nombre_agrupacion_politica
        int   votos_validos
        float pct_votos_validos
        float pct_votos_emitidos
        int   total_votos_validos_geo
        int   total_votos_emitidos_geo
        str   fuente
    }

    resumen_cobertura_departamentos {
        int   id_eleccion
        str   ubigeo
        str   nombre_departamento
        int   actas_contabilizadas
        float pct_actas_contabilizadas
        str   fuente
    }

    resumen_participacion_departamentos {
        int   id_eleccion
        str   ubigeo
        str   nombre_departamento
        float pct_asistentes
        float pct_ausentes
        str   fuente
    }

    resumen_nacional }o..o{ agrupaciones : "partido_id (cuando viene poblado)"
    resumen_departamentos }o..o{ agrupaciones : "partido_id"
    resumen_provincias }o..o{ agrupaciones : "partido_id"
    resumen_departamentos }o..o{ ubicaciones : "ubigeo DD0000"
    resumen_provincias }o..o{ ubicaciones : "ubigeo DDPP00"
    resumen_cobertura_departamentos }o..o{ ubicaciones : "ubigeo DD0000"
    resumen_participacion_departamentos }o..o{ ubicaciones : "ubigeo DD0000 (opcional)"
```

### Modelo analítico — tabla plana desnormalizada

Para análisis de drill-down geográfico se recomienda construir una tabla `hechos` que une todas las dimensiones. Es el punto de partida para dashboards, mapas y agregaciones ad-hoc.

```
hechos (tabla plana para BI)
├── codigo_mesa, id_eleccion          ← granularidad base
├── partido_id, nombre_partido        ← dimensión partido
├── votos, pct_votos_validos          ← métricas de votación
├── electores_habiles, votos_emitidos, participacion_ciudadana
├── codigo_estado_acta                ← estado del acta
│
├── PERÚ ──────────────────────────────────────────────────────
│   ├── departamento                  ← nivel 1 (drill-down)
│   ├── provincia                     ← nivel 2
│   ├── distrito                      ← nivel 3
│   └── ubigeo (6 dígitos)
│
├── EXTERIOR ──────────────────────────────────────────────────
│   ├── continente                    ← nivel 1
│   ├── pais                          ← nivel 2
│   └── ciudad                        ← nivel 3
│
└── LOCAL ─────────────────────────────────────────────────────
    ├── nombre_local_votacion
    ├── codigo_local_votacion
    ├── lat, lon                      ← coordenadas (tras enrich_geo)
    └── ambito (peru / exterior)
```

### Carga en pandas

```python
import pandas as pd

# ── Carga de tablas base ────────────────────────────────────────────────────
mesas  = pd.read_csv("output/mesas_data.txt",  sep="\t",
                     dtype={"codigo_mesa": str, "id_ubigeo": str, "codigo_local_votacion": str})
votos  = pd.read_csv("output/votos.txt",        sep="\t", dtype={"codigo_mesa": str})
agrup  = pd.read_csv("output/agrupaciones.txt", sep="\t")
ub     = pd.read_csv("output/ubicaciones.txt",  sep="\t", dtype={"ubigeo": str})
loc    = pd.read_csv("output/locales.txt",       sep="\t",
                     dtype={"ubigeo": str, "codigo_local_votacion": str})

# ── Tabla plana desnormalizada (hechos) ─────────────────────────────────────
hechos = (
    votos
    .merge(agrup.rename(columns={"nombre": "nombre_partido"}), on="partido_id")
    .merge(mesas, on=["codigo_mesa", "id_eleccion"])
    .merge(loc[["codigo_local_votacion", "ubigeo", "lat", "lon"]], on="codigo_local_votacion", how="left")
    .merge(ub, on="ubigeo", how="left")
)
# columna conveniencia: etiqueta geográfica de nivel 1
hechos["geo_nivel1"] = hechos["departamento"].fillna(hechos["continente"])
hechos["geo_nivel2"] = hechos["provincia"].fillna(hechos["pais"])
hechos["geo_nivel3"] = hechos["distrito"].fillna(hechos["ciudad"])
```

#### Drill-down: votos por partido

```python
# Nacional
hechos.groupby("nombre_partido")["votos"].sum().sort_values(ascending=False)

# Por departamento (Perú) → provincia → distrito
peru   = hechos[hechos["ambito"] == "peru"]
by_dep = peru.groupby(["departamento", "nombre_partido"])["votos"].sum().unstack(fill_value=0)
by_prov = peru.groupby(["departamento", "provincia", "nombre_partido"])["votos"].sum().unstack(fill_value=0)
by_dist = peru.groupby(["departamento", "provincia", "distrito", "nombre_partido"])["votos"].sum().unstack(fill_value=0)

# Por continente → país → ciudad (exterior)
ext = hechos[hechos["ambito"] == "exterior"]
by_cont  = ext.groupby(["continente", "nombre_partido"])["votos"].sum().unstack(fill_value=0)
by_pais  = ext.groupby(["continente", "pais", "nombre_partido"])["votos"].sum().unstack(fill_value=0)
by_ciudad = ext.groupby(["continente", "pais", "ciudad", "nombre_partido"])["votos"].sum().unstack(fill_value=0)
```

#### Participación ciudadana

```python
# Participación promedio por departamento
peru.drop_duplicates("codigo_mesa") \
    .groupby("departamento")["participacion_ciudadana"] \
    .mean().sort_values(ascending=False)

# Mesas aún no contabilizadas, agrupadas por departamento
pendientes = mesas[mesas["codigo_estado_acta"] != "C"]
pendientes.merge(loc[["codigo_local_votacion","ubigeo"]], on="codigo_local_votacion", how="left") \
          .merge(ub[["ubigeo","departamento"]], on="ubigeo", how="left") \
          .groupby("departamento").size().sort_values(ascending=False)
```

#### Mapa de calor (con lat/lon de enrich_geo)

```python
import folium
from folium.plugins import HeatMap

pts = (
    hechos[hechos["lat"].notna()]
    .drop_duplicates("codigo_local_votacion")
    [["lat", "lon", "electores_habiles"]]
    .dropna()
)
m = folium.Map(location=[-9.19, -75.0], zoom_start=5)
HeatMap(pts.values.tolist(), radius=8).add_to(m)
m.save("output/mapa_electores.html")
```

#### Evolución del conteo en el tiempo

```python
historial = pd.read_csv("output/candidatos_historial.txt", sep="\t")
historial["ts"] = pd.to_datetime(historial["timestampActualizacion"])
evolucion = historial.pivot_table(
    index="ts", columns="nombreAgrupacionPolitica",
    values="porcentajeVotosValidos", aggfunc="last"
)
evolucion.plot(title="Evolución % votos válidos — Segunda Vuelta 2026")
```

---

## Arquitectura

```
src/onpe_scraper/
├── models.py          # Dataclasses: MesaData, VotoData, AgrupacionData, UbicacionData, LocalData, MesaResult
├── client.py          # OnpeClient — toda la lógica HTTP (curl_cffi + Chrome impersonation)
├── exporters.py       # Escritura de archivos: upsert TSV, snapshot JSON
├── resumen_layer.py   # Capa de resumen: nacional (ONPE API), departamentos/provincias (bottom-up local)
├── main.py            # CLI (argparse): modos resumen / mesas / resumen-geo, ThreadPoolExecutor
└── enrich_geo.py      # Geocodificador opcional vía Nominatim (OpenStreetMap)
```

**Flujo modo mesas:**
```
proceso-electoral-activo → id_eleccion
dep-prov-distritos       → ubicaciones.txt  (2102 ubigeos, Perú + exterior)
mesas.json (si existe) o totalActas → lista de códigos (~92k)
        ↓ (parallel, 5 workers)
/actas/buscar/mesa?codigoMesa=XXXXXX  →  MesaResult
        ↓ (cada 500 mesas)
upsert → mesas_data.txt / votos.txt / agrupaciones.txt / locales.txt
        ↓ (al finalizar)
mesas_pendientes.txt  ← solo las no contabilizadas

Salida operativa por corrida (logs):
- procesadas = mesas consultadas en la corrida
- pendientes = mesas que siguen sin estado "Contabilizada"
- errores = consultas fallidas (se reintentan en corridas siguientes)
- nuevas contabilizadas = mesas que cambiaron a "Contabilizada" durante esa corrida
```

**Enriquecimiento geográfico (opcional):**
```powershell
# Añade lat/lon a locales.txt vía Nominatim (1 req/s, reanudable)
python -m src.onpe_scraper.enrich_geo --verbose

# Forzar re-geocodificación de todos los locales
python -m src.onpe_scraper.enrich_geo --force
```

---

## Notas técnicas

- **Chrome impersonation obligatoria:** todos los endpoints de ONPE retornan el SPA de Angular sin `curl_cffi` con `impersonate="chrome124"`. La librería estándar `requests` no funciona.
- **Upsert incremental:** los TXT usan el patrón load → merge → rewrite con claves compuestas `(id_eleccion, codigo_mesa)`. Cada corrida actualiza sin duplicar.
- **Resume automático:** `work/mesas_pendientes.txt` guarda las mesas no contabilizadas. La próxima corrida sin `--redescubrir` solo re-consulta esas.
- **Métricas por corrida:** para auditoría, revisar en logs `procesadas`, `pendientes`, `errores` y `nuevas contabilizadas`. Un valor de `nuevas contabilizadas=0` puede ser normal si ONPE no publicó nuevas actas en ese intervalo.
- **Retry con backoff:** `get_mesa_acta` reintenta hasta 5 veces con espera exponencial + jitter.
- **Sanitización de entrada:** el parser normaliza textos (trim/espacios), códigos (`mesa`, `ubigeo`, `local`) y numéricos antes del upsert para mantener archivos consistentes.
- Si ONPE cambia la forma del payload, solo hay que editar `client.py`.

---

## Proyectos relacionados

- [onpeescraper](https://github.com/oscarzamora/onpeescraper) — primera vuelta 2026 (scraper original)

---

## Contribuciones

Si ONPE modifica sus endpoints o encuentras datos incorrectos, **abre un issue o un PR**. La transparencia electoral es un esfuerzo colectivo.

---

## Licencia

MIT — los datos producidos son de dominio público.
