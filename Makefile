PYTHON ?= python3.12
VENV ?= .venv
VENV_PYTHON := $(VENV)/bin/python
READY := $(VENV)/.dashboard-ready

.PHONY: setup build compile test test-backend test-frontend audit verify-bundles check clean

setup: $(READY)

$(READY): requirements-dev.txt torrent-panel/requirements.txt prowlarr-panel/requirements.txt cloud-panel/requirements.txt
	$(PYTHON) -m venv $(VENV)
	$(VENV_PYTHON) -m pip install --upgrade pip
	$(VENV_PYTHON) -m pip install -r requirements-dev.txt
	@touch $(READY)

build: setup
	$(VENV_PYTHON) torrent-panel/build.py
	$(VENV_PYTHON) prowlarr-panel/build.py
	$(VENV_PYTHON) cloud-panel/build.py

compile: setup
	$(VENV_PYTHON) -m compileall -q common torrent-panel/torrent_panel prowlarr-panel/prowlarr_panel cloud-panel/cloud_panel

test-backend: setup
	$(VENV_PYTHON) -m pytest torrent-panel/tests/test_backend.py -q
	$(VENV_PYTHON) -m pytest prowlarr-panel/tests/test_backend.py -q
	$(VENV_PYTHON) -m pytest cloud-panel/tests/test_backend.py -q

test-frontend:
	node torrent-panel/tests/frontend_logic.test.js
	node prowlarr-panel/tests/frontend_logic.test.js
	node cloud-panel/tests/frontend_logic.test.js

test: test-backend test-frontend

audit: setup
	$(VENV_PYTHON) -m pip_audit -r torrent-panel/requirements.txt
	$(VENV_PYTHON) -m pip_audit -r prowlarr-panel/requirements.txt
	$(VENV_PYTHON) -m pip_audit -r cloud-panel/requirements.txt

verify-bundles:
	git diff --exit-code -- \
		torrent-panel/torrent_panel/static/dist \
		prowlarr-panel/prowlarr_panel/static/dist \
		cloud-panel/cloud_panel/static/dist

check: build compile test verify-bundles

clean:
	@echo "L'environnement $(VENV) est conservé. Supprimez-le explicitement si nécessaire."
