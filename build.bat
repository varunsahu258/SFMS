@echo off
pip install -r requirements.txt
python scripts\generate_icon.py
pyinstaller sfms.spec
echo Build complete. EXE is in dist/SFMS/
pause
