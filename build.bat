@echo off
pip install -r requirements.txt
pyinstaller sfms.spec
echo Build complete. EXE is in dist/SFMS/
pause
