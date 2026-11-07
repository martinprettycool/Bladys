from pathlib import Path
import os
import tempfile
import uuid

from bladys.config import get_payment_proofs_dir

from fastapi import HTTPException, UploadFile, status


MAX_PAYMENT_PROOF_SIZE = 5 * 1024 * 1024
ALLOWED_PAYMENT_PROOF_CONTENT_TYPES = {"image/jpeg", "image/png"}


async def read_and_validate_payment_proof(file: UploadFile) -> bytes:
    """Read a proof and validate its declared content type and byte size."""
    if file.content_type not in ALLOWED_PAYMENT_PROOF_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Payment proof must have content type image/jpeg or image/png.",
        )

    content = await file.read(MAX_PAYMENT_PROOF_SIZE + 1)
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payment proof must not be empty.",
        )
    if len(content) > MAX_PAYMENT_PROOF_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Payment proof must not exceed 5 MB (5242880 bytes).",
        )
    return content



def save_payment_proof(image_bytes: bytes, content_type: str) -> str:
    """Store validated proof bytes and return a relative filesystem reference."""
    extensions = {"image/jpeg": ".jpg", "image/png": ".png"}
    try:
        extension = extensions[content_type]
    except KeyError:
        raise ValueError("Payment proof content type must be image/jpeg or image/png.") from None

    storage = get_payment_proofs_dir()
    if os.environ.get("APP_ENV", "development").lower() != "production":
        storage.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = storage / f"{uuid.uuid4()}{extension}"
    # Exclusive creation refuses existing files and symlinks; private on POSIX.
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(image_bytes)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return f"media/payment_proofs/{destination.name}"


def validate_payment_proofs_storage() -> None:
    """Fail startup if storage cannot actually create, write and remove a file."""
    storage = get_payment_proofs_dir()
    try:
        if os.environ.get("APP_ENV", "development").lower() != "production":
            storage.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryFile(dir=storage) as probe:
            probe.write(b"storage probe")
            probe.flush()
    except OSError as exc:
        raise RuntimeError("PAYMENT_PROOFS_DIR must be an existing writable directory") from exc


def _proof_path(proof_reference: str, *, strict: bool) -> Path:
    # Database references are logical legacy keys, never physical server paths.
    parts = proof_reference.split("/")
    if (len(parts) != 3 or parts[:2] != ["media", "payment_proofs"]
            or parts[2] in {"", ".", ".."} or "\\" in proof_reference
            or ":" in parts[2]):
        raise ValueError("Payment proof reference is outside the storage directory.")
    storage = get_payment_proofs_dir()
    candidate = (storage / parts[2]).resolve(strict=strict)
    if candidate.parent != storage or candidate.suffix not in {".jpg", ".png"}:
        raise ValueError("Payment proof reference is outside the storage directory.")
    return candidate


def delete_payment_proof(proof_reference: str) -> None:
    """Delete only a proof inside configured storage; tolerate missing files."""
    _proof_path(proof_reference, strict=False).unlink(missing_ok=True)


def resolve_payment_proof(proof_reference: str) -> Path:
    """Resolve a legacy reference against the configured private directory."""
    candidate = _proof_path(proof_reference, strict=True)
    if not candidate.is_file():
        raise ValueError("Payment proof reference is not a file.")
    return candidate
