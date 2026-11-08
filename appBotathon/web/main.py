"""Rutas de la app: sesión, cargas, panel, parámetros, publicación e historial."""
from __future__ import annotations

import datetime as dt
import json
import re
import secrets
import uuid
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as ErrorHttp
from starlette.middleware.sessions import SessionMiddleware

from web import planificacion as pl
from web.config import Config, cargar_config
from web.motor import DistribucionCargo, GrupalArea
from web.repositorio import ParamApp, RepoMemoria, Repositorio, Usuario
from web.seguridad import ControlIntentos, hash_contrasena, verificar_contrasena

AQUI = Path(__file__).resolve().parent
TIPOS = {"COMPLETO", "CORRECCIONES"}
MESES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
ROTULOS_ESTADO = {
    "PENDIENTE": ("Esperando al bot", "g"), "EN_PROCESO": ("En proceso", "w"), "OK": ("Terminada", "ok"),
    "PARCIAL": ("Terminada con filas rechazadas", "w"), "RECHAZADA": ("Archivo rechazado", "b"),
    "ERROR": ("Con error", "b"), "DUPLICADA": ("Archivo repetido", "b"),
}
ROTULOS_CAMBIO = {"ALTA": "Altas", "BAJA": "Bajas", "CAMBIO_JORNADA": "Cambios de jornada", "CAMBIO_CARGO": "Cambios de cargo",
                  "CAMBIO_CONTRATO": "Cambios de contrato", "CAMBIO_VIGENCIA": "Cambios de vigencia", "CAMBIO_DIAS": "Cambios de días"}
ROTULOS_ACCION = {"CARGA": "Carga de archivo", "BORRADOR": "Cálculo de borrador", "PARAMETROS": "Cambio de parámetros",
                  "PUBLICACION": "Publicación", "CIERRE_ANIO": "Cierre de año"}


def miles(v) -> str:
    if v is None:
        return "—"
    return f"{round(v):,}".replace(",", ".")


def signo(v) -> str:
    return ("+" if v > 0 else "−" if v < 0 else "") + miles(abs(v))


def pct(v) -> str:
    return f"{v * 100:.1f}".replace(".", ",") + " %"


def dec(v, n: int = 1) -> str:
    return f"{v:.{n}f}".replace(".", ",")


def nombre_visible(archivo: str) -> str:
    """Quita el prefijo que agrega la app al guardar el archivo."""
    return re.sub(r"^\d{8}_\d{6}_[0-9a-f]{8}_", "", archivo)


def nombre_seguro(original: str) -> str:
    base = Path(original.replace("\\", "/")).name
    limpio = re.sub(r"[^\w.\- ]", "_", base, flags=re.UNICODE).strip() or "archivo.xlsx"
    return f"{dt.datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}_{limpio[-120:]}"


def fecha(v) -> str:
    return v.strftime("%d-%m-%Y %H:%M") if v else "—"


def crear_repo_demo() -> RepoMemoria:
    from web.simulador_bot import AREAS, datos_excel
    _, institutos, param = datos_excel()
    clave = hash_contrasena("demo1234")
    return RepoMemoria(
        [Usuario(1, "planificacion", "Planificación central", clave, "CENTRAL"),
         Usuario(2, "arica", "Jefatura de Arica", clave, "JEFATURA", 11)],
        institutos, deepcopy(param), AREAS)


def entero(texto: str, minimo: int, maximo: int) -> int:
    v = int(texto)
    if not minimo <= v <= maximo:
        raise ValueError
    return v


def crear_app(config: Config | None = None, repo: Repositorio | None = None) -> FastAPI:
    config = config or cargar_config()
    if repo is None:
        if config.es_demo:
            repo = crear_repo_demo()
        else:
            from web.repo_sql import RepoSQL
            repo = RepoSQL(config.conexion)

    bot = None
    if config.es_demo:
        from web.simulador_bot import SimuladorBot
        bot = SimuladorBot(repo, config)

    app = FastAPI(title="Cada hora cuenta", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(SessionMiddleware, secret_key=config.secreto, same_site="lax",
                       session_cookie="botathon_sesion", max_age=8 * 3600)
    app.mount("/estaticos", StaticFiles(directory=AQUI / "estaticos"), name="estaticos")
    plantillas = Jinja2Templates(directory=AQUI / "plantillas")
    plantillas.env.filters.update(miles=miles, fecha=fecha, nombre_visible=nombre_visible, pct=pct, signo=signo, dec=dec)
    intentos = ControlIntentos()
    planificador = pl.Planificador(repo)
    referencia = json.loads((AQUI / "datos" / "referencia_excel.json").read_text(encoding="utf-8"))
    config.ruta_entrada.mkdir(parents=True, exist_ok=True)
    app.state.bot, app.state.repo, app.state.planificador = bot, repo, planificador

    def usuario_sesion(request: Request) -> Usuario | None:
        uid = request.session.get("uid")
        u = repo.obtener_usuario(uid) if uid else None
        return u if u and u.activo else None

    def exigir_usuario(request: Request) -> Usuario:
        u = usuario_sesion(request)
        if u is None:
            raise HTTPException(status_code=401)
        return u

    def exigir_central(request: Request) -> Usuario:
        u = exigir_usuario(request)
        if u.rol != "CENTRAL":
            raise HTTPException(status_code=403)
        return u

    def token_csrf(request: Request) -> str:
        return request.session.setdefault("csrf", secrets.token_urlsafe(32))

    def revisar_csrf(request: Request, token: str) -> None:
        if not token or not secrets.compare_digest(token, request.session.get("csrf", "")):
            raise HTTPException(status_code=403)

    def mostrar(request: Request, plantilla: str, usuario: Usuario | None = None, codigo: int = 200, **datos):
        datos.update(usuario=usuario, csrf=token_csrf(request), demo=config.es_demo, rotulos=ROTULOS_ESTADO,
                     rotulos_cambio=ROTULOS_CAMBIO, rotulos_accion=ROTULOS_ACCION, meses=MESES)
        return plantillas.TemplateResponse(request, plantilla, datos, status_code=codigo)

    def leer_filtros(request: Request, it_forzado: int | None = None) -> pl.Filtros:
        q = request.query_params

        def num(nombre, minimo, maximo):
            try:
                return entero(q.get(nombre, ""), minimo, maximo)
            except ValueError:
                return None

        tipo = q.get("tipo", "tot")
        return pl.Filtros(it=it_forzado if it_forzado is not None else num("it", 0, 32000), area=q.get("area", ""),
                          cargo=q.get("cargo", ""), mes=num("mes", 0, 11), tipo=tipo if tipo in ("tot", "dir", "ind") else "tot",
                          persona=q.get("persona", "")[:12])

    def datos_tablero(regs, f: pl.Filtros, nombres: dict[int, str]) -> dict:
        elegidos = pl.filtrar(regs, f)
        filas_inst = pl.por_instituto(elegidos, f.mes, nombres)
        barras = sorted(({"nombre": x["nombre"], "v": pl.valor_tipo(x, f.tipo)} for x in filas_inst), key=lambda b: -b["v"])
        mensual = pl.por_mes(elegidos)
        return dict(
            filtros=f, resumen=pl.resumir(elegidos, f.mes), filas_inst=filas_inst, barras=barras, tope_barras=max([b["v"] for b in barras] or [1]) or 1,
            mensual=mensual, tope_mes=max([m["dir"] + m["ind"] for m in mensual] or [1]) or 1, nombres=nombres,
            areas=repo.nombres_areas(), cargos=sorted({r.cargo for r in regs}), personas=sorted(elegidos, key=lambda r: (nombres.get(r.it, ""), r.persona_id))[:200],
            total_personas=len(elegidos), ver_personas=bool(f.it is not None or f.area or f.cargo or f.persona))

    @app.exception_handler(ErrorHttp)
    async def manejar_error(request: Request, exc: ErrorHttp):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": exc.status_code}, status_code=exc.status_code)
        if exc.status_code == 401:
            return RedirectResponse("/login", status_code=303)
        titulos = {403: "No tienes permiso para ver esta página", 404: "No encontramos esta página"}
        return mostrar(request, "error.html", usuario_sesion(request), exc.status_code,
                       titulo=titulos.get(exc.status_code, "Ocurrió un problema"))

    # ---------- Sesión ----------

    @app.get("/")
    def inicio(request: Request):
        u = usuario_sesion(request)
        if u is None:
            return RedirectResponse("/login", status_code=303)
        return RedirectResponse("/panel" if u.rol == "CENTRAL" else "/mi-instituto", status_code=303)

    @app.get("/login")
    def ver_login(request: Request):
        if usuario_sesion(request):
            return RedirectResponse("/", status_code=303)
        return mostrar(request, "login.html")

    @app.post("/login")
    def iniciar_sesion(request: Request, usuario: str = Form(""), contrasena: str = Form(""), csrf: str = Form("")):
        revisar_csrf(request, csrf)
        if intentos.bloqueado(usuario):
            return mostrar(request, "login.html", codigo=429, error="Demasiados intentos. Espera un minuto.")
        u = repo.buscar_usuario(usuario)
        if not verificar_contrasena(contrasena, u.hash_contrasena if u else None) or not u.activo:
            intentos.fallo(usuario)
            return mostrar(request, "login.html", codigo=401, error="Usuario o contraseña incorrectos.")
        intentos.exito(usuario)
        request.session.clear()
        request.session["uid"] = u.id_usuario
        return RedirectResponse("/", status_code=303)

    @app.post("/salir")
    def salir(request: Request, csrf: str = Form("")):
        revisar_csrf(request, csrf)
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    # ---------- Panel ----------

    @app.get("/panel")
    def panel(request: Request):
        u = exigir_central(request)
        version = planificador.sincronizar(u.usuario)
        if version is None:
            return mostrar(request, "panel.html", u, version=None)
        incompletos = sorted(repo.incompletos())
        return mostrar(request, "panel.html", u, version=version, incompletos=incompletos, ruta_base="/panel",
                       **datos_tablero(repo.registros(version.id_version), leer_filtros(request), repo.institutos()))

    @app.get("/mi-instituto")
    def mi_instituto(request: Request):
        u = exigir_usuario(request)
        if u.rol != "JEFATURA":
            return RedirectResponse("/", status_code=303)
        nombre = repo.nombre_instituto(u.it)
        pub = repo.publicaciones().get(u.it)
        if pub is None:
            return mostrar(request, "mi_instituto.html", u, instituto=nombre, publicacion=None)
        regs = [r for r in repo.registros(pub["id_version"]) if r.it == u.it]
        return mostrar(request, "mi_instituto.html", u, instituto=nombre, publicacion=pub, ruta_base="/mi-instituto",
                       **datos_tablero(regs, leer_filtros(request, u.it), repo.institutos()))

    @app.get("/profesional/{persona}/{it}")
    def profesional(request: Request, persona: int, it: int):
        u = exigir_usuario(request)
        if u.rol == "JEFATURA":
            if it != u.it:
                raise HTTPException(status_code=403)
            pub = repo.publicaciones().get(u.it)
            version = repo.obtener_version(pub["id_version"]) if pub else None
        else:
            version = planificador.sincronizar(u.usuario)
        if version is None:
            raise HTTPException(status_code=404)
        r = next((x for x in repo.registros(version.id_version) if x.persona_id == persona and x.it == it), None)
        if r is None:
            raise HTTPException(status_code=404)
        previa_v = repo.version_anterior(version) if u.rol == "CENTRAL" else None
        previo = next((x for x in repo.registros(previa_v.id_version) if x.persona_id == persona and x.it == it), None) if previa_v else None
        return mostrar(request, "profesional.html", u, r=r, instituto=repo.nombre_instituto(it), version=version, previo=previo,
                       area=repo.nombres_areas().get(r.cod_esp, r.cod_esp), tramo=pl.NOMBRES_TRAMO[r.tramo])

    @app.get("/cambios")
    def cambios(request: Request):
        u = exigir_central(request)
        version = planificador.sincronizar(u.usuario)
        previa = repo.version_anterior(version) if version else None
        filas = pl.comparar_versiones(repo.registros(version.id_version), repo.registros(previa.id_version)) if previa else []
        return mostrar(request, "cambios.html", u, version=version, previa=previa, filas=filas[:200], total=len(filas),
                       nombres=repo.institutos(), suma_antes=sum(f["antes"] for f in filas), suma_despues=sum(f["despues"] for f in filas))

    # ---------- Parámetros ----------

    def formulario_parametros(request: Request, u: Usuario, param: ParamApp, cargo: str, impacto=None, error: str = "",
                              motivo: str = "", desde: int = 0, codigo: int = 200):
        version = repo.version_actual()
        if cargo not in param.motor.distribucion:
            cargo = sorted({r.cargo for r in repo.registros(version.id_version)} or param.motor.distribucion)[0] if version else sorted(param.motor.distribucion)[0]
        return mostrar(request, "parametros.html", u, codigo, param=param, cargo=cargo, d=param.motor.distribucion[cargo],
                       cargos=sorted(param.motor.distribucion), en_uso={r.cargo for r in repo.registros(version.id_version)} if version else set(),
                       impacto=impacto, error=error, motivo=motivo, desde=desde, version=version, tramos=["40 horas o más", "22 a menos de 40 horas", "Menos de 22 horas"],
                       areas=repo.nombres_areas())

    @app.get("/parametros")
    def ver_parametros(request: Request, cargo: str = ""):
        u = exigir_central(request)
        planificador.sincronizar(u.usuario)
        return formulario_parametros(request, u, repo.parametros(), cargo)

    def leer_formulario(form, base: ParamApp, cargo: str) -> ParamApp:
        p = base.motor
        meta = entero(form.get("meta", ""), 1, 100) / 100
        dias, vac = [], []
        for m in range(12):
            dias.append(entero(form.get(f"dias_{m}", ""), 0, 31))
            vac.append(entero(form.get(f"vac_{m}", ""), 0, 31))
        tramos = []
        for i in range(3):
            fila = tuple(entero(form.get(f"t{i}_{c}", ""), 0, 100) / 100 for c in "din")
            if round(sum(fila) * 100) != 100:
                raise ValueError("Los porcentajes de cada tramo deben sumar 100.")
            tramos.append(fila)
        distribucion = dict(p.distribucion)
        distribucion[cargo] = DistribucionCargo(entero(form.get("min_dir", ""), 5, 480), entero(form.get("min_ind", ""), 5, 480), tuple(tramos))
        grupal = dict(p.grupal)
        for area in p.grupal:
            grupal[area] = GrupalArea(entero(form.get(f"gp_{area}", ""), 0, 100) / 100,
                                      float(form.get(f"ga_{area}", "").replace(",", ".")), float(form.get(f"go_{area}", "").replace(",", ".")))
            if min(grupal[area].pacientes_santiago_concepcion, grupal[area].pacientes_otros) <= 0:
                raise ValueError("Los pacientes por grupo deben ser mayores que cero.")
        asignaciones = []
        for i, a in enumerate(base.asignaciones):
            texto = form.get(f"as_{i}", "").strip().replace(",", ".")
            horas = float(texto) if texto else None
            if horas is not None and not 0 <= horas <= 44:
                raise ValueError("Las horas de una asignación deben estar entre 0 y 44.")
            asignaciones.append({**a, "horas": horas})
        motor = replace(p, distribucion=distribucion, grupal=grupal, factor_meta=meta,
                        dias_habiles_mes=tuple(dias), dias_vacaciones_mes=tuple(vac))
        return ParamApp(base.id_version, base.nombre, motor, asignaciones)

    @app.post("/parametros")
    async def guardar_parametros(request: Request):
        u = exigir_central(request)
        form = await request.form()
        revisar_csrf(request, form.get("csrf", ""))
        base = repo.parametros()
        cargo = form.get("cargo", "")
        motivo = form.get("motivo", "").strip()
        if cargo not in base.motor.distribucion:
            raise HTTPException(status_code=400)
        try:
            desde = entero(form.get("desde", "0"), 0, 11)
            nuevo = leer_formulario(form, base, cargo)
        except ValueError as e:
            texto = str(e) if str(e) and not str(e).startswith("invalid") else "Revisa los valores: hay un campo vacío o fuera de rango."
            return formulario_parametros(request, u, base, cargo, error=texto, motivo=motivo, codigo=400)
        version = repo.version_actual()
        impacto = None
        if version:
            antes = pl.resumir(repo.registros(version.id_version), None)
            regs = planificador.simular(nuevo.motor, desde)
            despues = pl.resumir(regs, None)
            afectados = sum(1 for x in pl.comparar_versiones(regs, repo.registros(version.id_version)))
            impacto = dict(antes=antes, despues=despues, afectados=afectados)
        if form.get("accion") != "aplicar":
            return formulario_parametros(request, u, nuevo, cargo, impacto=impacto, motivo=motivo, desde=desde)
        if len(motivo) < 5:
            return formulario_parametros(request, u, nuevo, cargo, impacto=impacto, motivo=motivo, desde=desde, codigo=400,
                                         error="Escribe el motivo del cambio.")
        planificador.aplicar_parametros(nuevo, desde, u.usuario, motivo)
        return RedirectResponse("/cambios", status_code=303)

    # ---------- Publicación ----------

    @app.get("/publicacion")
    def ver_publicacion(request: Request):
        u = exigir_central(request)
        version = planificador.sincronizar(u.usuario)
        filas = []
        if version:
            pubs = repo.publicaciones()
            nombres = repo.institutos()
            for x in pl.por_instituto(repo.registros(version.id_version), None, nombres):
                filas.append({**x, "publicacion": pubs.get(x["it"])})
        return mostrar(request, "publicacion.html", u, version=version, filas=filas, incompletos=repo.incompletos())

    @app.post("/publicacion")
    async def publicar(request: Request):
        u = exigir_central(request)
        form = await request.form()
        revisar_csrf(request, form.get("csrf", ""))
        version = repo.version_actual()
        if version is None:
            raise HTTPException(status_code=400)
        elegidos = [int(x) for x in form.getlist("it") if x.isdigit()]
        validos = {r.it for r in repo.registros(version.id_version)}
        for it in elegidos:
            if it in validos:
                repo.publicar(version.id_version, it, u.usuario)
        if elegidos:
            repo.auditar("PUBLICACION", f"Versión {version.id_version} publicada en {len(elegidos)} institutos", u.usuario)
        return RedirectResponse("/publicacion", status_code=303)

    # ---------- Historial, diferencia y cierre ----------

    @app.get("/historial")
    def historial(request: Request):
        return mostrar(request, "historial.html", exigir_central(request), filas=repo.historial())

    @app.get("/diferencia")
    def diferencia(request: Request):
        u = exigir_central(request)
        version = planificador.sincronizar(u.usuario)
        filas = []
        if version:
            for x in pl.por_instituto(repo.registros(version.id_version), None, repo.institutos()):
                excel = referencia["por_instituto"].get(str(x["it"]))
                filas.append(dict(nombre=x["nombre"], excel=excel, buk=x["plan_tot"],
                                  dif=(x["plan_tot"] - excel) / excel if excel else None))
        total = sum(f["buk"] for f in filas)
        return mostrar(request, "diferencia.html", u, version=version, ref=referencia, filas=filas, total=total,
                       base_dif=referencia["plan_buk"] - referencia["plan_total"])

    def contexto_cierre(request, u, error="", codigo=200):
        version = repo.version_actual()
        return mostrar(request, "cierre.html", u, codigo, version=version, error=error,
                       anio=version.anio if version else dt.date.today().year, atrasado=bool(version and version.anio < dt.date.today().year))

    @app.get("/cierre-anio")
    def ver_cierre(request: Request):
        return contexto_cierre(request, exigir_central(request))

    @app.post("/cierre-anio")
    def cerrar(request: Request, confirmacion: str = Form(""), motivo: str = Form(""), csrf: str = Form("")):
        u = exigir_central(request)
        revisar_csrf(request, csrf)
        version = repo.version_actual()
        if version is None:
            return RedirectResponse("/panel", status_code=303)
        if confirmacion.strip() != f"CERRAR {version.anio}" or len(motivo.strip()) < 5:
            return contexto_cierre(request, u, f"Escribe CERRAR {version.anio} y el motivo.", 400)
        repo.cerrar_anio(version.anio, u.usuario, motivo.strip())
        return RedirectResponse("/panel", status_code=303)

    # ---------- Cargas ----------

    @app.get("/cargas")
    def ver_cargas(request: Request, error: str = ""):
        u = exigir_central(request)
        return mostrar(request, "cargas.html", u, cargas=repo.listar_cargas(), error=error)

    @app.post("/cargas")
    async def subir(request: Request, archivo: UploadFile = File(None), tipo: str = Form(""), csrf: str = Form("")):
        u = exigir_central(request)
        revisar_csrf(request, csrf)

        def fallo(mensaje: str):
            return mostrar(request, "cargas.html", u, 400, cargas=repo.listar_cargas(), error=mensaje)

        if tipo not in TIPOS:
            return fallo("Elige si es un reporte completo o un archivo de correcciones.")
        if archivo is None or not archivo.filename:
            return fallo("Selecciona un archivo.")
        if not archivo.filename.lower().endswith(".xlsx"):
            return fallo("El archivo debe ser un Excel .xlsx.")

        nombre = nombre_seguro(archivo.filename)
        temporal = config.ruta_entrada / (nombre + ".parte")
        limite = config.max_mb * 1024 * 1024
        total = 0
        try:
            with open(temporal, "wb") as salida:
                while bloque := await archivo.read(1024 * 1024):
                    if total == 0 and not bloque.startswith(b"PK"):
                        raise ValueError("El archivo no es un Excel válido.")
                    total += len(bloque)
                    if total > limite:
                        raise ValueError(f"El archivo supera los {config.max_mb} MB.")
                    salida.write(bloque)
            if total == 0:
                raise ValueError("El archivo está vacío.")
            # Se publica completo: el bot nunca debe ver un archivo a medio copiar.
            destino = config.ruta_entrada / nombre
            temporal.replace(destino)
        except ValueError as e:
            temporal.unlink(missing_ok=True)
            return fallo(str(e))

        try:
            id_carga = repo.crear_carga(nombre, tipo, u.usuario)
        except Exception:
            destino.unlink(missing_ok=True)
            raise
        repo.auditar("CARGA", f"Carga {id_carga}: {nombre_visible(nombre)} ({tipo})", u.usuario)
        if bot:
            bot.iniciar(id_carga)
        return RedirectResponse(f"/cargas/{id_carga}", status_code=303)

    @app.get("/cargas/{id_carga}")
    def ver_carga(request: Request, id_carga: int):
        u = exigir_central(request)
        carga = repo.obtener_carga(id_carga)
        if carga is None:
            raise HTTPException(status_code=404)
        if carga.terminada:
            planificador.sincronizar(u.usuario)
        espera = dt.datetime.now() - carga.fecha_solicitud
        return mostrar(request, "carga.html", u, carga=carga, pasos=repo.pasos(id_carga),
                       rechazos=repo.rechazos(id_carga), advertencias=repo.advertencias(id_carga),
                       cambios=repo.cambios(id_carga), plan_actualizado=carga.estado in ("OK", "PARCIAL"),
                       bot_demorado=carga.estado == "PENDIENTE" and espera.total_seconds() > config.minutos_aviso_bot * 60)

    @app.get("/api/cargas/{id_carga}")
    def estado_carga(request: Request, id_carga: int):
        exigir_central(request)
        carga = repo.obtener_carga(id_carga)
        if carga is None:
            raise HTTPException(status_code=404)
        return {"estado": carga.estado, "terminada": carga.terminada, "rotulo": ROTULOS_ESTADO[carga.estado][0],
                "pasos": [dict(paso=p["paso"], estado=p["estado"], filas=miles(p["filas"]) if p["filas"] is not None else None)
                          for p in repo.pasos(id_carga)]}

    @app.get("/cargas/{id_carga}/correcciones")
    def bajar_correcciones(request: Request, id_carga: int):
        exigir_central(request)
        carga = repo.obtener_carga(id_carga)
        if carga is None or not carga.ruta_correcciones:
            raise HTTPException(status_code=404)
        ruta = Path(carga.ruta_correcciones).resolve()
        if not ruta.is_relative_to(config.ruta_reportes.resolve()) or not ruta.is_file():
            raise HTTPException(status_code=404)
        return FileResponse(ruta, filename=f"correcciones_carga_{id_carga}.xlsx",
                            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    return app
