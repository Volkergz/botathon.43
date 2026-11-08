"""Bot simulado, solo para el modo demo.

Hace lo que hará Blue Prism, con las mismas reglas de app/datos/buk.py, para poder
probar la app sin la VM. En modo sql no se usa: las cargas las toma el bot real.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import shutil
import sys
import threading
import time
import warnings
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

import openpyxl

from web.config import Config
from web.motor import DistribucionCargo, GrupalArea, Parametros
from web.planificacion import AREAS  # noqa: F401 - la usa crear_repo_demo
from web.repositorio import FilaVigente, ParamApp, RepoMemoria

RAIZ_PROYECTO = Path(__file__).resolve().parents[2]
if str(RAIZ_PROYECTO) not in sys.path:
    sys.path.insert(0, str(RAIZ_PROYECTO))

from app.datos.buk import COLUMNAS, FILA_TITULOS, EstructuraInvalida, comparar, leer_filas, validar  # noqa: E402
from app.datos.excel_inicial import PLAN, leer_catalogos, leer_parametros  # noqa: E402

NOMBRES_REGLAS = {"RC-B07": "Horas no múltiplos de 0,5", "RC-B08": "Ndias distinto de los días de la jornada",
                  "RC-B09": "Plazo fijo sin fecha de término", "RC-B10": "Indefinido con fecha de término",
                  "RC-B12": "Tipo de trabajador distinto al registrado", "RC-B15": "Reemplazo sin titular informado"}


@lru_cache(maxsize=1)
def datos_excel():
    """Leer el Excel de parámetros tarda varios segundos: se hace una sola vez."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        libro = openpyxl.load_workbook(PLAN, data_only=True)
    cat = leer_catalogos(libro)
    origen = leer_parametros(libro)
    hoja = libro["Parámetros"]
    institutos: dict[int, str] = {}
    for r in range(2, hoja.max_row + 1):
        it, nombre = hoja.cell(r, 39).value, hoja.cell(r, 41).value
        if isinstance(it, (int, float)) and nombre and int(it) not in institutos and int(it) != 133:
            institutos[int(it)] = str(nombre)
    dist = libro["Distribucion_NAC"]
    asignaciones = {}
    for r in range(7, 40):
        nombre, horas, desc = dist.cell(r, 18).value, dist.cell(r, 19).value, dist.cell(r, 20).value
        if nombre:
            texto = "Por contrato individual" if desc and "Contrato" in str(desc) else (str(desc) if desc else "")
            asignaciones[nombre.strip()] = dict(nombre=nombre.strip(), horas=float(horas) if horas else None,
                                                requiere="DMN" in texto, definido_por=texto)
    # El motor de la app es una copia: se traduce a sus propias clases.
    motor = Parametros(
        distribucion={k: DistribucionCargo(v.min_directa, v.min_indirecta, v.tramos) for k, v in origen.distribucion.items()},
        grupal={k: GrupalArea(v.porcentaje, v.pacientes_santiago_concepcion, v.pacientes_otros) for k, v in origen.grupal.items()},
        dias_habiles_mes=origen.dias_habiles_mes, dias_vacaciones_mes=origen.dias_vacaciones_mes)
    return cat, institutos, ParamApp(1, "Inicial 2026", motor, sorted(asignaciones.values(), key=lambda a: a["nombre"]))


def catalogos_compartidos():
    return datos_excel()[0]


class SimuladorBot:
    def __init__(self, repo: RepoMemoria, config: Config) -> None:
        self.repo = repo
        self.config = config
        self.catalogos = catalogos_compartidos()
        self.vigentes: dict = {}
        self.huellas: dict[str, int] = {}
        self.bloqueo = threading.Lock()  # el bot atiende una carga a la vez

    def iniciar(self, id_carga: int) -> None:
        threading.Thread(target=self.procesar, args=(id_carga,), daemon=True).start()

    def _paso(self, id_carga: int, paso: str, filas: int | None = None, estado: str = "OK") -> None:
        time.sleep(self.config.espera_bot)
        self.repo.lista_pasos[id_carga].append(
            dict(paso=paso, estado=estado, filas=filas, mensaje=None, fecha=dt.datetime.now()))

    def procesar(self, id_carga: int) -> None:
        with self.bloqueo:
            carga = self.repo.obtener_carga(id_carga)
            ruta = self.config.ruta_entrada / carga.nombre_archivo
            try:
                self._procesar(carga, ruta)
            except Exception as e:  # noqa: BLE001 - el error debe quedar visible en la carga
                carga.estado, carga.mensaje = "ERROR", f"Error del bot simulado: {e}"
                carga.fecha_fin = dt.datetime.now()

    def _procesar(self, carga, ruta: Path) -> None:
        id_carga = carga.id_carga
        carga.estado, carga.fecha_inicio = "EN_PROCESO", dt.datetime.now()
        self._paso(id_carga, "Blue Prism tomó el archivo")

        huella = hashlib.sha256(ruta.read_bytes()).hexdigest()
        if huella in self.huellas:
            carga.estado, carga.fecha_fin = "DUPLICADA", dt.datetime.now()
            carga.mensaje = f"Este archivo ya fue procesado en la carga {self.huellas[huella]}"
            self._paso(id_carga, "Archivo repetido", estado="ERROR")
            return

        try:
            filas = leer_filas(ruta)
        except EstructuraInvalida as e:
            carga.estado, carga.mensaje, carga.fecha_fin = "RECHAZADA", str(e), dt.datetime.now()
            self._paso(id_carga, "Revisión de columnas", estado="ERROR")
            return
        self._paso(id_carga, "Columnas revisadas")
        carga.filas_leidas = len(filas)
        self._paso(id_carga, "Filas guardadas en cobre", len(filas))

        validas, observaciones = validar(filas, self.catalogos)
        bloqueantes = [o for o in observaciones if o.nivel == "BLOQUEANTE"]
        avisos = [o for o in observaciones if o.nivel == "ADVERTENCIA"]
        self._paso(id_carga, "Filas validadas", len(validas))

        completo = carga.tipo_archivo == "COMPLETO"
        r = comparar(validas, self.vigentes, completo, filas)
        self._paso(id_carga, "Comparación con lo vigente", len(r.nuevas) + len(r.cambiadas) + r.sin_cambios)

        for f in r.nuevas + r.cambiadas:
            self.vigentes[f.clave] = f
            destino, area = self.catalogos.homologacion[(f.cargo, f.cc)]
            self.repo.filas[f.clave] = FilaVigente(f.persona_id, f.it, f.periodo, f.horas, destino, area,
                                                   f.tipo_contrato, f.fecha_termino, id_carga)
            self.repo.pendientes.discard(f.clave)
        for k in r.eliminadas:
            del self.vigentes[k]
            del self.repo.filas[k]
        carga.institutos_procesados = len({f.it for f in filas if f.it is not None})
        self._paso(id_carga, "Resultado guardado en plata", len(r.nuevas) + len(r.cambiadas))

        self._guardar_detalle(carga, bloqueantes, avisos, r)
        rechazadas = {o.nro_fila for o in bloqueantes}
        if rechazadas:
            carga.ruta_correcciones = str(self._crear_correcciones(id_carga, ruta, bloqueantes))
            self._paso(id_carga, "Excel de correcciones creado", len(rechazadas))

        carga.filas_validas, carga.filas_cuarentena = len(validas), len(rechazadas)
        carga.filas_advertencia = len(avisos)
        carga.filas_nuevas, carga.filas_cambiadas = len(r.nuevas), len(r.cambiadas)
        carga.filas_sin_cambios, carga.filas_eliminadas = r.sin_cambios, len(r.eliminadas)
        self.huellas[huella] = id_carga
        self.config.ruta_procesado.mkdir(parents=True, exist_ok=True)
        shutil.move(str(ruta), str(self.config.ruta_procesado / ruta.name))
        carga.estado = "PARCIAL" if rechazadas else "OK"
        carga.fecha_fin = dt.datetime.now()
        self._paso(id_carga, "Carga terminada")

    def _guardar_detalle(self, carga, bloqueantes, avisos, r) -> None:
        por_fila: dict[int, list] = defaultdict(list)
        for o in bloqueantes:
            por_fila[o.nro_fila].append(o)
        self.repo.lista_rechazos[carga.id_carga] = [
            dict(fila=n, persona=os[0].persona_id, it=os[0].it, periodo=os[0].periodo,
                 reglas=";".join(sorted({o.codigo for o in os})), detalle=" | ".join(o.detalle for o in os), estado="PENDIENTE")
            for n, os in sorted(por_fila.items())]
        resumen: dict[str, dict] = {}
        for o in avisos:
            d = resumen.setdefault(o.codigo, dict(regla=o.codigo, descripcion=NOMBRES_REGLAS.get(o.codigo, o.codigo),
                                                  filas=0, personas=set()))
            d["filas"] += 1
            d["personas"].add(o.persona_id)
        self.repo.lista_advertencias[carga.id_carga] = [
            {**d, "personas": len(d["personas"])} for _, d in sorted(resumen.items())]
        cuenta: dict[str, int] = defaultdict(int)
        for e in r.eventos:
            cuenta[e["tipo"]] += 1
        self.repo.lista_cambios[carga.id_carga] = [dict(tipo=t, cantidad=n) for t, n in sorted(cuenta.items())]
        self.repo.lista_cambios_detalle[carga.id_carga] = [
            dict(tipo=e["tipo"], persona_id=e["persona_id"], it=e["it"], desde=e["desde"]) for e in r.eventos]
        for o in bloqueantes:
            if None not in (o.persona_id, o.it, o.periodo):
                self.repo.pendientes.add((o.persona_id, o.it, o.periodo))

    def _crear_correcciones(self, id_carga: int, ruta: Path, bloqueantes) -> Path:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            origen = openpyxl.load_workbook(ruta, data_only=True).worksheets[0]
        titulos = [c.value for c in origen[FILA_TITULOS]]
        posiciones = [titulos.index(c) + 1 for c in COLUMNAS]
        libro = openpyxl.Workbook()
        hoja = libro.active
        hoja.title = "Correcciones"
        hoja.append([])  # los títulos van en la fila 2, igual que el BUK
        hoja.append(COLUMNAS + ["Error"])
        por_fila: dict[int, list[str]] = defaultdict(list)
        for o in bloqueantes:
            por_fila[o.nro_fila].append(o.detalle)
        for n, detalles in sorted(por_fila.items()):
            hoja.append([origen.cell(n, p).value for p in posiciones] + [" | ".join(detalles)])
        self.config.ruta_reportes.mkdir(parents=True, exist_ok=True)
        salida = self.config.ruta_reportes / f"correcciones_{id_carga}.xlsx"
        libro.save(salida)
        return salida
