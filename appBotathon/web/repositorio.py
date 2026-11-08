"""Acceso a datos. La app usa esta interfaz, sin saber si hay SQL Server o memoria."""
from __future__ import annotations

import datetime as dt
import threading
from dataclasses import dataclass, field
from typing import Protocol

from web.motor import Parametros

ESTADOS_FINALES = {"OK", "PARCIAL", "RECHAZADA", "ERROR", "DUPLICADA"}
ESTADOS_APLICADOS = ("OK", "PARCIAL")


@dataclass
class Usuario:
    id_usuario: int
    usuario: str
    nombre_visible: str
    hash_contrasena: bytes | None
    rol: str  # CENTRAL o JEFATURA
    it: int | None = None
    activo: bool = True


@dataclass
class Carga:
    id_carga: int
    tipo_archivo: str
    nombre_archivo: str
    solicitado_por: str
    estado: str = "PENDIENTE"
    fecha_solicitud: dt.datetime = field(default_factory=dt.datetime.now)
    fecha_inicio: dt.datetime | None = None
    fecha_fin: dt.datetime | None = None
    filas_leidas: int | None = None
    filas_validas: int | None = None
    filas_cuarentena: int | None = None
    filas_advertencia: int | None = None
    filas_nuevas: int | None = None
    filas_cambiadas: int | None = None
    filas_sin_cambios: int | None = None
    filas_eliminadas: int | None = None
    ruta_correcciones: str | None = None
    mensaje: str | None = None
    institutos_procesados: int = 0

    @property
    def terminada(self) -> bool:
        return self.estado in ESTADOS_FINALES


@dataclass
class FilaVigente:
    """Una fila de plata.dotacion_vigente: persona, instituto y mes."""
    persona_id: int
    it: int
    periodo: dt.date
    horas: float
    cargo: str  # cargo homologado
    cod_esp: str
    tipo_contrato: str
    fecha_termino: dt.date | None = None
    id_carga: int = 0  # carga que dejó esta fila


@dataclass
class Registro:
    """Resultado de una persona en un instituto: listas de 12 meses."""
    persona_id: int
    it: int
    cargo: str
    cod_esp: str
    tipo_contrato: str
    promedio: float
    tramo: str
    horas: list[float]
    horas_directa: list[float]
    horas_indirecta: list[float]
    semanas: list[float]
    plan_directa: list[int]
    plan_total: list[int]
    meta_directa: list[int]
    meta_total: list[int]
    incompleto: bool = False


@dataclass
class VersionPlan:
    id_version: int
    anio: int
    id_carga: int
    id_parametros: int
    creado_por: str
    fecha: dt.datetime
    comentario: str


@dataclass
class ParamApp:
    id_version: int
    nombre: str
    motor: Parametros
    asignaciones: list[dict] = field(default_factory=list)


class Repositorio(Protocol):
    def buscar_usuario(self, usuario: str) -> Usuario | None: ...
    def obtener_usuario(self, id_usuario: int) -> Usuario | None: ...
    def nombre_instituto(self, it: int) -> str: ...
    def institutos(self) -> dict[int, str]: ...
    def nombres_areas(self) -> dict[str, str]: ...
    def crear_carga(self, nombre_archivo: str, tipo_archivo: str, solicitado_por: str) -> int: ...
    def listar_cargas(self, limite: int = 30) -> list[Carga]: ...
    def obtener_carga(self, id_carga: int) -> Carga | None: ...
    def pasos(self, id_carga: int) -> list[dict]: ...
    def rechazos(self, id_carga: int, limite: int = 200) -> list[dict]: ...
    def advertencias(self, id_carga: int) -> list[dict]: ...
    def cambios(self, id_carga: int) -> list[dict]: ...
    def cambios_detalle(self, ids_carga: list[int]) -> list[dict]: ...
    def cargas_aplicadas_desde(self, id_carga: int) -> list[int]: ...
    def filas_vigentes(self) -> list[FilaVigente]: ...
    def incompletos(self) -> set[tuple[int, int]]: ...
    def parametros(self) -> ParamApp: ...
    def guardar_parametros(self, nuevo: ParamApp, usuario: str, motivo: str) -> ParamApp: ...
    def version_actual(self) -> VersionPlan | None: ...
    def version_anterior(self, version: VersionPlan) -> VersionPlan | None: ...
    def obtener_version(self, id_version: int) -> VersionPlan | None: ...
    def registros(self, id_version: int) -> list[Registro]: ...
    def guardar_version(self, id_carga: int, id_parametros: int, anio: int, usuario: str,
                        comentario: str, registros: list[Registro]) -> VersionPlan: ...
    def publicaciones(self) -> dict[int, dict]: ...
    def publicar(self, id_version: int, it: int, usuario: str) -> None: ...
    def auditar(self, accion: str, detalle: str, usuario: str, motivo: str | None = None) -> None: ...
    def historial(self, limite: int = 200) -> list[dict]: ...
    def cerrar_anio(self, anio: int, usuario: str, motivo: str) -> None: ...


class RepoMemoria:
    """Repositorio del modo demo. Nada queda guardado al cerrar la app."""

    def __init__(self, usuarios: list[Usuario], institutos: dict[int, str], parametros: ParamApp,
                 areas: dict[str, str]) -> None:
        self.bloqueo = threading.RLock()
        self._usuarios = {u.usuario.lower(): u for u in usuarios}
        self._institutos = institutos
        self._areas = areas
        self._param = parametros
        self.cargas: dict[int, Carga] = {}
        self.lista_pasos: dict[int, list[dict]] = {}
        self.lista_rechazos: dict[int, list[dict]] = {}
        self.lista_advertencias: dict[int, list[dict]] = {}
        self.lista_cambios: dict[int, list[dict]] = {}
        self.lista_cambios_detalle: dict[int, list[dict]] = {}
        self.filas: dict[tuple, FilaVigente] = {}
        self.pendientes: set[tuple] = set()
        self.versiones: list[VersionPlan] = []
        self._registros: dict[int, list[Registro]] = {}
        self._publicado: dict[int, dict] = {}
        self._auditoria: list[dict] = []
        self._siguiente = 1
        self._corte = 0  # cargas hasta este id pertenecen a un año cerrado

    def buscar_usuario(self, usuario: str) -> Usuario | None:
        return self._usuarios.get(usuario.strip().lower())

    def obtener_usuario(self, id_usuario: int) -> Usuario | None:
        return next((u for u in self._usuarios.values() if u.id_usuario == id_usuario), None)

    def nombre_instituto(self, it: int) -> str:
        return self._institutos.get(it, f"Instituto {it}")

    def institutos(self) -> dict[int, str]:
        return dict(self._institutos)

    def nombres_areas(self) -> dict[str, str]:
        return dict(self._areas)

    def crear_carga(self, nombre_archivo: str, tipo_archivo: str, solicitado_por: str) -> int:
        with self.bloqueo:
            id_carga = self._siguiente
            self._siguiente += 1
            self.cargas[id_carga] = Carga(id_carga, tipo_archivo, nombre_archivo, solicitado_por)
            self.lista_pasos[id_carga] = []
            return id_carga

    def listar_cargas(self, limite: int = 30) -> list[Carga]:
        with self.bloqueo:
            return sorted(self.cargas.values(), key=lambda c: c.id_carga, reverse=True)[:limite]

    def obtener_carga(self, id_carga: int) -> Carga | None:
        return self.cargas.get(id_carga)

    def pasos(self, id_carga: int) -> list[dict]:
        return list(self.lista_pasos.get(id_carga, []))

    def rechazos(self, id_carga: int, limite: int = 200) -> list[dict]:
        return self.lista_rechazos.get(id_carga, [])[:limite]

    def advertencias(self, id_carga: int) -> list[dict]:
        return self.lista_advertencias.get(id_carga, [])

    def cambios(self, id_carga: int) -> list[dict]:
        return self.lista_cambios.get(id_carga, [])

    def cambios_detalle(self, ids_carga: list[int]) -> list[dict]:
        return [e for i in ids_carga for e in self.lista_cambios_detalle.get(i, [])]

    def cargas_aplicadas_desde(self, id_carga: int) -> list[int]:
        with self.bloqueo:
            return sorted(c.id_carga for c in self.cargas.values()
                          if c.id_carga > max(id_carga, self._corte) and c.estado in ESTADOS_APLICADOS)

    def filas_vigentes(self) -> list[FilaVigente]:
        with self.bloqueo:
            return list(self.filas.values())

    def incompletos(self) -> set[tuple[int, int]]:
        # Solo falta el dato si no hay una fila vigente para ese mes.
        return {(k[0], k[1]) for k in self.pendientes if k not in self.filas}

    def parametros(self) -> ParamApp:
        return self._param

    def guardar_parametros(self, nuevo: ParamApp, usuario: str, motivo: str) -> ParamApp:
        with self.bloqueo:
            nuevo.id_version = self._param.id_version + 1
            nuevo.nombre = f"Versión {nuevo.id_version}"
            self._param = nuevo
            return nuevo

    def version_actual(self) -> VersionPlan | None:
        vigentes = [v for v in self.versiones if v.id_carga > self._corte]
        return vigentes[-1] if vigentes else None

    def version_anterior(self, version: VersionPlan) -> VersionPlan | None:
        previas = [v for v in self.versiones if v.id_version < version.id_version and v.id_carga > self._corte]
        return previas[-1] if previas else None

    def obtener_version(self, id_version: int) -> VersionPlan | None:
        return next((v for v in self.versiones if v.id_version == id_version), None)

    def registros(self, id_version: int) -> list[Registro]:
        return self._registros.get(id_version, [])

    def guardar_version(self, id_carga: int, id_parametros: int, anio: int, usuario: str,
                        comentario: str, registros: list[Registro]) -> VersionPlan:
        with self.bloqueo:
            v = VersionPlan(len(self.versiones) + 1, anio, id_carga, id_parametros, usuario, dt.datetime.now(), comentario)
            self.versiones.append(v)
            self._registros[v.id_version] = registros
            return v

    def publicaciones(self) -> dict[int, dict]:
        return dict(self._publicado)

    def publicar(self, id_version: int, it: int, usuario: str) -> None:
        self._publicado[it] = dict(id_version=id_version, fecha=dt.datetime.now(), publicado_por=usuario)

    def auditar(self, accion: str, detalle: str, usuario: str, motivo: str | None = None) -> None:
        self._auditoria.append(dict(fecha=dt.datetime.now(), accion=accion, detalle=detalle, usuario=usuario, motivo=motivo))

    def historial(self, limite: int = 200) -> list[dict]:
        return list(reversed(self._auditoria))[:limite]

    def cerrar_anio(self, anio: int, usuario: str, motivo: str) -> None:
        with self.bloqueo:
            self.filas.clear()
            self.pendientes.clear()
            self._publicado.clear()
            self._corte = max(self.cargas, default=0)
        self.auditar("CIERRE_ANIO", f"Año {anio}", usuario, motivo)
