"""Review and safely apply a complete set of generated instruction files."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Mapping

from harness_gen.artifacts import GeneratedFile, GenerationResult, ValidationStatus
from harness_gen.errors import (
    ArtifactManagerError,
    PartialArtifactWriteError,
    StaleArtifactReview,
    UnvalidatedArtifactsError,
    WriteApprovalDeclined,
    WriteApprovalRequired,
)
from harness_gen.targets import ArtifactKind
from harness_gen.validation import is_safe_output_path, validate_generated_files

_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


class _AppliedFileError(Exception):
    def __init__(self, path: str) -> None:
        self.path = path


class WriteAction(StrEnum):
    CREATE = "create"
    REPLACE = "replace"


@dataclass(frozen=True, slots=True)
class DestinationIdentity:
    path: str = field(repr=False)
    device: int
    inode: int
    mode: int


@dataclass(frozen=True, slots=True)
class DirectorySnapshot:
    path: str
    exists: bool
    device: int | None = None
    inode: int | None = None
    mode: int | None = None


@dataclass(frozen=True, slots=True)
class FileSnapshot:
    exists: bool
    device: int | None = None
    inode: int | None = None
    mode: int | None = None
    size: int | None = None
    modified_ns: int | None = None
    content_digest: str | None = None


@dataclass(frozen=True, slots=True)
class ReviewedArtifact:
    file: GeneratedFile
    action: WriteAction
    snapshot: FileSnapshot


@dataclass(frozen=True, slots=True)
class ArtifactReview:
    destination: Path = field(repr=False)
    destination_identity: DestinationIdentity
    items: tuple[ReviewedArtifact, ...]
    directories: tuple[DirectorySnapshot, ...]
    generation_fingerprint: str = field(repr=False)
    review_fingerprint: str = field(repr=False)

    @property
    def new_directories(self) -> tuple[str, ...]:
        return tuple(item.path for item in self.directories if not item.exists)


@dataclass(frozen=True, slots=True)
class WriteApproval:
    review_fingerprint: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class WrittenArtifact:
    path: str
    action: WriteAction


@dataclass(frozen=True, slots=True)
class WriteResult:
    destination: Path = field(repr=False)
    written: tuple[WrittenArtifact, ...]
    created_directories: tuple[str, ...]


def prepare_artifact_review(
    destination: Path,
    generation: GenerationResult,
) -> ArtifactReview:
    """Snapshot the complete create-or-replace set without writing files."""
    if (
        not generation.writable
        or generation.validation.status is not ValidationStatus.PASS
        or validate_generated_files(generation.plan, generation.files).status
        is not ValidationStatus.PASS
    ):
        raise UnvalidatedArtifactsError()
    if not generation.files:
        raise UnvalidatedArtifactsError()
    if any(not is_safe_output_path(item.path) for item in generation.files):
        raise ArtifactManagerError("A generated file has an unsafe output path.")

    resolved_destination, root_fd, identity = _open_destination(destination)
    try:
        directory_paths = sorted(
            {
                directory
                for item in generation.files
                for directory in _parent_directories(item.path)
            },
            key=lambda value: (value.count("/"), value),
        )
        directories = tuple(
            _snapshot_directory(root_fd, path) for path in directory_paths
        )
        items: list[ReviewedArtifact] = []
        for file in generation.files:
            snapshot = _snapshot_file(root_fd, file.path)
            action = WriteAction.REPLACE if snapshot.exists else WriteAction.CREATE
            items.append(
                ReviewedArtifact(
                    file=file,
                    action=action,
                    snapshot=snapshot,
                )
            )

        generation_fingerprint = _generation_fingerprint(generation)
        review_fingerprint = _review_fingerprint(
            identity,
            directories,
            tuple(items),
            generation_fingerprint,
        )
        return ArtifactReview(
            destination=resolved_destination,
            destination_identity=identity,
            items=tuple(items),
            directories=directories,
            generation_fingerprint=generation_fingerprint,
            review_fingerprint=review_fingerprint,
        )
    except ArtifactManagerError:
        raise
    except OSError:
        raise ArtifactManagerError(
            "The selected project directory or an output path could not be inspected."
        ) from None
    finally:
        os.close(root_fd)


def format_artifact_review(review: ArtifactReview) -> tuple[str, ...]:
    """Return the directory and full create-or-replace set for a prompt."""
    lines = [f"Project directory: {review.destination}"]
    for item in review.items:
        lines.append(
            f"{item.action.value.title()}: {item.file.path} - {item.file.purpose}"
        )
    if review.new_directories:
        lines.append(
            "New directories: " + ", ".join(review.new_directories)
        )
    return tuple(lines)


def record_write_approval(
    review: ArtifactReview,
    approved: bool,
) -> WriteApproval:
    if not approved:
        raise WriteApprovalDeclined()
    if not _review_is_intact(review):
        raise StaleArtifactReview()
    return WriteApproval(review.review_fingerprint)


def apply_artifact_review(
    review: ArtifactReview,
    current_generation: GenerationResult,
    approval: WriteApproval | None,
) -> WriteResult:
    """Apply the saved snapshot after approval and abort on stale inputs."""
    if (
        approval is None
        or approval.review_fingerprint != review.review_fingerprint
        or not _review_is_intact(review)
    ):
        raise WriteApprovalRequired()
    if (
        not current_generation.writable
        or _generation_fingerprint(current_generation)
        != review.generation_fingerprint
        or validate_generated_files(
            current_generation.plan, current_generation.files
        ).status
        is not ValidationStatus.PASS
    ):
        raise StaleArtifactReview(tuple(item.file.path for item in review.items))

    try:
        root_fd = _open_reviewed_destination(review)
    except OSError:
        raise StaleArtifactReview() from None

    written: list[WrittenArtifact] = []
    created_directories: list[str] = []
    expected_directories = {
        item.path: item for item in review.directories
    }
    expected_files = {
        item.file.path: item.snapshot for item in review.items
    }
    try:
        _verify_snapshots(
            root_fd, review, expected_directories, expected_files
        )
        for directory in review.directories:
            if directory.exists:
                continue
            _verify_snapshots(
                root_fd, review, expected_directories, expected_files
            )
            _create_reviewed_directory(root_fd, directory.path)
            created_directories.append(directory.path)
            expected_directories[directory.path] = _snapshot_directory(
                root_fd, directory.path
            )

        for item in review.items:
            try:
                _verify_snapshots(
                    root_fd, review, expected_directories, expected_files
                )
                _write_reviewed_file(root_fd, item)
                written.append(
                    WrittenArtifact(path=item.file.path, action=item.action)
                )
                written_snapshot = _snapshot_file(root_fd, item.file.path)
                expected_content = hashlib.sha256(
                    item.file.content.encode("utf-8")
                ).hexdigest()
                expected_mode = (
                    item.snapshot.mode if item.snapshot.exists else 0o600
                )
                if (
                    not written_snapshot.exists
                    or written_snapshot.content_digest != expected_content
                    or written_snapshot.mode != expected_mode
                ):
                    raise StaleArtifactReview((item.file.path,))
                expected_files[item.file.path] = written_snapshot
            except StaleArtifactReview:
                if written or created_directories:
                    raise PartialArtifactWriteError(
                        tuple(item.path for item in written),
                        item.file.path,
                        tuple(created_directories),
                    ) from None
                raise
            except _AppliedFileError as error:
                written.append(
                    WrittenArtifact(path=error.path, action=item.action)
                )
                raise PartialArtifactWriteError(
                    tuple(saved.path for saved in written),
                    error.path,
                    tuple(created_directories),
                ) from None
            except (ArtifactManagerError, OSError) as error:
                if written or created_directories:
                    raise PartialArtifactWriteError(
                        tuple(saved.path for saved in written),
                        item.file.path,
                        tuple(created_directories),
                    ) from None
                if isinstance(error, ArtifactManagerError):
                    raise
                raise ArtifactManagerError(
                    "The approved file could not be written. Check project permissions."
                ) from None

        _verify_snapshots(root_fd, review, expected_directories, expected_files)
        return WriteResult(
            destination=review.destination,
            written=tuple(written),
            created_directories=tuple(created_directories),
        )
    except (ArtifactManagerError, OSError) as error:
        if isinstance(error, PartialArtifactWriteError):
            raise
        if written or created_directories:
            failed_path = (
                error.changed_paths[0]
                if isinstance(error, StaleArtifactReview) and error.changed_paths
                else review.items[min(len(written), len(review.items) - 1)].file.path
            )
            raise PartialArtifactWriteError(
                tuple(item.path for item in written),
                failed_path,
                tuple(created_directories),
            ) from None
        if isinstance(error, ArtifactManagerError):
            raise
        raise ArtifactManagerError(
            "The approved files could not be written. Check project permissions."
        ) from None
    finally:
        os.close(root_fd)


def _open_destination(destination: Path) -> tuple[Path, int, DestinationIdentity]:
    selected = Path(destination).expanduser().absolute()
    try:
        selected_stat = selected.lstat()
    except OSError:
        raise ArtifactManagerError(
            "Select an existing project directory that the application can access."
        ) from None
    if stat.S_ISLNK(selected_stat.st_mode):
        raise ArtifactManagerError(
            "The selected project directory cannot be a symbolic link."
        )
    if not stat.S_ISDIR(selected_stat.st_mode):
        raise ArtifactManagerError("The selected project path is not a directory.")

    try:
        resolved = selected.resolve(strict=True)
    except OSError:
        raise ArtifactManagerError(
            "The selected project directory could not be resolved safely."
        ) from None
    try:
        current_selected_stat = selected.lstat()
    except OSError:
        raise ArtifactManagerError(
            "The selected project directory changed during inspection."
        ) from None
    if (
        stat.S_ISLNK(current_selected_stat.st_mode)
        or current_selected_stat.st_dev != selected_stat.st_dev
        or current_selected_stat.st_ino != selected_stat.st_ino
    ):
        raise ArtifactManagerError(
            "The selected project directory changed during inspection."
        )
    try:
        root_fd = os.open(resolved, _DIRECTORY_FLAGS)
    except OSError:
        raise ArtifactManagerError(
            "The selected project directory could not be opened safely."
        ) from None
    root_stat = os.fstat(root_fd)
    if (
        not stat.S_ISDIR(root_stat.st_mode)
        or root_stat.st_dev != current_selected_stat.st_dev
        or root_stat.st_ino != current_selected_stat.st_ino
    ):
        os.close(root_fd)
        raise ArtifactManagerError(
            "The selected project directory changed during inspection."
        )
    return (
        resolved,
        root_fd,
        DestinationIdentity(
            str(resolved),
            root_stat.st_dev,
            root_stat.st_ino,
            stat.S_IMODE(root_stat.st_mode),
        ),
    )


def _open_reviewed_destination(review: ArtifactReview) -> int:
    root_fd = os.open(review.destination, _DIRECTORY_FLAGS)
    root_stat = os.fstat(root_fd)
    identity = review.destination_identity
    if (
        str(review.destination) != identity.path
        or root_stat.st_dev != identity.device
        or root_stat.st_ino != identity.inode
        or stat.S_IMODE(root_stat.st_mode) != identity.mode
        or not stat.S_ISDIR(root_stat.st_mode)
    ):
        os.close(root_fd)
        raise StaleArtifactReview()
    return root_fd


def _parent_directories(relative_path: str) -> tuple[str, ...]:
    parts = relative_path.split("/")[:-1]
    return tuple("/".join(parts[:index]) for index in range(1, len(parts) + 1))


def _open_directory_chain(root_fd: int, parts: tuple[str, ...]) -> int | None:
    current_fd = os.dup(root_fd)
    for part in parts:
        try:
            entry_stat = os.stat(part, dir_fd=current_fd, follow_symlinks=False)
        except FileNotFoundError:
            os.close(current_fd)
            return None
        if stat.S_ISLNK(entry_stat.st_mode):
            os.close(current_fd)
            raise ArtifactManagerError(
                "Symbolic links are not allowed in generated output paths."
            )
        if not stat.S_ISDIR(entry_stat.st_mode):
            os.close(current_fd)
            raise ArtifactManagerError(
                "A generated output directory conflicts with an existing file."
            )
        next_fd = os.open(part, _DIRECTORY_FLAGS, dir_fd=current_fd)
        opened_stat = os.fstat(next_fd)
        if (
            opened_stat.st_dev != entry_stat.st_dev
            or opened_stat.st_ino != entry_stat.st_ino
        ):
            os.close(next_fd)
            os.close(current_fd)
            raise StaleArtifactReview()
        os.close(current_fd)
        current_fd = next_fd
    return current_fd


def _snapshot_directory(root_fd: int, relative_path: str) -> DirectorySnapshot:
    parts = tuple(relative_path.split("/"))
    directory_fd = _open_directory_chain(root_fd, parts)
    if directory_fd is None:
        return DirectorySnapshot(relative_path, False)
    try:
        directory_stat = os.fstat(directory_fd)
        return DirectorySnapshot(
            relative_path,
            True,
            directory_stat.st_dev,
            directory_stat.st_ino,
            stat.S_IMODE(directory_stat.st_mode),
        )
    finally:
        os.close(directory_fd)


def _snapshot_file(root_fd: int, relative_path: str) -> FileSnapshot:
    parts = relative_path.split("/")
    parent_fd = _open_directory_chain(root_fd, tuple(parts[:-1]))
    if parent_fd is None:
        return FileSnapshot(False)
    name = parts[-1]
    try:
        try:
            entry_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return FileSnapshot(False)
        if stat.S_ISLNK(entry_stat.st_mode):
            raise ArtifactManagerError(
                "Symbolic links are not allowed for generated output files."
            )
        if not stat.S_ISREG(entry_stat.st_mode):
            raise ArtifactManagerError(
                "A generated output path conflicts with a non-file item."
            )

        descriptor = os.open(
            name,
            os.O_RDONLY | _NOFOLLOW,
            dir_fd=parent_fd,
        )
        try:
            opened_stat = os.fstat(descriptor)
            if (
                opened_stat.st_dev != entry_stat.st_dev
                or opened_stat.st_ino != entry_stat.st_ino
            ):
                raise StaleArtifactReview((relative_path,))
            digest = hashlib.sha256()
            while chunk := os.read(descriptor, 1024 * 1024):
                digest.update(chunk)
            final_stat = os.fstat(descriptor)
            if (
                final_stat.st_size != opened_stat.st_size
                or final_stat.st_mtime_ns != opened_stat.st_mtime_ns
                or final_stat.st_ino != opened_stat.st_ino
            ):
                raise StaleArtifactReview((relative_path,))
            return FileSnapshot(
                True,
                opened_stat.st_dev,
                opened_stat.st_ino,
                stat.S_IMODE(opened_stat.st_mode),
                opened_stat.st_size,
                opened_stat.st_mtime_ns,
                digest.hexdigest(),
            )
        finally:
            os.close(descriptor)
    finally:
        os.close(parent_fd)


def _verify_destination_path(root_fd: int, review: ArtifactReview) -> None:
    identity = review.destination_identity
    try:
        path_stat = os.stat(review.destination, follow_symlinks=False)
    except OSError:
        raise StaleArtifactReview() from None
    opened_stat = os.fstat(root_fd)
    if (
        stat.S_ISLNK(path_stat.st_mode)
        or not stat.S_ISDIR(path_stat.st_mode)
        or path_stat.st_dev != identity.device
        or path_stat.st_ino != identity.inode
        or stat.S_IMODE(path_stat.st_mode) != identity.mode
        or opened_stat.st_dev != identity.device
        or opened_stat.st_ino != identity.inode
        or stat.S_IMODE(opened_stat.st_mode) != identity.mode
    ):
        raise StaleArtifactReview()


def _verify_snapshots(
    root_fd: int,
    review: ArtifactReview,
    expected_directories: dict[str, DirectorySnapshot],
    expected_files: dict[str, FileSnapshot],
) -> None:
    _verify_destination_path(root_fd, review)
    changed: list[str] = []
    for path, expected in expected_directories.items():
        if _snapshot_directory(root_fd, path) != expected:
            changed.append(path)
    for path, expected in expected_files.items():
        if _snapshot_file(root_fd, path) != expected:
            changed.append(path)
    if changed:
        raise StaleArtifactReview(tuple(changed))


def _create_reviewed_directory(root_fd: int, relative_path: str) -> None:
    parts = relative_path.split("/")
    parent_fd = _open_directory_chain(root_fd, tuple(parts[:-1]))
    if parent_fd is None:
        raise StaleArtifactReview((relative_path,))
    try:
        try:
            os.mkdir(parts[-1], mode=0o700, dir_fd=parent_fd)
        except FileExistsError:
            raise StaleArtifactReview((relative_path,)) from None
    finally:
        os.close(parent_fd)


def _write_reviewed_file(root_fd: int, item: ReviewedArtifact) -> None:
    parts = item.file.path.split("/")
    parent_fd = _open_directory_chain(root_fd, tuple(parts[:-1]))
    if parent_fd is None:
        raise StaleArtifactReview((item.file.path,))

    temporary_name = f".harness-{secrets.token_hex(12)}.tmp"
    temporary_exists = False
    applied = False
    try:
        if _snapshot_file(root_fd, item.file.path) != item.snapshot:
            raise StaleArtifactReview((item.file.path,))

        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW,
            0o600,
            dir_fd=parent_fd,
        )
        temporary_exists = True
        try:
            remaining = memoryview(item.file.content.encode("utf-8"))
            while remaining:
                written = os.write(descriptor, remaining)
                if written == 0:
                    raise OSError("The generated file write did not make progress.")
                remaining = remaining[written:]
            os.fchmod(
                descriptor,
                item.snapshot.mode if item.snapshot.exists else 0o600,
            )
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

        if _snapshot_file(root_fd, item.file.path) != item.snapshot:
            raise StaleArtifactReview((item.file.path,))

        if item.action is WriteAction.CREATE:
            try:
                os.link(
                    temporary_name,
                    parts[-1],
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                    follow_symlinks=False,
                )
            except FileExistsError:
                raise StaleArtifactReview((item.file.path,)) from None
            applied = True
            try:
                os.unlink(temporary_name, dir_fd=parent_fd)
            except OSError:
                raise _AppliedFileError(item.file.path) from None
        else:
            os.replace(
                temporary_name,
                parts[-1],
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            applied = True
        temporary_exists = False
    finally:
        if temporary_exists:
            try:
                os.unlink(temporary_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
            except OSError:
                if applied:
                    raise _AppliedFileError(item.file.path) from None
                raise
        try:
            os.close(parent_fd)
        except OSError:
            if applied:
                raise _AppliedFileError(item.file.path) from None
            raise


def _generation_fingerprint(generation: GenerationResult) -> str:
    digest = hashlib.sha256()
    requirements_json = json.dumps(
        asdict(generation.plan.requirements),
        sort_keys=True,
        ensure_ascii=False,
    )
    digest.update(requirements_json.encode("utf-8"))
    digest.update(generation.plan.target.value.encode("utf-8"))
    for artifact in generation.plan.artifacts:
        artifact_data = (
            artifact.artifact_id,
            artifact.path,
            artifact.kind.value,
            artifact.purpose,
            artifact.path_pattern,
            artifact.frontmatter,
        )
        digest.update(json.dumps(artifact_data, ensure_ascii=False).encode("utf-8"))
    for file in generation.files:
        file_data = (
            file.artifact_id,
            file.path,
            file.kind.value,
            file.purpose,
            file.content,
        )
        digest.update(json.dumps(file_data, ensure_ascii=False).encode("utf-8"))
    digest.update(generation.validation.status.value.encode("ascii"))
    return digest.hexdigest()


def _review_fingerprint(
    identity: DestinationIdentity,
    directories: tuple[DirectorySnapshot, ...],
    items: tuple[ReviewedArtifact, ...],
    generation_fingerprint: str,
) -> str:
    payload = {
        "destination": asdict(identity),
        "directories": [asdict(item) for item in directories],
        "items": [
            {
                "path": item.file.path,
                "action": item.action.value,
                "artifact_id": item.file.artifact_id,
                "kind": item.file.kind.value,
                "purpose": item.file.purpose,
                "content_digest": hashlib.sha256(
                    item.file.content.encode("utf-8")
                ).hexdigest(),
                "snapshot": asdict(item.snapshot),
            }
            for item in items
        ],
        "generation_fingerprint": generation_fingerprint,
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _review_is_intact(review: ArtifactReview) -> bool:
    return review.review_fingerprint == _review_fingerprint(
        review.destination_identity,
        review.directories,
        review.items,
        review.generation_fingerprint,
    ) and bool(review.generation_fingerprint)


def serialize_artifact_review(review: ArtifactReview) -> dict[str, object]:
    """Convert an approved-write snapshot to checkpoint-compatible values."""
    return {
        "destination": str(review.destination),
        "destination_identity": asdict(review.destination_identity),
        "items": [
            {
                "file": {
                    "artifact_id": item.file.artifact_id,
                    "path": item.file.path,
                    "kind": item.file.kind.value,
                    "purpose": item.file.purpose,
                    "content": item.file.content,
                },
                "action": item.action.value,
                "snapshot": asdict(item.snapshot),
            }
            for item in review.items
        ],
        "directories": [asdict(item) for item in review.directories],
        "generation_fingerprint": review.generation_fingerprint,
        "review_fingerprint": review.review_fingerprint,
    }


def deserialize_artifact_review(value: Mapping[str, object]) -> ArtifactReview:
    """Restore a review snapshot and reject incomplete or modified state."""
    try:
        identity_data = _checkpoint_mapping(value["destination_identity"])
        identity = DestinationIdentity(
            path=_checkpoint_string(identity_data["path"]),
            device=_checkpoint_integer(identity_data["device"]),
            inode=_checkpoint_integer(identity_data["inode"]),
            mode=_checkpoint_integer(identity_data["mode"]),
        )
        items = tuple(
            _reviewed_artifact_from_state(item)
            for item in _checkpoint_list(value["items"])
        )
        directories = tuple(
            _directory_snapshot_from_state(item)
            for item in _checkpoint_list(value["directories"])
        )
        review = ArtifactReview(
            destination=Path(_checkpoint_string(value["destination"])),
            destination_identity=identity,
            items=items,
            directories=directories,
            generation_fingerprint=_checkpoint_string(
                value["generation_fingerprint"]
            ),
            review_fingerprint=_checkpoint_string(value["review_fingerprint"]),
        )
    except (KeyError, TypeError, ValueError):
        raise StaleArtifactReview() from None
    if not _review_is_intact(review):
        raise StaleArtifactReview()
    return review


def _reviewed_artifact_from_state(value: object) -> ReviewedArtifact:
    data = _checkpoint_mapping(value)
    file_data = _checkpoint_mapping(data["file"])
    file = GeneratedFile(
        artifact_id=_checkpoint_string(file_data["artifact_id"]),
        path=_checkpoint_string(file_data["path"]),
        kind=ArtifactKind(_checkpoint_string(file_data["kind"])),
        purpose=_checkpoint_string(file_data["purpose"]),
        content=_checkpoint_string(file_data["content"]),
    )
    return ReviewedArtifact(
        file=file,
        action=WriteAction(_checkpoint_string(data["action"])),
        snapshot=_file_snapshot_from_state(data["snapshot"]),
    )


def _file_snapshot_from_state(value: object) -> FileSnapshot:
    data = _checkpoint_mapping(value)
    return FileSnapshot(
        exists=_checkpoint_boolean(data["exists"]),
        device=_checkpoint_optional_integer(data.get("device")),
        inode=_checkpoint_optional_integer(data.get("inode")),
        mode=_checkpoint_optional_integer(data.get("mode")),
        size=_checkpoint_optional_integer(data.get("size")),
        modified_ns=_checkpoint_optional_integer(data.get("modified_ns")),
        content_digest=_checkpoint_optional_string(data.get("content_digest")),
    )


def _directory_snapshot_from_state(value: object) -> DirectorySnapshot:
    data = _checkpoint_mapping(value)
    return DirectorySnapshot(
        path=_checkpoint_string(data["path"]),
        exists=_checkpoint_boolean(data["exists"]),
        device=_checkpoint_optional_integer(data.get("device")),
        inode=_checkpoint_optional_integer(data.get("inode")),
        mode=_checkpoint_optional_integer(data.get("mode")),
    )


def _checkpoint_mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(
        not isinstance(key, str) for key in value
    ):
        raise TypeError("Expected a string-keyed mapping.")
    return value


def _checkpoint_list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise TypeError("Expected a list.")
    return value


def _checkpoint_string(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("Expected a string.")
    return value


def _checkpoint_optional_string(value: object) -> str | None:
    return None if value is None else _checkpoint_string(value)


def _checkpoint_integer(value: object) -> int:
    if type(value) is not int:
        raise TypeError("Expected an integer.")
    return value


def _checkpoint_optional_integer(value: object) -> int | None:
    return None if value is None else _checkpoint_integer(value)


def _checkpoint_boolean(value: object) -> bool:
    if not isinstance(value, bool):
        raise TypeError("Expected a boolean.")
    return value
