from ..puzzle import Puzzle
from .dfs_solver import solve_with_dfs
from .types import PathEdge, SolveResult, SolverName
from .validation import SolutionValidationReport, validate_solution
from .z3_solver import (
    PuzzleUnsolvableError,
    PuzzleValidationError,
    SolveTimeoutError,
    SolverInvariantError,
    SolverUnknownError,
    check_uniqueness_with_z3,
    solve_with_z3,
)

SOLVER_CHOICES: tuple[SolverName, ...] = ("z3", "dfs")


def solve_puzzle(puzzle: Puzzle, *, solver: SolverName = "z3", timeout_ms: int | None = 30_000) -> SolveResult:
    if solver == "z3":
        return solve_with_z3(puzzle, timeout_ms=timeout_ms)
    if solver == "dfs":
        return solve_with_dfs(puzzle, timeout_ms=timeout_ms)
    raise ValueError(f"Unknown solver {solver!r}. Choose one of: {', '.join(SOLVER_CHOICES)}")


__all__ = [
    "PathEdge",
    "PuzzleUnsolvableError",
    "PuzzleValidationError",
    "SolveResult",
    "SolveTimeoutError",
    "SolverName",
    "SolverInvariantError",
    "SolverUnknownError",
    "SolutionValidationReport",
    "SOLVER_CHOICES",
    "check_uniqueness_with_z3",
    "solve_puzzle",
    "solve_with_dfs",
    "solve_with_z3",
    "validate_solution",
]



