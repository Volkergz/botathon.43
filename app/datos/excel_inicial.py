"""Carga inicial desde los Excel del kit.

Solo sirve para desarrollar y probar el motor mientras no exista la base de datos.
Cuando esté lista, otro módulo leerá las mismas estructuras desde SQL Server.
"""
from __future__ import annotations

import datetime as dt
import warnings
from dataclasses import dataclass
from pathlib import Path

import openpyxl
from openpyxl.utils import column_index_from_string as col

from app.motor.calculo import DistribucionCargo, EntradaFila, GrupalArea, Parametros

RAIZ = Path(__file__).resolve().parents[2]
PLAN = RAIZ / "Nac - Planificación Asistencial x mes.xlsx"
BUK = RAIZ / "Nac - Base contratos x mes.xlsx"

FILA_INICIO = 5
SEMANAS_INI, SEMHAB_INI = col("BI"), col("DZ")
PLAN_DIR_INI, PLAN_TOT_INI, META_DIR_INI, META_TOT_INI = col("EN"), col("FA"), col("FN"), col("GA")


@dataclass
class Esperado:
    fila_excel: int
    persona_id: int
    promedio: float
    directas: float
    indirectas: float
    con_grupal: float
    plan_directa: list[int]
    plan_total: list[int]
    meta_directa: list[int]
    meta_total: list[int]
    anual_directa: int
    anual_total: int
    anual_meta_total: int


def _libro(ruta: Path):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return openpyxl.load_workbook(ruta, data_only=True)


def _num(v, defecto: float = 0.0) -> float:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else defecto


def leer_parametros(libro) -> Parametros:
    hoja = libro["Distribucion_NAC"]
    distribucion: dict[str, DistribucionCargo] = {}
    for r in range(7, 93):
        cargo = hoja.cell(r, 2).value
        if not cargo:
            continue
        v = [_num(hoja.cell(r, c).value) for c in range(4, 15)]
        distribucion[cargo] = DistribucionCargo(
            min_directa=v[0], min_indirecta=v[1],
            tramos=((v[2], v[3], v[4]), (v[5], v[6], v[7]), (v[8], v[9], v[10])),
        )
    hoja_p = libro["Parámetros"]
    grupal: dict[str, GrupalArea] = {}
    for r in range(4, 26):
        pct = hoja_p.cell(r, col("G")).value
        if pct:
            area = hoja_p.cell(r, col("F")).value
            grupal[area] = GrupalArea(float(pct), _num(hoja_p.cell(r, col("H")).value),
                                      _num(hoja_p.cell(r, col("I")).value))
    dias = tuple(int(hoja_p.cell(r, col("Y")).value) for r in range(3, 15))
    con_vacaciones = tuple(int(hoja_p.cell(r, col("Z")).value) for r in range(3, 15))
    return Parametros(distribucion=distribucion, grupal=grupal, dias_habiles_mes=dias,
                      dias_vacaciones_mes=tuple(a - b for a, b in zip(dias, con_vacaciones)))


def cargar_parametros() -> Parametros:
    """Parámetros iniciales. No usa los datos personales de Dotacion_Asistencial."""
    return leer_parametros(_libro(PLAN))


def cargo_homologado(cargo: str, area: str, distribucion: dict) -> str | None:
    """El mismo cargo tiene otros % según su unidad: TERAPEUTA OCUPACIONAL + ULAB."""
    con_area = f"{cargo} {area}"
    if con_area in distribucion:
        return con_area
    return cargo if cargo in distribucion else None


@dataclass
class Catalogos:
    institutos: dict[int, str]  # IT -> sigla
    homologacion: dict[tuple[str, str], tuple[str, str]]  # (cargo, c.costo) -> (homologado, área)


def leer_catalogos(libro=None) -> Catalogos:
    libro = libro or _libro(PLAN)
    p = leer_parametros(libro)
    hoja = libro["Parámetros"]
    institutos: dict[int, str] = {}
    for r in range(2, hoja.max_row + 1):
        it, sigla = hoja.cell(r, col("AM")).value, hoja.cell(r, col("AN")).value
        if isinstance(it, (int, float)) and sigla:
            institutos[int(it)] = str(sigla)
    homologacion: dict[tuple[str, str], tuple[str, str]] = {}
    for r in range(2, hoja.max_row + 1):
        area, cargo, cc = (hoja.cell(r, c).value for c in (1, 2, 3))
        if not cargo or cc is None:
            continue
        destino = cargo_homologado(cargo, area, p.distribucion)
        if destino:
            homologacion[(cargo, str(int(cc)))] = (destino, area)
    return Catalogos(institutos, homologacion)


def _horas_buk() -> dict[tuple[int, int], dict[int, float]]:
    hoja = _libro(BUK).worksheets[0]
    horas: dict[tuple[int, int], dict[int, float]] = {}
    for f in hoja.iter_rows(min_row=3, values_only=True):
        if f[2] is None:
            continue
        clave = (int(f[2]), int(f[0]))
        horas.setdefault(clave, {})[f[7].month - 1] = float(f[12])
    return horas


def _horas_excepciones(libro) -> dict[tuple[int, int], dict[int, float]]:
    hoja = libro["Excepciones"]
    horas: dict[tuple[int, int], dict[int, float]] = {}
    for r in range(3, 20):
        persona, it = hoja.cell(r, 5).value, hoja.cell(r, 3).value
        if persona is None:
            continue
        horas[(int(persona), int(it))] = {
            m: float(v) for m in range(12)
            if isinstance(v := hoja.cell(r, 11 + m).value, (int, float))
        }
    return horas


def _completar(conocidas: dict[int, float], diciembre: float) -> list[float]:
    """Rellena meses sin dato: agosto en adelante con H/S dic; el resto con el vecino."""
    h: list[float | None] = [conocidas.get(m) for m in range(12)]
    if diciembre > 0:
        for m in range(7, 12):
            h[m] = h[m] if h[m] is not None else diciembre
    ultimo = None
    for m in range(12):
        ultimo = h[m] if h[m] is not None else ultimo
        h[m] = ultimo
    siguiente = None
    for m in range(11, -1, -1):
        siguiente = h[m] if h[m] is not None else siguiente
        h[m] = siguiente
    return [x if x is not None else 0.0 for x in h]


def cargar() -> tuple[Parametros, list[EntradaFila], list[Esperado]]:
    libro = _libro(PLAN)
    p = leer_parametros(libro)
    buk, excepciones = _horas_buk(), _horas_excepciones(libro)
    hoja = libro["Dotacion_Asistencial"]
    entradas: list[EntradaFila] = []
    esperados: list[Esperado] = []

    def v(r: int, letra: str):
        return hoja.cell(r, col(letra)).value

    def serie(r: int, ini: int) -> list[float]:
        return [_num(hoja.cell(r, ini + m).value) for m in range(12)]

    for r in range(FILA_INICIO, hoja.max_row + 1):
        persona = v(r, "K")
        if not isinstance(persona, (int, float)):
            continue
        persona, it = int(persona), int(v(r, "B"))
        area = v(r, "N")
        cargo = cargo_homologado(v(r, "M"), area, p.distribucion) or v(r, "M")
        diciembre = _num(v(r, "W"))
        conocidas = excepciones.get((persona, it)) or buk.get((persona, it)) or {}
        asignaciones = _num(v(r, "AS"))
        minutos = _num(v(r, "AY"))
        base = p.distribucion[cargo].min_directa
        instituto = "PMo" if v(r, "C") == "Pmo" else v(r, "C")
        entradas.append(EntradaFila(
            persona_id=persona, instituto=instituto, cargo=cargo, area=area,
            horas_mes=_completar(conocidas, diciembre),
            vigente_mes=[x > 0 for x in serie(r, SEMANAS_INI)],
            semanas_habiles=serie(r, SEMHAB_INI),
            horas_diciembre=diciembre, colacion=_num(v(r, "AC")),
            horas_asignaciones=asignaciones,
            min_directa_excepcion=minutos if minutos and minutos != base else None,
        ))
        esperados.append(Esperado(
            r, persona, _num(v(r, "X")), _num(v(r, "BA")), _num(v(r, "BB")), _num(v(r, "BF")),
            [int(x) for x in serie(r, PLAN_DIR_INI)], [int(x) for x in serie(r, PLAN_TOT_INI)],
            [int(x) for x in serie(r, META_DIR_INI)], [int(x) for x in serie(r, META_TOT_INI)],
            int(_num(v(r, "GN"))), int(_num(v(r, "GP"))), int(_num(v(r, "GV"))),
        ))
    return p, entradas, esperados
