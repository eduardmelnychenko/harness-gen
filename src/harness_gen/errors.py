"""Application errors with safe, actionable messages."""


class HarnessError(Exception):
    """Base class for errors that can be reported to the user."""


class ConfigurationError(HarnessError):
    """The selected provider or application configuration is invalid."""


class RequirementsError(HarnessError):
    """A structured requirement is invalid or incomplete."""


class RequirementsIncompleteError(RequirementsError):
    """Requirements do not meet a workflow gate."""

    def __init__(self, blockers: tuple[str, ...]) -> None:
        self.blockers = blockers
        super().__init__("Resolve the required harness details before continuing.")


class UnsupportedTargetError(HarnessError):
    """The requested agent target is not supported."""


class UnsupportedArtifactError(HarnessError):
    """The selected target does not support a requested artifact."""


class ArtifactManagerError(HarnessError):
    """A project destination or approved artifact write is invalid."""


class SessionPersistenceError(HarnessError):
    """A workflow session could not be stored or recovered safely."""


class UnvalidatedArtifactsError(ArtifactManagerError):
    """Generated files did not pass every offline validation check."""

    def __init__(self) -> None:
        super().__init__(
            "Only artifacts with a passing validation result can be reviewed."
        )


class WriteApprovalDeclined(ArtifactManagerError):
    """The user did not approve the complete artifact write set."""

    def __init__(self) -> None:
        super().__init__("No files were written because approval was not granted.")


class WriteApprovalRequired(ArtifactManagerError):
    """The approval does not match the reviewed file set."""

    def __init__(self) -> None:
        super().__init__(
            "Approve the complete current create-or-replace set before writing."
        )


class StaleArtifactReview(ArtifactManagerError):
    """The destination, files, or generated draft changed after review."""

    def __init__(self, changed_paths: tuple[str, ...] = ()) -> None:
        self.changed_paths = changed_paths
        changed = (
            f" Changed paths: {', '.join(changed_paths)}."
            if changed_paths
            else ""
        )
        super().__init__(
            "The destination or generated files changed after review. "
            f"Review the full write set again before you apply it.{changed}"
        )


class PartialArtifactWriteError(ArtifactManagerError):
    """A file write failed after part of the approved set was applied."""

    def __init__(
        self,
        written_paths: tuple[str, ...],
        failed_path: str,
        created_directories: tuple[str, ...] = (),
    ) -> None:
        self.written_paths = written_paths
        self.failed_path = failed_path
        self.created_directories = created_directories
        written = ", ".join(written_paths) if written_paths else "none"
        directories = (
            f" New directories: {', '.join(created_directories)}."
            if created_directories
            else ""
        )
        super().__init__(
            f"Writing stopped at {failed_path}. Files already written: {written}."
            f"{directories} Inspect the project and review the write set again."
        )


class ProviderApprovalRequired(HarnessError):
    """External provider use is not approved for the current configuration."""

    def __init__(self) -> None:
        super().__init__(
            "User approval is required before sending data to this external provider."
        )


class ProviderRequestError(HarnessError):
    """A provider request failed without exposing provider response details."""

    def __init__(self, output_paths: tuple[str, ...] = ()) -> None:
        self.output_paths = output_paths
        affected = (
            f" Affected planned files: {', '.join(output_paths)}."
            if output_paths
            else ""
        )
        super().__init__(
            "The request to the configured model provider failed. "
            f"Check its availability and configuration, then try again.{affected}"
        )
