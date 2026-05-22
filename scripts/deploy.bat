@echo off
setlocal enabledelayedexpansion

echo =============================================
echo    Starting HendAxis PoS Deployment Process   
echo =============================================

:: 1. Navigate to project root
cd /d "%~dp0\.."
echo [INFO] Project root: %CD%

:: 2. Activate virtual environment
if exist "venv\Scripts\activate.bat" (
    echo [INFO] Activating virtual environment...
    call venv\Scripts\activate.bat
) else (
    echo [WARNING] Virtual environment 'venv' not found.
)

:: 3. Optional: Run tests if the flag is provided
if "%1"=="--run-tests" (
    echo =============================================
    echo    Running Workflow Tests...                 
    echo =============================================
    python scripts\run_workflow_tests.py
    if !ERRORLEVEL! neq 0 (
        echo [ERROR] Tests failed! Deployment aborted.
        exit /b 1
    )
    echo [INFO] All tests passed successfully.
) else (
    echo [INFO] Skipping workflow tests. Use --run-tests flag to run them.
)

echo =============================================
echo    Installing Dependencies...                
echo =============================================
python -m pip install -r requirements.txt

echo =============================================
echo    Applying Database Migrations...           
echo =============================================
python manage.py migrate --noinput

echo =============================================
echo    Collecting Static Files...                
echo =============================================
python manage.py collectstatic --noinput

echo =============================================
echo    Seeding Database Data...                  
echo =============================================
echo [INFO] Seeding Feature Messages...
python manage.py seed_feature_messages

echo [INFO] Setting up Demo/Base Environment...
python manage.py setup_demo

echo =============================================
echo    Deployment Complete!                      
echo =============================================
echo [INFO] If you are running under IIS, Apache, or a service, please restart the service.

endlocal
