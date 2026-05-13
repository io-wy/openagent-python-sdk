.PHONY: train test serve clean

train:
	uv run python -m src.train

test:
	uv run python -m pytest -q

serve:
	uv run uvicorn src.api:app --reload --port 8000

clean:
	if exist artifacts\model.pkl del artifacts\model.pkl
	if exist artifacts\metrics.json del artifacts\metrics.json
