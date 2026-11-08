"""Repositorio real contra SQL Server. Necesita pyodbc y un driver ODBC de SQL Server.

Usa las tablas, procedimientos y vistas de botathon.43/sql/v2. Todas las consultas van
con parámetros: ningún texto del usuario se concatena en SQL.
"""
from __future__ import annotations

from collections import defaultdict

from web.motor import MESES, DistribucionCargo, GrupalArea, Parametros
from web.planificacion import AREAS
from web.repositorio import Carga, FilaVigente, ParamApp, Registro, Usuario, VersionPlan

COLUMNAS_CARGA = ("id_carga, tipo_archivo, nombre_archivo, solicitado_por, estado, fecha_solicitud, fecha_inicio, "
                  "fecha_fin, filas_leidas, filas_validas, filas_cuarentena, filas_advertencia, filas_nuevas, "
                  "filas_cambiadas, filas_sin_cambios, filas_eliminadas, ruta_correcciones, mensaje, institutos_procesados")
ULTIMO_CIERRE = "(SELECT MAX(fecha_cierre) FROM ctl.anio_planificacion)"
SQL_VERSIONES = ("SELECT v.sk_version AS id_version, v.anio, v.id_carga_buk AS id_carga, v.id_version_param AS id_parametros, "
                 "v.creado_por, v.fecha_creacion AS fecha, ISNULL(v.comentario, N'') AS comentario "
                 "FROM oro.dim_version v LEFT JOIN ctl.anio_planificacion a ON a.anio = v.anio "
                 "WHERE ISNULL(a.estado, 'ABIERTO') = 'ABIERTO' ")
TRAMOS = ("GE40", "22A40", "LT22")


class RepoSQL:
    def __init__(self, conexion: str) -> None:
        import pyodbc  # solo se necesita en modo sql
        self._pyodbc = pyodbc
        self._conexion = conexion
        self._cache_registros: dict[int, list[Registro]] = {}  # una versión nunca cambia

    def _conectar(self, autocommit: bool = True):
        return self._pyodbc.connect(self._conexion, timeout=30, autocommit=autocommit)

    def _filas(self, sql: str, *parametros) -> list[dict]:
        with self._conectar() as c:
            cursor = c.execute(sql, *parametros)
            nombres = [d[0] for d in cursor.description]
            return [dict(zip(nombres, f)) for f in cursor.fetchall()]

    @staticmethod
    def _usuario(f: dict) -> Usuario:
        return Usuario(f["id_usuario"], f["usuario"], f["nombre_visible"],
                       bytes(f["hash_contrasena"]) if f["hash_contrasena"] else None,
                       f["rol"], f["it"], bool(f["activo"]))

    # ---------- Usuarios y catálogos ----------

    def buscar_usuario(self, usuario: str) -> Usuario | None:
        filas = self._filas("SELECT * FROM ctl.usuario WHERE usuario = ?", usuario.strip())
        return self._usuario(filas[0]) if filas else None

    def obtener_usuario(self, id_usuario: int) -> Usuario | None:
        filas = self._filas("SELECT * FROM ctl.usuario WHERE id_usuario = ?", id_usuario)
        return self._usuario(filas[0]) if filas else None

    def nombre_instituto(self, it: int) -> str:
        return self.institutos().get(it, f"Instituto {it}")

    def institutos(self) -> dict[int, str]:
        return {f["it"]: f["nombre"] for f in self._filas(
            "SELECT it, nombre FROM plata.instituto WHERE activo = 1 AND es_asistencial = 1")}

    def nombres_areas(self) -> dict[str, str]:
        return dict(AREAS)

    # ---------- Cargas ----------

    def crear_carga(self, nombre_archivo: str, tipo_archivo: str, solicitado_por: str) -> int:
        with self._conectar() as c:
            fila = c.execute("EXEC ctl.sp_solicitar_carga ?, ?, ?", nombre_archivo, tipo_archivo, solicitado_por).fetchone()
            return int(fila[0])

    def listar_cargas(self, limite: int = 30) -> list[Carga]:
        filas = self._filas(f"SELECT TOP (?) {COLUMNAS_CARGA} FROM ctl.vw_estado_carga ORDER BY id_carga DESC", limite)
        return [Carga(**f) for f in filas]

    def obtener_carga(self, id_carga: int) -> Carga | None:
        filas = self._filas(f"SELECT {COLUMNAS_CARGA} FROM ctl.vw_estado_carga WHERE id_carga = ?", id_carga)
        return Carga(**filas[0]) if filas else None

    def pasos(self, id_carga: int) -> list[dict]:
        return self._filas("SELECT paso, estado, filas, mensaje, fecha FROM ctl.ejecucion_paso "
                           "WHERE id_carga = ? ORDER BY fecha, id_paso", id_carga)

    def rechazos(self, id_carga: int, limite: int = 200) -> list[dict]:
        return self._filas("SELECT TOP (?) nro_fila AS fila, persona_id AS persona, it_cola AS it, periodo_fila AS periodo, "
                           "codigos_error AS reglas, detalle, estado FROM ctl.vw_correcciones "
                           "WHERE id_carga = ? ORDER BY nro_fila", limite, id_carga)

    def advertencias(self, id_carga: int) -> list[dict]:
        return self._filas("SELECT e.codigo_error AS regla, c.descripcion, COUNT(*) AS filas, "
                           "COUNT(DISTINCT e.persona_id) AS personas FROM ctl.error_registro e "
                           "JOIN ctl.catalogo_error c ON c.codigo_error = e.codigo_error "
                           "WHERE e.id_carga = ? AND c.severidad = 'ADVERTENCIA' "
                           "GROUP BY e.codigo_error, c.descripcion ORDER BY e.codigo_error", id_carga)

    def cambios(self, id_carga: int) -> list[dict]:
        return self._filas("SELECT tipo_cambio AS tipo, COUNT(*) AS cantidad FROM ctl.cambio_detectado "
                           "WHERE id_carga = ? GROUP BY tipo_cambio ORDER BY tipo_cambio", id_carga)

    def cambios_detalle(self, ids_carga: list[int]) -> list[dict]:
        if not ids_carga:
            return []
        marcas = ", ".join("?" * len(ids_carga))
        return self._filas(f"SELECT tipo_cambio AS tipo, persona_id, it, vigente_desde AS desde FROM ctl.cambio_detectado "
                           f"WHERE id_carga IN ({marcas})", *ids_carga)

    def cargas_aplicadas_desde(self, id_carga: int) -> list[int]:
        filas = self._filas("SELECT id_carga FROM ctl.carga WHERE id_carga > ? AND estado IN ('OK', 'PARCIAL') "
                            f"AND ({ULTIMO_CIERRE} IS NULL OR fecha_fin > {ULTIMO_CIERRE}) ORDER BY id_carga", id_carga)
        return [f["id_carga"] for f in filas]

    # ---------- Datos vigentes ----------

    def filas_vigentes(self) -> list[FilaVigente]:
        filas = self._filas("SELECT d.persona_id, d.it, d.periodo, d.horas_semanales, c.cargo_homologado, d.cod_esp, "
                            "d.tipo_contrato, d.fecha_termino, d.id_carga_origen FROM plata.dotacion_vigente d "
                            "JOIN plata.cargo c ON c.id_cargo = d.id_cargo")
        return [FilaVigente(f["persona_id"], f["it"], f["periodo"], float(f["horas_semanales"]), f["cargo_homologado"],
                            f["cod_esp"], f["tipo_contrato"], f["fecha_termino"], f["id_carga_origen"]) for f in filas]

    def incompletos(self) -> set[tuple[int, int]]:
        return {(f["persona_id"], f["it"]) for f in self._filas("SELECT persona_id, it FROM plata.vw_contrato_incompleto")}

    # ---------- Parámetros ----------

    def parametros(self) -> ParamApp:
        v = self._filas("SELECT TOP (1) id_version, nombre FROM ctl.version_parametros WHERE estado = 'VIGENTE' ORDER BY anio DESC")[0]
        i = v["id_version"]
        tramos: dict[str, dict] = defaultdict(dict)
        for f in self._filas("SELECT c.cargo_homologado AS cargo, p.tramo, p.pct_directo, p.pct_indirecto, p.pct_nas "
                             "FROM plata.param_distribucion p JOIN plata.cargo c ON c.id_cargo = p.id_cargo WHERE p.id_version = ?", i):
            tramos[f["cargo"]][f["tramo"]] = (float(f["pct_directo"]), float(f["pct_indirecto"]), float(f["pct_nas"]))
        distribucion = {}
        for f in self._filas("SELECT c.cargo_homologado AS cargo, p.min_directa, p.min_indirecta FROM plata.param_minutos_estandar p "
                             "JOIN plata.cargo c ON c.id_cargo = p.id_cargo WHERE p.id_version = ?", i):
            distribucion[f["cargo"]] = DistribucionCargo(float(f["min_directa"]), float(f["min_indirecta"]),
                                                         tuple(tramos[f["cargo"]][t] for t in TRAMOS))
        grupos: dict[str, dict] = defaultdict(dict)
        for f in self._filas("SELECT cod_esp, grupo_grupal, pct_grupal, pacientes_por_grupo FROM plata.param_grupal WHERE id_version = ?", i):
            grupos[f["cod_esp"]][f["grupo_grupal"]] = (float(f["pct_grupal"]), float(f["pacientes_por_grupo"]))
        grupal = {a: GrupalArea(g["SAN_CON"][0], g["SAN_CON"][1], g["OTROS"][1]) for a, g in grupos.items()}
        calendario = self._filas("SELECT mes, dias_habiles, dias_vacaciones FROM plata.param_calendario_mes WHERE id_version = ? ORDER BY mes", i)
        generales = {f["clave"]: float(f["valor"]) for f in self._filas("SELECT clave, valor FROM plata.param_general WHERE id_version = ?", i)}
        periodo1 = self._filas("SELECT mes_hasta FROM plata.param_periodo_legal WHERE id_version = ? AND periodo = 1", i)[0]["mes_hasta"]
        motor = Parametros(distribucion=distribucion, grupal=grupal, dias_habiles_mes=tuple(f["dias_habiles"] for f in calendario),
                           dias_vacaciones_mes=tuple(f["dias_vacaciones"] for f in calendario), factor_meta=generales.get("META_PCT", 0.9),
                           dias_por_semana=int(generales.get("DIAS_POR_SEMANA", 5)), meses_periodo_1=periodo1)
        asignaciones = [dict(nombre=f["nombre"], horas=float(f["horas_semana"]) if f["horas_semana"] is not None else None,
                             requiere=bool(f["requiere_autorizacion"]), definido_por=f["definido_por"] or "")
                        for f in self._filas("SELECT * FROM plata.param_asignacion WHERE id_version = ? ORDER BY nombre", i)]
        return ParamApp(i, v["nombre"], motor, asignaciones)

    def guardar_parametros(self, nuevo: ParamApp, usuario: str, motivo: str) -> ParamApp:
        c = self._conectar(autocommit=False)
        try:
            k = c.cursor()
            base = k.execute("SELECT id_version, anio FROM ctl.version_parametros WHERE id_version = ?", nuevo.id_version).fetchone()
            cuenta = k.execute("SELECT COUNT(*) FROM ctl.version_parametros").fetchone()[0]
            nombre = f"Versión {cuenta + 1}"
            k.execute("UPDATE ctl.version_parametros SET estado = 'HISTORICA' WHERE id_version = ?", base.id_version)
            nueva = k.execute("INSERT INTO ctl.version_parametros (nombre, anio, id_version_base, creado_por, motivo) "
                              "OUTPUT inserted.id_version VALUES (?, ?, ?, ?, ?)",
                              nombre, base.anio, base.id_version, usuario, motivo).fetchone()[0]
            for tabla, columnas in (("param_general", "clave, valor, descripcion"), ("param_tramo", "tramo, horas_desde, horas_hasta"),
                                    ("param_periodo_legal", "periodo, mes_desde, mes_hasta, jornada_max_horas"),
                                    ("param_feriado", "fecha, descripcion, es_regional")):
                k.execute(f"INSERT INTO plata.{tabla} (id_version, {columnas}) SELECT ?, {columnas} FROM plata.{tabla} WHERE id_version = ?",
                          nueva, base.id_version)
            k.execute("UPDATE plata.param_general SET valor = ? WHERE id_version = ? AND clave = 'META_PCT'", nuevo.motor.factor_meta, nueva)
            ids = {f[0]: f[1] for f in k.execute("SELECT cargo_homologado, id_cargo FROM plata.cargo").fetchall()}
            for cargo, d in nuevo.motor.distribucion.items():
                k.execute("INSERT INTO plata.param_minutos_estandar (id_version, id_cargo, min_directa, min_indirecta) VALUES (?, ?, ?, ?)",
                          nueva, ids[cargo], int(d.min_directa), int(d.min_indirecta))
                for t, (a, b, n) in zip(TRAMOS, d.tramos):
                    k.execute("INSERT INTO plata.param_distribucion (id_version, id_cargo, tramo, pct_directo, pct_indirecto, pct_nas) "
                              "VALUES (?, ?, ?, ?, ?, ?)", nueva, ids[cargo], t, a, b, n)
            for area, g in nuevo.motor.grupal.items():
                for grupo, pacientes in (("SAN_CON", g.pacientes_santiago_concepcion), ("OTROS", g.pacientes_otros)):
                    k.execute("INSERT INTO plata.param_grupal (id_version, cod_esp, grupo_grupal, pct_grupal, pacientes_por_grupo) "
                              "VALUES (?, ?, ?, ?, ?)", nueva, area, grupo, g.porcentaje, pacientes)
            for m in range(MESES):
                k.execute("INSERT INTO plata.param_calendario_mes (id_version, mes, dias_habiles, dias_vacaciones) VALUES (?, ?, ?, ?)",
                          nueva, m + 1, nuevo.motor.dias_habiles_mes[m], nuevo.motor.dias_vacaciones_mes[m])
            for a in nuevo.asignaciones:
                k.execute("INSERT INTO plata.param_asignacion (id_version, nombre, horas_semana, requiere_autorizacion, definido_por) "
                          "VALUES (?, ?, ?, ?, ?)", nueva, a["nombre"], a["horas"], 1 if a["requiere"] else 0, a["definido_por"] or None)
            c.commit()
            return ParamApp(nueva, nombre, nuevo.motor, nuevo.asignaciones)
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    # ---------- Versiones ----------

    @staticmethod
    def _version(f: dict) -> VersionPlan:
        return VersionPlan(**f)

    def version_actual(self) -> VersionPlan | None:
        filas = self._filas(SQL_VERSIONES.replace("SELECT ", "SELECT TOP (1) ", 1) + "ORDER BY v.sk_version DESC")
        return self._version(filas[0]) if filas else None

    def version_anterior(self, version: VersionPlan) -> VersionPlan | None:
        filas = self._filas(SQL_VERSIONES.replace("SELECT ", "SELECT TOP (1) ", 1) + "AND v.sk_version < ? ORDER BY v.sk_version DESC", version.id_version)
        return self._version(filas[0]) if filas else None

    def obtener_version(self, id_version: int) -> VersionPlan | None:
        filas = self._filas(SQL_VERSIONES + "AND v.sk_version = ?", id_version)
        return self._version(filas[0]) if filas else None

    def registros(self, id_version: int) -> list[Registro]:
        if id_version in self._cache_registros:
            return self._cache_registros[id_version]
        filas = self._filas(
            "SELECT t.mes, i.it, p.persona_id, c.cargo_homologado, c.cod_esp, p.tipo_contrato, f.horas_prom, f.tramo, "
            "f.horas_sem_contratadas, f.horas_sem_directa, f.horas_sem_indirecta, f.semanas_habiles, f.plan_ate_directa, "
            "f.plan_ate_dir_ind, f.meta_ate_directa, f.meta_ate_dir_ind, f.es_incompleto "
            "FROM oro.fact_planificacion_mensual f JOIN oro.dim_tiempo t ON t.id_tiempo = f.id_tiempo "
            "JOIN oro.dim_instituto i ON i.sk_instituto = f.sk_instituto JOIN oro.dim_cargo c ON c.sk_cargo = f.sk_cargo "
            "JOIN oro.dim_profesional p ON p.sk_profesional = f.sk_profesional WHERE f.sk_version = ?", id_version)
        salida: dict[tuple[int, int], Registro] = {}
        for f in filas:
            clave = (f["persona_id"], f["it"])
            r = salida.get(clave)
            if r is None:
                r = salida[clave] = Registro(f["persona_id"], f["it"], f["cargo_homologado"], f["cod_esp"], f["tipo_contrato"],
                                             float(f["horas_prom"]), f["tramo"], *[[0.0] * MESES for _ in range(4)],
                                             *[[0] * MESES for _ in range(4)], bool(f["es_incompleto"]))
            m = f["mes"] - 1
            r.horas[m], r.horas_directa[m] = float(f["horas_sem_contratadas"]), float(f["horas_sem_directa"])
            r.horas_indirecta[m], r.semanas[m] = float(f["horas_sem_indirecta"]), float(f["semanas_habiles"])
            r.plan_directa[m], r.plan_total[m] = f["plan_ate_directa"], f["plan_ate_dir_ind"]
            r.meta_directa[m], r.meta_total[m] = f["meta_ate_directa"], f["meta_ate_dir_ind"]
        self._cache_registros[id_version] = list(salida.values())
        return self._cache_registros[id_version]

    def guardar_version(self, id_carga: int, id_parametros: int, anio: int, usuario: str,
                        comentario: str, registros: list[Registro]) -> VersionPlan:
        c = self._conectar(autocommit=False)
        try:
            k = c.cursor()
            k.execute("EXEC oro.sp_sincronizar_dimensiones").fetchall()
            sk_inst = {f[0]: f[1] for f in k.execute("SELECT it, sk_instituto FROM oro.dim_instituto").fetchall()}
            sk_prof = {f[0]: f[1] for f in k.execute("SELECT persona_id, sk_profesional FROM oro.dim_profesional").fetchall()}
            sk_cargo = {(f[0], f[1]): f[2] for f in k.execute("SELECT cargo_homologado, cod_esp, sk_cargo FROM oro.dim_cargo").fetchall()}
            id_version = k.execute("INSERT INTO oro.dim_version (anio, id_carga_buk, id_version_param, creado_por, comentario) "
                                   "OUTPUT inserted.sk_version VALUES (?, ?, ?, ?, ?)",
                                   anio, id_carga, id_parametros, usuario, comentario).fetchone()[0]
            filas = []
            for r in registros:
                for m in range(MESES):
                    if not (r.horas[m] or r.plan_total[m]):
                        continue
                    nas = max(r.horas[m] - r.horas_directa[m] - r.horas_indirecta[m], 0)
                    filas.append((id_version, anio * 100 + m + 1, sk_inst[r.it], sk_prof[r.persona_id], sk_cargo[(r.cargo, r.cod_esp)],
                                  r.promedio, r.tramo, r.horas[m], round(nas, 3), 0, round(r.horas_directa[m], 3), round(r.horas_indirecta[m], 3),
                                  0, 0, r.semanas[m], r.plan_directa[m], r.plan_total[m], r.meta_directa[m], r.meta_total[m],
                                  round(r.horas[m] * r.semanas[m], 3), round(r.horas_directa[m] * r.semanas[m], 3), 1 if r.incompleto else 0))
            k.fast_executemany = True
            k.executemany(
                "INSERT INTO oro.fact_planificacion_mensual (sk_version, id_tiempo, sk_instituto, sk_profesional, sk_cargo, horas_prom, tramo, "
                "horas_sem_contratadas, horas_sem_nas, horas_sem_asignaciones, horas_sem_directa, horas_sem_indirecta, ate_sem_directa_grupal, "
                "ate_sem_indirecta, semanas_habiles, plan_ate_directa, plan_ate_dir_ind, meta_ate_directa, meta_ate_dir_ind, "
                "horas_mes_contratadas, horas_mes_directa, es_incompleto) VALUES (" + ", ".join("?" * 22) + ")", filas)
            c.commit()
            self._cache_registros[id_version] = registros
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()
        return self.obtener_version(id_version)

    # ---------- Publicación, auditoría y cierre ----------

    def publicaciones(self) -> dict[int, dict]:
        return {f["it"]: dict(id_version=f["sk_version"], fecha=f["fecha"], publicado_por=f["publicado_por"]) for f in
                self._filas("SELECT it, sk_version, fecha, publicado_por FROM ctl.publicacion WHERE activa = 1")}

    def publicar(self, id_version: int, it: int, usuario: str) -> None:
        with self._conectar() as c:
            c.execute("EXEC ctl.sp_publicar ?, ?, ?", id_version, it, usuario).fetchall()

    def auditar(self, accion: str, detalle: str, usuario: str, motivo: str | None = None) -> None:
        with self._conectar() as c:
            c.execute("INSERT INTO ctl.auditoria (accion, detalle, usuario, motivo) VALUES (?, ?, ?, ?)", accion, detalle, usuario, motivo)

    def historial(self, limite: int = 200) -> list[dict]:
        return self._filas("SELECT TOP (?) fecha, accion, detalle, usuario, motivo FROM ctl.auditoria ORDER BY id_auditoria DESC", limite)

    def cerrar_anio(self, anio: int, usuario: str, motivo: str) -> None:
        with self._conectar() as c:
            c.execute("EXEC ctl.sp_cerrar_anio ?, ?, ?", anio, usuario, motivo).fetchall()
