from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from ..config import Settings
from ..domain import WorkbenchError


def merge_results(left: dict, right: dict) -> dict:
    merged = dict(left)
    for key, value in right.items():
        if key in merged and merged[key] != value:
            raise WorkbenchError(f"Conflicting duplicate activity result: {key}")
        merged[key] = value
    return merged


class CycleState(TypedDict):
    cycle_id: str
    results: Annotated[dict, merge_results]


def compile_workflow(settings: Settings, node_factory):
    builder = StateGraph(CycleState)
    parents = set()
    for step in settings.workflow.steps:
        builder.add_node(step.id, node_factory(step))
    for step in settings.workflow.steps:
        if not step.after:
            builder.add_edge(START, step.id)
        elif len(step.after) == 1:
            builder.add_edge(step.after[0], step.id)
        else:
            # This is an AND barrier, not several independent triggering edges.
            builder.add_edge(step.after, step.id)
        parents.update(step.after)
    for step in settings.workflow.steps:
        if step.id not in parents:
            builder.add_edge(step.id, END)
    return builder.compile()
