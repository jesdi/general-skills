"""The names of one run: its ticket branches, its recovery ref and its lock file.

Each name carries the PR branch as one name component, so runs on other PR branches share none of them. One
component, because git refs are a tree: `ticket/feat/01` and `ticket/feat/01/02` cannot both exist. `%` and `/` are
escaped, so two PR branches never give the same component: `feat/x` gives `feat%2Fx`.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Names:
    pr: str  # the PR branch

    @property
    def _key(self) -> str:
        return self.pr.replace("%", "%25").replace("/", "%2F")

    def ticket(self, number: str) -> str:
        """The branch of ticket `number`."""
        return f"ticket/{self._key}/{number}"

    @property
    def recovery(self) -> str:
        """The ref that holds the PR tip before the tidy."""
        return f"refs/implement-spec/{self._key}/recovery"

    def lock_file(self, common_dir: Path) -> Path:
        """The file one driver per PR branch locks, in the git common directory that every worktree shares."""
        return common_dir / "implement-spec" / f"{self._key}.lock"
