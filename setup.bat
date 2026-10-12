@echo off

setlocal enabledelayedexpansion

:: Set console title and colors
title Code Blue Order Automation - Setup and Dependencies
color 0B

echo ==========================================
echo    CODE BLUE ORDER AUTOMATION SETUP
echo ==========================================
echo.

:: Check if Python is installed
echo [1/7] Checking Python installation...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH
    echo Please install Python 3.8+ from https://python.org
    pause
    exit /b 1
)

:: Get Python version
for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYTHON_VERSION=%%i
echo [OK] Python %PYTHON_VERSION% found

:: Check if pip is available
echo.
echo [2/7] Checking pip installation...
python -m pip --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] pip is not available
    echo Please ensure pip is installed with Python
    pause
    exit /b 1
)
echo [OK] pip is available

:: Check if virtual environment exists
echo.
echo [3/7] Checking virtual environment...
if not exist "venv\" (
    echo [INFO] Virtual environment not found. Creating one...
    python -m venv venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment
        pause
        exit /b 1
    )
    echo [OK] Virtual environment created
) else (
    echo [OK] Virtual environment found
)

:: Activate virtual environment
echo.
echo [4/7] Activating virtual environment...
call venv\Scripts\activate.bat
if %errorlevel% neq 0 (
    echo [ERROR] Failed to activate virtual environment
    pause
    exit /b 1
)
echo [OK] Virtual environment activated

:: Check and install requirements
echo.
echo [5/7] Checking dependencies...
if exist "requirements.txt" (
    echo [INFO] Installing/updating dependencies from requirements.txt...
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to install dependencies
        pause
        exit /b 1
    )
    echo [OK] Dependencies installed successfully
) else (
    echo [INFO] requirements.txt not found. Installing common dependencies...
    python -m pip install --upgrade pip selenium webdriver-manager python-dotenv undetected-chromedriver
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to install dependencies
        pause
        exit /b 1
    )
    echo [OK] Common dependencies installed
)

:: Check if .env file exists
echo.
echo [6/7] Checking environment configuration...
if not exist ".env" (
    echo [WARNING] .env file not found
    echo Please create a .env file with the following variables:
    echo - ENW_USERNAME
    echo - ENW_PASSWORD
    echo - ATLAS_USERNAME
    echo - ATLAS_PASSWORD
    echo - DISCORD_WEBHOOK_URL
    echo.
    echo [INFO] You can create this file manually or continue setup without it
) else (
    echo [OK] Environment file found
)

:: Check if main.py exists
echo.
echo [7/7] Checking main application file...
if not exist "main.py" (
    echo [ERROR] main.py not found in current directory
    echo Current directory: %CD%
    dir *.py
    pause
    exit /b 1
)
echo [OK] main.py found

:: Create logs directory if it doesn't exist
if not exist "logs\" (
    mkdir logs
    echo [INFO] Created logs directory
)

:: Create Utils directory if it doesn't exist
if not exist "Utils\" (
    mkdir Utils
    echo [INFO] Created Utils directory
)

echo.
echo ==========================================
echo    SETUP COMPLETED SUCCESSFULLY
echo ==========================================
echo.
echo [SUCCESS] All dependencies and environment setup completed!
echo [INFO] Virtual environment is ready at: venv\
echo [INFO] Logs directory created at: logs\
echo.
echo Next steps:
echo 1. Make sure your .env file contains all required credentials
echo 2. Run 'run.bat' to start the application
echo.
color 0A
pause

:: Deactivate virtual environment
call deactivate >nul 2>&1

exit /b 0

