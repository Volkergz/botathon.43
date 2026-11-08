"""Genera appBotathon/web/datos/referencia_excel.json desde prototipo/datos.js.

Uso, desde la carpeta del proyecto: python -m herramientas.generar_referencia_app
Solo trae totales para explicar la diferencia con el Excel, sin datos de personas.
"""
import json

from app.datos.excel_inicial import RAIZ, leer_catalogos

texto = (RAIZ / "prototipo" / "datos.js").read_text(encoding="utf-8")
datos = json.loads(texto[texto.index("{"):texto.rindex("}") + 1])
c = datos["comparacion"]
its = {s: it for it, s in leer_catalogos().institutos.items()}
salida = {"plan_total": c["excelPlan"], "meta_total": c["excelMeta"],
          "por_instituto": {str(its[s]): v for s, v in c["porInstituto"].items()},
          "cascada": c["cascada"], "plan_buk": datos["control"]["planCorregido"]}
(RAIZ / "appBotathon" / "web" / "datos" / "referencia_excel.json").write_text(
    json.dumps(salida, ensure_ascii=False, indent=1), encoding="utf-8")
print("ok", salida["plan_total"], salida["plan_buk"])
