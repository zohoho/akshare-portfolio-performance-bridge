@echo off
setlocal
set "BRIDGE_DATA=%LOCALAPPDATA%\AkSharePPBridge"
set "AKSHARE_PP_DATA_DIR=%BRIDGE_DATA%"
set "PYTHONPATH="
for /f "usebackq delims=" %%P in (`"%~dp0runtime\python.exe" -c "import json,pathlib; p=pathlib.Path(r'%BRIDGE_DATA%\state\runtime.json'); d=json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}; print(d.get('active',{}).get('path') or '')"`) do set "PYTHONPATH=%%P"
"%~dp0runtime\pythonw.exe" "%~dp0app.py" --service
endlocal
