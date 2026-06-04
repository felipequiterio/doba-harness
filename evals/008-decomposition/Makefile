# 008 — Decomposition: does decompose-then-solve beat a monolithic solve on
# hard multi-step problems? Knowledge held constant (oracle cards in both).
#
#   make smoke — stub, no Ollama, <1s
#   make quick — 3 hard problems × {mono,decomp} × N=3 = 18 cells (~30-50 min)
#   make run   — adds astro-005, astro-035 at N=5 = 50 cells (~80-110 min)
#
# Slow: mono thinks, decomp plans (thinking) + walks every leaf. Warmup keeps
# the model resident.

ROOT      := ..
PY        := cd $(ROOT) && uv run python
CONFIG    := $(abspath config.yaml)

# The hard multi-step cases: bi-elliptic, the 0/3 combined-plane-change wall,
# and the small rounding-sensitive Hohmann (007's drop-a-burn failure).
QUICK_PROBLEMS := astro-028,astro-031,astro-014
RUN_PROBLEMS   := astro-005,astro-014,astro-028,astro-031,astro-035

.PHONY: help smoke quick run list-runs clean-latest

help:
	@awk 'BEGIN {FS = ":.*?##"} /^[a-zA-Z_-]+:.*?##/ {printf "  \033[36m%-13s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

smoke: ## Stub end-to-end (no model) — verifies plan/leaf/compose + mono wiring.
	$(PY) -m lib.run_decompose --config $(CONFIG) --stub \
	  --problems $(QUICK_PROBLEMS) --n-runs 1 --no-logs

quick: ## 3 hard problems × {mono,decomp} × N=3 = 18 cells.
	$(PY) -m lib.run_decompose --config $(CONFIG) \
	  --problems $(QUICK_PROBLEMS) --n-runs 3

run: ## 5 problems × {mono,decomp} × N=5 = 50 cells.
	$(PY) -m lib.run_decompose --config $(CONFIG) \
	  --problems $(RUN_PROBLEMS) --n-runs 5

list-runs: ## List historical runs under results/.
	@find results -maxdepth 1 -mindepth 1 -type d -not -name '.*' -printf '%f\n' 2>/dev/null | sort

clean-latest: ## Wipe only the latest run dir.
	@target=$$(readlink results/latest 2>/dev/null); \
	if [ -n "$$target" ]; then rm -rf results/$$target results/latest; echo "wiped results/$$target"; \
	else echo "no latest run to wipe"; fi
