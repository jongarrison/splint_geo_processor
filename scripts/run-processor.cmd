@echo off
REM Wrapper: relaunches the Node.js processor forever.
REM Task Scheduler runs this wrapper at logon. If node ever exits (crash, network
REM error, anything), this loop restarts it after a short delay. This is more
REM reliable than relying on Task Scheduler's RestartCount/RestartInterval.

setlocal
set "REPO_DIR=%~dp0.."
set "ENTRY=%REPO_DIR%\dist\index.js"
set "LOG_DIR=%USERPROFILE%\SplintFactoryFiles\logs"
set "WRAPPER_LOG=%LOG_DIR%\wrapper.log"

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

:loop
echo [%date% %time%] Starting node %ENTRY% >> "%WRAPPER_LOG%"
"C:\Program Files\nodejs\node.exe" "%ENTRY%"
set "EXITCODE=%ERRORLEVEL%"
echo [%date% %time%] node exited with code %EXITCODE%, restarting in 10s >> "%WRAPPER_LOG%"
timeout /t 10 /nobreak >nul
goto loop
