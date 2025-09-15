from arcade_tdk import tool
from typing import Annotated


@tool(
    desc="Greet a person",
)
def greet(name: Annotated[str, "The name to greet"]) -> Annotated[str, "The greeting"]:
    return f"Hello, {name}!"

