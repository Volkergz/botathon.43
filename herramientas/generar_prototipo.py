"""Genera prototipo/datos.js con cálculos reales desde el BUK.

Uso, desde la carpeta del proyecto: python -m herramientas.generar_prototipo

`Dotacion_Asistencial` se usa solo para la pantalla de comparación, nunca como
dato de entrada de la planificación.
"""
from __future__ import annotations

import json
from collections import defaultdict
from copy import copy

from app.datos.buk import Contrato, a_entrada, contratos_desde, leer_filas, validar
from app.datos.excel_inicial import RAIZ, cargar, cargar_parametros, leer_catalogos
from app.motor.calculo import calcular_fila

SALIDA = RAIZ / "prototipo" / "datos.js"
BASE, ERRORES = RAIZ / "pruebas_bp" / "BUK_1_base.xlsx", RAIZ / "pruebas_bp" / "BUK_2_con_errores.xlsx"


def total(entradas, p) -> int:
    return sum(calcular_fila(e, p).anual_total for e in entradas)


def cascada(p, entradas, esperados, total_buk: int) -> list[dict]:
    """Explica paso a paso la diferencia entre el Excel y el cálculo solo con BUK."""
    excel = sum(x.anual_total for x in esperados)
    semanas = p.semanas_habiles_mes()
    pasos = [("Ajustes manuales del Excel y reglas sin cerrar", total(entradas, p))]

    actual = [copy(e) for e in entradas]
    for e in actual:
        e.horas_asignaciones = 0
    pasos.append(("Sin asignaciones especiales (no vienen en el BUK)", total(actual, p)))
    for e in actual:
        e.semanas_habiles = [semanas[m] if e.vigente_mes[m] else 0.0 for m in range(12)]
    pasos.append(("Sin licencias, vacaciones personales ni permisos (no vienen en el BUK)", total(actual, p)))
    for e in actual:
        e.colacion = 0
    pasos.append(("Sin colación en contrato (no viene en el BUK)", total(actual, p)))
    pasos.append(("Proyección de agosto a diciembre y reparto multi-instituto", total_buk))

    salida, previo = [], excel
    for nombre, valor in pasos:
        salida.append({"causa": nombre, "efecto": valor - previo})
        previo = valor
    return salida


def main() -> None:
    p = cargar_parametros()
    cat = leer_catalogos()
    filas_error = leer_filas(ERRORES)
    validas_error, observaciones = validar(filas_error, cat)
    validas_base, _ = validar(leer_filas(BASE), cat)
    corregidos = contratos_desde(validas_base, cat)
    con_error = contratos_desde(validas_error, cat)
    filas, institutos = [], {}
    plan = meta = plan_c = meta_c = 0
    por_inst = defaultdict(int)

    for clave, c in corregidos.items():
        institutos[c.instituto] = c.nombre_instituto
        ce = con_error.get(clave) or Contrato(c.persona_id, c.instituto, c.nombre_instituto,
                                              c.cargo, c.cargo_homologado, c.area, c.tipo_contrato, c.fecha_termino)
        e, ec = a_entrada(ce, p), a_entrada(c, p)
        r, rc = calcular_fila(e, p), calcular_fila(ec, p)
        plan += r.anual_total
        meta += r.anual_meta_total
        plan_c += rc.anual_total
        meta_c += rc.anual_meta_total
        por_inst[c.instituto] += rc.anual_total
        f = {"id": c.persona_id, "inst": c.instituto, "cargo": c.cargo, "cargoH": c.cargo_homologado,
             "area": c.area, "tipo": c.tipo_contrato, "h": e.horas_mes, "v": e.vigente_mes, "dic": e.horas_diciembre}
        if ce.horas != c.horas:
            f.update(hc=ec.horas_mes, vc=ec.vigente_mes, dicc=ec.horas_diciembre,
                     err=sorted(set(c.horas) - set(ce.horas)))
        filas.append(f)

    _, entradas_x, esperados = cargar()
    excel_inst = defaultdict(int)
    for e, x in zip(entradas_x, esperados):
        excel_inst[e.instituto] += x.anual_total

    cargos = sorted({c.cargo_homologado for c in corregidos.values()})
    rechazos = [o for o in observaciones if o.nivel == "BLOQUEANTE"]
    avisos = defaultdict(lambda: {"filas": 0, "personas": set()})
    NOMBRES = {"RC-B07": "Horas con decimales no válidos", "RC-B08": "Ndias distinto de los días de la jornada",
               "RC-B09": "Plazo fijo sin fecha de término", "RC-B10": "Indefinido con fecha de término",
               "RC-B12": "Tipo de trabajador distinto al registrado", "RC-B15": "Reemplazo sin titular informado"}
    for o in observaciones:
        if o.nivel == "ADVERTENCIA":
            avisos[o.codigo]["filas"] += 1
            avisos[o.codigo]["personas"].add(o.persona_id)
    datos = {
        "params": {
            "dias": list(p.dias_habiles_mes), "vac": list(p.dias_vacaciones_mes), "diasSemana": p.dias_por_semana,
            "factor": p.factor_meta, "tramoAlto": p.tramo_alto_desde,
            "tramoMedio": p.tramo_medio_desde, "periodo1": p.meses_periodo_1, "grupoAlto": sorted(p.institutos_grupo_alto),
            "distribucion": {k: {"mind": p.distribucion[k].min_directa, "mini": p.distribucion[k].min_indirecta,
                                 "tramos": [list(t) for t in p.distribucion[k].tramos]} for k in cargos},
            "grupal": {k: {"pct": v.porcentaje, "alto": v.pacientes_santiago_concepcion, "otros": v.pacientes_otros}
                       for k, v in p.grupal.items()},
        },
        "filas": filas,
        "institutos": institutos,
        "errores": [{"fila": o.nro_fila, "persona": o.persona_id, "inst": cat.institutos.get(o.it, ""),
                     "mes": o.periodo.month - 1 if o.periodo else None, "codigo": o.codigo, "detalle": o.detalle}
                    for o in rechazos],
        "advertencias": [{"codigo": k, "detalle": NOMBRES[k], "filas": v["filas"], "personas": len(v["personas"])}
                         for k, v in sorted(avisos.items())],
        "carga": {"archivo": ERRORES.name, "filas": len(filas_error), "validas": len(validas_error),
                  "rechazadas": len(rechazos), "contratos": len(corregidos),
                  "personas": len({c.persona_id for c in corregidos.values()})},
        "comparacion": {"excelPlan": sum(x.anual_total for x in esperados),
                        "excelMeta": sum(x.anual_meta_total for x in esperados),
                        "porInstituto": excel_inst,
                        "cascada": cascada(p, entradas_x, esperados, plan_c)},
        "control": {"plan": plan, "meta": meta, "planCorregido": plan_c, "metaCorregida": meta_c},
    }
    SALIDA.parent.mkdir(exist_ok=True)
    SALIDA.write_text("window.DATOS = " + json.dumps(datos, ensure_ascii=False) + ";\n", encoding="utf-8")
    print(f"{SALIDA.name}: {len(filas)} contratos, plan {plan_c:,}, meta {meta_c:,}")
    for paso in datos["comparacion"]["cascada"]:
        print(f"  {paso['efecto']:+10,}  {paso['causa']}")


if __name__ == "__main__":
    main()
