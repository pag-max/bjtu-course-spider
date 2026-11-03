@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 goto run_py
where python >nul 2>nul
if %errorlevel%==0 goto run_python
echo [ERROR] Python 3 not found. Install it and check "Add Python to PATH".
pause
exit /b 1

:run_py
py -3 "%~dp0run_spider.py"
goto done

:run_python
python "%~dp0run_spider.py"
goto done

:done
if errorlevel 1 pause
