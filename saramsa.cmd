@echo off
rem Shim so `.\saramsa <args>` works without typing `.ps1`. Forwards all args
rem verbatim to saramsa.ps1 in the same directory. -NoProfile keeps startup fast.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0saramsa.ps1" %*
exit /b %errorlevel%
