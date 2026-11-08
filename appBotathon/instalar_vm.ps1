# Instala la app en la VM sin descargar paquetes de internet.
# Requisito: Python 3.12 de 64 bits instalado.
# Uso: powershell -ExecutionPolicy Bypass -File .\instalar_vm.ps1
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$python = Get-Command py -ErrorAction SilentlyContinue
if ($python) { $exe = 'py'; $args0 = @('-3.12') } else { $exe = 'python'; $args0 = @() }
try { $version = & $exe @args0 --version } catch { throw "No encuentro Python 3.12. Instálalo y marca 'Add python.exe to PATH'." }
Write-Host "Usando $version"
if ($version -notmatch '3\.12') { Write-Host "Ojo: las librerías incluidas son para Python 3.12." -ForegroundColor Yellow }

& $exe @args0 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --no-index --find-links .\paquetes -r .\requirements_vm.txt
Write-Host ""
Write-Host "Drivers ODBC de SQL Server en esta VM:"
Get-OdbcDriver | Where-Object Name -like '*SQL Server*' | Select-Object -ExpandProperty Name -Unique
Write-Host ""
Write-Host "Listo. Siguiente paso: crear usuarios con crear_usuario.py y luego iniciar_vm.ps1" -ForegroundColor Green
