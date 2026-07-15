"""Deterministic read-only repository inventory and snapshot identity."""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import orjson

from infinitecontex.task_context.errors import InvalidRepositoryRootError, RepositoryNotFoundError
from infinitecontex.task_context.models import (
    InventoryClassification,
    InventoryEntry,
    RepositoryInventory,
    RepositorySnapshot,
    RepositoryType,
)
from infinitecontex.task_context.policy import RepositoryResolutionPolicy


@dataclass(frozen=True)
class GitRepositoryState:
    available: bool
    commit_hash: str | None = None
    branch: str | None = None
    remote_identity: str | None = None
    tracked_paths: tuple[str, ...] = ()
    untracked_paths: tuple[str, ...] = ()
    ignored_paths: tuple[str, ...] = ()
    staged_paths: tuple[str, ...] = ()
    modified_paths: tuple[str, ...] = ()


class GitStateProvider(Protocol):
    def inspect(self, root: Path) -> GitRepositoryState: ...


class SubprocessGitStateProvider:
    """Read-only Git adapter; task content never controls its fixed arguments."""

    def inspect(self, root: Path) -> GitRepositoryState:
        if not (root / ".git").exists():
            return GitRepositoryState(available=False)
        commit = self._text(root, ["rev-parse", "HEAD"]) or None
        branch = self._text(root, ["branch", "--show-current"]) or "detached"
        remote = self._text(root, ["config", "--get", "remote.origin.url"]) or None
        tracked = self._paths(root, ["ls-files", "-z"])
        untracked = self._paths(root, ["ls-files", "--others", "--exclude-standard", "-z"])
        ignored = self._paths(root, ["ls-files", "--others", "--ignored", "--exclude-standard", "-z"])
        status = self._text(root, ["status", "--porcelain=v1", "--untracked-files=all"])
        staged: set[str] = set()
        modified: set[str] = set()
        for line in status.splitlines():
            if len(line) < 4:
                continue
            left, right = line[0], line[1]
            value = line[3:].split(" -> ")[-1].replace("\\", "/")
            if left not in {" ", "?"}:
                staged.add(value)
            if right != " " or left == "?":
                modified.add(value)
        return GitRepositoryState(
            available=True,
            commit_hash=commit,
            branch=branch,
            remote_identity=remote,
            tracked_paths=tuple(sorted(set(tracked))),
            untracked_paths=tuple(sorted(set(untracked))),
            ignored_paths=tuple(sorted(set(ignored))),
            staged_paths=tuple(sorted(staged)),
            modified_paths=tuple(sorted(modified)),
        )

    @staticmethod
    def _run(root: Path, args: list[str]) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["git", *args],
            cwd=root,
            check=False,
            capture_output=True,
            timeout=30,
        )

    def _text(self, root: Path, args: list[str]) -> str:
        try:
            result = self._run(root, args)
        except (OSError, subprocess.SubprocessError):
            return ""
        return result.stdout.decode("utf-8", errors="replace").strip() if result.returncode == 0 else ""

    def _paths(self, root: Path, args: list[str]) -> tuple[str, ...]:
        try:
            result = self._run(root, args)
        except (OSError, subprocess.SubprocessError):
            return ()
        if result.returncode != 0:
            return ()
        return tuple(
            sorted(
                value.replace("\\", "/")
                for value in result.stdout.decode("utf-8", errors="surrogateescape").split("\0")
                if value
            )
        )


class RepositoryInventoryService:
    def __init__(
        self,
        policy: RepositoryResolutionPolicy | None = None,
        git_provider: GitStateProvider | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.policy = policy or RepositoryResolutionPolicy()
        self.git_provider = git_provider or SubprocessGitStateProvider()
        self.clock = clock or (lambda: datetime.now(UTC))

    def build(self, repository_root: Path) -> RepositoryInventory:
        root = self._validate_root(repository_root)
        git = self.git_provider.inspect(root)
        if git.available:
            candidates = [(path, True, False) for path in git.tracked_paths]
            if self.policy.include_permitted_untracked_files:
                candidates.extend((path, False, True) for path in git.untracked_paths)
        else:
            candidates = [(path, False, True) for path in self._filesystem_paths(root)]
        deduplicated = {(value.replace("\\", "/"), tracked, untracked) for value, tracked, untracked in candidates}
        ordered = sorted(deduplicated, key=lambda item: (self._case_key(item[0]), item[0], not item[1]))
        if len(ordered) > self.policy.maximum_inventory_files:
            raise InvalidRepositoryRootError(
                f"Repository inventory has {len(ordered)} files; the safe limit is "
                f"{self.policy.maximum_inventory_files}. Narrow the repository root."
            )
        entries = tuple(
            self._entry(root, path, tracked, untracked, git)
            for path, tracked, untracked in ordered
            if not self.is_policy_excluded(path)
        )
        return self.from_entries(root, entries, git, created_at=self.clock())

    def from_entries(
        self,
        repository_root: Path,
        entries: Iterable[InventoryEntry],
        git: GitRepositoryState | None = None,
        *,
        created_at: datetime | None = None,
    ) -> RepositoryInventory:
        root = repository_root.resolve(strict=False)
        state = git or GitRepositoryState(available=False)
        ordered = tuple(sorted(entries, key=lambda item: (self._case_key(item.path), item.path)))
        tracked = tuple(item for item in ordered if item.tracked)
        untracked = tuple(item for item in ordered if item.untracked)
        tracked_fp = _fingerprint_entries(tracked)
        untracked_fp = _fingerprint_entries(untracked)
        inventory_fp = _fingerprint_entries(ordered)
        repository_identity = _repository_identity(root, state)
        inventoried_paths = {item.path for item in ordered}
        staged_paths = tuple(sorted(path for path in state.staged_paths if path in inventoried_paths))
        modified_paths = tuple(sorted(path for path in state.modified_paths if path in inventoried_paths))
        semantic_payload = {
            "schema_version": 1,
            "repository_identity": repository_identity,
            "repository_type": "git" if state.available else "filesystem",
            "git_available": state.available,
            "commit_hash": state.commit_hash,
            "branch": state.branch,
            "dirty": bool(staged_paths or modified_paths or untracked),
            "staged_paths": staged_paths,
            "modified_paths": modified_paths,
            "tracked_file_state_fingerprint": tracked_fp,
            "permitted_untracked_state_fingerprint": untracked_fp,
            "ignore_policy_version": self.policy.ignore_policy_version,
            "path_case_policy": self.policy.path_case_policy,
            "symlink_policy": self.policy.symlink_policy,
            "inventory_fingerprint": inventory_fp,
        }
        semantic = _sha256(semantic_payload)
        snapshot = RepositorySnapshot(
            snapshot_id=f"repository-snapshot-{semantic[:24]}",
            repository_root_reference=str(root),
            repository_identity=repository_identity,
            repository_type=RepositoryType.GIT if state.available else RepositoryType.FILESYSTEM,
            git_available=state.available,
            commit_hash=state.commit_hash,
            branch=state.branch,
            dirty=bool(staged_paths or modified_paths or untracked),
            staged_paths=staged_paths,
            modified_paths=modified_paths,
            tracked_file_state_fingerprint=tracked_fp,
            permitted_untracked_state_fingerprint=untracked_fp,
            ignore_policy_version=self.policy.ignore_policy_version,
            path_case_policy=self.policy.path_case_policy,
            symlink_policy=self.policy.symlink_policy,
            inventory_fingerprint=inventory_fp,
            semantic_fingerprint=semantic,
            file_count=len(ordered),
            tracked_file_count=len(tracked),
            permitted_untracked_file_count=len(untracked),
            created_at=created_at or self.clock(),
            warnings=("Repository has uncommitted or permitted untracked content.",)
            if semantic_payload["dirty"]
            else (),
        )
        ignored = tuple(
            sorted(
                (path for path in state.ignored_paths if not self.is_policy_excluded(path)),
                key=lambda value: (self._case_key(value), value),
            )
        )[: self.policy.maximum_inventory_files]
        return RepositoryInventory(snapshot=snapshot, entries=ordered, ignored_paths=ignored)

    def is_policy_excluded(self, path: str) -> bool:
        normalized = path.replace("\\", "/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        segments = normalized.strip("/").split("/")
        excluded_names = {prefix.strip("/") for prefix in self.policy.excluded_prefixes}
        return any(segment in excluded_names for segment in segments) or any(
            normalized.endswith(suffix) for suffix in self.policy.excluded_suffixes
        )

    def _entry(
        self,
        root: Path,
        relative: str,
        tracked: bool,
        untracked: bool,
        git: GitRepositoryState,
    ) -> InventoryEntry:
        path = root / Path(relative)
        relative = relative.replace("\\", "/")
        if not path.exists():
            return InventoryEntry(
                path=relative,
                size_bytes=0,
                classification=InventoryClassification.MISSING,
                tracked=tracked,
                staged=relative in git.staged_paths,
                modified=True,
                untracked=untracked,
            )
        symlink = _is_reparse_or_symlink(path)
        if symlink:
            return InventoryEntry(
                path=relative,
                size_bytes=0,
                classification=InventoryClassification.UNSAFE_SYMLINK,
                tracked=tracked,
                staged=relative in git.staged_paths,
                modified=relative in git.modified_paths,
                untracked=untracked,
                symlink=True,
            )
        size = path.stat().st_size
        if size > self.policy.maximum_source_file_bytes:
            return InventoryEntry(
                path=relative,
                size_bytes=size,
                classification=InventoryClassification.TOO_LARGE,
                tracked=tracked,
                staged=relative in git.staged_paths,
                modified=relative in git.modified_paths,
                untracked=untracked,
            )
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if b"\0" in data[:8192]:
            classification = InventoryClassification.BINARY
        else:
            try:
                data.decode("utf-8")
                classification = InventoryClassification.TEXT
            except UnicodeDecodeError:
                classification = InventoryClassification.INVALID_ENCODING
        return InventoryEntry(
            path=relative,
            size_bytes=size,
            content_hash=digest,
            classification=classification,
            tracked=tracked,
            staged=relative in git.staged_paths,
            modified=relative in git.modified_paths,
            untracked=untracked,
        )

    def _filesystem_paths(self, root: Path) -> tuple[str, ...]:
        values: list[str] = []
        for directory, directory_names, file_names in os.walk(root, followlinks=False):
            base = Path(directory)
            kept: list[str] = []
            for name in sorted(directory_names):
                relative = (base / name).relative_to(root).as_posix()
                if not self.is_policy_excluded(relative) and not _is_reparse_or_symlink(base / name):
                    kept.append(name)
            directory_names[:] = kept
            for name in sorted(file_names):
                relative = (base / name).relative_to(root).as_posix()
                if not self.is_policy_excluded(relative):
                    values.append(relative)
        return tuple(values)

    def _case_key(self, value: str) -> str:
        return value.casefold() if self.policy.path_case_policy == "insensitive" else value

    @staticmethod
    def _validate_root(root: Path) -> Path:
        if not root.exists():
            raise RepositoryNotFoundError(f"Repository {root} does not exist; select an existing local directory")
        if _is_reparse_or_symlink(root):
            raise InvalidRepositoryRootError("Repository root cannot itself be a symbolic link or reparse point")
        resolved = root.resolve(strict=True)
        if not resolved.is_dir():
            raise InvalidRepositoryRootError(f"Repository root {root} is not a directory")
        return resolved


def _is_reparse_or_symlink(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = path.lstat().st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _repository_identity(root: Path, state: GitRepositoryState) -> str:
    stable = state.remote_identity or root.name
    return hashlib.sha256(stable.strip().casefold().encode("utf-8")).hexdigest()


def _fingerprint_entries(entries: Iterable[InventoryEntry]) -> str:
    return _sha256([entry.model_dump(mode="json") for entry in entries])


def _sha256(payload: object) -> str:
    return hashlib.sha256(orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)).hexdigest()
