@echo off
title Claude-Mem Worker
echo Starting Claude-Mem worker service...
echo.
echo Viewer UI: http://localhost:37777
echo Press Ctrl+C to stop
echo.
"%USERPROFILE%\.bun\bin\bun.exe" "%USERPROFILE%\.claude\plugins\marketplaces\thedotmack\scripts\worker-service.cjs" run
