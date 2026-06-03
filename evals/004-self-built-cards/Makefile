# 004 — Self-built knowledge cards: can the 4B build a usable card itself?
#
# Thin wrapper over lib.run_cards. The whole eval is one run mode (quick).
#   make smoke — stub, no Ollama, <1s — verifies wiring
#   make quick — 3 problems × {C2, C3_oracle, C3_self} × N=3 = 27 cells

ROOT      := ..
PY        := cd $(ROOT) && uv run python
CONFIG    := $(abspath config.yaml)

PROBLEMS  := astro-016,astro-025,astro-027

.PHONY: help smoke quick list-runs clean-latest

help:
	@awk 'BEGIN {FS = ":.*?##"} /^[a-zA-Z_-]+:.*?##/ {printf "  \033[36m%-13s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

smoke: ## Stub end-to-end (no model calls) — verifies card-build + solve wiring.
	$(PY) -m lib.run_cards --config $(CONFIG) --stub \
	  --problems $(PROBLEMS) --n-runs 1 --no-logs

quick: ## 3 problems × {C2, C3_oracle, C3_self} × N=3 (~20 min on the 4B).
	$(PY) -m lib.run_cards --config $(CONFIG) \
	  --problems $(PROBLEMS) --n-runs 3

list-runs: ## List historical runs under results/.
	@find results -maxdepth 1 -mindepth 1 -type d -not -name '.*' -printf '%f\n' 2>/dev/null | sort

clean-latest: ## Wipe only the latest run dir.
	@target=$$(readlink results/latest 2>/dev/null); \
	if [ -n "$$target" ]; then rm -rf results/$$target results/latest; echo "wiped results/$$target"; \
	else echo "no latest run to wipe"; fi
