"""El motor debe funcionar solo con BUK y parámetros."""
from app.datos.buk import a_entrada, comparar, contratos_desde, leer_filas, validar
from app.datos.excel_inicial import cargar_parametros, leer_catalogos
from app.motor.calculo import calcular_fila


def test_lee_buk_y_calcula():
    p = cargar_parametros()
    cat = leer_catalogos()
    filas = leer_filas()
    validas, observaciones = validar(filas, cat)
    assert len(filas) == 3123
    assert len(validas) == 3123  # el BUK del kit no tiene filas para rechazar
    assert p.semanas_habiles_mes()[1] == 1.0  # febrero con 15 días de vacaciones
    assert sum(o.codigo == "RC-B08" for o in observaciones) == 2
    contratos = contratos_desde(validas, cat)
    assert len(contratos) == 465
    assert sum(calcular_fila(a_entrada(c, p), p).anual_total for c in contratos.values()) > 0


def test_fila_rechazada_no_es_baja():
    cat = leer_catalogos()
    filas = leer_filas()
    validas, _ = validar(filas, cat)
    vigentes = {f.clave: f for f in validas}
    r = comparar(validas[1:], vigentes, completo=True, todas=filas)
    assert r.eliminadas == [] and r.sin_cambios == len(validas) - 1


def test_correcciones_no_genera_bajas():
    cat = leer_catalogos()
    validas, _ = validar(leer_filas(), cat)
    r = comparar(validas[:10], {f.clave: f for f in validas}, completo=False)
    assert r.eliminadas == []
