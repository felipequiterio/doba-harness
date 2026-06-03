# 001 — astrodynamics failure modes.
#
# This Makefile lives inside the experiment folder. It calls into the
# shared harness at evals/lib/ via `python -m lib.run --config ./config.yaml`.
# Shared targets (test, gt-check, etc.) live in ../Makefile.

ROOT      := ..
PY        := cd $(ROOT) && uv run python
CONFIG    := $(abspath config.yaml)

# Local model the README references — used by helper targets only.
LOCAL_MODEL := qwen3.5:4b

# Curated subset for `make quick`. See README for selection rationale.
QUICK_PROBLEMS := astro-001,astro-005,astro-006,astro-007,astro-016,astro-023,astro-027,astro-028,astro-029,astro-031

# CLI knobs forwarded to lib.run: e.g. `make run N=3 CONDITIONS=C1,C2,C3`.
N ?=
CONDITIONS ?=
PROBLEMS ?=
RUN_FLAGS :=
ifdef N
RUN_FLAGS += --n-runs $(N)
endif
ifdef CONDITIONS
RUN_FLAGS += --conditions $(CONDITIONS)
endif
ifdef PROBLEMS
RUN_FLAGS += --problems $(PROBLEMS)
endif

.PHONY: help smoke-tiny smoke probe probe-stub run run-cheap run-stub quick \
        analyze logs ollama-up ollama-pull clean

help:
	@awk 'BEGIN {FS = ":.*?##"} /^[a-zA-Z_-]+:.*?##/ {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# ---------------------------------------------------------------------------
# Local model setup
# ---------------------------------------------------------------------------
ollama-up: ## Background Ollama daemon (no-op if already running).
	@pgrep -f "ollama serve" >/dev/null || (ollama serve >/tmp/ollama.log 2>&1 & echo "started ollama (logs: /tmp/ollama.log)")
	@sleep 1
	@curl -s http://localhost:11434/api/tags >/dev/null || (echo "ollama not reachable" && exit 1)
	@echo "ollama OK"

ollama-pull: ## Pull the local model declared in this Makefile.
	ollama pull $(LOCAL_MODEL)

# ---------------------------------------------------------------------------
# Stub / smoke (no model calls)
# ---------------------------------------------------------------------------
smoke-tiny: ## Stub end-to-end on 1 problem × 1 run.
	$(PY) -m lib.run --config $(CONFIG) --stub --conditions C1,C2,C3,REF --n-runs 1 --problems astro-001

smoke: ## Stub end-to-end on the full dataset × 2 runs.
	$(PY) -m lib.run --config $(CONFIG) --stub --conditions C1,C2,C3,REF --n-runs 2
	$(PY) -m lib.analyze --config $(CONFIG)

# ---------------------------------------------------------------------------
# Real experiment (needs Ollama + frontier API key)
# ---------------------------------------------------------------------------
probe: ## Label each problem knowledge=present|absent (writes back to dataset JSON).
	$(PY) -m lib.probe --config $(CONFIG) --in-place

probe-stub: ## Probe with stub clients (no API keys).
	$(PY) -m lib.probe --config $(CONFIG) --stub

run: ## Full sweep. Use N=, CONDITIONS=, PROBLEMS= to narrow.
	$(PY) -m lib.run --config $(CONFIG) $(RUN_FLAGS)

run-stub: ## Same as run but stubbed.
	$(PY) -m lib.run --config $(CONFIG) --stub $(RUN_FLAGS)

run-cheap: ## N=1 per cell.
	$(PY) -m lib.run --config $(CONFIG) --n-runs 1 $(RUN_FLAGS)

quick: ## Real run, curated 10 problems × N=2 (~30 min).
	$(PY) -m lib.run --config $(CONFIG) --conditions C1,C2,C3,REF,REF_C3 --n-runs 2 --problems $(QUICK_PROBLEMS)
	$(PY) -m lib.analyze --config $(CONFIG)

analyze: ## Aggregate runs.jsonl → summary.md + charts.
	$(PY) -m lib.analyze --config $(CONFIG)

logs: ## Cat a random per-run log from the latest run (debug aid).
	@ls -1 results/latest/logs/*.log 2>/dev/null | shuf -n 1 | xargs -r -I{} sh -c 'echo "--- {} ---"; cat {}'

list-runs: ## List historical runs under results/ with timestamps + size.
	@find results -maxdepth 1 -mindepth 1 -type d -not -name '.*' -printf '%f\n' 2>/dev/null | sort

clean: ## Wipe this eval's outputs (keeps runs.jsonl history).
	@echo "use 'make clean-results' to wipe; 'make clean' is a no-op now to protect history."

clean-latest: ## Wipe only the latest run dir (keeps older history).
	@target=$$(readlink results/latest 2>/dev/null); \
	if [ -n "$$target" ]; then rm -rf results/$$target results/latest; echo "wiped results/$$target"; \
	else echo "no latest run to wipe"; fi

clean-all-runs: ## Wipe ALL historical runs. Asks for confirmation.
	@read -p "wipe ALL results under results/? [y/N] " ans && [ "$$ans" = "y" ] && \
	  find results -mindepth 1 -not -name '.gitkeep' -exec rm -rf {} + 2>/dev/null && echo "wiped" || echo "aborted"
