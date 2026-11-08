"""Convierte datos vigentes en planificación, versiones y resúmenes para el dashboard."""
from __future__ import annotations

import datetime as dt
import threading
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass, replace

from web.motor import MESES, EntradaFila, Parametros, calcular_fila
from web.repositorio import FilaVigente, ParamApp, Registro, Repositorio, VersionPlan

TRAMOS = ("GE40", "22A40", "LT22")
AREAS = {"KI": "Kinesiología", "TO": "Terapia ocupacional", "FI": "Fisiatría", "ULAB": "Unidad laboral",
         "AS": "Asistente social", "PC": "Psicología", "EN": "Enfermería", "FO": "Fonoaudiología",
         "UTAC": "Arte", "UEDA": "Deporte", "ED": "Educación", "UTA": "Tecnología asistiva"}
NOMBRES_TRAMO = {"GE40": "40 horas o más", "22A40": "22 a menos de 40 horas", "LT22": "Menos de 22 horas"}


def proyectar(filas: list[FilaVigente], p: Parametros, anio: int, ultimo_mes: int) -> tuple[EntradaFila, FilaVigente]:
    """Arma las 12 horas de un contrato.

    `ultimo_mes` es el último mes informado en el BUK (1 a 12). Quien sigue vigente
    ese mes se proyecta hasta diciembre, salvo que su fecha de término diga otra cosa.
    """
    orden = sorted(filas, key=lambda f: f.periodo)
    ultima = orden[-1]
    horas: list[float | None] = [None] * MESES
    for f in orden:
        horas[f.periodo.month - 1] = f.horas
    vigente = [h is not None for h in horas]
    if ultima.periodo.month == ultimo_mes:
        for m in range(ultimo_mes, MESES):
            vigente[m] = ultima.fecha_termino is None or ultima.fecha_termino >= dt.date(anio, m + 1, 1)
    previo = None
    for m in range(MESES):
        previo = horas[m] if horas[m] is not None else previo
        horas[m] = previo
    siguiente = None
    for m in range(MESES - 1, -1, -1):
        siguiente = horas[m] if horas[m] is not None else siguiente
        horas[m] = siguiente
    limpias = [h if h is not None else 0.0 for h in horas]
    semanas = p.semanas_habiles_mes()
    return EntradaFila(ultima.persona_id, str(ultima.it), ultima.cargo, ultima.cod_esp, limpias, vigente,
                       [semanas[m] if vigente[m] else 0.0 for m in range(MESES)],
                       horas_diciembre=limpias[11] if vigente[11] else 0.0), ultima


def calcular(filas: list[FilaVigente], p: Parametros, grupo_alto_its: frozenset[str],
             incompletos: set[tuple[int, int]]) -> list[Registro]:
    grupos: dict[tuple[int, int], list[FilaVigente]] = defaultdict(list)
    for f in filas:
        grupos[(f.persona_id, f.it)].append(f)
    if not grupos:
        return []
    anio = max(f.periodo.year for f in filas)
    ultimo_mes = max(f.periodo.month for f in filas)
    motor = replace(p, institutos_grupo_alto=grupo_alto_its)
    salida = []
    for clave, contrato in grupos.items():
        e, ultima = proyectar(contrato, motor, anio, ultimo_mes)
        r = calcular_fila(e, motor)
        horas_dir, horas_ind = [], []
        suma = cargo_pct(motor, e, r)
        for m in range(MESES):
            s = r.periodo_1 if m < motor.meses_periodo_1 else r.periodo_2
            util = max(s.horas_disponibles, 0.0)
            horas_dir.append(util * suma if e.vigente_mes[m] else 0.0)
            horas_ind.append(util * (1 - suma) if e.vigente_mes[m] else 0.0)
        salida.append(Registro(
            clave[0], clave[1], ultima.cargo, ultima.cod_esp, ultima.tipo_contrato, r.promedio_horas, TRAMOS[r.tramo],
            [e.horas_mes[m] if e.vigente_mes[m] else 0.0 for m in range(MESES)], horas_dir, horas_ind,
            e.semanas_habiles, r.plan_directa, r.plan_total, r.meta_directa, r.meta_total, clave in incompletos))
    return salida


def cargo_pct(p: Parametros, e: EntradaFila, r) -> float:
    d, i, _ = p.distribucion[e.cargo].tramos[r.tramo]
    return d / (d + i) if d + i else 0.0


def congelar(nuevos: list[Registro], anteriores: list[Registro], desde: dict[tuple[int, int], int],
             defecto: int) -> list[Registro]:
    """Mantiene los meses anteriores a la fecha en que rige cada cambio.

    `desde` indica, por persona e instituto, el primer mes (0 a 11) que cambia.
    `defecto` aplica a quien no aparece en `desde`: 12 conserva todo el año, y un
    mes menor rige para todos, por ejemplo un parámetro que cambia desde julio.
    """
    previo = {(r.persona_id, r.it): r for r in anteriores}
    salida = []
    for n in nuevos:
        clave = (n.persona_id, n.it)
        limite = desde.get(clave, defecto)
        a = previo.get(clave)
        if a is None or limite == 0:
            salida.append(n)
            continue
        r = deepcopy(n)
        for campo in ("horas", "horas_directa", "horas_indirecta", "semanas", "plan_directa", "plan_total", "meta_directa", "meta_total"):
            getattr(r, campo)[:limite] = getattr(a, campo)[:limite]
        salida.append(r)
    presentes = {(r.persona_id, r.it) for r in salida}
    for clave, a in previo.items():  # bajas totales: conservan los meses antes de la baja
        limite = desde.get(clave, 0)
        if clave not in presentes and limite > 0:
            r = deepcopy(a)
            for campo in ("horas", "horas_directa", "horas_indirecta", "semanas", "plan_directa", "plan_total", "meta_directa", "meta_total"):
                lista = getattr(r, campo)
                lista[limite:] = [0] * (MESES - limite)
            salida.append(r)
    return salida


class Planificador:
    """Mantiene el borrador al día. Calcular todo el país toma milisegundos."""

    def __init__(self, repo: Repositorio) -> None:
        self.repo = repo
        self.bloqueo = threading.Lock()

    def _grupo_alto(self, p: Parametros) -> frozenset[str]:
        # El motor recibe el instituto como texto del IT.
        nombres = self.repo.institutos()
        return frozenset(str(it) for it, n in nombres.items() if n in ("Santiago", "Concepción"))

    def sincronizar(self, usuario: str = "sistema") -> VersionPlan | None:
        """Crea el borrador si hay cargas terminadas que aún no se calculan."""
        with self.bloqueo:
            actual = self.repo.version_actual()
            nuevas = self.repo.cargas_aplicadas_desde(actual.id_carga if actual else 0)
            if not nuevas:
                return actual
            param = self.repo.parametros()
            filas = self.repo.filas_vigentes()
            registros = calcular(filas, param.motor, self._grupo_alto(param.motor), self.repo.incompletos())
            if actual:
                # Cambia lo que tocaron estas cargas: filas nuevas o editadas, y bajas.
                desde: dict[tuple[int, int], int] = {}
                for f in filas:
                    if f.id_carga in nuevas:
                        clave = (f.persona_id, f.it)
                        desde[clave] = min(desde.get(clave, 12), f.periodo.month - 1)
                for e in self.repo.cambios_detalle(nuevas):
                    if e["tipo"] == "BAJA":
                        clave = (e["persona_id"], e["it"])
                        desde[clave] = min(desde.get(clave, 12), e["desde"].month - 1)
                salida = congelar(registros, self.repo.registros(actual.id_version), desde, 12)
            else:
                salida = registros
            anio = max((f.periodo.year for f in filas), default=dt.date.today().year)
            v = self.repo.guardar_version(nuevas[-1], param.id_version, anio, usuario,
                                          f"Calculada tras la carga {nuevas[-1]}", salida)
            self.repo.auditar("BORRADOR", f"Se calculó la versión {v.id_version} con la carga {nuevas[-1]}", usuario)
            return v

    def simular(self, nuevo: Parametros, desde_mes: int) -> list[Registro]:
        actual = self.repo.version_actual()
        registros = calcular(self.repo.filas_vigentes(), nuevo, self._grupo_alto(nuevo), self.repo.incompletos())
        if actual is None:
            return registros
        return congelar(registros, self.repo.registros(actual.id_version), {}, desde_mes)

    def aplicar_parametros(self, nuevo: ParamApp, desde_mes: int, usuario: str, motivo: str) -> VersionPlan | None:
        with self.bloqueo:
            guardado = self.repo.guardar_parametros(nuevo, usuario, motivo)
            self.repo.auditar("PARAMETROS", f"Se creó {guardado.nombre}, rige desde el mes {desde_mes + 1}", usuario, motivo)
            actual = self.repo.version_actual()
            if actual is None:
                return None
            registros = calcular(self.repo.filas_vigentes(), guardado.motor, self._grupo_alto(guardado.motor), self.repo.incompletos())
            salida = congelar(registros, self.repo.registros(actual.id_version), {}, desde_mes)
            v = self.repo.guardar_version(actual.id_carga, guardado.id_version, actual.anio, usuario, motivo, salida)
            self.repo.auditar("BORRADOR", f"Se calculó la versión {v.id_version} por cambio de parámetros", usuario, motivo)
            return v


# ---------- Resúmenes ----------

@dataclass
class Filtros:
    it: int | None = None
    area: str = ""
    cargo: str = ""
    mes: int | None = None
    tipo: str = "tot"
    persona: str = ""


def filtrar(regs: list[Registro], f: Filtros) -> list[Registro]:
    return [r for r in regs if (f.it is None or r.it == f.it) and (not f.area or r.cod_esp == f.area)
            and (not f.cargo or r.cargo == f.cargo) and (not f.persona or f.persona.strip() in str(r.persona_id))]


def resumir(regs: list[Registro], mes: int | None) -> dict:
    meses = range(MESES) if mes is None else [mes]
    fila = 11 if mes is None else mes
    plan_dir = sum(r.plan_directa[m] for r in regs for m in meses)
    plan_tot = sum(r.plan_total[m] for r in regs for m in meses)
    cont = sum(r.horas[m] * r.semanas[m] for r in regs for m in meses)
    direc = sum(r.horas_directa[m] * r.semanas[m] for r in regs for m in meses)
    horas = sum(r.horas[fila] for r in regs)
    h_dir = sum(r.horas_directa[fila] for r in regs)
    h_ind = sum(r.horas_indirecta[fila] for r in regs)
    return dict(personas=len({r.persona_id for r in regs}), horas=horas, h_dir=h_dir, h_ind=h_ind, h_nas=max(horas - h_dir - h_ind, 0),
                plan_dir=plan_dir, plan_ind=plan_tot - plan_dir, plan_tot=plan_tot,
                meta_dir=sum(r.meta_directa[m] for r in regs for m in meses),
                meta_tot=sum(r.meta_total[m] for r in regs for m in meses), foja=direc / cont if cont else 0.0)


def valor_tipo(res: dict, tipo: str) -> int:
    return {"dir": res["plan_dir"], "ind": res["plan_ind"]}.get(tipo, res["plan_tot"])


def por_instituto(regs: list[Registro], mes: int | None, nombres: dict[int, str]) -> list[dict]:
    grupos: dict[int, list[Registro]] = defaultdict(list)
    for r in regs:
        grupos[r.it].append(r)
    return [dict(it=it, nombre=nombres.get(it, str(it)), **resumir(g, mes))
            for it, g in sorted(grupos.items(), key=lambda x: nombres.get(x[0], str(x[0])))]


def por_mes(regs: list[Registro]) -> list[dict]:
    return [dict(dir=sum(r.plan_directa[m] for r in regs),
                 ind=sum(r.plan_total[m] - r.plan_directa[m] for r in regs)) for m in range(MESES)]


def comparar_versiones(actual: list[Registro], previa: list[Registro]) -> list[dict]:
    """Personas cuya planificación cambió, con el primer mes afectado."""
    previos = {(r.persona_id, r.it): r for r in previa}
    salida = []
    for r in actual:
        a = previos.pop((r.persona_id, r.it), None)
        antes = a.meta_total if a else [0] * MESES
        distintos = [m for m in range(MESES) if antes[m] != r.meta_total[m]]
        if distintos:
            salida.append(dict(persona=r.persona_id, it=r.it, cargo=r.cargo, desde=distintos[0], antes=sum(antes),
                               despues=sum(r.meta_total), tipo="Nueva" if a is None else "Modificada"))
    for (persona, it), a in previos.items():
        if sum(a.meta_total):
            salida.append(dict(persona=persona, it=it, cargo=a.cargo, desde=0, antes=sum(a.meta_total), despues=0, tipo="Eliminada"))
    return sorted(salida, key=lambda x: abs(x["despues"] - x["antes"]), reverse=True)
