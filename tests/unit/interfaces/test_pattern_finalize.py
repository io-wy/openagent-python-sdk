import pytest
from pydantic import BaseModel

from openagents.errors.exceptions import ModelRetryError
from openagents.interfaces.pattern import PatternPlugin


class UserProfile(BaseModel):
    name: str
    age: int


class _TestPattern(PatternPlugin):
    async def execute(self):  # pragma: no cover
        return None


@pytest.mark.asyncio
async def test_finalize_returns_raw_when_no_output_type():
    pattern = _TestPattern(config={})
    assert await pattern.finalize("hello", None) == "hello"
    assert await pattern.finalize({"x": 1}, None) == {"x": 1}


@pytest.mark.asyncio
async def test_finalize_validates_and_returns_model_instance():
    pattern = _TestPattern(config={})
    out = await pattern.finalize({"name": "a", "age": 1}, UserProfile)
    assert isinstance(out, UserProfile)
    assert out.age == 1


@pytest.mark.asyncio
async def test_finalize_raises_model_retry_error_on_invalid():
    pattern = _TestPattern(config={})
    with pytest.raises(ModelRetryError) as exc_info:
        await pattern.finalize({"name": "a"}, UserProfile)  # missing age
    assert exc_info.value.validation_error is not None
    assert "age" in str(exc_info.value)
