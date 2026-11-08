"""Compara el motor contra las filas del Excel y resume dónde difiere.

Uso, desde la carpeta del proyecto: python -m herramientas.comparar_oraculo
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

from app.datos.excel_inicial import RAIZ, cargar
from app.motor.calculo import calcular_fila

sys.stdout.reconfigure(encoding="utf-8")
SALIDA = RAIZ / "herramientas" / "diferencias.csv"


def main() -> None:
    p, entradas, esperados = cargar()
    resultados = [calcular_fila(e, p) for e in entradas]
    n = len(entradas)

    def linea(nombre: str, ok: int, total: int) -> None:
        print(f"{nombre:34s}{ok:6d} de {total:<6d}{ok / total * 100:6.1f} %")

    print(f"Filas comparadas: {n}\n")
    linea("Promedio de horas", sum(r.promedio_horas == x.promedio for r, x in zip(resultados, esperados)), n)
    linea("Atenciones directas semana", sum(r.periodo_2.directas == x.directas for r, x in zip(resultados, esperados)), n)
    linea("Atenciones indirectas semana", sum(r.periodo_2.indirectas == x.indirectas for r, x in zip(resultados, esperados)), n)
    linea("Directas con grupal", sum(abs(r.periodo_2.directas_con_grupal - x.con_grupal) < 1e-6 for r, x in zip(resultados, esperados)), n)
    print()
    for nombre, a, b in [("Plan directo", "plan_directa", "plan_directa"), ("Plan total", "plan_total", "plan_total"),
                         ("Meta directa", "meta_directa", "meta_directa"), ("Meta total", "meta_total", "meta_total")]:
        ok = sum(ra == rb for r, x in zip(resultados, esperados) for ra, rb in zip(getattr(r, a), getattr(x, b)))
        linea(f"{nombre} (celdas persona-mes)", ok, n * 12)
    print()
    linea("Filas con los 12 meses exactos", sum(
        r.plan_directa == x.plan_directa and r.plan_total == x.plan_total
        and r.meta_directa == x.meta_directa and r.meta_total == x.meta_total
        for r, x in zip(resultados, esperados)), n)

    calc, real = sum(r.anual_total for r in resultados), sum(x.anual_total for x in esperados)
    print(f"\nPlan total anual:  motor {calc:,}  Excel {real:,}  diferencia {calc - real:+,} ({(calc - real) / real * 100:+.2f} %)")
    calc, real = sum(r.anual_meta_total for r in resultados), sum(x.anual_meta_total for x in esperados)
    print(f"Meta total anual:  motor {calc:,}  Excel {real:,}  diferencia {calc - real:+,} ({(calc - real) / real * 100:+.2f} %)")

    with open(SALIDA, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["fila_excel", "persona", "instituto", "cargo", "prom_motor", "prom_excel",
                    "anual_total_motor", "anual_total_excel", "diferencia"])
        difs = 0
        for e, r, x in zip(entradas, resultados, esperados):
            if r.anual_total != x.anual_total:
                difs += 1
                w.writerow([x.fila_excel, x.persona_id, e.instituto, e.cargo, r.promedio_horas, x.promedio,
                            r.anual_total, x.anual_total, r.anual_total - x.anual_total])
    print(f"\n{difs} filas con diferencia anual, detalle en {Path(SALIDA).name}")


if __name__ == "__main__":
    main()
