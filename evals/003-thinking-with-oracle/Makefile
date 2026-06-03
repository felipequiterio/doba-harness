# 003 — Thinking mode with oracle: does it pay off?
#
# Thin wrapper. All logic lives in evals/lib/. Two run modes:
#   make quick — 5 problems × N=2 ≈ 20 min
#   make run   — 10 problems × N=3 ≈ 70 min

ROOT      := ..
PY        := cd $(ROOT) && uv run python
CONFIG    := $(abspath config.yaml)

# Quick subset — 5 problems chosen for known discrimination potential.
QUICK_PROBLEMS := astro-005,astro-016,astro-027,astro-028,astro-031

# Long subset — quick + 5 more, including the 2 new problems written
# specifically for this eval (astro-035, astro-036).
LONG_PROBLEMS := astro-005,astro-014,astro-016,astro-023,astro-027,astro-028,astro-029,astro-031,astro-035,astro-036

CONDITIONS := C3_NT,C3_T

.PHONY: help smoke quick run analyze list-runs clean-latest clean-all-runs

help:
	@awk 'BEGIN {FS = ":.*?##"} /^[a-zA-Z_-]+:.*?##/ {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

smoke: ## Stub end-to-end (no model calls, <1 sec) — verifies wiring.
	$(PY) -m lib.run --config $(CONFIG) --stub \
	  --conditions $(CONDITIONS) \
	  --n-runs 1 \
	  --problems $(QUICK_PROBLEMS) \
	  --no-judge

quick: ## 5 problems × N=2 (~20 min). Discrimination-focused subset.
	$(PY) -m lib.run --config $(CONFIG) \
	  --conditions $(CONDITIONS) \
	  --n-runs 2 \
	  --problems $(QUICK_PROBLEMS) \
	  --no-judge
	$(MAKE) analyze

run: ## 10 problems × N=3 (~70 min). Includes astro-035, astro-036.
	$(PY) -m lib.run --config $(CONFIG) \
	  --conditions $(CONDITIONS) \
	  --n-runs 3 \
	  --problems $(LONG_PROBLEMS) \
	  --no-judge
	$(MAKE) analyze

analyze: ## Aggregate the LATEST run → summary.md + charts.
	$(PY) -m lib.analyze --config $(CONFIG)

list-runs: ## List historical runs under results/.
	@find results -maxdepth 1 -mindepth 1 -type d -not -name '.*' -printf '%f\n' 2>/dev/null | sort

clean-latest: ## Wipe only the latest run dir.
	@target=$$(readlink results/latest 2>/dev/null); \
	if [ -n "$$target" ]; then rm -rf results/$$target results/latest; echo "wiped results/$$target"; \
	else echo "no latest run to wipe"; fi

clean-all-runs: ## Wipe ALL historical runs. Asks for confirmation.
	@read -p "wipe ALL results under results/? [y/N] " ans && [ "$$ans" = "y" ] && \
	  find results -mindepth 1 -not -name '.gitkeep' -exec rm -rf {} + 2>/dev/null && echo "wiped" || echo "aborted"
