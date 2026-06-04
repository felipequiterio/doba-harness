# 007 — Multi-card building: can the 4B build a whole card SET and compose it?
#
#   make smoke — stub, no Ollama, <1s
#   make quick — 3 transfer problems × {C2,C3_oracle,C3_self} × N=3 = 27 cells (~30-50 min)
#   make run   — adds astro-035 (5-card Hohmann-vs-bi-elliptic) at N=5 = 60 cells (~70-90 min)
#
# These are MULTI-STEP problems with thinking ON + a 16k budget, so cells are
# slower than 004-006. Warmup keeps the model resident.

ROOT      := ..
PY        := cd $(ROOT) && uv run python
CONFIG    := $(abspath config.yaml)

QUICK_PROBLEMS := astro-005,astro-014,astro-028
RUN_PROBLEMS   := astro-005,astro-014,astro-028,astro-035

.PHONY: help smoke quick run list-runs clean-latest

help:
	@awk 'BEGIN {FS = ":.*?##"} /^[a-zA-Z_-]+:.*?##/ {printf "  \033[36m%-13s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

smoke: ## Stub end-to-end (no model) — verifies build + coverage + solve wiring.
	$(PY) -m lib.run_multicard --config $(CONFIG) --stub \
	  --problems $(QUICK_PROBLEMS) --n-runs 1 --no-logs

quick: ## 3 transfer problems × {C2,C3_oracle,C3_self} × N=3 = 27 cells.
	$(PY) -m lib.run_multicard --config $(CONFIG) \
	  --problems $(QUICK_PROBLEMS) --n-runs 3

run: ## + astro-035 (5-card comparison) at N=5 = 60 cells.
	$(PY) -m lib.run_multicard --config $(CONFIG) \
	  --problems $(RUN_PROBLEMS) --n-runs 5

list-runs: ## List historical runs under results/.
	@find results -maxdepth 1 -mindepth 1 -type d -not -name '.*' -printf '%f\n' 2>/dev/null | sort

clean-latest: ## Wipe only the latest run dir.
	@target=$$(readlink results/latest 2>/dev/null); \
	if [ -n "$$target" ]; then rm -rf results/$$target results/latest; echo "wiped results/$$target"; \
	else echo "no latest run to wipe"; fi
