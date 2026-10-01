@echo off
REM Gera dist\MigradorPlaylists.exe (um arquivo só, sem janela de console)
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconsole --onefile --name MigradorPlaylists --collect-all customtkinter --collect-all ytmusicapi app.py
echo.
echo Pronto: dist\MigradorPlaylists.exe
pause
