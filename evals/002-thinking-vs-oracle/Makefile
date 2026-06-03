# 002 — thinking vs oracle on the 5 hardest problems.
#
# This Makefile lives inside the experiment folder. It calls into the
# shared harness at evals/lib/ via `python -m lib.run --config ./config.yaml`.
# No source code lives here — this is just an experiment definition.

ROOT      := ..
PY        := cd $(ROOT) && uv run python
CONFIG    := $(abspath config.yaml)

# The 5 hardest problems from the astrodynamics dataset — see README.md
# for the selection rationale. Locked here so the eval is reproducible.
HARD_PROBLEMS := astro-005,astro-014,astro-016,astro-028,astro-031

.PHONY: help smoke run analyze clean

help:
	@awk 'BEGIN {FS = ":.*?##"} /^[a-zA-Z_-]+:.*?##/ {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

smoke: ## Stub run (no model calls, <1 sec) — verifies wiring.
	$(PY) -m lib.run --config $(CONFIG) --stub \
	  --conditions C1_NT,C1_T,C3_NT,C3_T \
	  --n-runs 1 \
	  --problems $(HARD_PROBLEMS) \
	  --no-judge

run: ## Real run on Ollama. 5 problems × 4 conditions × N=2.
	$(PY) -m lib.run --config $(CONFIG) \
	  --conditions C1_NT,C1_T,C3_NT,C3_T \
	  --n-runs 2 \
	  --problems $(HARD_PROBLEMS) \
	  --no-judge
	$(MAKE) analyze

analyze: ## Aggregate the LATEST run's runs.jsonl → summary.md + charts.
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
