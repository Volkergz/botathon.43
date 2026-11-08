"""Pruebas de la app en modo demo, con los archivos de pruebas_bp."""
import io
import re
import shutil
import sys
from pathlib import Path

import openpyxl
import pytest
from fastapi.testclient import TestClient

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from web.config import Config  # noqa: E402
from web.main import crear_app, nombre_seguro  # noqa: E402

PRUEBAS = RAIZ.parent / "pruebas_bp"


@pytest.fixture()
def entorno(tmp_path):
    c = Config(modo="demo", secreto="x" * 40, espera_bot=0, ruta_entrada=tmp_path / "e",
               ruta_reportes=tmp_path / "r", ruta_procesado=tmp_path / "p")
    return crear_app(c), c


def csrf(cliente, ruta="/login"):
    return re.search(r'name="csrf" value="([^"]+)"', cliente.get(ruta).text).group(1)


def entrar(cliente, usuario="planificacion", clave="demo1234"):
    return cliente.post("/login", data=dict(usuario=usuario, contrasena=clave, csrf=csrf(cliente)), follow_redirects=False)


def subir(cliente, app, archivo, tipo="COMPLETO"):
    token = csrf(cliente, "/cargas")
    datos = (PRUEBAS / archivo).read_bytes()
    r = cliente.post("/cargas", data=dict(tipo=tipo, csrf=token), files=dict(archivo=(archivo, io.BytesIO(datos))), follow_redirects=False)
    assert r.status_code == 303, r.text[:300]
    id_carga = int(r.headers["location"].rsplit("/", 1)[1])
    # Espera a que termine el hilo del bot simulado.
    bot = app.state.bot
    import time
    for _ in range(2000):
        c = app.state.repo.obtener_carga(id_carga)
        if c.terminada:
            break
        time.sleep(0.05)
    with bot.bloqueo:
        return app.state.repo.obtener_carga(id_carga)


def test_redirige_sin_sesion(entorno):
    app, _ = entorno
    with TestClient(app) as c:
        assert c.get("/cargas", follow_redirects=False).headers["location"] == "/login"
        assert c.get("/api/cargas/1").status_code == 401


def test_contrasena_incorrecta(entorno):
    app, _ = entorno
    with TestClient(app) as c:
        r = entrar(c, clave="mala")
        assert r.status_code == 401 and "incorrectos" in r.text


def test_bloqueo_por_intentos(entorno):
    app, _ = entorno
    with TestClient(app) as c:
        for _ in range(5):
            entrar(c, clave="mala")
        assert entrar(c).status_code == 429


def test_login_rechaza_sin_csrf(entorno):
    app, _ = entorno
    with TestClient(app) as c:
        c.get("/login")
        assert c.post("/login", data=dict(usuario="planificacion", contrasena="demo1234", csrf="x")).status_code == 403


def test_jefatura_no_entra_a_cargas(entorno):
    app, _ = entorno
    with TestClient(app) as c:
        entrar(c, "arica")
        assert c.get("/", follow_redirects=False).headers["location"] == "/mi-instituto"
        assert "Arica" in c.get("/mi-instituto").text
        assert c.get("/cargas").status_code == 403
        assert c.get("/api/cargas/1").status_code == 403
        assert c.post("/cargas", data=dict(tipo="COMPLETO", csrf=csrf(c, "/mi-instituto"))).status_code == 403


def test_subida_invalida(entorno):
    app, conf = entorno
    with TestClient(app) as c:
        entrar(c)
        token = csrf(c, "/cargas")
        r = c.post("/cargas", data=dict(tipo="COMPLETO", csrf=token), files=dict(archivo=("a.txt", b"hola")))
        assert r.status_code == 400 and ".xlsx" in r.text
        r = c.post("/cargas", data=dict(tipo="COMPLETO", csrf=token), files=dict(archivo=("a.xlsx", b"no es excel")))
        assert r.status_code == 400 and "válido" in r.text
        r = c.post("/cargas", data=dict(tipo="OTRO", csrf=token), files=dict(archivo=("a.xlsx", b"PK")))
        assert r.status_code == 400
        assert list(conf.ruta_entrada.iterdir()) == []


def test_escenario_a(entorno):
    app, _ = entorno
    with TestClient(app) as c:
        entrar(c)
        k = subir(c, app, "BUK_2_con_errores.xlsx")
        assert (k.estado, k.filas_leidas, k.filas_validas, k.filas_cuarentena, k.filas_nuevas) == ("PARCIAL", 3123, 3119, 4, 3119)
        assert k.filas_advertencia == 184
        pagina = c.get(f"/cargas/{k.id_carga}").text
        assert "Sin dato" in pagina and "RC-B06" in pagina and "Altas" in pagina

        r = c.get(f"/cargas/{k.id_carga}/correcciones")
        assert r.status_code == 200
        hoja = openpyxl.load_workbook(io.BytesIO(r.content)).active
        assert hoja.cell(2, 20).value == "Error" and hoja.max_row == 6

        k2 = subir(c, app, "BUK_3_correcciones.xlsx", "CORRECCIONES")
        assert (k2.estado, k2.filas_nuevas, k2.filas_cuarentena, k2.filas_eliminadas) == ("OK", 4, 0, 0)
        assert len(app.state.bot.vigentes) == 3123


def test_escenario_b(entorno):
    app, _ = entorno
    with TestClient(app) as c:
        entrar(c)
        assert subir(c, app, "BUK_1_base.xlsx").estado == "OK"
        k = subir(c, app, "BUK_5_cambios.xlsx")
        assert (k.estado, k.filas_nuevas, k.filas_cambiadas, k.filas_sin_cambios, k.filas_eliminadas) == ("PARCIAL", 1, 3, 3117, 3)
        tipos = {x["tipo"]: x["cantidad"] for x in app.state.repo.cambios(k.id_carga)}
        assert tipos == {"ALTA": 1, "BAJA": 1, "CAMBIO_CARGO": 1, "CAMBIO_JORNADA": 2}
        assert len(app.state.bot.vigentes) == 3121


def test_escenario_c(entorno):
    app, _ = entorno
    with TestClient(app) as c:
        entrar(c)
        k = subir(c, app, "BUK_4_sin_columna.xlsx")
        assert k.estado == "RECHAZADA" and "Horas Semanales" in k.mensaje
        assert subir(c, app, "BUK_1_base.xlsx").estado == "OK"
        assert subir(c, app, "BUK_1_base.xlsx").estado == "DUPLICADA"


def test_nombre_seguro_quita_rutas():
    n = nombre_seguro("..\\..\\windows\\evil<>.xlsx")
    assert "/" not in n and "\\" not in n and ".." not in n and "<" not in n
    assert n.endswith("evil__.xlsx")


def test_correcciones_fuera_de_carpeta(entorno, tmp_path):
    app, _ = entorno
    with TestClient(app) as c:
        entrar(c)
        id_carga = app.state.repo.crear_carga("x.xlsx", "COMPLETO", "t")
        secreto = tmp_path / "secreto.xlsx"
        secreto.write_bytes(b"PK")
        app.state.repo.obtener_carga(id_carga).ruta_correcciones = str(secreto)
        assert c.get(f"/cargas/{id_carga}/correcciones").status_code == 404
