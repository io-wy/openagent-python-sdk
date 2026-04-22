from __future__ import annotations

from pydantic import BaseModel

from openagents.errors import ErrorDetails
from openagents.errors.exceptions import (
    OpenAgentsError,
    PatternError,
    ToolRateLimitError,
)
from openagents.interfaces.runtime import RunResult, StopReason


class UserProfile(BaseModel):
    name: str
    age: int


def test_run_result_is_generic_any_by_default():
    result: RunResult = RunResult(run_id="r1", final_output={"foo": 1})
    assert result.final_output == {"foo": 1}
    assert result.stop_reason is StopReason.COMPLETED


def test_run_result_generic_accepts_typed_final_output():
    profile = UserProfile(name="ada", age=33)
    typed: RunResult[UserProfile] = RunResult[UserProfile](
        run_id="r2",
        final_output=profile,
    )
    assert isinstance(typed.final_output, UserProfile)
    assert typed.final_output.name == "ada"


def test_run_result_generic_dumps_final_output():
    typed: RunResult[UserProfile] = RunResult[UserProfile](
        run_id="r3",
        final_output=UserProfile(name="lin", age=7),
    )
    dumped = typed.model_dump()
    assert dumped["final_output"] == {"name": "lin", "age": 7}


def test_error_details_from_openagents_error():
    exc = ToolRateLimitError("429", tool_name="api", retry_after_ms=3000, hint="slow down")
    details = ErrorDetails.from_exception(exc)
    assert details.code == "tool.rate_limit"
    assert details.message == "429"
    assert details.hint == "slow down"
    assert details.retryable is True
    assert details.context["retry_after_ms"] == 3000
    assert details.cause is None


def test_error_details_from_non_openagents_error():
    details = ErrorDetails.from_exception(ValueError("bad input"))
    assert details.code == "error.unknown"
    assert details.message == "bad input"
    assert details.retryable is False
    assert details.cause is None


def test_error_details_walks_cause_up_to_three_layers():
    root = ValueError("layer 3")
    mid = PatternError("layer 2")
    mid.__cause__ = root
    top = OpenAgentsError("layer 1")
    top.__cause__ = mid

    details = ErrorDetails.from_exception(top)
    assert details.message == "layer 1"
    assert details.cause is not None
    assert details.cause.code == "pattern.error"
    assert details.cause.cause is not None
    assert details.cause.cause.code == "error.unknown"
    assert details.cause.cause.cause is None  # cut at depth 3


def test_error_details_stops_at_depth_limit():
    deepest = OpenAgentsError("l5")
    l4 = OpenAgentsError("l4")
    l4.__cause__ = deepest
    l3 = OpenAgentsError("l3")
    l3.__cause__ = l4
    l2 = OpenAgentsError("l2")
    l2.__cause__ = l3
    l1 = OpenAgentsError("l1")
    l1.__cause__ = l2

    details = ErrorDetails.from_exception(l1)
    # depth 0 = l1, depth 1 = l2, depth 2 = l3, depth 3 = l4; l5 dropped
    assert details.cause.cause.cause.message == "l4"
    assert details.cause.cause.cause.cause is None


def test_error_details_cycle_safe():
    a = OpenAgentsError("a")
    a.__cause__ = a  # self-cycle
    details = ErrorDetails.from_exception(a)
    assert details.cause is None
