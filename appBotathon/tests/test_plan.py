"""Pruebas de planificación, panel, parámetros, publicación y cierre, en modo demo."""
import re

import pytest
from fastapi.testclient import TestClient

from tests.test_app import csrf, entrar, subir
from web.config import Config
from web.main import crear_app

PLAN_KIT, META_KIT = 925807, 830833  # mismos totales del motor y del prototipo


@pytest.fixture()
def entorno(tmp_path):
    c = Config(modo="demo", secreto="x" * 40, espera_bot=0, ruta_entrada=tmp_path / "e",
               ruta_reportes=tmp_path / "r", ruta_procesado=tmp_path / "p")
    return crear_app(c)


def totales(app):
    from web.planificacion import resumir
    v = app.state.planificador.sincronizar()
    r = resumir(app.state.repo.registros(v.id_version), None)
    return r["plan_tot"], r["meta_tot"]


def registro(app, persona, it=11):
    v = app.state.repo.version_actual()
    return next(r for r in app.state.repo.registros(v.id_version) if r.persona_id == persona and r.it == it)


def form_parametros(c, **cambios):
    texto = c.get("/parametros").text
    datos = {}
    formulario = texto[texto.index('method="post" action="/parametros"'):]
    for m in re.finditer(r'<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"', formulario):
        datos[m.group(1)] = m.group(2)
    datos.update(cambios)
    return datos


def test_borrador_automatico_igual_al_motor(entorno):
    app = entorno
    with TestClient(app) as c:
        entrar(c)
        assert "Aún no hay planificación" in c.get("/panel").text
        subir(c, app, "BUK_1_base.xlsx")
        assert totales(app) == (PLAN_KIT, META_KIT)
        pagina = c.get("/panel").text
        assert "925.807" in pagina and "830.833" in pagina and "Resumen por instituto" in pagina


def test_correcciones_completan_el_plan(entorno):
    app = entorno
    with TestClient(app) as c:
        entrar(c)
        subir(c, app, "BUK_2_con_errores.xlsx")
        assert totales(app)[0] == 925053
        assert "contratos incompletos" in c.get("/panel").text
        subir(c, app, "BUK_3_correcciones.xlsx", "CORRECCIONES")
        assert totales(app) == (PLAN_KIT, META_KIT)
        assert "contratos incompletos" not in c.get("/panel").text


def test_cambio_rige_desde_su_fecha(entorno):
    app = entorno
    with TestClient(app) as c:
        entrar(c)
        subir(c, app, "BUK_1_base.xlsx")
        c.get("/panel")
        antes = registro(app, 140126)
        baja_antes = registro(app, 76852)
        subir(c, app, "BUK_5_cambios.xlsx")
        c.get("/panel")
        despues = registro(app, 140126)
        assert despues.plan_total[:5] == antes.plan_total[:5]  # enero a mayo no se tocan
        assert despues.plan_total[5] < antes.plan_total[5]  # desde junio baja
        baja = registro(app, 76852)
        assert baja.plan_total[:4] == baja_antes.plan_total[:4] and sum(baja.plan_total[4:]) == 0
        assert "incompleto" not in c.get("/panel").text.split("Instituto")[0]  # una fila repetida no deja datos faltantes
        pagina = c.get("/cambios").text
        assert "140126" in pagina and "76852" in pagina and "999001" in pagina and "Jun" in pagina
        assert "Meta anterior" in c.get("/profesional/140126/11").text


def test_persona_sin_cambios_conserva_meses(entorno):
    app = entorno
    with TestClient(app) as c:
        entrar(c)
        subir(c, app, "BUK_1_base.xlsx")
        c.get("/panel")
        token = csrf(c, "/parametros")
        d = form_parametros(c, meta="80", desde="6", motivo="Prueba de meta", accion="aplicar", csrf=token)
        assert c.post("/parametros", data=d, follow_redirects=False).status_code == 303
        congelado = registro(app, 74031)
        subir(c, app, "BUK_5_cambios.xlsx")
        c.get("/panel")
        assert registro(app, 74031).meta_total == congelado.meta_total  # la 2.ª versión no lo altera


def test_parametros_simular_y_aplicar(entorno):
    app = entorno
    with TestClient(app) as c:
        entrar(c)
        subir(c, app, "BUK_1_base.xlsx")
        c.get("/panel")
        antes = registro(app, 74031)
        token = csrf(c, "/parametros")
        r = c.post("/parametros", data=form_parametros(c, meta="80", desde="6", accion="simular", csrf=token))
        assert r.status_code == 200 and "Contratos afectados" in r.text
        assert totales(app) == (PLAN_KIT, META_KIT)  # simular no cambia nada

        r = c.post("/parametros", data=form_parametros(c, meta="80", accion="aplicar", motivo="", csrf=token))
        assert r.status_code == 400 and "motivo" in r.text

        d = form_parametros(c, meta="80", desde="6", accion="aplicar", motivo="Ajuste de meta", csrf=token)
        assert c.post("/parametros", data=d, follow_redirects=False).status_code == 303
        nuevo = registro(app, 74031)
        assert nuevo.meta_total[:6] == antes.meta_total[:6]  # enero a junio se mantienen
        assert nuevo.meta_total[7] < antes.meta_total[7]
        h = c.get("/historial").text
        assert "Ajuste de meta" in h and "planificacion" in h


def test_parametros_invalidos(entorno):
    app = entorno
    with TestClient(app) as c:
        entrar(c)
        token = csrf(c, "/parametros")
        d = form_parametros(c, t0_d="99", accion="simular", csrf=token)
        r = c.post("/parametros", data=d)
        assert r.status_code == 400 and "sumar 100" in r.text


def test_publicacion_y_jefatura(entorno):
    app = entorno
    with TestClient(app) as c:
        entrar(c)
        subir(c, app, "BUK_1_base.xlsx")
        c.get("/panel")
        token = csrf(c, "/publicacion")
        assert c.post("/publicacion", data=dict(csrf=token, it=["11"]), follow_redirects=False).status_code == 303
        c.post("/salir", data=dict(csrf=csrf(c, "/panel")))

        entrar(c, "arica")
        p = c.get("/mi-instituto").text
        assert "Versión 1" in p and "Profesionales" in p and "Santiago" not in p
        assert c.get("/panel").status_code == 403
        assert c.get("/parametros").status_code == 403
        assert c.get("/profesional/74031/11").status_code == 200
        otro = next(r for r in app.state.repo.registros(1) if r.it == 131)
        assert c.get(f"/profesional/{otro.persona_id}/131").status_code == 403
        # Aunque pida otro instituto por la URL, sigue viendo el suyo.
        assert "Santiago" not in c.get("/mi-instituto?it=131").text


def test_instituto_sin_publicar(entorno):
    app = entorno
    with TestClient(app) as c:
        entrar(c)
        subir(c, app, "BUK_1_base.xlsx")
        c.get("/panel")
        c.post("/salir", data=dict(csrf=csrf(c, "/panel")))
        entrar(c, "arica")
        assert "aún no está publicada" in c.get("/mi-instituto").text


def test_jefatura_no_ve_cambios_sin_publicar(entorno):
    app = entorno
    with TestClient(app) as c:
        entrar(c)
        subir(c, app, "BUK_1_base.xlsx")
        c.get("/panel")
        c.post("/publicacion", data=dict(csrf=csrf(c, "/publicacion"), it=["11"]))
        subir(c, app, "BUK_5_cambios.xlsx")
        c.get("/panel")
        assert "hay cambios" in c.get("/publicacion").text
        assert app.state.repo.publicaciones()[11]["id_version"] == 1


def test_filtros_del_panel(entorno):
    app = entorno
    with TestClient(app) as c:
        entrar(c)
        subir(c, app, "BUK_1_base.xlsx")
        assert "Resumen por instituto" not in c.get("/panel?it=11").text
        assert "/profesional/" in c.get("/panel?it=11").text
        assert "Sin resultados" in c.get("/panel?persona=000000").text
        assert c.get("/panel?mes=4&tipo=dir&area=KI").status_code == 200
        assert c.get("/panel?mes=99&tipo=x").status_code == 200


def test_diferencia(entorno):
    app = entorno
    with TestClient(app) as c:
        entrar(c)
        subir(c, app, "BUK_1_base.xlsx")
        p = c.get("/diferencia").text
        assert "772.356" in p and "925.807" in p and "Sin asignaciones especiales" in p


def test_cierre_de_anio(entorno):
    app = entorno
    with TestClient(app) as c:
        entrar(c)
        subir(c, app, "BUK_1_base.xlsx")
        c.get("/panel")
        c.post("/publicacion", data=dict(csrf=csrf(c, "/publicacion"), it=["11"]))
        token = csrf(c, "/cierre-anio")
        r = c.post("/cierre-anio", data=dict(csrf=token, confirmacion="si", motivo="Fin del año"))
        assert r.status_code == 400
        r = c.post("/cierre-anio", data=dict(csrf=token, confirmacion="CERRAR 2026", motivo="Fin del año"), follow_redirects=False)
        assert r.status_code == 303
        assert "Aún no hay planificación" in c.get("/panel").text
        assert app.state.repo.publicaciones() == {}
        assert "Cierre de año" in c.get("/historial").text
