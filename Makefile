PY  := .venv/bin/python
PIP := .venv/bin/pip
PYBIN := $(shell command -v python3.11 || command -v python3)

.PHONY: setup seed run-api run-ui build-ui demo clean

setup:                ## create venv, install backend + frontend deps, make .env
	$(PYBIN) -m venv .venv
	$(PIP) install -q -r requirements.txt
	@[ -f .env ] || cp .env.example .env
	cd frontend && npm install
	@echo "Setup complete. Next: make seed && make run-api (then make run-ui in another shell)."

seed:                 ## reset DB, create ACME customer + ruleset, generate samples
	$(PY) -m backend.seed

run-api:              ## start the FastAPI backend on :8099
	$(PY) -m uvicorn backend.app:app --reload --port 8099

run-ui:               ## start the Vite dev server on :5173
	cd frontend && npm run dev

build-ui:             ## build the UI so the backend serves it at http://localhost:8099/
	cd frontend && npm run build

demo:                 ## run the real pipeline on all 3 sample docs via the CLI
	$(PY) -m backend.cli run samples/clean/commercial_invoice_acme.pdf
	$(PY) -m backend.cli run samples/clean/commercial_invoice_mismatch.pdf
	$(PY) -m backend.cli run samples/messy/commercial_invoice_scan.png
	$(PY) -m backend.cli query "how many shipments were flagged for review this week?"

clean:                ## remove DB, uploads, samples, build artifacts
	rm -rf data/*.db data/*.db-* data/uploads samples/clean/*.pdf samples/messy/*.png frontend/dist
