VENV   := .venv
PYTHON := $(VENV)/bin/python
PIP    := $(VENV)/bin/pip
BIN    := $(VENV)/bin/intervallic

.PHONY: install setup sync dry-run clean

## Create venv and install dependencies
install:
	@bash scripts/install.sh $(VENV)
	@$(PIP) install -e . --quiet

## Interactive setup wizard
setup: install
	$(BIN) setup

## Run a full sync
sync: install
	$(BIN) sync

## Dry-run (no files written)
dry-run: install
	$(BIN) sync --dry-run

## Remove the virtual environment
clean:
	rm -rf $(VENV)
