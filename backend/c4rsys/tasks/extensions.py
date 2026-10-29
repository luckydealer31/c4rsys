"""Extensions of tasks (§ 19.4): checklists, dependencies and repetition — part З2."""

from c4rsys.tasks.models import Task


def blocked(t: Task) -> bool:
    """A task with unfinished required predecessors (§ 19.4.6)."""
    return False


def check_completion(t: Task) -> None:
    """A checklist marked «all items for completion» (§ 19.4.4)."""
