# appBotathon

Aplicación de planificación asistencial: cargas del BUK, cálculo, panel, parámetros, publicación e historial.

## Probarla en tu PC (modo demo)

```bat
cd /d C:\Users\Marco\Desktop\botathon\appBotathon
C:\Users\Marco\AppData\Local\Python\bin\python.exe -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python iniciar.py
```

Espera cerca de un minuto a que arranque y abre <http://127.0.0.1:8000>. Para detenerla, presiona `Ctrl+C`.

| Usuario | Contraseña | Puede |
|---|---|---|
| `planificacion` | `demo1234` | Subir archivos y ver todas las cargas |
| `arica` | `demo1234` | Ver solo su instituto |

Prueba subiendo los archivos de `..\pruebas_bp`, con los resultados de la sección 9 de la guía de Blue Prism.

El modo demo usa un bot simulado con las mismas reglas del bot real, y nada queda guardado al cerrar. Necesita las carpetas `app` y `pruebas_bp`, y el Excel de planificación, junto a esta carpeta.

## Usarla en la VM (modo sql)

1. Instala Python 3.12 de 64 bits desde python.org y marca **Add python.exe to PATH**.
2. Descomprime `appBotathon_VM.zip` en `C:\Botathon`, para que quede `C:\Botathon\appBotathon`.
3. La base debe estar creada con `C:\Botathon\sql\ejecutar.ps1`, con el mismo usuario de Windows.

```powershell
cd C:\Botathon\appBotathon
powershell -ExecutionPolicy Bypass -File .\instalar_vm.ps1
.\.venv\Scripts\python.exe crear_usuario.py planificacion "Planificación central" CENTRAL
.\.venv\Scripts\python.exe crear_usuario.py arica "Jefatura de Arica" JEFATURA 11
powershell -ExecutionPolicy Bypass -File .\iniciar_vm.ps1
```

`instalar_vm.ps1` instala las librerías desde la carpeta `paquetes`, sin internet. `iniciar_vm.ps1` crea la clave de sesión, elige el driver ODBC disponible y abre la app en <http://127.0.0.1:8000>.

**El modo sql aún no se ha probado**, porque requiere SQL Server.

## Qué hace

| Pantalla | Función |
|---|---|
| Panel | Indicadores, gráficos, filtros y detalle por persona |
| Cargas BUK | Subida, avance del bot, rechazos y Excel de correcciones |
| Parámetros | Editar reglas, ver el impacto y aplicar con motivo y fecha |
| Cambios | Qué cambió entre versiones y desde qué mes |
| Publicación | Publicar por instituto |
| Mi instituto | Lo que ve una jefatura: solo lo publicado de su instituto |
| Diferencia | Explicación de la distancia con el Excel |
| Historial | Quién hizo qué, cuándo y por qué |
| Cierre de año | Se abre desde Historial |

Cada carga terminada genera un borrador automático. Los meses anteriores a un cambio no se recalculan.

## Cómo se conecta con Blue Prism

1. La app guarda el archivo en la carpeta de entrada, con un nombre único.
2. Crea la solicitud con `ctl.sp_solicitar_carga`.
3. El bot la toma con `ctl.sp_tomar_carga`, y deja sus avances en `ctl.ejecucion_paso`.
4. La app muestra esos avances, y el resultado desde `ctl.vw_estado_carga`.

## Estructura

| Archivo | Contenido |
|---|---|
| `iniciar.py` | Arranque de la app |
| `crear_usuario.py` | Crea usuarios en SQL Server |
| `web/main.py` | Rutas y permisos |
| `web/seguridad.py` | Contraseñas y bloqueo por intentos |
| `web/motor.py` | Motor de cálculo |
| `web/planificacion.py` | Borrador, congelamiento de meses y resúmenes |
| `web/repositorio.py` | Interfaz de datos y versión en memoria |
| `web/repo_sql.py` | Versión con SQL Server |
| `web/simulador_bot.py` | Bot simulado del modo demo |
| `web/plantillas/`, `web/estaticos/` | Pantallas |
| `web/datos/referencia_excel.json` | Totales para explicar la diferencia con el Excel |
| `tests/` | Pruebas |

## Pruebas

```bat
pip install pytest httpx
python -m pytest tests -q
```
