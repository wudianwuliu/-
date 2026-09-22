@echo off
rem Launcher for the jobfill GUI. ASCII-only: cmd.exe reads .cmd as GBK.
rem Double-click this file.
setlocal
set "HERE=%~dp0"
set "PYTHONPATH=%HERE%libs;%HERE%"
start "" pyw -3 "%HERE%jobfill_gui.py"
