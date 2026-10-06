@echo off
REM Gera dist\RHYFT.exe (um arquivo so, sem janela de console) com branding oficial.
python -m pip install -r requirements.txt pyinstaller "cairosvg>=2.7,<3"
python ferramentas\gerar_branding_windows.py
if errorlevel 1 exit /b %errorlevel%
python ferramentas\gerar_version_info_windows.py
if errorlevel 1 exit /b %errorlevel%
python -m PyInstaller --clean --noconfirm --noconsole --onefile --name RHYFT --icon "assets\generated\rhyft_icon.ico" --version-file "build_meta\version_info.txt" --add-data "assets\generated;assets\generated" --collect-all customtkinter --collect-all ytmusicapi --collect-all certifi app_branded.py
echo.
echo Pronto: dist\RHYFT.exe
pause
