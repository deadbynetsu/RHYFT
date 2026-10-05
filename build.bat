@echo off
REM Gera dist\RHYFT.exe (um arquivo só, sem janela de console)
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconsole --onefile --name RHYFT --collect-all customtkinter --collect-all ytmusicapi app.py
echo.
echo Pronto: dist\RHYFT.exe
pause
