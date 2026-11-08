"""Crea los archivos BUK de prueba para Blue Prism y sus resultados esperados.

Uso, desde la carpeta del proyecto: python -m herramientas.generar_archivos_prueba
Salida: pruebas_bp/ con 5 Excel y resultados_esperados.json
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
import warnings
from collections import Counter

import openpyxl

from app.datos.buk import COLUMNAS, FILA_TITULOS, comparar, leer_filas, validar
from app.datos.excel_inicial import BUK, RAIZ, leer_catalogos

SALIDA = RAIZ / "pruebas_bp"
A1, A2, A3, A4, A5 = ("BUK_1_base.xlsx", "BUK_2_con_errores.xlsx", "BUK_3_correcciones.xlsx",
                      "BUK_4_sin_columna.xlsx", "BUK_5_cambios.xlsx")


def abrir():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        libro = openpyxl.load_workbook(BUK)
    return libro, libro.worksheets[0]


def col(nombre: str, hoja) -> int:
    return [c.value for c in hoja[FILA_TITULOS]].index(nombre) + 1


def buscar_fila(hoja, persona: int, mes: int) -> int:
    cp, cper = col("PersonaId", hoja), col("Periodo", hoja)
    for r in range(FILA_TITULOS + 1, hoja.max_row + 1):
        per = hoja.cell(r, cper).value
        if hoja.cell(r, cp).value == persona and isinstance(per, dt.datetime) and per.month == mes:
            return r
    raise LookupError((persona, mes))


# Errores: persona, mes, columna, valor malo
ERRORES = [(137732, 5, "Horas Semanales", 50), (71580, 6, "Cargo", "CARGO DESCONOCIDO"),
           (121286, 3, "PersonaId", None), (115921, 4, "Fecha Término", "no aplica")]


def crear_errores() -> list[int]:
    libro, hoja = abrir()
    filas = []
    for persona, mes, nombre, valor in ERRORES:
        r = buscar_fila(hoja, persona, mes)
        hoja.cell(r, col(nombre, hoja)).value = valor
        filas.append(r)
    libro.save(SALIDA / A2)
    return filas


def crear_correcciones(filas: list[int]) -> None:
    """Solo las filas corregidas, con la misma estructura del reporte."""
    libro, hoja = abrir()
    conservar = set(filas)
    for r in range(hoja.max_row, FILA_TITULOS, -1):
        if r not in conservar:
            hoja.delete_rows(r)
    libro.save(SALIDA / A3)


def crear_sin_columna() -> None:
    libro, hoja = abrir()
    hoja.delete_cols(col("Horas Semanales", hoja))
    libro.save(SALIDA / A4)


def crear_cambios() -> dict:
    libro, hoja = abrir()
    ch, cc = col("Horas Semanales", hoja), col("Cargo", hoja)
    # Cambio de jornada desde junio
    for mes in (6, 7):
        hoja.cell(buscar_fila(hoja, 140126, mes), ch).value = 30
    # Cambio de cargo desde julio
    hoja.cell(buscar_fila(hoja, 71306, 7), cc).value = "ENFERMERA"
    # Baja: deja de aparecer desde mayo
    for mes in (7, 6, 5):
        hoja.delete_rows(buscar_fila(hoja, 76852, mes))
    # Alta: persona nueva copiada de otra, desde julio
    origen = buscar_fila(hoja, 129328, 7)
    nueva = hoja.max_row + 1
    for c in range(1, len(COLUMNAS) + 1):
        hoja.cell(nueva, c).value = hoja.cell(origen, c).value
    hoja.cell(nueva, col("PersonaId", hoja)).value = 999001
    hoja.cell(nueva, col("Fecha Ingreso Compañía", hoja)).value = dt.datetime(2026, 7, 1)
    # Fila repetida
    otra = hoja.max_row + 1
    origen = buscar_fila(hoja, 74031, 7)
    for c in range(1, len(COLUMNAS) + 1):
        hoja.cell(otra, c).value = hoja.cell(origen, c).value
    libro.save(SALIDA / A5)


def resumen(archivo: str, cat, vigentes=None, completo=True) -> dict:
    filas = leer_filas(SALIDA / archivo)
    validas, obs = validar(filas, cat)
    r = comparar(validas, vigentes or {}, completo, filas)
    rechazadas = {o.nro_fila for o in obs if o.nivel == "BLOQUEANTE"}
    return {
        "archivo": archivo, "tipo": "COMPLETO" if completo else "CORRECCIONES",
        "filas_leidas": len(filas), "filas_validas": len(validas), "filas_rechazadas": len(rechazadas),
        "advertencias_por_regla": dict(sorted(Counter(o.codigo for o in obs if o.nivel == "ADVERTENCIA").items())),
        "rechazos_por_regla": dict(sorted(Counter(o.codigo for o in obs if o.nivel == "BLOQUEANTE").items())),
        "detalle_rechazos": [dict(fila=o.nro_fila, codigo=o.codigo, persona=o.persona_id, campo=o.campo, detalle=o.detalle)
                             for o in obs if o.nivel == "BLOQUEANTE"],
        "nuevas": len(r.nuevas), "cambiadas": len(r.cambiadas), "sin_cambios": r.sin_cambios,
        "eliminadas": len(r.eliminadas),
        "eventos": dict(sorted(Counter(e["tipo"] for e in r.eventos).items())),
        "detalle_eventos": [{k: str(v) for k, v in e.items()} for e in r.eventos if e["tipo"] != "ALTA" or len(vigentes or {}) > 0],
    }, {f.clave: f for f in validas}


def main() -> None:
    SALIDA.mkdir(exist_ok=True)
    shutil.copyfile(BUK, SALIDA / A1)
    filas_error = crear_errores()
    crear_correcciones(filas_error)
    crear_sin_columna()
    crear_cambios()

    cat = leer_catalogos()
    esperado = {}
    # Escenario A: base vacía, archivo con errores y luego correcciones.
    a2, vig = resumen(A2, cat)
    a3, nuevos = resumen(A3, cat, vig, completo=False)
    vig_a = {**vig, **nuevos}
    esperado["A_errores_y_correcciones"] = {"paso_1": a2, "paso_2": a3, "filas_vigentes_final": len(vig_a)}
    # Escenario B: base completa y reporte con cambios.
    a1, vig_b = resumen(A1, cat)
    a5, _ = resumen(A5, cat, vig_b)
    esperado["B_cambios"] = {"paso_1": a1, "paso_2": a5}
    # Escenario C: archivo inválido.
    esperado["C_sin_columna"] = {"archivo": A4, "resultado": "RECHAZADA", "motivo": "Faltan columnas: Horas Semanales"}
    (SALIDA / "resultados_esperados.json").write_text(json.dumps(esperado, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    for nombre, e in [("BUK_2", a2), ("BUK_3", a3), ("BUK_1", a1), ("BUK_5", a5)]:
        print(nombre, {k: e[k] for k in ("filas_leidas", "filas_validas", "filas_rechazadas", "nuevas", "cambiadas", "sin_cambios", "eliminadas", "eventos", "rechazos_por_regla", "advertencias_por_regla")})
    print("vigentes final A", len(vig_a))


if __name__ == "__main__":
    main()
