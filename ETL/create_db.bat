@echo off
REM ======================================================================
REM  Botathon 2026 - Crea la base Botathon en LocalDB y la capa Bronce
REM  Uso:   crear_bronce.bat          (crea la base si no existe)
REM         crear_bronce.bat /reset   (borra la base y la crea de nuevo)
REM  Debe estar en la misma carpeta que 01_bronce.sql y 01b_bronce_buk_v2.sql
REM  Ejecutar con el mismo usuario de Windows que usara Blue Prism.
REM ======================================================================
setlocal
set "INSTANCIA=MSSQLLocalDB"
set "SERVIDOR=(localdb)\%INSTANCIA%"
set "BASE=Botathon"
set "CARPETA=%~dp0"

echo.
echo === 1. Revisando herramientas ===
where sqllocaldb >nul 2>&1 || (echo ERROR: no se encontro sqllocaldb. Instala SQL Server Express LocalDB. & goto :error)
where sqlcmd >nul 2>&1 || (echo ERROR: no se encontro sqlcmd. Instala las herramientas de linea de comandos de SQL Server. & goto :error)
if not exist "%CARPETA%01_bronce.sql" (echo ERROR: falta 01_bronce.sql en %CARPETA% & goto :error)
if not exist "%CARPETA%01b_bronce_buk_v2.sql" (echo ERROR: falta 01b_bronce_buk_v2.sql en %CARPETA% & goto :error)

echo.
echo === 2. Iniciando la instancia %INSTANCIA% ===
sqllocaldb start %INSTANCIA%
if errorlevel 1 (
    echo La instancia no existe, se crea...
    sqllocaldb create %INSTANCIA% -s || goto :error
)

if /I "%~1"=="/reset" (
    echo.
    echo === Borrando la base %BASE% ^(/reset^) ===
    sqlcmd -S "%SERVIDOR%" -b -Q "IF DB_ID('%BASE%') IS NOT NULL BEGIN ALTER DATABASE [%BASE%] SET SINGLE_USER WITH ROLLBACK IMMEDIATE; DROP DATABASE [%BASE%]; END" || goto :error
)

echo.
echo === 3. Creando la base %BASE% si no existe ===
sqlcmd -S "%SERVIDOR%" -b -Q "IF DB_ID('%BASE%') IS NULL CREATE DATABASE [%BASE%]" || goto :error

echo.
echo === 4. Revisando si Bronce ya existe ===
set "EXISTE="
for /f %%i in ('sqlcmd -S "%SERVIDOR%" -d %BASE% -h -1 -W -Q "SET NOCOUNT ON; SELECT CASE WHEN OBJECT_ID('bronze.carga','U') IS NULL THEN 0 ELSE 1 END"') do set "EXISTE=%%i"
if "%EXISTE%"=="1" (
    echo La capa Bronce ya existe. Se omite 01_bronce.sql.
    echo Para recrear todo desde cero usa: crear_bronce.bat /reset
) else (
    echo.
    echo === 5. Ejecutando 01_bronce.sql ===
    sqlcmd -S "%SERVIDOR%" -d %BASE% -b -f 65001 -i "%CARPETA%01_bronce.sql" || goto :error
)

echo.
echo === 6. Ejecutando 01b_bronce_buk_v2.sql ===
sqlcmd -S "%SERVIDOR%" -d %BASE% -b -f 65001 -i "%CARPETA%01b_bronce_buk_v2.sql" || goto :error

echo.
echo === 7. Verificando ===
sqlcmd -S "%SERVIDOR%" -d %BASE% -b -W -Q "SET NOCOUNT ON; SELECT name AS tabla_bronce FROM sys.tables WHERE SCHEMA_NAME(schema_id)='bronze' ORDER BY name; SELECT COUNT(*) AS columnas_esperadas_BUK FROM bronze.columna_esperada WHERE fuente='BUK';" || goto :error

echo.
echo LISTO. Base %BASE% en %SERVIDOR% con la capa Bronce.
echo Debe listar 6 tablas y 19 columnas esperadas.
echo Cadena de conexion para Blue Prism:
echo   Server=%SERVIDOR%;Database=%BASE%;Integrated Security=true;
endlocal
exit /b 0

:error
echo.
echo FALLO. Revisa el mensaje de arriba.
endlocal
exit /b 1
