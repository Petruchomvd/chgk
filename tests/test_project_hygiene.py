from pathlib import Path

from scraper.date_utils import parse_russian_date


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_parse_russian_date_handles_common_pack_dates():
    assert parse_russian_date("5 января 2026 г.") == "2026-01-05"
    assert parse_russian_date("15 февраля 2024") == "2024-02-15"
    assert parse_russian_date(None) is None
    assert parse_russian_date("2026-01-05") is None


def test_active_scraper_no_longer_imports_legacy_modules_pars():
    src = (_project_root() / "scraper" / "pack_parser.py").read_text(encoding="utf-8")
    assert "modules_pars.utils" not in src


def test_benchmark_ids_live_under_scripts_package():
    root = _project_root()
    assert (root / "scripts" / "benchmark_ids.py").exists()
    assert not (root / "test_benchmark_ids.py").exists()


def test_benchmark_scripts_use_scripts_package_for_ids():
    root = _project_root()
    for rel_path in (
        "scripts/benchmark.py",
        "scripts/benchmark_groq.py",
        "scripts/compare_models.py",
    ):
        src = (root / rel_path).read_text(encoding="utf-8")
        assert "from scripts.benchmark_ids import BENCHMARK_IDS" in src
        assert "from test_benchmark_ids import BENCHMARK_IDS" not in src


def test_runtime_databases_are_not_delivered_through_git_lfs():
    root = _project_root()
    gitignore = (root / ".gitignore").read_text(encoding="utf-8")
    gitattributes = (root / ".gitattributes").read_text(encoding="utf-8")
    dashboard = (root / "dashboard" / "app.py").read_text(encoding="utf-8")

    assert "*.db" in gitignore
    assert "*.db-wal" in gitignore
    assert "*.db-shm" in gitignore
    assert "filter=lfs" not in gitattributes
    assert "git lfs pull" not in dashboard
    assert not (root / "packages.txt").exists()
    assert not (root / "render.yaml").exists()


def test_server_facing_tools_use_configured_database_paths():
    root = _project_root()
    expected_imports = {
        "dashboard/tournament.py": "from config import DB_PATH",
        "scripts/candidate_test_pool.py": "from config import DB_PATH as DB",
        "scripts/candidate_test_final.py": "from config import DB_PATH as DB",
        "scripts/candidate_test_select.py": "from config import DB_PATH as DB",
        "scripts/generate_team_reports.py": "from config import DB_PATH as DB",
        "database/training_db.py": "CHGK_TRAINING_DB_PATH",
    }

    for rel_path, marker in expected_imports.items():
        src = (root / rel_path).read_text(encoding="utf-8")
        assert marker in src
