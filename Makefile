.PHONY: help install dev test lint fmt typecheck up down logs seed admin reminders check clean

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install:  ## Create a venv and install everything
	python3 -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -e ".[dev]"
	@echo "Done. Activate with: source .venv/bin/activate"

dev:  ## Run the dev server
	FLASK_ENV=development .venv/bin/python -m flask --app wsgi:app run --debug --port 5000

test:  ## Run the test suite
	.venv/bin/pytest -v

lint:  ## Check style
	.venv/bin/ruff check .

fmt:  ## Fix what can be fixed automatically
	.venv/bin/ruff check --fix .
	.venv/bin/ruff format .

typecheck:  ## Run mypy
	.venv/bin/mypy app

up:  ## Start the full stack in Docker
	docker compose up --build -d
	@echo "App      http://localhost:5000"
	@echo "Mailhog  http://localhost:8025"

down:  ## Stop the stack
	docker compose down

logs:  ## Tail the stack logs
	docker compose logs -f web worker beat

seed:  ## Create indexes and load the plant catalog
	.venv/bin/python manage.py ensure-indexes
	.venv/bin/python manage.py seed-catalog

admin:  ## Create an admin account
	.venv/bin/python manage.py create-admin

reminders:  ## Preview the next reminder sweep
	.venv/bin/python manage.py send-reminders --dry-run

check:  ## Report dependency status
	.venv/bin/python manage.py check

clean:  ## Remove caches
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage
