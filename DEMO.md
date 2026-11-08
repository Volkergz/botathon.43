# Demo de «Cada hora cuenta»

Aplicación web de planificación asistencial, con un bot simulado que aplica las mismas reglas que Blue Prism.

## Qué incluye este repositorio

| Carpeta | Contenido |
|---|---|
| `appBotathon/` | La aplicación: cargas, panel, parámetros, publicación e historial |
| `app/` | Motor de cálculo y reglas de validación de referencia |
| `herramientas/` | Scripts que generan los archivos de prueba |
| `tests/` | Pruebas del motor |

**No incluye los Excel del kit** ni datos por persona. El repositorio es público y esos archivos son de la organización.

## Cómo ejecutarla

1. Copia en la raíz del repositorio los dos Excel del kit, con estos nombres exactos:
   - `Nac - Base contratos x mes.xlsx`
   - `Nac - Planificación Asistencial x mes.xlsx`

2. Instala y genera los archivos de prueba:

```powershell
cd appBotathon
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
cd ..
.\appBotathon\.venv\Scripts\python.exe -m herramientas.generar_archivos_prueba
```

3. Inicia la app:

```powershell
cd appBotathon
.\.venv\Scripts\python.exe iniciar.py
```

4. Abre <http://127.0.0.1:8000> y entra con `planificacion` / `demo1234`, o con `arica` / `demo1234` para ver la vista de jefatura.

## Recorrido sugerido

1. En **Cargas BUK**, sube `pruebas_bp/BUK_2_con_errores.xlsx` como reporte completo: 4 filas rechazadas.
2. Sube `pruebas_bp/BUK_3_correcciones.xlsx` como correcciones.
3. Revisa el **Panel**, cambia la meta en **Parámetros** y publica en **Publicación**.
4. Entra como `arica` para ver solo ese instituto.

En modo demo nada queda guardado al cerrar la app.
