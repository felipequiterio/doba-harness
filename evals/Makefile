# Shared Doba evals Makefile. Run from evals/ root.
#
# Cross-cutting (not eval-specific) targets:
#   - install, sync       — venv setup
#   - test, verify        — unit tests + dataset/ground-truth consistency
#   - gt-check, gt-regen  — check / regenerate astrodynamics dataset
#   - smoke               — stub end-to-end on the astro dataset (no models)
#   - list-evals          — show which experiment folders exist
#   - clean               — wipe caches (keeps results)
#
# Per-eval targets live in NNN-*/Makefile. They are thin: each just calls
# `python -m lib.run --config ./config.yaml`.

PY      := uv run python
PYTEST  := uv run pytest

ASTRO_DATASET := datasets/astrodynamics

# Find all experiment folders (NNN-...).
EVALS := $(sort $(notdir $(wildcard [0-9]*-*)))

.PHONY: help install sync test verify gt-check gt-regen smoke list-evals clean

help:
	@awk 'BEGIN {FS = ":.*?##"} /^[a-zA-Z_-]+:.*?##/ {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo
	@echo "  per-eval targets — cd into one of:"
	@for e in $(EVALS); do echo "    $$e"; done

install: sync ## Alias for sync.
sync: ## Install deps via uv into evals/.venv (shared by all evals).
	uv sync

test: ## Run the full unit-test suite (lib + datasets).
	$(PYTEST)

gt-check: ## Verify the astrodynamics dataset matches ground_truth.py.
	$(PY) -m $(subst /,.,$(ASTRO_DATASET)).ground_truth --check

gt-regen: ## Recompute astrodynamics "expected" values into the dataset JSON.
	$(PY) -m $(subst /,.,$(ASTRO_DATASET)).ground_truth --regenerate

verify: gt-check test ## Both checks — green = safe to run any eval.

smoke: ## Stub end-to-end (no model calls) using 001's config — wiring smoke.
	$(PY) -m lib.run --config 001-astro-failure-modes/config.yaml \
	  --stub --conditions C1,C2,C3,REF --n-runs 1 --problems astro-001 --no-judge

list-evals: ## Show every experiment folder.
	@for e in $(EVALS); do \
	  desc=$$(head -1 $$e/README.md 2>/dev/null | sed 's/^# //'); \
	  printf "  \033[36m%-30s\033[0m %s\n" "$$e" "$$desc"; \
	done

clean: ## Recursively remove __pycache__ + .pytest_cache. Keeps all eval results.
	find . -type d \( -name __pycache__ -o -name .pytest_cache \) -not -path './.venv/*' -exec rm -rf {} + 2>/dev/null; true
	@echo "caches cleaned"
