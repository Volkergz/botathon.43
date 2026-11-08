"""Configuración de la app. Todo se puede cambiar con variables de entorno."""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
CONEXION_VM = ("Driver={ODBC Driver 18 for SQL Server};Server=(localdb)\\MSSQLLocalDB;"
               "Database=TeletonPlanificacion;Trusted_Connection=yes;TrustServerCertificate=yes")


@dataclass
class Config:
    modo: str = "demo"  # "demo" o "sql"
    conexion: str = CONEXION_VM
    ruta_entrada: Path = RAIZ / "datos_demo" / "entrada"
    ruta_reportes: Path = RAIZ / "datos_demo" / "reportes"
    ruta_procesado: Path = RAIZ / "datos_demo" / "procesado"
    secreto: str = ""
    max_mb: int = 20
    espera_bot: float = 0.7  # solo demo: pausa entre pasos del bot simulado
    minutos_aviso_bot: int = 2  # después de esto se avisa que el bot no ha tomado la carga

    @property
    def es_demo(self) -> bool:
        return self.modo == "demo"


def cargar_config() -> Config:
    modo = os.environ.get("BOTATHON_MODO", "demo").lower()
    if modo not in ("demo", "sql"):
        raise SystemExit("BOTATHON_MODO debe ser demo o sql")
    secreto = os.environ.get("BOTATHON_SECRETO", "")
    if modo == "sql" and len(secreto) < 32:
        raise SystemExit("En modo sql, BOTATHON_SECRETO debe tener al menos 32 caracteres")
    c = Config(modo=modo, secreto=secreto or secrets.token_hex(32))
    c.conexion = os.environ.get("BOTATHON_CONEXION", c.conexion)
    if modo == "sql":
        c.ruta_entrada = Path(os.environ.get("BOTATHON_ENTRADA", r"C:\Botathon\entrada"))
        c.ruta_reportes = Path(os.environ.get("BOTATHON_REPORTES", r"C:\Botathon\reportes"))
        c.ruta_procesado = Path(os.environ.get("BOTATHON_PROCESADO", r"C:\Botathon\procesado"))
    return c
