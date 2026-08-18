#!/bin/sh
# Development container entrypoint: migrate, optionally seed, then serve.
# Production applies migrations as a separate controlled deployment step.
set -eu

echo "Applying migrations..."
python manage.py migrate --noinput

echo "Verifying Estonian search capabilities..."
python manage.py check_search_capabilities || echo "WARNING: search capability check failed"

if [ "${SEED_DEV_DATA:-0}" = "1" ]; then
  echo "Seeding synthetic development data..."
  python manage.py seed_dev_data
fi

echo "Starting development server on 0.0.0.0:8000"
exec python manage.py runserver 0.0.0.0:8000
