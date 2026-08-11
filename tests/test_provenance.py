"""Tests for manifest writing and personal-data removal."""

from __future__ import annotations

import json

from eudamed.provenance import (
    PERSONAL_DATA_FIELDS,
    strip_personal_data,
    write_file_manifest,
    write_manifest,
)


def test_contact_fields_are_removed_at_every_depth():
    """manufacturer.electronicMail is personal data under the GDPR when it
    identifies a natural person, and small manufacturers routinely register a
    named individual's address."""
    record = {"name": "Acme", "manufacturer": {
        "name": "Acme", "electronicMail": "a.person@acme.example",
        "telephone": "+3212345678",
        "addresses": [{"city": "Ghent", "email": "b.person@acme.example"}]}}
    cleaned = strip_personal_data(record)
    blob = json.dumps(cleaned)
    for field in PERSONAL_DATA_FIELDS:
        assert field not in blob
    assert cleaned["manufacturer"]["name"] == "Acme"


def test_the_manifest_hashes_every_artefact(tmp_path):
    (tmp_path / "a.jsonl").write_text("one\n")
    (tmp_path / "b.jsonl").write_text("two\n")
    path = write_manifest(tmp_path, label="test-label")
    manifest = json.loads(path.read_text())
    assert manifest["n_files"] == 2
    assert {f["path"] for f in manifest["files"]} == {"a.jsonl", "b.jsonl"}
    assert all(len(f["sha256"]) == 64 for f in manifest["files"])


def test_the_manifest_does_not_hash_itself(tmp_path):
    (tmp_path / "a.jsonl").write_text("one\n")
    write_manifest(tmp_path, label="test-label")
    manifest = json.loads(write_manifest(tmp_path, label="test-label").read_text())
    assert manifest["n_files"] == 1


def test_a_file_manifest_hashes_only_the_files_it_was_given(tmp_path):
    """The directory form is for a directory that *is* a snapshot. Given a set
    of artefacts instead, nothing else in the directory is opened, hashed or
    named."""
    (tmp_path / "wanted.jsonl").write_text("one\n")
    (tmp_path / "unrelated.pdf").write_bytes(b"someone else's file")

    path = write_file_manifest(
        [tmp_path / "wanted.jsonl"],
        tmp_path / "wanted.jsonl.manifest.json",
        label="wanted",
    )
    manifest = json.loads(path.read_text())

    assert manifest["n_files"] == 1
    assert [f["path"] for f in manifest["files"]] == ["wanted.jsonl"]
    assert "unrelated.pdf" not in path.read_text()


def test_a_directory_manifest_ignores_per_file_manifests(tmp_path):
    """A manifest is provenance, not an artefact; hashing one export's manifest
    into another's file list would make the two describe each other."""
    (tmp_path / "a.jsonl").write_text("one\n")
    (tmp_path / "a.jsonl.manifest.json").write_text("{}")
    manifest = json.loads(write_manifest(tmp_path, label="test-label").read_text())
    assert [f["path"] for f in manifest["files"]] == ["a.jsonl"]


def test_the_manifest_records_the_legal_basis(tmp_path):
    """A deposited extract has to say why the reuse is lawful. Stating it in the
    manifest means it travels with the data."""
    (tmp_path / "a.jsonl").write_text("one\n")
    manifest = json.loads(write_manifest(tmp_path, label="test-label").read_text())
    assert "2011/833" in manifest["legal_basis"]
    assert manifest["legal_notice_url"].startswith("https://ec.europa.eu/tools/eudamed")
