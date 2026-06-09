# Plan Formal de Enhancement: Capa Resumen (Full + Delta)

## 1. Objetivo
Agregar una capa de resumen electoral en carpeta `resumen/` sin romper el flujo actual del scraper de mesas.

Resultado esperado:
- Primera ejecucion: build completo (`full`) de los archivos de resumen.
- Ejecuciones siguientes: actualizacion incremental (`delta`) con el mismo ciclo del scraper.

## 2. Estrategia aprobada
Se adopta **Opcion B (hibrida)**:
- Top-down ONPE para resumen nacional oficial.
- Bottom-up local para resumen de candidatos por departamento/provincia.

Razon:
- Menor costo de llamadas al API.
- Nacional oficial directo y rapido.
- Evita endpoints de candidatos por ubigeo que actualmente responden `204`.

## 3. Fuentes -> salidas -> uso

| Fuente | Salida (TXT tab-delimited) | Uso principal |
|---|---|---|
| `/eleccion-presidencial/participantes-ubicacion-geografica` + `/resumen-general/totales` (Peru) | `resumen/resumen_nacional.txt` | Portada nacional oficial |
| `output/mesas_data.txt` + `output/votos.txt` + `output/agrupaciones.txt` + `output/ubicaciones.txt` | `resumen/resumen_departamentos.txt` | Ranking por departamento |
| `output/mesas_data.txt` + `output/votos.txt` + `output/agrupaciones.txt` + `output/ubicaciones.txt` | `resumen/resumen_provincias.txt` | Drill-down por provincia |
| `/resumen-general/mapa-calor` (departamentos) | `resumen/resumen_cobertura_departamentos.txt` | Avance de actas por departamento |
| `/participacion-ciudadana/ubigeos-total` (departamentos) | `resumen/resumen_participacion_departamentos.txt` | Participacion por departamento |

## 4. Archivos Python afectados

### 4.1 Archivos existentes a modificar
- `src/onpe_scraper/client.py`
  - Agregar wrappers explicitos para endpoints de resumen (candidatos compactos, mapa-calor, participacion).
- `src/onpe_scraper/main.py`
  - Integrar orquestacion de resumen en modo `resumen`.
  - Ejecutar `full` o `delta` segun estado.
- `src/onpe_scraper/exporters.py`
  - Reusar utilidades de escritura tab-delimited.
  - Agregar upsert/helpers para archivos de `resumen/`.

### 4.2 Archivos nuevos a crear
- `src/onpe_scraper/resumen_layer.py`
  - Logica de agregacion local (departamento/provincia), merge de metadata ONPE y escritura final.
- `work/resumen_state.txt`
  - Estado incremental para `delta`.

## 5. Modelo de ejecucion (Full -> Delta)

### 5.1 Regla de decision
Se ejecuta `FULL` cuando:
- No existe `work/resumen_state.txt`.
- Se detecta cambio de esquema en fuentes (`output/*.txt`).
- Se fuerza manualmente (`--resumen-full`).

Se ejecuta `DELTA` cuando:
- Existe estado valido.
- No hay cambio de esquema.

### 5.2 Full build
1. Cargar todos los registros de `output/mesas_data.txt` y `output/votos.txt`.
2. Filtrar segun regla de negocio (ej. `codigo_estado_acta = C` para agregado estricto).
3. Agregar por:
   - Departamento: `DD0000`.
   - Provincia: `DDPP00`.
4. Enriquecer con `agrupaciones` y `ubicaciones`.
5. Consultar ONPE nacional y metadata de cobertura/participacion.
6. Escribir todos los TXT en `resumen/`.
7. Persistir estado en `work/resumen_state.txt`.

### 5.3 Delta build
1. Cargar `work/resumen_state.txt`.
2. Identificar mesas nuevas o cambiadas desde ultimo corte.
3. Recalcular solo llaves impactadas (`id_eleccion + ubigeo + partido_id`).
4. Aplicar upsert sobre `resumen_departamentos` y `resumen_provincias`.
5. Refrescar `resumen_nacional` y metadata ONPE (top-down directo).
6. Guardar nuevo estado incremental.

## 6. Integracion con el scraper actual

### 6.1 Sin romper flujo actual
- El scraping de mesas sigue igual.
- La capa resumen corre al final de cada ciclo (post-proceso).

### 6.2 Orden operativo recomendado por corrida
1. Scraper de mesas (`mesas`) actualiza `output/`.
2. Resumen builder corre:
   - `full` primera vez.
   - `delta` en corridas siguientes.
3. Se actualiza `resumen/`.

## 7. Formato de estado incremental
Archivo: `work/resumen_state.txt` (tab-delimited o key=value simple)

Campos minimos:
- `id_eleccion`
- `last_run_utc`
- `mode` (`full`/`delta`)
- `last_source_signature` (hash de headers + conteos)
- `last_processed_mesa_marker` (estrategia incremental)

## 8. Criterios de aceptacion
- Primera corrida genera todos los archivos `resumen/*.txt` sin error.
- Segunda corrida usa `delta` y solo actualiza llaves impactadas.
- `resumen_nacional.txt` siempre sale de ONPE directo.
- Si falta estado o hay inconsistencia, cae a `full` automaticamente.
- No se altera la logica de pendientes del scraper actual.

## 9. Modo de ejecucion listo para operar

### 9.1 Primera ejecucion (full)
- Ejecutar scraper normal.
- Ejecutar resumen con flag de full (`--resumen-full`).

### 9.2 Ejecuciones continuas (delta)
- Ejecutar scraper normal (manual o con intervalo).
- Ejecutar resumen sin flag de full (auto-delta por estado).

## 10. Muestras de filas (salida final)

### resumen/resumen_nacional.txt
`id_eleccion\tid_ambito_geografico\tpartido_id\tnombre_candidato\tnombre_agrupacion_politica\tvotos_validos\tpct_votos_validos\tpct_votos_emitidos\tactas_contabilizadas_pct\tcontabilizadas\ttotal_actas\tparticipacion_ciudadana\tfecha_actualizacion\tfuente`

### resumen/resumen_departamentos.txt
`id_eleccion\tubigeo\tpartido_id\tnombre_candidato\tnombre_agrupacion_politica\tvotos_validos\tpct_votos_validos\tpct_votos_emitidos\ttotal_votos_validos_geo\ttotal_votos_emitidos_geo\tfuente`

### resumen/resumen_provincias.txt
`id_eleccion\tubigeo\tpartido_id\tnombre_candidato\tnombre_agrupacion_politica\tvotos_validos\tpct_votos_validos\tpct_votos_emitidos\ttotal_votos_validos_geo\ttotal_votos_emitidos_geo\tfuente`

### resumen/resumen_cobertura_departamentos.txt
`id_eleccion\tubigeo\tactas_contabilizadas\tpct_actas_contabilizadas\tfuente`

### resumen/resumen_participacion_departamentos.txt
`id_eleccion\tubigeo\tpct_asistentes\tpct_ausentes\tfuente`

## 11. Decision final
Este documento queda como plan total y formal del enhancement para implementar `resumen/` con estrategia Full + Delta integrada al scraper actual.
