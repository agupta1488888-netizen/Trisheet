"""Supabase Storage client.

The only module that uploads a rendered artifact. Uses the same service role
key as `app.services.db`, which is why nothing reachable from the browser may
import it.

Degradation
    Storage is a sink, not a source. A report whose PDF could not be uploaded
    is a complete report with a missing download link, and the interface says
    so — it is never a failed report. Every function here returns a typed
    outcome rather than raising into the pipeline.

Public interface
    upload(report_id, ticker, kind, content) -> ArtifactRef
    is_configured() -> bool
"""

from __future__ import annotations

import datetime as dt
import logging

from app.config import (
    ARTIFACT_CONTENT_TYPES,
    ARTIFACT_PATH_TEMPLATE,
    STORAGE_BUCKET,
)
from app.models import ArtifactKind, ArtifactRef
from app.services import db

logger = logging.getLogger(__name__)

#: Said when there is nowhere to upload to. In the interface's voice, because
#: it reaches the reader as the reason a download link is absent.
NOT_CONFIGURED_REASON = (
    "File storage is not configured, so this report was not published for "
    "download. The figures below are unaffected."
)

UPLOAD_FAILED_REASON = (
    "This file was rendered but could not be uploaded. Try generating the "
    "report again."
)


def is_configured() -> bool:
    """True when there is somewhere to upload to."""
    return db.is_configured()


def storage_path(report_id: str, ticker: str, kind: ArtifactKind) -> str:
    """The object key an artifact is written to.

    Deterministic: re-running a report overwrites its own artifact rather than
    accumulating a new copy per attempt.
    """
    return ARTIFACT_PATH_TEMPLATE.format(
        report_id=report_id,
        ticker=ticker.upper().replace("/", "-").replace(".", "-"),
        date=dt.date.today().isoformat(),
        extension=str(kind),
    )


async def upload(
    report_id: str,
    ticker: str,
    kind: ArtifactKind,
    content: bytes,
) -> ArtifactRef:
    """Uploads one rendered artifact and returns a reference to it.

    Args:
        report_id: The report the artifact belongs to. Also its folder.
        ticker: Used in the filename so a download is recognisable.
        kind: PDF or XLSX. Decides the extension and the content type.
        content: The rendered bytes. Must be non-empty — an empty artifact is
            a rendering failure that the caller should have caught.

    Returns:
        An `ArtifactRef`. `url` is None and `unavailable_reason` is set when
        the upload could not happen; the size and content type are still
        reported, because the file was still rendered.
    """
    content_type = ARTIFACT_CONTENT_TYPES[str(kind)]
    path = storage_path(report_id, ticker, kind)
    now = dt.datetime.now(dt.UTC)

    if not content:
        return ArtifactRef(
            kind=kind,
            size_bytes=0,
            content_type=content_type,
            created_at=now,
            unavailable_reason=(
                "This file could not be rendered, so there is nothing to "
                "download."
            ),
        )

    if not is_configured():
        logger.info(
            "Storage is not configured; artifact kept in memory only",
            extra={"report_id": report_id, "kind": str(kind)},
        )
        return ArtifactRef(
            kind=kind,
            size_bytes=len(content),
            content_type=content_type,
            storage_path=path,
            created_at=now,
            unavailable_reason=NOT_CONFIGURED_REASON,
        )

    try:
        client = db.get_client()
        bucket = client.storage.from_(STORAGE_BUCKET)
        # `upsert` because a re-run of the same report must converge on one
        # file rather than collide with its own earlier upload.
        bucket.upload(
            path=path,
            file=content,
            file_options={
                "content-type": content_type,
                "upsert": "true",
                "cache-control": "3600",
            },
        )
        url = str(bucket.get_public_url(path))
    except Exception as cause:  # noqa: BLE001 — a sink never fails the report
        logger.error(
            "Artifact could not be uploaded",
            extra={
                "report_id": report_id,
                "kind": str(kind),
                "path": path,
                "error": str(cause),
            },
        )
        return ArtifactRef(
            kind=kind,
            size_bytes=len(content),
            content_type=content_type,
            storage_path=path,
            created_at=now,
            unavailable_reason=UPLOAD_FAILED_REASON,
        )

    logger.info(
        "Artifact uploaded",
        extra={
            "report_id": report_id,
            "kind": str(kind),
            "path": path,
            "bytes": len(content),
        },
    )
    return ArtifactRef(
        kind=kind,
        size_bytes=len(content),
        content_type=content_type,
        url=url,
        storage_path=path,
        created_at=now,
    )
