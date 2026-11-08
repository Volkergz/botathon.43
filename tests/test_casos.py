"""Casos de la sección 7 del documento de análisis, con valores reales del Excel."""
import pytest

from app.datos.excel_inicial import cargar
from app.motor.calculo import calcular_fila

CASOS = [
    # fila_excel, descripción, plan directo, plan total, meta total
    (5, "simple con asignación", 1638, 2139, 1921),
    (7, "baja de 44 a 42 horas", 1648, 1961, 1760),
    (19, "grupal 55 % y 3 pacientes", 1930, 2370, 2128),
    (20, "aumento de 20 a 33 horas", 1214, 1570, 1408),
    (37, "multi-instituto Iquique", 1533, 1973, 1771),
    (38, "multi-instituto Coyhaique", 251, 309, 272),
    (26, "licencia extensa", 716, 849, 760),
    (445, "reemplazo desde mayo", 1344, 1855, 1665),
]


@pytest.fixture(scope="module")
def datos():
    p, entradas, esperados = cargar()
    return p, {x.fila_excel: e for e, x in zip(entradas, esperados)}


@pytest.mark.parametrize("fila,desc,directo,total,meta", CASOS, ids=[c[1] for c in CASOS])
def test_anual(datos, fila, desc, directo, total, meta):
    p, por_fila = datos
    r = calcular_fila(por_fila[fila], p)
    assert (r.anual_directa, r.anual_total, r.anual_meta_total) == (directo, total, meta)


def test_cambiar_factor_meta_es_parametro(datos):
    from dataclasses import replace
    p, por_fila = datos
    base = calcular_fila(por_fila[5], p).anual_meta_total
    assert calcular_fila(por_fila[5], replace(p, factor_meta=0.8)).anual_meta_total < base
