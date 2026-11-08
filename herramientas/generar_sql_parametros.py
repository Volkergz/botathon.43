"""Genera botathon.43/sql/v2/03_parametros_iniciales.sql desde el Excel de planificación.

Uso, desde la carpeta del proyecto: python -m herramientas.generar_sql_parametros
No copia datos de personas: solo catálogos, parámetros y totales por instituto.
"""
from __future__ import annotations

import datetime as dt
import warnings

import openpyxl
from openpyxl.utils import column_index_from_string as col

from app.datos.excel_inicial import PLAN, RAIZ, cargar, cargo_homologado, leer_catalogos, leer_parametros

SALIDA = RAIZ / "botathon.43" / "sql" / "v2" / "03_parametros_iniciales.sql"
VERSION = "Inicial 2026"
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]
ESPECIALIDADES_EXTRA = {"ED": "EDUCADORA"}
REGLAS = [
    ("RC-E01", "Archivo sin la hoja o columnas esperadas", "BLOQUEANTE"),
    ("RC-B02", "Campo obligatorio vacío", "BLOQUEANTE"),
    ("RC-B03", "Fila repetida por PersonaId, IT y Periodo", "BLOQUEANTE"),
    ("RC-B04", "IT inexistente o incoherente con Instituto", "BLOQUEANTE"),
    ("RC-B05", "Cargo sin homologar por cargo y centro de costo", "BLOQUEANTE"),
    ("RC-B06", "Horas fuera de rango: 1 a 44 hasta abril, 1 a 42 desde mayo", "BLOQUEANTE"),
    ("RC-B07", "Horas no múltiplos de 0,5", "ADVERTENCIA"),
    ("RC-B08", "Ndias distinto de los días de la jornada", "ADVERTENCIA"),
    ("RC-B09", "Plazo fijo sin fecha de término", "ADVERTENCIA"),
    ("RC-B10", "Indefinido con fecha de término", "ADVERTENCIA"),
    ("RC-B11", "Fecha de término anterior al período o ingreso posterior", "BLOQUEANTE"),
    ("RC-B12", "Tipo_Trab_161225 distinto de Tipo_Trabajador", "ADVERTENCIA"),
    ("RC-B13", "Fecha con formato inválido", "BLOQUEANTE"),
    ("RC-B15", "Reemplazo sin titular informado", "ADVERTENCIA"),
]


def q(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, str):
        return "N'" + v.replace("'", "''") + "'"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float):
        return repr(round(v, 6))
    return str(v)


def lote(tabla: str, cols: str, filas: list[tuple], condicion: str) -> str:
    """Inserta lo que no existe, para poder repetir el script."""
    valores = ",\n    ".join("(" + ", ".join(q(x) for x in f) + ")" for f in filas)
    alias = ", ".join(c.strip() for c in cols.split(","))
    return (f"INSERT INTO {tabla} ({cols})\nSELECT {alias}\nFROM (VALUES\n    {valores}\n) v ({alias})\n"
            f"WHERE NOT EXISTS (SELECT 1 FROM {tabla} t WHERE {condicion});\nGO\n")


def main() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        libro = openpyxl.load_workbook(PLAN, data_only=True)
    p = leer_parametros(libro)
    cat = leer_catalogos(libro)
    hoja = libro["Parámetros"]
    dist = libro["Distribucion_NAC"]
    sal = ["/* Generado por herramientas/generar_sql_parametros.py. No editar a mano. */",
           "USE TeletonPlanificacion;\nGO\n"]

    sal.append("IF NOT EXISTS (SELECT 1 FROM ctl.anio_planificacion WHERE anio = 2026)\n"
               "    INSERT INTO ctl.anio_planificacion (anio) VALUES (2026);\n"
               f"IF NOT EXISTS (SELECT 1 FROM ctl.version_parametros WHERE nombre = {q(VERSION)})\n"
               "    INSERT INTO ctl.version_parametros (nombre, anio, creado_por, motivo)\n"
               f"    VALUES ({q(VERSION)}, 2026, N'Carga inicial', N'Parámetros del Excel de planificación');\nGO\n")
    sal.append(lote("ctl.catalogo_error", "codigo_error, descripcion, severidad", REGLAS, "t.codigo_error = v.codigo_error"))

    # Institutos: la primera fila de cada IT es el nombre sin "(G)".
    nombres: dict[int, str] = {}
    for r in range(2, hoja.max_row + 1):
        it, nombre = hoja.cell(r, col("AM")).value, hoja.cell(r, col("AO")).value
        if isinstance(it, (int, float)) and nombre and int(it) not in nombres:
            nombres[int(it)] = nombre
    filas = [(it, cat.institutos[it], nombre, 1 if cat.institutos[it] == "Coy" else 0,
              "SAN_CON" if cat.institutos[it] in ("San", "Con") else "OTROS",
              0 if cat.institutos[it] == "Adm" else 1) for it, nombre in sorted(nombres.items())]
    sal.append(lote("plata.instituto", "it, sigla, nombre, zona_extrema, grupo_grupal, es_asistencial", filas, "t.it = v.it"))

    # Especialidades
    esp: dict[str, str] = {}
    for r in range(2, hoja.max_row + 1):
        c, n = hoja.cell(r, col("AX")).value, hoja.cell(r, col("AY")).value
        if c and n:
            esp[c] = n
    for _, (_, area) in cat.homologacion.items():
        esp.setdefault(area, ESPECIALIDADES_EXTRA.get(area, area))
    for area in p.grupal:
        esp.setdefault(area, area)
    sal.append(lote("plata.especialidad", "cod_esp, nombre", sorted(esp.items()), "t.cod_esp = v.cod_esp"))

    # Cargos y parámetros por cargo
    unidades = {dist.cell(r, 2).value: dist.cell(r, 3).value for r in range(7, 93) if dist.cell(r, 2).value}
    cargos = sorted(p.distribucion)
    sal.append(lote("plata.cargo", "cargo_homologado, unidad", [(c, unidades.get(c)) for c in cargos],
                    "t.cargo_homologado = v.cargo_homologado"))
    filas = sorted((cargo, cc, destino, area) for (cargo, cc), (destino, area) in cat.homologacion.items())
    valores = ",\n    ".join("(" + ", ".join(q(x) for x in f) + ")" for f in filas)
    sal.append("INSERT INTO plata.cargo_homologacion (cargo_buk, centro_costo, id_cargo, cod_esp)\n"
               f"SELECT v.cargo_buk, v.cc, c.id_cargo, v.cod_esp\nFROM (VALUES\n    {valores}\n) v (cargo_buk, cc, destino, cod_esp)\n"
               "JOIN plata.cargo c ON c.cargo_homologado = v.destino\n"
               "WHERE NOT EXISTS (SELECT 1 FROM plata.cargo_homologacion t WHERE t.cargo_buk = v.cargo_buk AND t.centro_costo = v.cc);\nGO\n")

    ver = f"(SELECT id_version FROM ctl.version_parametros WHERE nombre = {q(VERSION)})"
    # Se declara en cada lote porque GO termina las variables.
    def con_version(tabla: str, cols: str, filas: list[tuple], clave: str) -> str:
        valores = ",\n    ".join("(" + ", ".join(q(x) for x in f) + ")" for f in filas)
        alias = ", ".join(c.strip() for c in cols.split(","))
        select = ", ".join("v." + c.strip() for c in cols.split(","))
        cond = " AND ".join(f"t.{c} = v.{c}" for c in clave.split(","))
        return (f"INSERT INTO {tabla} (id_version, {cols})\nSELECT {ver}, {select}\nFROM (VALUES\n    {valores}\n) v ({alias})\n"
                f"WHERE NOT EXISTS (SELECT 1 FROM {tabla} t WHERE t.id_version = {ver} AND {cond});\nGO\n")

    def con_cargo(tabla: str, cols: str, filas: list[tuple], clave: str) -> str:
        valores = ",\n    ".join("(" + ", ".join(q(x) for x in f) + ")" for f in filas)
        alias = "cargo, " + ", ".join(c.strip() for c in cols.split(","))
        select = ", ".join("v." + c.strip() for c in cols.split(","))
        extra = "".join(f" AND t.{c} = v.{c}" for c in clave.split(",") if c)
        return (f"INSERT INTO {tabla} (id_version, id_cargo, {cols})\nSELECT {ver}, c.id_cargo, {select}\nFROM (VALUES\n    {valores}\n) v ({alias})\n"
                "JOIN plata.cargo c ON c.cargo_homologado = v.cargo\n"
                f"WHERE NOT EXISTS (SELECT 1 FROM {tabla} t WHERE t.id_version = {ver} AND t.id_cargo = c.id_cargo{extra});\nGO\n")

    sal.append(con_version("plata.param_general", "clave, valor, descripcion", [
        ("META_PCT", 0.9, "Meta de gestión sobre el plan"), ("DIAS_POR_SEMANA", 5.0, "Días para convertir a semanas"),
        ("MAX_ASIGNACIONES", 2.0, "Máximo con más de 30 horas"), ("HORAS_MAX_ASIGNACIONES", 30.0, "Jornada desde la que aplica el máximo"),
    ], "clave"))
    sal.append(con_version("plata.param_tramo", "tramo, horas_desde, horas_hasta",
                           [("GE40", 40.0, None), ("22A40", 22.0, 40.0), ("LT22", 0.0, 22.0)], "tramo"))
    sal.append(con_version("plata.param_periodo_legal", "periodo, mes_desde, mes_hasta, jornada_max_horas",
                           [(1, 1, 4, 44.0), (2, 5, 12, 42.0)], "periodo"))
    tramos = ("GE40", "22A40", "LT22")
    sal.append(con_cargo("plata.param_distribucion", "tramo, pct_directo, pct_indirecto, pct_nas",
                         [(c, tramos[i], *d.tramos[i]) for c, d in sorted(p.distribucion.items()) for i in range(3)], "tramo"))
    sal.append(con_cargo("plata.param_minutos_estandar", "min_directa, min_indirecta",
                         [(c, int(d.min_directa), int(d.min_indirecta)) for c, d in sorted(p.distribucion.items())], ""))
    filas = []
    for area, g in sorted(p.grupal.items()):
        filas += [(area, "SAN_CON", g.porcentaje, g.pacientes_santiago_concepcion), (area, "OTROS", g.porcentaje, g.pacientes_otros)]
    sal.append(con_version("plata.param_grupal", "cod_esp, grupo_grupal, pct_grupal, pacientes_por_grupo", filas, "cod_esp,grupo_grupal"))

    asign: dict[str, tuple] = {}
    for r in range(7, 40):
        nombre, horas, desc = dist.cell(r, 18).value, dist.cell(r, 19).value, dist.cell(r, 20).value
        if not nombre:
            continue
        # Algunas descripciones traen nombres propios: no se copian.
        texto = "Por contrato individual" if desc and "Contrato" in str(desc) else (str(desc) if desc else None)
        requiere = bool(texto and "DMN" in texto)
        asign[nombre.strip()] = (nombre.strip(), float(horas) if horas else None, requiere, texto)
    sal.append(con_version("plata.param_asignacion", "nombre, horas_semana, requiere_autorizacion, definido_por",
                           sorted(asign.values()), "nombre"))
    sal.append(con_version("plata.param_calendario_mes", "mes, dias_habiles, dias_vacaciones",
                           [(m + 1, p.dias_habiles_mes[m], p.dias_vacaciones_mes[m]) for m in range(12)], "mes"))

    cal = libro["Calendario 2026"]
    feriados = []
    for r in range(105, 123):
        dia, mes, texto = cal.cell(r, 1).value, cal.cell(r, 2).value, cal.cell(r, 3).value
        if dia and mes in MESES:
            feriados.append((dt.date(2026, MESES.index(mes) + 1, int(dia)).isoformat(), texto, 1 if "(" in texto else 0))
    sal.append(con_version("plata.param_feriado", "fecha, descripcion, es_regional", feriados, "fecha,descripcion"))

    _, entradas, esperados = cargar()
    siglas = {s: it for it, s in cat.institutos.items()}
    plan, meta = {}, {}
    for e, x in zip(entradas, esperados):
        it = siglas[e.instituto]
        plan[it] = plan.get(it, 0) + x.anual_total
        meta[it] = meta.get(it, 0) + x.anual_meta_total
    sal.append(lote("ctl.referencia_excel", "anio, it, plan_total, meta_total",
                    [(2026, it, plan[it], meta[it]) for it in sorted(plan)], "t.anio = v.anio AND t.it = v.it"))
    sal.append(lote("oro.dim_tiempo", "id_tiempo, fecha_mes, anio, mes, nombre_mes, nombre_mes_corto, periodo_legal",
                    [(202600 + m + 1, dt.date(2026, m + 1, 1).isoformat(), 2026, m + 1, MESES[m].capitalize(),
                      MESES[m][:3].capitalize(), 1 if m < 4 else 2) for m in range(12)], "t.id_tiempo = v.id_tiempo"))
    sal.append("PRINT N'TeletonPlanificacion: parámetros cargados.';\nGO\n")
    SALIDA.write_text("\n".join(sal), encoding="utf-8")
    print(SALIDA.name, "institutos", len(nombres), "esp", len(esp), "cargos", len(cargos),
          "homologacion", len(cat.homologacion), "asignaciones", len(asign), "feriados", len(feriados))


if __name__ == "__main__":
    main()
