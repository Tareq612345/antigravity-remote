@echo off
cd /d "C:\Projects\antigravity-remote"
".venv\Scripts\python.exe" -m uvicorn server:app --host 127.0.0.1 --port 8765 --no-access-log
