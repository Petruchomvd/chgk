import importlib.util
import sys
import types
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_scripts_analyze_imports_uppercase_analytics_modules(monkeypatch):
    pkg = types.ModuleType("Analytics")
    pkg.__path__ = []  # mark as package-like

    visualize = types.ModuleType("Analytics.visualize")
    report = types.ModuleType("Analytics.report")
    visualize.generate_all_charts = lambda: None
    report.generate_report = lambda: None

    monkeypatch.setitem(sys.modules, "Analytics", pkg)
    monkeypatch.setitem(sys.modules, "Analytics.visualize", visualize)
    monkeypatch.setitem(sys.modules, "Analytics.report", report)

    script_path = _project_root() / "scripts" / "analyze.py"
    spec = importlib.util.spec_from_file_location("scripts_analyze_under_test", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    assert module.generate_all_charts is visualize.generate_all_charts
    assert module.generate_report is report.generate_report


def test_analytics_modules_use_uppercase_imports():
    root = _project_root()

    report_src = (root / "Analytics" / "report.py").read_text(encoding="utf-8")
    visualize_src = (root / "Analytics" / "visualize.py").read_text(encoding="utf-8")

    assert "from Analytics.queries import" in report_src
    assert "from analytics.queries import" not in report_src
    assert "from Analytics.queries import" in visualize_src
    assert "from analytics.queries import" not in visualize_src
