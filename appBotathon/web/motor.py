"""Motor de planificación asistencial (copia de app/motor/calculo.py).

No conoce Excel, base de datos ni web: recibe una fila y parámetros, y devuelve
sus 12 meses. Toda regla de negocio sale de `Parametros`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

MESES = 12


@dataclass(frozen=True)
class DistribucionCargo:
    min_directa: float
    min_indirecta: float
    # % por tramo: (directo, indirecto, nas). Índice 0: alto, 1: medio, 2: bajo.
    tramos: tuple[tuple[float, float, float], ...]


@dataclass(frozen=True)
class GrupalArea:
    porcentaje: float
    pacientes_santiago_concepcion: float
    pacientes_otros: float


@dataclass(frozen=True)
class Parametros:
    distribucion: dict[str, DistribucionCargo]
    grupal: dict[str, GrupalArea]
    dias_habiles_mes: tuple[int, ...]
    institutos_grupo_alto: frozenset[str] = frozenset({"San", "Con"})
    tramo_alto_desde: float = 40
    tramo_medio_desde: float = 22
    meses_periodo_1: int = 4  # enero a abril
    dias_por_semana: int = 5
    factor_meta: float = 0.90
    tolerancia: float = 1e-9  # protege los redondeos hacia abajo del error decimal
    dias_vacaciones_mes: tuple[int, ...] = (0,) * MESES  # vacaciones generales por mes

    def semanas_mes(self) -> list[float]:
        return [round(d / self.dias_por_semana, 1) for d in self.dias_habiles_mes]

    def semanas_habiles_mes(self) -> list[float]:
        """Semanas del mes descontando las vacaciones generales."""
        return [round(max(d - v, 0) / self.dias_por_semana, 1)
                for d, v in zip(self.dias_habiles_mes, self.dias_vacaciones_mes)]


@dataclass
class EntradaFila:
    persona_id: int
    instituto: str
    cargo: str
    area: str
    horas_mes: list[float]  # horas semanales de cada mes
    vigente_mes: list[bool]
    semanas_habiles: list[float]  # por ahora, dato inicial importado del Excel
    horas_diciembre: float = 0
    colacion: float = 0
    horas_asignaciones: float = 0
    min_directa_excepcion: float | None = None  # excepción autorizada por persona


@dataclass
class ResultadoSemanal:
    horas_base: float
    horas_disponibles: float
    directas: float
    indirectas: float
    directas_con_grupal: float

    @property
    def total(self) -> float:
        return self.directas_con_grupal + self.indirectas


@dataclass
class ResultadoFila:
    promedio_horas: float
    tramo: int
    horas_nas: float
    periodo_1: ResultadoSemanal
    periodo_2: ResultadoSemanal
    plan_directa: list[int] = field(default_factory=list)
    plan_total: list[int] = field(default_factory=list)
    meta_directa: list[int] = field(default_factory=list)
    meta_total: list[int] = field(default_factory=list)

    @property
    def anual_directa(self) -> int:
        return sum(self.plan_directa)

    @property
    def anual_total(self) -> int:
        return sum(self.plan_total)

    @property
    def anual_meta_directa(self) -> int:
        return sum(self.meta_directa)

    @property
    def anual_meta_total(self) -> int:
        return sum(self.meta_total)


def piso(valor: float, p: Parametros) -> int:
    return math.floor(valor + p.tolerancia)


def promedio_simple(e: EntradaFila) -> float:
    """Promedio truncado de las horas de los meses vigentes (regla RN-12)."""
    horas = [e.horas_mes[m] for m in range(MESES) if e.vigente_mes[m]]
    return float(math.floor(sum(horas) / len(horas) + 1e-9)) if horas else 0.0


def promedio_ponderado(e: EntradaFila, p: Parametros, meses: range) -> float:
    semanas = p.semanas_mes()
    suma = peso = 0.0
    for m in meses:
        if e.vigente_mes[m]:
            suma += e.horas_mes[m] * semanas[m]
            peso += semanas[m]
    return suma / peso if peso else 0.0


def elegir_tramo(promedio: float, p: Parametros) -> int:
    if promedio >= p.tramo_alto_desde:
        return 0
    return 1 if promedio >= p.tramo_medio_desde else 2


def calcular_semana(e: EntradaFila, p: Parametros, horas_base: float, horas_nas: float,
                    dir_pct: float, ind_pct: float, min_dir: float, min_ind: float) -> ResultadoSemanal:
    disponibles = horas_base - e.colacion - horas_nas - e.horas_asignaciones
    util = max(disponibles, 0.0)
    suma_pct = dir_pct + ind_pct
    prop_dir = dir_pct / suma_pct if suma_pct else 0.0
    prop_ind = ind_pct / suma_pct if suma_pct else 0.0
    directas = piso(util * prop_dir * 60 / min_dir, p)
    indirectas = piso(util * prop_ind * 60 / min_ind, p)

    con_grupal = float(directas)
    regla = p.grupal.get(e.area)
    if regla:
        alto = e.instituto in p.institutos_grupo_alto
        pacientes = regla.pacientes_santiago_concepcion if alto else regla.pacientes_otros
        con_grupal = directas * (1 - regla.porcentaje + regla.porcentaje * pacientes)
    return ResultadoSemanal(horas_base, disponibles, directas, indirectas, con_grupal)


def calcular_fila(e: EntradaFila, p: Parametros) -> ResultadoFila:
    cargo = p.distribucion.get(e.cargo)
    if cargo is None:
        raise KeyError(f"Cargo sin parámetros: {e.cargo}")

    promedio = promedio_simple(e)
    tramo = elegir_tramo(promedio, p)
    dir_pct, ind_pct, nas_pct = cargo.tramos[tramo]
    horas_nas = nas_pct * promedio
    min_dir = e.min_directa_excepcion or cargo.min_directa

    horas_p1 = promedio_ponderado(e, p, range(p.meses_periodo_1))
    horas_p2 = e.horas_diciembre if e.horas_diciembre > 0 else promedio
    args = (horas_nas, dir_pct, ind_pct, min_dir, cargo.min_indirecta)
    p1 = calcular_semana(e, p, horas_p1, *args)
    p2 = calcular_semana(e, p, horas_p2, *args)

    r = ResultadoFila(promedio, tramo, horas_nas, p1, p2)
    for m in range(MESES):
        semana = p1 if m < p.meses_periodo_1 else p2
        s = e.semanas_habiles[m]
        plan_dir = piso(semana.directas_con_grupal * s, p)
        plan_tot = piso(semana.total * s, p)
        r.plan_directa.append(plan_dir)
        r.plan_total.append(plan_tot)
        r.meta_directa.append(piso(plan_dir * p.factor_meta, p))
        r.meta_total.append(piso(plan_tot * p.factor_meta, p))
    return r
