.PHONY: install check run

install:
	python3 -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -e ".[dev]"

check:
	.venv/bin/ruff check .
	.venv/bin/pytest -q

run:
	.venv/bin/uvicorn cabinet.web.app:create_app --factory --host 127.0.0.1 --port 8765
