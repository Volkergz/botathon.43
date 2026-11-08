"""Inicia la app. Uso: python iniciar.py"""
import uvicorn

from web.main import crear_app

if __name__ == "__main__":
    # Solo escucha en el propio equipo. No se expone a la red.
    uvicorn.run(crear_app(), host="127.0.0.1", port=8000)
