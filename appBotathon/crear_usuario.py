"""Crea un usuario en SQL Server, para el modo sql.

Uso:
  python crear_usuario.py planificacion "Planificación central" CENTRAL
  python crear_usuario.py arica "Jefatura de Arica" JEFATURA 11

La contraseña se pide por teclado: no queda en el historial de la consola.
"""
import getpass
import os
import sys

import pyodbc

from web.config import CONEXION_VM
from web.seguridad import hash_contrasena


def main() -> None:
    if len(sys.argv) < 4 or sys.argv[3] not in ("CENTRAL", "JEFATURA"):
        raise SystemExit(__doc__)
    usuario, nombre, rol = sys.argv[1:4]
    it = int(sys.argv[4]) if len(sys.argv) > 4 else None
    if rol == "JEFATURA" and it is None:
        raise SystemExit("Una jefatura necesita el código de su instituto.")
    clave = getpass.getpass("Contraseña: ")
    if len(clave) < 8:
        raise SystemExit("La contraseña debe tener al menos 8 caracteres.")
    with pyodbc.connect(os.environ.get("BOTATHON_CONEXION", CONEXION_VM), autocommit=True) as c:
        c.execute("INSERT INTO ctl.usuario (usuario, nombre_visible, hash_contrasena, rol, it) VALUES (?, ?, ?, ?, ?)",
                  usuario, nombre, hash_contrasena(clave), rol, it)
    print(f"Usuario {usuario} creado.")


if __name__ == "__main__":
    main()
