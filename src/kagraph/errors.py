class KaGraphError(Exception):
    """Base exception for KaGraph errors."""


class CycleError(KaGraphError):
    """Raised when graph validation finds an unsupported cycle."""


class NodeError(KaGraphError):
    """Raised when a node fails during execution."""


class NodeTimeoutError(NodeError):
    """Raised when a node attempt exceeds its configured timeout."""


class InvalidGraphError(KaGraphError):
    """Raised when graph structure is invalid."""


class InvalidUpdateError(KaGraphError):
    """Raised when a node returns an invalid state update."""


class GraphRecursionError(KaGraphError):
    """Raised when graph execution exceeds the recursion limit."""
