from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import openagents.errors as errors_pkg
import openagents.errors.exceptions as errors_mod
from openagents.errors.exceptions import (
    BudgetExhausted,
    ExecutionError,
    LLMError,
    ModelRetryError,
    OutputValidationError,
)


def test_openagents_error_with_context_returns_typed_instance():
    err_type = getattr(errors_mod, "MaxStepsExceeded")
    err = err_type("tool call limit").with_context(
        agent_id="assistant",
        session_id="demo",
        run_id="run-1",
    )

    assert isinstance(err, errors_mod.OpenAgentsError)
    assert err.agent_id == "assistant"
    assert err.session_id == "demo"
    assert err.run_id == "run-1"


def test_new_error_types_are_importable_from_package_surface():
    config_load_error = getattr(errors_pkg, "ConfigLoadError")
    agent_not_found_error = getattr(errors_mod, "AgentNotFoundError")
    output_validation_error = getattr(errors_pkg, "OutputValidationError")

    assert issubclass(config_load_error, errors_mod.OpenAgentsError)
    assert issubclass(agent_not_found_error, errors_mod.OpenAgentsError)
    assert issubclass(output_validation_error, errors_mod.OpenAgentsError)


def test_output_validation_error_is_execution_error():
    err = OutputValidationError(
        "schema mismatch",
        output_type=None,
        attempts=3,
    )
    assert isinstance(err, ExecutionError)
    assert err.attempts == 3
    assert err.output_type is None
    assert err.last_validation_error is None


def test_budget_exhausted_carries_kind_current_limit():
    err = BudgetExhausted("cost budget", kind="cost", current=1.25, limit=1.00)
    assert err.kind == "cost"
    assert err.current == pytest.approx(1.25)
    assert err.limit == pytest.approx(1.00)


def test_model_retry_error_carries_validation_error():
    err = ModelRetryError(
        "please fix: name missing",
        validation_error=None,
    )
    assert isinstance(err, LLMError)
    assert err.validation_error is None


def test_errors_module_imports_when_typing_self_is_unavailable():
    module_path = Path(__file__).resolve().parents[3] / "openagents" / "errors" / "exceptions.py"
    script = textwrap.dedent(
        f"""
        import importlib.abc
        import importlib.util
        import sys
        import types
        import typing as real_typing

        class _TypingShimLoader(importlib.abc.Loader):
            def create_module(self, spec):
                module = types.ModuleType("typing")
                for name in dir(real_typing):
                    if name != "Self":
                        setattr(module, name, getattr(real_typing, name))
                return module

            def exec_module(self, module):
                return None

        class _TypingShimFinder(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path, target=None):
                if fullname == "typing":
                    return importlib.util.spec_from_loader(fullname, _TypingShimLoader())
                return None

        sys.modules.pop("typing", None)
        sys.meta_path.insert(0, _TypingShimFinder())

        module_path = r"{module_path}"
        spec = importlib.util.spec_from_file_location("compat_exceptions", module_path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
