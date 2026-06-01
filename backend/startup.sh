#!/bin/bash
set -e

echo "Starting Saramsa API..."
echo "Current directory: $(pwd)"
echo "Contents: $(ls -la | head -10)"

# Oryx sets the correct working directory, no need to cd
# The application is extracted to /tmp/... by Oryx, not /home/site/wwwroot

# Verify apis module exists
if [ -d "apis" ]; then
    echo "✓ apis directory found"
else
    echo "✗ ERROR: apis directory not found!"
    echo "Looking in: $(pwd)"
    echo "Files available:"
    ls -la
    exit 1
fi

# Apply Django migrations BEFORE gunicorn binds the port. Azure App Service
# is configured with `appCommandLine: "bash startup.sh"`, which overrides the
# Dockerfile.api CMD — so the migrate step in CMD never ran in prod, and any
# migration that shipped since PR #49 silently did not apply (e.g. taxonomy
# lock relaxation in 0003_relax_taxonomy_locks). Running migrate here keeps
# every deploy self-contained: if migrate fails, gunicorn never starts, the
# health probe fails, and Azure rolls back to the previous container.
echo "Applying migrations with advisory lock protection..."
python manage.py migrate_safe

# Start Gunicorn
echo "Starting Gunicorn from: $(pwd)"
exec gunicorn --bind=0.0.0.0:8000 --timeout 600 --workers 2 --access-logfile - --error-logfile - --log-level info apis.wsgi:application
