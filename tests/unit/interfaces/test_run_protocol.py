from pydantic import BaseModel

from openagents.interfaces.runtime import RunBudget, RunRequest, RunUsage
from openagents.interfaces.tool import ToolExecutionSpec


class Foo(BaseModel):
    value: int


def test_run_request_output_type_defaults_none():
    req = RunRequest(agent_id="a", session_id="s", input_text="hi")
    assert req.output_type is None


def test_run_request_accepts_pydantic_output_type():
    req = RunRequest(
        agent_id="a",
        session_id="s",
        input_text="hi",
        output_type=Foo,
    )
    assert req.output_type is Foo


def test_run_budget_has_new_fields():
    b = RunBudget(max_validation_retries=5, max_cost_usd=1.5)
    assert b.max_validation_retries == 5
    assert b.max_cost_usd == 1.5

    b2 = RunBudget()
    assert b2.max_validation_retries == 3  # default
    assert b2.max_cost_usd is None


def test_run_usage_has_cost_and_cache_fields():
    u = RunUsage()
    assert u.input_tokens_cached == 0
    assert u.input_tokens_cache_creation == 0
    assert u.cost_usd is None
    assert u.cost_breakdown == {}


def test_tool_execution_spec_supports_streaming_defaults_false():
    spec = ToolExecutionSpec()
    assert spec.supports_streaming is False
