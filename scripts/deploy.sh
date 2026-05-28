#!/bin/bash

# Deployment script for HendAxis PoS
# Run this script on your production server to deploy new updates.
# Usage: ./scripts/deploy.sh [--run-tests]

echo "============================================="
echo "   Starting HendAxis PoS Deployment Process   "
echo "============================================="

# 1. Navigate to the project root directory
# Assuming the script is run from the project root or the scripts directory
cd "$(dirname "$0")/.."
PROJECT_ROOT=$(pwd)
echo "[INFO] Project root: $PROJECT_ROOT"

# 2. Activate virtual environment
if [ -d "venv" ]; then
    echo "[INFO] Activating virtual environment..."
    source venv/bin/activate
elif [ -d "../venv" ]; then
    echo "[INFO] Activating virtual environment (parent dir)..."
    source ../venv/bin/activate
else
    echo "[WARNING] Virtual environment 'venv' not found. Assuming global python or Docker."
fi

# 3. Optional: Run tests if the flag is provided
if [[ "$1" == "--run-tests" ]]; then
    echo "============================================="
    echo "   Running Workflow Tests...                 "
    echo "============================================="
    python scripts/run_workflow_tests.py
    if [ $? -ne 0 ]; then
        echo "[ERROR] Tests failed! Deployment aborted."
        exit 1
    fi
    echo "[INFO] All tests passed successfully."
else
    echo "[INFO] Skipping workflow tests. Use --run-tests flag to run them."
fi

echo "============================================="
echo "   Installing Dependencies...                "
echo "============================================="
pip install -r requirements.txt

echo "============================================="
echo "   Applying Database Migrations...           "
echo "============================================="
python manage.py migrate --noinput

echo "============================================="
echo "   Collecting Static Files...                "
echo "============================================="
python manage.py collectstatic --noinput

echo "============================================="
echo "   Seeding Database Data...                  "
echo "============================================="
# Seed feature introduction messages for the Email Campaign system
echo "[INFO] Seeding Feature Messages..."
python manage.py seed_feature_messages

# Setup demo data (creates initial tenant, products, demo admin, etc.)
# WARNING: The user has requested to run this in production for demonstration purposes.
echo "[INFO] Setting up Demo/Base Environment..."
python manage.py setup_demo

echo "============================================="
echo "   Restarting Web Services (Optional)        "
echo "============================================="
# Uncomment and adjust the following lines based on your production setup:
# echo "[INFO] Restarting Gunicorn/Supervisor..."
# sudo systemctl restart gunicorn
# sudo supervisorctl restart pos

echo "============================================="
echo "   Deployment Complete!                      "
echo "============================================="
