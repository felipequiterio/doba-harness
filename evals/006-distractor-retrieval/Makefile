# 006 — Distractor retrieval: does the right card survive being buried in wrong ones?
#
#   make smoke — stub, no Ollama, <1s
#   make quick — 3 problems × {D0,D2,D4,D4_select} × N=3 = 36 cells (~10 min)
#   make run   — the breaking-point sweep: clean baseline + high load (D8) +
#                NEAR-MISS cards. 3 problems × {D0,D8,NM,NM8,NM8_select} × N=5
#                = 75 cells (~25 min). D0 included so the near-miss deltas resolve.

ROOT      := ..
PY        := cd $(ROOT) && uv run python
CONFIG    := $(abspath config.yaml)

# astro-016/027: card-essential (knowledge=absent). astro-004: single &
# knowledge=present (escape velocity) — every distractor is genuinely unneeded,
# so it cleanly measures pure distraction cost on a problem the model knows.
PROBLEMS  := astro-016,astro-027,astro-004

.PHONY: help smoke smoke-run quick run list-runs clean-latest

help:
	@awk 'BEGIN {FS = ":.*?##"} /^[a-zA-Z_-]+:.*?##/ {printf "  \033[36m%-13s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

smoke: ## Stub end-to-end (no model) — verifies distractor assembly + select wiring.
	$(PY) -m lib.run_distract --config $(CONFIG) --stub \
	  --problems $(PROBLEMS) --n-runs 1 --no-logs

quick: ## 3 problems × {D0,D2,D4,D4_select} × N=3 = 36 cells. Generic distractors, low load.
	$(PY) -m lib.run_distract --config $(CONFIG) \
	  --problems $(PROBLEMS) --n-runs 3

smoke-run: ## Stub check of the run conditions (high load + near-miss wiring).
	$(PY) -m lib.run_distract --config $(CONFIG) --stub \
	  --problems $(PROBLEMS) --conditions D0,D8,NM,NM8,NM8_select --n-runs 1 --no-logs

run: ## Breaking-point sweep: D0 baseline + D8 (high load) + NM/NM8/NM8_select (near-miss). N=5.
	$(PY) -m lib.run_distract --config $(CONFIG) \
	  --problems $(PROBLEMS) --conditions D0,D8,NM,NM8,NM8_select --n-runs 5

list-runs: ## List historical runs under results/.
	@find results -maxdepth 1 -mindepth 1 -type d -not -name '.*' -printf '%f\n' 2>/dev/null | sort

clean-latest: ## Wipe only the latest run dir.
	@target=$$(readlink results/latest 2>/dev/null); \
	if [ -n "$$target" ]; then rm -rf results/$$target results/latest; echo "wiped results/$$target"; \
	else echo "no latest run to wipe"; fi
