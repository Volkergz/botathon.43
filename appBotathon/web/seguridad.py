"""Contraseñas, bloqueo por intentos y protección de formularios."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time

ITERACIONES = 390_000
LARGO_SAL = 16
MAX_INTENTOS = 5
SEGUNDOS_BLOQUEO = 60


def hash_contrasena(contrasena: str) -> bytes:
    sal = secrets.token_bytes(LARGO_SAL)
    return sal + hashlib.pbkdf2_hmac("sha256", contrasena.encode("utf-8"), sal, ITERACIONES)


def verificar_contrasena(contrasena: str, guardado: bytes | None) -> bool:
    # Sin usuario también se calcula, para no revelar por el tiempo si existe.
    referencia = guardado or (b"\0" * LARGO_SAL + b"\0" * 32)
    sal, esperado = referencia[:LARGO_SAL], referencia[LARGO_SAL:]
    calculado = hashlib.pbkdf2_hmac("sha256", contrasena.encode("utf-8"), sal, ITERACIONES)
    return hmac.compare_digest(calculado, esperado) and guardado is not None


class ControlIntentos:
    """Frena intentos repetidos de inicio de sesión. Vive en memoria."""

    def __init__(self) -> None:
        self._fallos: dict[str, tuple[int, float]] = {}

    def bloqueado(self, usuario: str) -> bool:
        fallos, hasta = self._fallos.get(usuario.lower(), (0, 0.0))
        return fallos >= MAX_INTENTOS and time.monotonic() < hasta

    def fallo(self, usuario: str) -> None:
        clave = usuario.lower()
        fallos, hasta = self._fallos.get(clave, (0, 0.0))
        if fallos >= MAX_INTENTOS and time.monotonic() >= hasta:
            fallos = 0
        self._fallos[clave] = (fallos + 1, time.monotonic() + SEGUNDOS_BLOQUEO)

    def exito(self, usuario: str) -> None:
        self._fallos.pop(usuario.lower(), None)
