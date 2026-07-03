@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    py -m venv .venv
)

".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
".venv\Scripts\python.exe" -m pip install -r requirements-build.txt

if exist "build" rmdir /s /q "build"
if exist "dist\EstoqueJIMontadora" rmdir /s /q "dist\EstoqueJIMontadora"
if exist "dist\EstoqueJIMontadora.zip" del /q "dist\EstoqueJIMontadora.zip"

".venv\Scripts\python.exe" -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --windowed ^
  --onedir ^
  --name EstoqueJIMontadora ^
  --collect-submodules webview ^
  --add-data "templates;templates" ^
  --add-data "static;static" ^
  --add-data "templates_zpl;templates_zpl" ^
  --add-data "template_importacao_skus.xlsx;." ^
  --add-data "template_etiquetas_lote.xlsx;." ^
  --add-data "template_baixa_consumo.xlsx;." ^
  --add-data "template_empenhos.xlsx;." ^
  --add-data "dados_exemplo.xlsx;." ^
  --add-data "env_online_exemplo.txt;." ^
  desktop_launcher.py

if errorlevel 1 (
    echo Falha ao gerar o executavel.
    exit /b 1
)

copy /Y README.md "dist\EstoqueJIMontadora\README.md" > nul
copy /Y "env_online_exemplo.txt" "dist\EstoqueJIMontadora\env_online_exemplo.txt" > nul
if not exist "dist\EstoqueJIMontadora\data" mkdir "dist\EstoqueJIMontadora\data"
if not exist "dist\EstoqueJIMontadora\exports" mkdir "dist\EstoqueJIMontadora\exports"
if not exist "dist\EstoqueJIMontadora\backups" mkdir "dist\EstoqueJIMontadora\backups"
if not exist "dist\EstoqueJIMontadora\logs" mkdir "dist\EstoqueJIMontadora\logs"
if not exist "dist\EstoqueJIMontadora\templates_zpl" mkdir "dist\EstoqueJIMontadora\templates_zpl"
copy /Y "templates_zpl\etiqueta_base.zpl" "dist\EstoqueJIMontadora\templates_zpl\etiqueta_base.zpl" > nul

powershell -NoProfile -ExecutionPolicy Bypass -Command "Compress-Archive -Path 'dist\EstoqueJIMontadora' -DestinationPath 'dist\EstoqueJIMontadora.zip' -Force"

echo.
echo Pacote gerado:
echo %CD%\dist\EstoqueJIMontadora.zip
echo.
endlocal
