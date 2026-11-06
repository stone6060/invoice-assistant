@echo off
chcp 65001 >nul 2>nul
cd /d "%~dp0"
set "PY="
if exist "%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" set "PY=%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
if not defined PY if exist "%~dp0runtime\python.exe" set "PY=%~dp0runtime\python.exe"
if not defined PY (
    py -3 --version >nul 2>nul
    if not errorlevel 1 set "PY=py -3"
)
if not defined PY (
    python --version >nul 2>nul
    if not errorlevel 1 set "PY=python"
)
if not defined PY goto NOPY
echo.
echo   Python: %PY%
"%PY%" -c "import openpyxl" >nul 2>nul
if errorlevel 1 (
    echo   Installing openpyxl ...
    "%PY%" -m pip install openpyxl -q
)
echo   Starting invoice assistant ...
echo.
"%PY%" "%~dp0src\serve_api.py"
goto END

:NOPY
echo.
echo   [ERROR] Python not found. Please install Python 3.9+.
echo.
pause
exit /b 1

:END
if errorlevel 1 pause
