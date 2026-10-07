import ast
import pathlib


def test_every_complete_json_call_passes_data_class():
    calls = 0
    for f in pathlib.Path("app").glob("*.py"):
        for n in ast.walk(ast.parse(f.read_text())):
            if isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", "")) == "complete_json":
                calls += 1
                assert any(k.arg == "data_class" for k in n.keywords), f"{f}:{n.lineno}"
    assert calls >= 5  # the walk found the callers
