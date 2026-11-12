@echo off
rem Comparte una copia demo de NearLeads con enlaces publicos (Cloudflare) mientras esta ventana siga abierta.
title NearLeads - demo compartida
cd /d "%~dp0"
python compartir_demo.py
echo.
echo La demo se detuvo. Cierra esta ventana.
pause >nul
