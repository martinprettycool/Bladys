import asyncio
from io import BytesIO
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers

from bladys.payment_proofs import read_and_validate_payment_proof, resolve_payment_proof


@pytest.mark.parametrize("content_type", ["image/jpeg", "image/png"])
def test_accepted_upload_returns_original_bytes(content_type):
    content = b"original uploaded bytes\x00\xff"
    file = UploadFile(BytesIO(content), headers=Headers({"content-type": content_type}))

    result = asyncio.run(read_and_validate_payment_proof(file))

    assert result == content
    assert isinstance(result, bytes)


@pytest.mark.parametrize("content_type", ["text/plain", "image/gif", "application/octet-stream", None])
def test_unsupported_content_type_is_rejected_before_read(content_type):
    headers = Headers({"content-type": content_type}) if content_type else Headers()
    file = UploadFile(BytesIO(b"content"), headers=headers)
    file.read = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(read_and_validate_payment_proof(file))

    assert exc.value.status_code == 415
    file.read.assert_not_awaited()


def test_empty_upload_is_rejected():
    file = UploadFile(BytesIO(b""), headers=Headers({"content-type": "image/png"}))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(read_and_validate_payment_proof(file))

    assert exc.value.status_code == 400
    assert "empty" in exc.value.detail


def test_upload_larger_than_five_mb_is_rejected():
    file = UploadFile(
        BytesIO(b"x" * (5 * 1024 * 1024 + 1)),
        headers=Headers({"content-type": "image/jpeg"}),
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(read_and_validate_payment_proof(file))

    assert exc.value.status_code == 413
    assert "5 MB" in exc.value.detail


def test_upload_exactly_five_mb_is_accepted():
    content = b"x" * (5 * 1024 * 1024)
    file = UploadFile(BytesIO(content), headers=Headers({"content-type": "image/png"}))

    assert asyncio.run(read_and_validate_payment_proof(file)) == content


def test_read_uses_async_api_with_bounded_size():
    file = UploadFile(BytesIO(), headers=Headers({"content-type": "image/png"}))
    file.read = AsyncMock(return_value=b"content")

    assert asyncio.run(read_and_validate_payment_proof(file)) == b"content"
    file.read.assert_awaited_once_with(5 * 1024 * 1024 + 1)


@pytest.mark.parametrize(
    ("content_type", "extension"),
    [("image/jpeg", ".jpg"), ("image/png", ".png")],
)
def test_save_payment_proof_stores_original_bytes(tmp_path, monkeypatch, content_type, extension):
    from pathlib import Path
    from uuid import UUID

    from bladys.payment_proofs import save_payment_proof

    monkeypatch.chdir(tmp_path)
    content = b"original proof bytes\x00\xff"
    assert not (tmp_path / "media").exists()

    reference = save_payment_proof(content, content_type)

    assert isinstance(reference, str)
    destination = Path(reference)
    assert destination.parent == Path("media") / "payment_proofs"
    assert destination.suffix == extension
    assert UUID(destination.stem).version == 4
    assert reference == destination.as_posix()
    assert (tmp_path / destination).is_file()
    assert (tmp_path / destination).read_bytes() == content


def test_save_payment_proof_generates_unique_filenames(tmp_path, monkeypatch):
    from pathlib import Path

    from bladys.payment_proofs import save_payment_proof

    monkeypatch.chdir(tmp_path)

    first = save_payment_proof(b"first proof", "image/jpeg")
    second = save_payment_proof(b"second proof", "image/jpeg")

    assert first != second
    assert resolve_payment_proof(first).read_bytes() == b"first proof"
    assert resolve_payment_proof(second).read_bytes() == b"second proof"


def test_save_payment_proof_rejects_unsupported_type_without_writing(tmp_path, monkeypatch):
    from bladys.payment_proofs import save_payment_proof

    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError, match="content type"):
        save_payment_proof(b"proof", "image/gif")

    assert not (tmp_path / "media").exists()


def test_delete_payment_proof_is_safe_and_tolerates_missing_file(tmp_path, monkeypatch):
    from pathlib import Path

    from bladys.payment_proofs import delete_payment_proof, save_payment_proof

    monkeypatch.chdir(tmp_path)
    reference = save_payment_proof(b"proof", "image/png")
    delete_payment_proof(reference)
    delete_payment_proof(reference)
    assert not Path(reference).exists()
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"keep")
    with pytest.raises(ValueError, match="outside"):
        delete_payment_proof("media/payment_proofs/../../outside.jpg")
    assert outside.read_bytes() == b"keep"



def test_default_storage_is_independent_of_cwd(tmp_path, monkeypatch):
    from bladys.config import PROJECT_ROOT, get_payment_proofs_dir
    monkeypatch.delenv("PAYMENT_PROOFS_DIR")
    monkeypatch.chdir(tmp_path)
    assert get_payment_proofs_dir() == (PROJECT_ROOT / "media/payment_proofs").resolve()
    monkeypatch.setenv("PAYMENT_PROOFS_DIR", "custom/proofs")
    assert get_payment_proofs_dir() == (PROJECT_ROOT / "custom/proofs").resolve()


def test_configured_storage_preserves_legacy_reference_across_cwd(tmp_path, monkeypatch):
    from bladys.payment_proofs import save_payment_proof, delete_payment_proof
    storage = tmp_path / "persistent"
    storage.mkdir()
    old = storage / "existing.jpg"
    old.write_bytes(b"old proof")
    monkeypatch.setenv("PAYMENT_PROOFS_DIR", str(storage))
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.chdir(tmp_path)
    assert resolve_payment_proof("media/payment_proofs/existing.jpg") == old
    reference = save_payment_proof(b"new proof", "image/png")
    assert reference.startswith("media/payment_proofs/")
    monkeypatch.chdir(tmp_path.parent)
    assert resolve_payment_proof(reference).read_bytes() == b"new proof"
    delete_payment_proof(reference)
    assert list(storage.iterdir()) == [old]


@pytest.mark.parametrize("configured", [None, "", "relative/proofs"])
def test_production_requires_explicit_absolute_storage(monkeypatch, configured):
    from bladys.payment_proofs import validate_payment_proofs_storage
    monkeypatch.setenv("APP_ENV", "production")
    if configured is None:
        monkeypatch.delenv("PAYMENT_PROOFS_DIR")
    else:
        monkeypatch.setenv("PAYMENT_PROOFS_DIR", configured)
    with pytest.raises(RuntimeError, match="PAYMENT_PROOFS_DIR"):
        validate_payment_proofs_storage()


@pytest.mark.parametrize("kind", ["missing", "file", "unwritable"])
def test_startup_rejects_unusable_production_storage(tmp_path, monkeypatch, kind):
    from bladys import payment_proofs
    storage = tmp_path / "configured"
    if kind == "file":
        storage.write_bytes(b"keep")
    elif kind == "unwritable":
        storage.mkdir()
        def deny(*args, **kwargs):
            raise PermissionError("denied")
        monkeypatch.setattr(payment_proofs.tempfile, "TemporaryFile", deny)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PAYMENT_PROOFS_DIR", str(storage))
    with pytest.raises(RuntimeError, match="existing writable directory"):
        payment_proofs.validate_payment_proofs_storage()
    if kind == "missing":
        assert not storage.exists()


@pytest.mark.parametrize("reference", [
    "media/payment_proofs/../secret.jpg", "media/payment_proofs/./proof.jpg",
    "media/payment_proofs/../../proof.jpg", "/server/proof.jpg",
    "media/payment_proofs/..\\secret.jpg",
])
def test_invalid_reference_rejected_for_read_and_delete(reference):
    from bladys.payment_proofs import delete_payment_proof
    for operation in (resolve_payment_proof, delete_payment_proof):
        with pytest.raises(ValueError):
            operation(reference)


def test_symlink_escape_rejected_for_read_and_delete(tmp_path, monkeypatch):
    from bladys.payment_proofs import delete_payment_proof
    storage = tmp_path / "proofs"
    storage.mkdir()
    outside = tmp_path / "secret.jpg"
    outside.write_bytes(b"keep")
    try:
        (storage / "link.jpg").symlink_to(outside)
    except OSError:
        pytest.skip("Creating symlinks requires OS permission")
    monkeypatch.setenv("PAYMENT_PROOFS_DIR", str(storage))
    for operation in (resolve_payment_proof, delete_payment_proof):
        with pytest.raises(ValueError):
            operation("media/payment_proofs/link.jpg")
    assert outside.read_bytes() == b"keep"



def test_application_startup_rejects_missing_production_directory(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from bladys.main import app
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PAYMENT_PROOFS_DIR", str(tmp_path / "missing"))
    with pytest.raises(RuntimeError, match="PAYMENT_PROOFS_DIR"):
        with TestClient(app):
            pytest.fail("Startup must reject missing production storage")
