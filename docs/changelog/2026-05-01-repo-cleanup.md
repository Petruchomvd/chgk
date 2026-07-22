# 2026-05-01 Repo Cleanup

- active `scraper/` code no longer imports `parse_date()` from `modules_pars/`
- `.gitignore` now excludes local coverage, runtime logs, generated studies, and research artifacts
- added `docs/repository-structure.md` as the canonical map of active, generated, and legacy areas
- moved legacy parser code and ad hoc root scripts under `archive/`
- moved benchmark id constants from repo root into `scripts/benchmark_ids.py`
