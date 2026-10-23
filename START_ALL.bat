@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "ARMORED_ROOT=%~dp0."
set "PYTHON=%ARMORED_ROOT%\.venv\Scripts\python.exe"
set "PYTHONPATH=%ARMORED_ROOT%;%PYTHONPATH%"

if not exist "%PYTHON%" (
  echo ERRO: ambiente virtual Python 3.11.9 nao encontrado em .venv.
  exit /b 2
)

for /f "tokens=2" %%V in ('"%PYTHON%" --version') do set "PYTHON_VERSION=%%V"
if not "%PYTHON_VERSION%"=="3.11.9" (
  echo ERRO: START_ALL exige Python 3.11.9; encontrado %PYTHON_VERSION%.
  exit /b 2
)

if not exist "%ARMORED_ROOT%\credentials\project.env" (
  echo ERRO: credentials\project.env nao encontrado.
  exit /b 2
)

echo [1/4] Verificando Python, Speech, RVC e IA...
"%PYTHON%" "%ARMORED_ROOT%\run_audio_preflight.py"
if errorlevel 1 (
  echo ERRO: preflight de runtime falhou. O Coordinator nao sera iniciado.
  exit /b 3
)

set "PREFLIGHT_EVIDENCE=%TEMP%\armoredcreator_preflight_%RANDOM%.json"
echo [2/4] Compondo os sete modulos e verificando uma copia consistente do SQLite...
"%PYTHON%" "%ARMORED_ROOT%\run_coordinator.py" --root "%ARMORED_ROOT%" --preflight --evidence "%PREFLIGHT_EVIDENCE%"
if errorlevel 1 (
  if exist "%PREFLIGHT_EVIDENCE%" type "%PREFLIGHT_EVIDENCE%"
  del "%PREFLIGHT_EVIDENCE%" >nul 2>&1
  echo ERRO: preflight integrado falhou. O Coordinator nao sera iniciado.
  exit /b 3
)
del "%PREFLIGHT_EVIDENCE%" >nul 2>&1

if "%~1"=="" (
  echo [3/4] Validando configuracao operacional sem iniciar o Coordinator...
  "%PYTHON%" "%ARMORED_ROOT%\run_coordinator.py" --root "%ARMORED_ROOT%" --validate-config
  exit /b %ERRORLEVEL%
)

set "RUN_REQUESTED=0"
for %%A in (%*) do (
  if /I "%%~A"=="--run" set "RUN_REQUESTED=1"
  if /I "%%~A"=="--continuous" set "RUN_REQUESTED=1"
)

if "%RUN_REQUESTED%"=="0" (
  echo [3/4] Validando configuracao sem iniciar o Bot API ou o Coordinator...
  "%PYTHON%" "%ARMORED_ROOT%\run_coordinator.py" --root "%ARMORED_ROOT%" %*
  exit /b %ERRORLEVEL%
)

echo [3/4] Validando as tres fontes e suas rotas...
"%PYTHON%" "%ARMORED_ROOT%\run_coordinator.py" --root "%ARMORED_ROOT%" --validate-config
if errorlevel 1 (
  echo ERRO: configuracao operacional invalida. O Coordinator nao sera iniciado.
  exit /b 4
)

echo [4/4] Verificando Telegram Bot API local na porta 8081...
set "BOT_API_OWNED=0"
set "BOT_API_STATUS_FILE=%TEMP%\armoredcreator_botapi_%RANDOM%.txt"
"%PYTHON%" "%ARMORED_ROOT%\scripts\ensure_bot_api.py" --ensure --root "%ARMORED_ROOT%" > "%BOT_API_STATUS_FILE%"
if errorlevel 1 (
  if exist "%BOT_API_STATUS_FILE%" type "%BOT_API_STATUS_FILE%"
  del "%BOT_API_STATUS_FILE%" >nul 2>&1
  echo ERRO: Bot API local indisponivel ou sem autenticacao. START interrompido.
  exit /b 5
)
for /f "tokens=1,2 delims==" %%A in (%BOT_API_STATUS_FILE%) do if /I "%%A"=="BOT_API_OWNED" set "BOT_API_OWNED=%%B"
del "%BOT_API_STATUS_FILE%" >nul 2>&1
echo Bot API local autenticado e pronto. Credenciais ocultas.

"%PYTHON%" "%ARMORED_ROOT%\run_coordinator.py" --root "%ARMORED_ROOT%" %*
set "EXIT_CODE=%ERRORLEVEL%"

if "%BOT_API_OWNED%"=="1" (
  echo Encerrando somente o Bot API iniciado por este START_ALL...
  "%PYTHON%" "%ARMORED_ROOT%\scripts\ensure_bot_api.py" --stop-owned --root "%ARMORED_ROOT%"
  if errorlevel 1 if "%EXIT_CODE%"=="0" set "EXIT_CODE=6"
)

exit /b %EXIT_CODE%
