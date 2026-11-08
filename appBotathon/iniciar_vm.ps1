# Inicia la app en la VM conectada a SQL Server.
# Uso: powershell -ExecutionPolicy Bypass -File .\iniciar_vm.ps1
# Luego abre http://127.0.0.1:8000 en Edge.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

# Clave de sesión: se crea una vez y queda en este equipo.
$archivo = Join-Path $PSScriptRoot 'secreto.txt'
if (-not (Test-Path $archivo)) {
    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    -join ($bytes | ForEach-Object { $_.ToString('x2') }) | Set-Content $archivo -Encoding ascii
}
$env:BOTATHON_SECRETO = (Get-Content $archivo -Raw).Trim()
$env:BOTATHON_MODO = 'sql'

# Usa el driver ODBC que exista en la VM.
$drivers = Get-OdbcDriver | Where-Object Name -like 'ODBC Driver * for SQL Server' | Select-Object -ExpandProperty Name -Unique | Sort-Object -Descending
if (-not $drivers) { throw 'No hay un driver "ODBC Driver 17/18 for SQL Server" instalado.' }
$driver = @($drivers)[0]
$env:BOTATHON_CONEXION = "Driver={$driver};Server=(localdb)\MSSQLLocalDB;Database=TeletonPlanificacion;Trusted_Connection=yes;TrustServerCertificate=yes"
Write-Host "Usando $driver"
Write-Host "Abre http://127.0.0.1:8000 en Edge. Para detener: Ctrl+C" -ForegroundColor Green
.\.venv\Scripts\python.exe iniciar.py
