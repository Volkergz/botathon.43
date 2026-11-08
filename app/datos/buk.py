"""Lectura, validación y comparación de referencia del reporte BUK.

En la solución real, validar y comparar lo hace Blue Prism. Este módulo aplica las
mismas reglas para generar resultados esperados y probar al bot, y entrega las
entradas del motor mientras la base de datos no esté conectada.

Solo se usa lo que trae el BUK: asignaciones, licencias, permisos y colación quedan
en cero. Las vacaciones generales salen de los parámetros.
"""
from __future__ import annotations

import datetime as dt
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl

from app.datos.excel_inicial import BUK, Catalogos
from app.motor.calculo import MESES, EntradaFila, Parametros

FILA_TITULOS = 2
COLUMNAS = ["IT", "Instituto", "PersonaId", "Área", "Nombre Sub-área", "Centro de Costo",
            "Fecha de Nacimiento", "Periodo", "Tipo de Contrato", "Tipo_Trab_161225",
            "Fecha Ingreso Compañía", "Fecha Término", "Horas Semanales", "Cargo",
            "Tipo_Trabajador", "Días de la Jornada", "Reg_SIC", "Cod_Esp", "Ndias"]
CAMPOS_COMPARADOS = {"horas": "CAMBIO_JORNADA", "cargo": "CAMBIO_CARGO", "cc": "CAMBIO_CARGO",
                     "tipo_contrato": "CAMBIO_CONTRATO", "tipo_trabajador": "CAMBIO_CONTRATO",
                     "fecha_termino": "CAMBIO_VIGENCIA", "dias_jornada": "CAMBIO_DIAS"}


class EstructuraInvalida(Exception):
    """El archivo completo se rechaza: faltan columnas o no se puede leer."""


@dataclass
class FilaBuk:
    nro_fila: int
    persona_id: int | None
    it: int | None
    sigla: str | None
    nombre_instituto: str | None
    periodo: dt.date | None
    horas: float | None
    cargo: str | None
    cc: str | None
    tipo_contrato: str | None
    tipo_trabajador: str | None
    fecha_ingreso: dt.date | None
    fecha_termino: dt.date | None
    dias_jornada: str | None
    ndias: int | None

    @property
    def clave(self):
        return (self.persona_id, self.it, self.periodo)


@dataclass
class Observacion:
    nro_fila: int
    codigo: str
    nivel: str  # "BLOQUEANTE" o "ADVERTENCIA"
    persona_id: int | None
    it: int | None
    periodo: dt.date | None
    campo: str
    detalle: str


def _fecha(v):
    """Devuelve (fecha, es_valida). Vacío es válido y da None."""
    if v in (None, ""):
        return None, True
    if isinstance(v, dt.datetime):
        return v.date(), True
    if isinstance(v, dt.date):
        return v, True
    return None, False


def _entero(v):
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def leer_filas(ruta: Path = BUK) -> list[FilaBuk]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        hoja = openpyxl.load_workbook(ruta, data_only=True).worksheets[0]
    titulos = [c.value for c in hoja[FILA_TITULOS]]
    faltan = [c for c in COLUMNAS if c not in titulos]
    if faltan:
        raise EstructuraInvalida("Faltan columnas: " + ", ".join(faltan))
    pos = {c: titulos.index(c) for c in COLUMNAS}
    salida = []
    for n, f in enumerate(hoja.iter_rows(min_row=FILA_TITULOS + 1, values_only=True), FILA_TITULOS + 1):
        if all(x in (None, "") for x in f):
            continue
        g = lambda c: f[pos[c]]
        per, per_ok = _fecha(g("Periodo"))
        ing, ing_ok = _fecha(g("Fecha Ingreso Compañía"))
        ter, ter_ok = _fecha(g("Fecha Término"))
        fila = FilaBuk(n, _entero(g("PersonaId")), _entero(g("IT")), g("Instituto"), g("Área"), per,
                       float(g("Horas Semanales")) if isinstance(g("Horas Semanales"), (int, float)) else None,
                       g("Cargo") or None, str(g("Centro de Costo") or "").strip() or None,
                       g("Tipo de Contrato") or None, g("Tipo_Trabajador") or None, ing, ter,
                       g("Días de la Jornada") or None, _entero(g("Ndias")))
        fila.fechas_invalidas = [c for c, ok in (("Periodo", per_ok), ("Fecha Ingreso Compañía", ing_ok),
                                                 ("Fecha Término", ter_ok)) if not ok]
        fila.tipo_trab_antiguo = g("Tipo_Trab_161225") or None
        salida.append(fila)
    return salida


def validar(filas: list[FilaBuk], cat: Catalogos) -> tuple[list[FilaBuk], list[Observacion]]:
    """Devuelve las filas válidas y todas las observaciones."""
    validas, obs, vistas = [], [], set()
    for f in filas:
        def marcar(codigo, nivel, campo, detalle):
            obs.append(Observacion(f.nro_fila, codigo, nivel, f.persona_id, f.it, f.periodo, campo, detalle))

        antes = len(obs)
        faltan = [n for n, v in (("IT", f.it), ("PersonaId", f.persona_id), ("Cargo", f.cargo),
                                 ("Horas Semanales", f.horas), ("Tipo de Contrato", f.tipo_contrato),
                                 ("Ndias", f.ndias)) if v is None]
        if f.periodo is None and "Periodo" not in f.fechas_invalidas:
            faltan.append("Periodo")
        for campo in faltan:
            marcar("RC-B02", "BLOQUEANTE", campo, f"{campo} vacío")
        for campo in f.fechas_invalidas:
            marcar("RC-B13", "BLOQUEANTE", campo, f"{campo} no es una fecha válida")
        if f.it is not None and cat.institutos.get(f.it) != f.sigla:
            marcar("RC-B04", "BLOQUEANTE", "IT", f"IT {f.it} no coincide con {f.sigla}")
        if f.cargo and (f.cargo, f.cc) not in cat.homologacion:
            marcar("RC-B05", "BLOQUEANTE", "Cargo", f"Cargo sin parámetros: {f.cargo}")
        if f.horas is not None:
            tope = 42 if f.periodo and f.periodo.month >= 5 else 44
            if not 1 <= f.horas <= tope:
                marcar("RC-B06", "BLOQUEANTE", "Horas Semanales", f"{f.horas:g} horas, el rango es de 1 a {tope}")
        if f.periodo and f.fecha_termino and f.fecha_termino < f.periodo:
            marcar("RC-B11", "BLOQUEANTE", "Fecha Término", "La fecha de término es anterior al período")
        if f.periodo and f.fecha_ingreso and f.fecha_ingreso.replace(day=1) > f.periodo:
            marcar("RC-B11", "BLOQUEANTE", "Fecha Ingreso Compañía", "El ingreso es posterior al período")
        if all(x is not None for x in f.clave):
            if f.clave in vistas:
                marcar("RC-B03", "BLOQUEANTE", "PersonaId", "Fila repetida para la misma persona, instituto y mes")
            vistas.add(f.clave)

        if len(obs) > antes:
            continue
        validas.append(f)
        if (f.horas * 2) % 1:
            marcar("RC-B07", "ADVERTENCIA", "Horas Semanales", f"{f.horas:g} horas no es múltiplo de 0,5")
        if len(f.dias_jornada or "") != f.ndias:
            marcar("RC-B08", "ADVERTENCIA", "Ndias", f"Ndias dice {f.ndias}, pero «{f.dias_jornada}» tiene {len(f.dias_jornada or '')} días")
        if f.tipo_contrato == "Plazo fijo" and f.fecha_termino is None:
            marcar("RC-B09", "ADVERTENCIA", "Fecha Término", "Plazo fijo sin fecha de término")
        if f.tipo_contrato == "Indefinido" and f.fecha_termino is not None:
            marcar("RC-B10", "ADVERTENCIA", "Fecha Término", "Indefinido con fecha de término")
        if f.tipo_trab_antiguo != f.tipo_trabajador:
            marcar("RC-B12", "ADVERTENCIA", "Tipo_Trab_161225", "Tipo de trabajador distinto al registrado")
        if f.tipo_trabajador == "Reemplazo":
            marcar("RC-B15", "ADVERTENCIA", "Tipo_Trabajador", "Reemplazo sin titular informado")
    return validas, obs


@dataclass
class ResultadoComparacion:
    nuevas: list[FilaBuk] = field(default_factory=list)
    cambiadas: list[FilaBuk] = field(default_factory=list)
    sin_cambios: int = 0
    eliminadas: list[tuple] = field(default_factory=list)
    eventos: list[dict] = field(default_factory=list)


def comparar(validas: list[FilaBuk], vigentes: dict, completo: bool,
             todas: list[FilaBuk] | None = None) -> ResultadoComparacion:
    """Compara las filas válidas con lo vigente. `vigentes` usa la clave de FilaBuk.

    Una fila rechazada que ya estaba vigente se mantiene: no cuenta como baja. Por eso
    las bajas se calculan contra `todas` las filas del archivo, incluidas las rechazadas.
    Si alguna fila no tiene clave completa, ese instituto no genera bajas.
    """
    r = ResultadoComparacion()
    personas = {(k[0], k[1]) for k in vigentes}
    altas = set()
    for f in validas:
        v = vigentes.get(f.clave)
        if v is None:
            r.nuevas.append(f)
            if (f.persona_id, f.it) not in personas and (f.persona_id, f.it) not in altas:
                altas.add((f.persona_id, f.it))
                r.eventos.append(dict(tipo="ALTA", persona_id=f.persona_id, it=f.it, desde=f.periodo))
            continue
        cambios = [(c, t) for c, t in CAMPOS_COMPARADOS.items() if getattr(f, c) != getattr(v, c)]
        if not cambios:
            r.sin_cambios += 1
            continue
        r.cambiadas.append(f)
        for campo, tipo in cambios:
            r.eventos.append(dict(tipo=tipo, persona_id=f.persona_id, it=f.it, desde=f.periodo, campo=campo,
                                  antes=getattr(v, campo), despues=getattr(f, campo)))
    todas = validas if todas is None else todas
    claves_incompletas = {f.it for f in todas if None in f.clave}
    if completo:
        presentes = {f.clave for f in todas}
        faltantes: dict[tuple, dt.date] = {}
        for k in vigentes:
            if k not in presentes and k[1] not in claves_incompletas:
                r.eliminadas.append(k)
                par = (k[0], k[1])
                faltantes[par] = min(faltantes.get(par, k[2]), k[2])
        for (persona, it), desde in faltantes.items():
            r.eventos.append(dict(tipo="BAJA", persona_id=persona, it=it, desde=desde))
    return r


@dataclass
class Contrato:
    persona_id: int
    instituto: str
    nombre_instituto: str
    cargo: str = ""
    cargo_homologado: str = ""
    area: str = ""
    tipo_contrato: str = ""
    fecha_termino: dt.date | None = None
    horas: dict[int, float] = field(default_factory=dict)


def contratos_desde(filas: list[FilaBuk], cat: Catalogos) -> dict[tuple[int, str], Contrato]:
    salida: dict[tuple[int, str], Contrato] = {}
    for f in sorted(filas, key=lambda x: x.periodo):
        c = salida.setdefault((f.persona_id, f.sigla), Contrato(f.persona_id, f.sigla, f.nombre_instituto))
        c.cargo, c.tipo_contrato, c.fecha_termino = f.cargo, f.tipo_contrato, f.fecha_termino
        c.cargo_homologado, c.area = cat.homologacion[(f.cargo, f.cc)]
        c.horas[f.periodo.month - 1] = f.horas
    return salida


def a_entrada(c: Contrato, p: Parametros) -> EntradaFila:
    """Convierte un contrato en entrada del motor.

    Agosto a diciembre no vienen en el reporte: se proyectan con el último valor,
    salvo que la fecha de término diga otra cosa.
    """
    vigente = [m in c.horas for m in range(MESES)]
    if c.horas and max(c.horas) == 6:
        for m in range(7, MESES):
            vigente[m] = c.fecha_termino is None or c.fecha_termino >= dt.date(2026, m + 1, 1)
    horas: list[float | None] = [c.horas.get(m) for m in range(MESES)]
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
    return EntradaFila(
        persona_id=c.persona_id, instituto=c.instituto, cargo=c.cargo_homologado, area=c.area,
        horas_mes=limpias, vigente_mes=vigente,
        semanas_habiles=[semanas[m] if vigente[m] else 0.0 for m in range(MESES)],
        horas_diciembre=limpias[11] if vigente[11] else 0.0,
    )
