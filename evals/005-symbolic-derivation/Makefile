# 005 — Deriving formulas with a symbolic tool (SymPy).
#
#   make smoke — stub, no Ollama, <1s — verifies wiring
#   make quick — 3 problems × {text, sympy, verify} × N=3 = 27 cells
#                (build_sympy/build_verify are multi-turn → allow ~40-60 min)

ROOT      := ..
PY        := cd $(ROOT) && uv run python
CONFIG    := $(abspath config.yaml)

PROBLEMS  := astro-025,astro-037,astro-038

.PHONY: help smoke quick list-runs clean-latest

help:
	@awk 'BEGIN {FS = ":.*?##"} /^[a-zA-Z_-]+:.*?##/ {printf "  \033[36m%-13s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

smoke: ## Stub end-to-end (no model) — verifies build modes + formula scoring wiring.
	$(PY) -m lib.run_derive --config $(CONFIG) --stub \
	  --problems $(PROBLEMS) --n-runs 1 --no-logs

quick: ## 3 problems × {text, sympy, verify} × N=3 = 27 cells.
	$(PY) -m lib.run_derive --config $(CONFIG) \
	  --problems $(PROBLEMS) --n-runs 3

list-runs: ## List historical runs under results/.
	@find results -maxdepth 1 -mindepth 1 -type d -not -name '.*' -printf '%f\n' 2>/dev/null | sort

clean-latest: ## Wipe only the latest run dir.
	@target=$$(readlink results/latest 2>/dev/null); \
	if [ -n "$$target" ]; then rm -rf results/$$target results/latest; echo "wiped results/$$target"; \
	else echo "no latest run to wipe"; fi
