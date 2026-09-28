.ONESHELL:

run:
	@echo "Loading environment and starting Uvicorn..."
	@test -f .env || (echo "Missing .env. Copy .env.example to .env and configure it." >&2; exit 1)
	@test -x .venv/bin/uvicorn || (echo "Missing virtual environment. See README.md." >&2; exit 1)
	@set -a; . ./.env; set +a; exec .venv/bin/uvicorn proxy:app --host 127.0.0.1 --port 8000
