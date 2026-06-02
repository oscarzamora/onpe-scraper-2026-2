from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class AgrupacionData:
    partido_id: int
    codigo_op: str
    nombre: str


@dataclass(slots=True)
class VotoData:
    codigo_mesa: str
    id_eleccion: int
    partido_id: int
    votos: int
    pct_votos_validos: float
    pct_votos_emitidos: float


@dataclass(slots=True)
class MesaData:
    codigo_mesa: str
    id_eleccion: int
    id_ubigeo: int
    nombre_local_votacion: str
    codigo_local_votacion: str
    id_ambito_geografico: int
    electores_habiles: int
    votos_emitidos: int
    votos_validos: int
    total_asistentes: int
    participacion_ciudadana: float
    codigo_estado_acta: str
    descripcion_estado_acta: str


@dataclass(slots=True)
class MesaResult:
    codigo_mesa: str
    mesa_data: MesaData | None
    agrupaciones: list[AgrupacionData]
    votos: list[VotoData]
