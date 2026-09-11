VENV   := .venv
PYTHON := $(VENV)/bin/python
PIP    := $(VENV)/bin/pip
BIN    := $(VENV)/bin/intervallic

.PHONY: help install run setup sync dry-run doctor audit clean

## Show available targets
help:
	@echo ""
	@echo "  make install    Create the venv and install Intervallic"
	@echo "  make run        Set up on first run, sync thereafter"
	@echo "  make doctor     Check everything and explain what's broken"
	@echo "  make sync       Run a full sync"
	@echo "  make dry-run    Preview a sync without writing files"
	@echo "  make audit      Find incomplete albums in your Plex library"
	@echo "  make clean      Remove the virtual environment"
	@echo ""

## Create venv and install dependencies
install:
	@bash scripts/install.sh $(VENV)
	@$(PIP) install -e . --quiet
	@echo ""
	@echo "  Installed. Next:  make run"
	@echo ""

## Do the right thing — setup on first run, sync after that
run: install
	@$(BIN)

## Interactive setup wizard
setup: install
	@$(BIN) setup

## Run a full sync
sync: install
	@$(BIN) sync

## Dry-run (no files written)
dry-run: install
	@$(BIN) sync --dry-run

## Diagnose configuration and connectivity problems
doctor: install
	@$(BIN) doctor

## Find incomplete albums in the Plex library
audit: install
	@$(BIN) audit

## Remove the virtual environment
clean:
	rm -rf $(VENV)
