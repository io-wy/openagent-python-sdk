from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


app = FastAPI(title="TODO API Example")


class TodoCreate(BaseModel):
    title: str
    completed: bool = False


class Todo(TodoCreate):
    id: int


todos: list[Todo] = []


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/todos", response_model=list[Todo])
def list_todos() -> list[Todo]:
    return todos


@app.post("/todos", response_model=Todo, status_code=201)
def create_todo(payload: TodoCreate) -> Todo:
    todo = Todo(id=len(todos) + 1, **payload.model_dump())
    todos.append(todo)
    return todo


@app.get("/todos/{todo_id}", response_model=Todo)
def get_todo(todo_id: int) -> Todo:
    for todo in todos:
        if todo.id == todo_id:
            return todo
    raise HTTPException(status_code=404, detail="Todo not found")


@app.put("/todos/{todo_id}", response_model=Todo)
def update_todo(todo_id: int, payload: TodoCreate) -> Todo:
    for idx, todo in enumerate(todos):
        if todo.id == todo_id:
            updated = Todo(id=todo_id, **payload.model_dump())
            todos[idx] = updated
            return updated
    raise HTTPException(status_code=404, detail="Todo not found")


@app.delete("/todos/{todo_id}", status_code=204)
def delete_todo(todo_id: int) -> None:
    for idx, todo in enumerate(todos):
        if todo.id == todo_id:
            todos.pop(idx)
            return None
    raise HTTPException(status_code=404, detail="Todo not found")
