@echo off
rem jobfill launcher: puts this dir and libs/ on PYTHONPATH, then runs the CLI.
rem Usage:
rem   jobfill check
rem   jobfill fill --learn
rem NOTE: keep this file ASCII-only. cmd.exe reads .cmd as GBK and
rem       non-ASCII comments can break parsing.
setlocal
set "HERE=%~dp0"
set "PYTHONPATH=%HERE%libs;%HERE%"
py -3 -m jobfill %*
