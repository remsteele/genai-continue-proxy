.ONESHELL:

run:
	@echo "Loading environment and starting Uvicorn..."
	@. ~/continue-proxy/.env && . ~/continue-proxy/.venv/bin/activate && uvicorn proxy:app --host 127.0.0.1 --port 8000
