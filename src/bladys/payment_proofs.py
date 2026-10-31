from pathlib import Path
import uuid

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

    destination = Path("media") / "payment_proofs" / f"{uuid.uuid4()}{extension}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(image_bytes)
    return destination.as_posix()


def delete_payment_proof(proof_reference: str) -> None:
    """Delete a stored proof, refusing references outside the proof directory."""
    storage = (Path("media") / "payment_proofs").resolve()
    candidate = Path(proof_reference).resolve()
    if candidate.parent != storage:
        raise ValueError("Payment proof reference is outside the storage directory.")
    candidate.unlink(missing_ok=True)


def resolve_payment_proof(proof_reference: str) -> Path:
    """Resolve an existing image only within the local proof storage area."""
    storage = (Path("media") / "payment_proofs").resolve()
    candidate = Path(proof_reference).resolve(strict=True)
    if candidate.parent != storage or candidate.suffix not in {".jpg", ".png"}:
        raise ValueError("Invalid payment proof reference.")
    if not candidate.is_file():
        raise ValueError("Payment proof reference is not a file.")
    return candidate
