"""Explicit project lifecycle transitions."""

from karlancer_agent.domain.enums import ProjectState


class InvalidStateTransition(ValueError):
    """Raised when a workflow attempts to skip or leave a terminal state."""


_VALID_TRANSITIONS: dict[ProjectState, frozenset[ProjectState]] = {
    ProjectState.DISCOVERED: frozenset({ProjectState.EXTRACT_PENDING}),
    ProjectState.EXTRACT_PENDING: frozenset(
        {ProjectState.EXTRACTED, ProjectState.RETRYABLE_ERROR, ProjectState.PAUSED_ERROR}
    ),
    ProjectState.EXTRACTED: frozenset(
        {ProjectState.REJECTED, ProjectState.NEEDS_REVIEW, ProjectState.ELIGIBLE}
    ),
    ProjectState.REJECTED: frozenset(),
    ProjectState.NEEDS_REVIEW: frozenset({ProjectState.REJECTED, ProjectState.ELIGIBLE}),
    ProjectState.ELIGIBLE: frozenset(
        {ProjectState.DRAFTED, ProjectState.RETRYABLE_ERROR, ProjectState.PAUSED_ERROR}
    ),
    ProjectState.DRAFTED: frozenset(
        {
            ProjectState.AWAITING_APPROVAL,
            ProjectState.SUBMIT_PENDING,
            ProjectState.NEEDS_REVIEW,
        }
    ),
    ProjectState.AWAITING_APPROVAL: frozenset(
        {ProjectState.DRAFTED, ProjectState.REJECTED, ProjectState.SUBMIT_PENDING}
    ),
    ProjectState.SUBMIT_PENDING: frozenset(
        {ProjectState.SUBMITTED, ProjectState.RETRYABLE_ERROR, ProjectState.PAUSED_ERROR}
    ),
    ProjectState.SUBMITTED: frozenset({ProjectState.REPLY_WATCH}),
    ProjectState.REPLY_WATCH: frozenset(
        {
            ProjectState.REPLY_NOTIFIED,
            ProjectState.RETRYABLE_ERROR,
            ProjectState.PAUSED_ERROR,
        }
    ),
    ProjectState.REPLY_NOTIFIED: frozenset({ProjectState.HUMAN_HANDOFF}),
    ProjectState.HUMAN_HANDOFF: frozenset(),
    ProjectState.RETRYABLE_ERROR: frozenset(
        {
            ProjectState.EXTRACT_PENDING,
            ProjectState.ELIGIBLE,
            ProjectState.SUBMIT_PENDING,
            ProjectState.REPLY_WATCH,
            ProjectState.PAUSED_ERROR,
        }
    ),
    ProjectState.PAUSED_ERROR: frozenset(),
}


def transition(current: ProjectState, target: ProjectState) -> ProjectState:
    """Validate and return an explicitly permitted target state."""

    if target not in _VALID_TRANSITIONS[current]:
        message = f"Invalid project state transition: {current.value} -> {target.value}"
        raise InvalidStateTransition(message)
    return target
