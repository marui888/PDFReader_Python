param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PyInstaller = Join-Path $ProjectRoot ".venv\Scripts\pyinstaller.exe"
$IconPath = Join-Path $ProjectRoot "assets\app_icon.ico"
$MetaDataPath = Join-Path $ProjectRoot "metaData"
$MainPath = Join-Path $ProjectRoot "main.py"

Set-Location $ProjectRoot

if (-not (Test-Path -LiteralPath $PyInstaller)) {
    throw "PyInstaller not found: $PyInstaller"
}

if (-not (Test-Path -LiteralPath $IconPath)) {
    throw "Icon not found: $IconPath"
}

if (-not (Test-Path -LiteralPath $MetaDataPath)) {
    throw "metaData folder not found: $MetaDataPath"
}

if (-not (Test-Path -LiteralPath $MainPath)) {
    throw "main.py not found: $MainPath"
}

if ($Clean) {
    Remove-Item -LiteralPath (Join-Path $ProjectRoot "build") -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $ProjectRoot "dist") -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $ProjectRoot "PDFNoteReader.spec") -Force -ErrorAction SilentlyContinue
}

& $PyInstaller `
    --windowed `
    --name PDFNoteReader `
    --icon "assets\app_icon.ico" `
    --add-data "metaData;metaData" `
    --add-data "assets\app_icon.ico;assets" `
    main.py
