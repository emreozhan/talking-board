@echo off
rem Konsolsuz baslatir; hatalar ema-tts.log dosyasina yazilir.
cd /d "%~dp0"
start "" /b ".venv\Scripts\pythonw.exe" -c "import sys; sys.stdout = sys.stderr = open('ema-tts.log', 'w', encoding='utf-8', buffering=1); from ema_tts.app import main; main()"
