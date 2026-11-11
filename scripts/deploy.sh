#!/bin/bash
# ==============================================================================
# HendAxis PoS - Production Deployment Script
# Quickly pull new updates from the 'alpha' branch and deploy on production.
#
# Usage:
#   ./scripts/deploy.sh [OPTIONS]
#
# Options:
#   --branch <name>    Specify git branch to deploy (default: alpha)
#   --run-tests        Run automated workflow tests before deploying
#   --skip-demo        Skip running setup_demo (preserves existing database)
#   --no-pull          Skip git fetch/pull (deploy existing local codebase)
#   --help, -h         Show this help message
# ==============================================================================

set -eo pipefail

BRANCH="alpha"
RUN_TESTS=false
RUN_DEMO=true
DO_PULL=true

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --branch)
            BRANCH="$2"
            shift 2
            ;;
        --run-tests)
            RUN_TESTS=true
            shift
            ;;
        --skip-demo)
            RUN_DEMO=false
            shift
            ;;
        --no-pull)
            DO_PULL=false
            shift
            ;;
        --help|-h)
            echo "Usage: ./scripts/deploy.sh [--branch <branch>] [--run-tests] [--skip-demo] [--no-pull]"
            exit 0
            ;;
        *)
            echo "[WARNING] Unknown argument: $1"
            shift
            ;;
    esac
done

echo "=========================================================="
echo "   HendAxis PoS Production Deployment ($BRANCH)           "
echo "=========================================================="
echo "[INFO] Started at: $(date '+%Y-%m-%d %H:%M:%S %Z')"

# 1. Project Root Directory
cd "$(dirname "$0")/.."
PROJECT_ROOT=$(pwd)
echo "[INFO] Project root: $PROJECT_ROOT"

# Ensure git recognizes directory even across different system users
if command -v git &> /dev/null; then
    git config --global --add safe.directory "$PROJECT_ROOT" 2>/dev/null || true
fi

# 2. Pull Updates from Git Branch (default: alpha)
if [ "$DO_PULL" = true ]; then
    echo "=========================================================="
    echo "   [1/7] Pulling Updates from Git Branch: $BRANCH         "
    echo "=========================================================="
    if [ ! -d ".git" ]; then
        echo "[ERROR] Not a git repository. Cannot pull updates."
        exit 1
    fi

    # Stash any uncommitted local changes to avoid merge conflicts
    if ! git diff-index --quiet HEAD -- 2>/dev/null; then
        echo "[INFO] Stashing local changes before pulling..."
        git stash --include-untracked
    fi

    echo "[INFO] Fetching origin..."
    git fetch origin "$BRANCH" --prune

    # Ensure we are on the target branch
    CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
    if [ "$CURRENT_BRANCH" != "$BRANCH" ]; then
        echo "[INFO] Switching from '$CURRENT_BRANCH' to '$BRANCH'..."
        git checkout "$BRANCH"
    fi

    echo "[INFO] Pulling latest changes from origin/$BRANCH..."
    git pull origin "$BRANCH"

    LATEST_COMMIT=$(git log -1 --format="%h - %s (%cr by %an)")
    echo "[INFO] Deployed Commit: $LATEST_COMMIT"
else
    echo "[INFO] Skipping git pull (--no-pull flag specified)."
fi

# 3. Virtual Environment
echo "=========================================================="
echo "   [2/7] Activating Python Virtual Environment...         "
echo "=========================================================="
VENV_ACTIVATED=false
for venv_path in "venv/bin/activate" "../venv/bin/activate" "/var/www/pos/venv/bin/activate" "$HOME/.venv/bin/activate"; do
    if [ -f "$venv_path" ]; then
        echo "[INFO] Activating virtual environment: $venv_path"
        # shellcheck disable=SC1090
        source "$venv_path"
        VENV_ACTIVATED=true
        break
    fi
done

if [ "$VENV_ACTIVATED" = false ]; then
    echo "[WARNING] No standard virtual environment found. Using system python3."
fi

PYTHON_BIN=$(command -v python || command -v python3)
echo "[INFO] Using Python: $PYTHON_BIN ($($PYTHON_BIN --version 2>&1))"

# 4. Optional Pre-deployment Tests
if [ "$RUN_TESTS" = true ]; then
    echo "=========================================================="
    echo "   Running Pre-Deployment Workflow Tests...               "
    echo "=========================================================="
    if [ -f "scripts/run_workflow_tests.py" ]; then
        python scripts/run_workflow_tests.py
    else
        python manage.py test apps.sales.test_workflows --settings=pos_system.settings
    fi
    if [ $? -ne 0 ]; then
        echo "[ERROR] Tests failed! Aborting deployment."
        exit 1
    fi
    echo "[INFO] All tests passed successfully."
fi

# 5. Dependencies
echo "=========================================================="
echo "   [3/7] Updating Python Dependencies...                  "
echo "=========================================================="
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
# Ensure standard production server libraries
pip install gunicorn psycopg2-binary --quiet 2>/dev/null || true

# 6. Database Migrations
echo "=========================================================="
echo "   [4/7] Applying Database Migrations...                  "
echo "=========================================================="
python manage.py migrate --noinput

# 7. Static Files
echo "=========================================================="
echo "   [5/7] Collecting Static Files...                       "
echo "=========================================================="
python manage.py collectstatic --noinput

# 8. Seed Feature Messages & Demo
echo "=========================================================="
echo "   [6/7] Seeding Feature Messages & Data...               "
echo "=========================================================="
python manage.py seed_feature_messages

if [ "$RUN_DEMO" = true ]; then
    echo "[INFO] Setting up / Refreshing Demo Environment..."
    python manage.py setup_demo
else
    echo "[INFO] Skipping setup_demo (--skip-demo flag active)."
fi

# 9. Restart Web Services
echo "=========================================================="
echo "   [7/7] Restarting Application Services...               "
echo "=========================================================="

# Systemd services (pos, gunicorn, etc.)
RESTARTED=false
for service_name in "pos" "gunicorn" "pos_system"; do
    if systemctl is-active --quiet "$service_name" 2>/dev/null || systemctl list-unit-files "$service_name.service" 2>/dev/null | grep -q "$service_name"; then
        echo "[INFO] Restarting systemd service: $service_name..."
        if [ "$EUID" -eq 0 ]; then
            systemctl restart "$service_name"
        else
            sudo systemctl restart "$service_name"
        fi
        RESTARTED=true
        break
    fi
done

# Supervisor
if command -v supervisorctl &> /dev/null; then
    if supervisorctl status pos 2>/dev/null | grep -q "RUNNING"; then
        echo "[INFO] Restarting supervisor process: pos..."
        supervisorctl restart pos || true
        RESTARTED=true
    fi
fi

# Reload Nginx if installed
if command -v nginx &> /dev/null; then
    echo "[INFO] Reloading Nginx..."
    if [ "$EUID" -eq 0 ]; then
        systemctl reload nginx 2>/dev/null || nginx -s reload 2>/dev/null || true
    else
        sudo systemctl reload nginx 2>/dev/null || sudo nginx -s reload 2>/dev/null || true
    fi
fi

if [ "$RESTARTED" = false ]; then
    echo "[NOTICE] No active 'pos' or 'gunicorn' systemd service automatically detected."
    echo "         If your WSGI server is managed manually, please restart it."
fi

echo "=========================================================="
echo "   [SUCCESS] Deployment Completed Successfully!           "
echo "=========================================================="
echo "[INFO] Completed at: $(date '+%Y-%m-%d %H:%M:%S %Z')"
