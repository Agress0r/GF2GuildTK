@echo off
cd /d "%~dp0.."
echo Running tests...
GF2TTK\Scripts\python.exe -m pytest tests\ -v %*
