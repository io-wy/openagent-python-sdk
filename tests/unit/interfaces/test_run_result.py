from pydantic import BaseModel

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
