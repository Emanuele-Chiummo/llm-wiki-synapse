"""
Regression tests for the vault-file permission invariant (2.1.16, I5).

THE INVARIANT: every file Synapse publishes into the vault — a generated wiki page, a page
the user edited through the UI, an uploaded/clipped/pasted source — carries
``app.vault_io.VAULT_FILE_MODE`` (0644), never ``tempfile.mkstemp``'s 0600.

WHY IT BREAKS SILENTLY: each of those writes is crash-safe, i.e. ``mkstemp`` into the target
directory followed by ``os.replace``. ``os.replace`` does NOT copy the destination's mode onto
the incoming file — it unlinks the destination inode and hands its NAME to the temp file,
which keeps its own 0600. So the bug is not only "new vault files are owner-only": rewriting
an existing 0644 page DOWNGRADES it. The backend runs as uid 1000 in its container while
``vault/`` is a bind mount shared with Obsidian/LiveSync on the host (CLAUDE.md §1), so the
Obsidian side of a shared vault stops being able to read the file — an I5 violation produced
by an ordinary page edit, with nothing in any log.

``POST /ingest/convert-marker`` got an open-coded ``os.chmod`` in 2.1.12 (asserted by
``test_convert_marker.py``) when it moved from ``write_bytes()`` to ``mkstemp``; the six older
sites that write the same way never had it. The chmod+rename pair now lives in ONE place
(``app.vault_io``) and these tests pin it at every site that uses it.

Each test here FAILS without the fix: the assertion reads 0o600 instead of 0o644.
"""

from __future__ import annotations

import io
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

import pytest
from app.vault_io import VAULT_FILE_MODE, atomic_write_bytes, publish_tmp_file
from httpx import AsyncClient

# Shared fixtures from the sibling suites (conftest.py auto-discovery registers them).
from tests.test_api import api_client, api_env  # noqa: F401
from tests.test_clip import _VALID_BODY, _VALID_TOKEN, clip_env  # noqa: F401
from tests.test_page_content_api import _ingest_wiki_entity
from tests.test_upload_and_schedule import upload_env  # noqa: F401


def _mode(path: Path) -> int:
    """The permission bits of *path* (e.g. 0o644)."""
    return stat.S_IMODE(path.stat().st_mode)


def _modestr(path: Path) -> str:
    return oct(_mode(path))


# ── app.vault_io unit behaviour ───────────────────────────────────────────────


class TestVaultIoPrimitives:
    """The two primitives every vault write site now goes through."""

    def test_atomic_write_bytes_creates_file_at_vault_mode(self, tmp_path: Path) -> None:
        dst = tmp_path / "nested" / "page.md"
        atomic_write_bytes(dst, b"body\n", suffix=".t_tmp")

        assert dst.read_bytes() == b"body\n"
        assert _mode(dst) == VAULT_FILE_MODE, f"new file is {_modestr(dst)}, want 0o644"
        assert list(tmp_path.rglob("*.t_tmp")) == [], "temp file left behind"

    def test_atomic_write_bytes_does_not_downgrade_an_existing_file(self, tmp_path: Path) -> None:
        """The regression that bites hardest: a REWRITE must not take 0644 down to 0600."""
        dst = tmp_path / "page.md"
        dst.write_text("old\n", encoding="utf-8")
        os.chmod(dst, 0o644)

        atomic_write_bytes(dst, b"new\n", suffix=".t_tmp")

        assert dst.read_bytes() == b"new\n"
        assert _mode(dst) == VAULT_FILE_MODE, (
            f"rewriting the page left it {_modestr(dst)} — os.replace carried the temp "
            f"file's 0600 onto the destination"
        )

    def test_publish_tmp_file_chmods_before_the_rename(self, tmp_path: Path) -> None:
        """A streamed write (upload body) publishes its own temp file through this."""
        dst = tmp_path / "streamed.md"
        fd, name = tempfile.mkstemp(dir=str(tmp_path), suffix=".upload_tmp")
        os.write(fd, b"streamed\n")
        os.close(fd)
        assert stat.S_IMODE(os.stat(name).st_mode) == 0o600, "mkstemp precondition"

        publish_tmp_file(name, dst)

        assert dst.read_bytes() == b"streamed\n"
        assert _mode(dst) == VAULT_FILE_MODE, f"published file is {_modestr(dst)}"
        assert not Path(name).exists()

    def test_atomic_write_bytes_closes_the_descriptor_exactly_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        A failure in the rename happens AFTER the fd is closed. The previous per-site idiom
        closed it again in its error path, so ``os.close`` ran on a descriptor number the
        process may already have reissued to an unrelated file or socket.
        """
        closed: list[int] = []
        real_close = os.close

        def _tracking_close(fd: int) -> None:
            closed.append(fd)
            real_close(fd)

        def _boom(src: Any, dst: Any) -> None:
            raise OSError("rename failed")

        monkeypatch.setattr(os, "close", _tracking_close)
        monkeypatch.setattr(os, "replace", _boom)

        dst = tmp_path / "page.md"
        with pytest.raises(OSError, match="rename failed"):
            atomic_write_bytes(dst, b"data\n", suffix=".t_tmp")

        assert len(closed) == len(set(closed)) == 1, f"descriptor closed {len(closed)}x: {closed}"
        assert not dst.exists()
        assert list(tmp_path.glob("*.t_tmp")) == [], "temp file not cleaned up after failure"


# ── PUT /pages/{id}/content — the edit that downgraded a live page ────────────


class TestPutPageContentKeepsVaultMode:
    async def test_put_does_not_downgrade_the_edited_page(
        self, api_client: AsyncClient, api_env: dict[str, Any]
    ) -> None:
        page_id, wiki_file = await _ingest_wiki_entity(
            api_env,
            filename="mode_target.md",
            content="---\ntype: entity\ntitle: Mode Target\nsources: []\n---\n\nOld body.\n",
        )
        os.chmod(wiki_file, 0o644)

        resp = await api_client.put(
            f"/pages/{page_id}/content",
            json={
                "content": (
                    "---\ntype: entity\ntitle: Mode Target\nsources: []\n---\n\nNew body.\n"
                )
            },
        )
        assert resp.status_code == 200, resp.text

        assert _mode(wiki_file) == VAULT_FILE_MODE, (
            f"saving an edit left the page at {_modestr(wiki_file)} — the Obsidian side of "
            f"a shared vault can no longer read it (I5)"
        )
        assert list(wiki_file.parent.glob("*.content_tmp")) == []


# ── Generated pages: block_writer + the reindex rewrite ───────────────────────


class TestGeneratedPageMode:
    def test_block_writer_atomic_write_uses_vault_mode(self, tmp_path: Path) -> None:
        """Every page the ingest loop writes goes through this helper."""
        from app.ingest.block_writer import _atomic_write

        dst = tmp_path / "wiki" / "entities" / "generated.md"
        _atomic_write(dst, b"---\ntype: entity\n---\n\nGenerated.\n")

        assert _mode(dst) == VAULT_FILE_MODE, f"generated page is {_modestr(dst)}"
        assert list(tmp_path.rglob("*.block_tmp")) == []

    def test_block_writer_atomic_write_does_not_downgrade_a_rewrite(self, tmp_path: Path) -> None:
        dst = tmp_path / "entities" / "generated.md"
        dst.parent.mkdir(parents=True)
        dst.write_text("old\n", encoding="utf-8")
        os.chmod(dst, 0o644)

        from app.ingest.block_writer import _atomic_write

        _atomic_write(dst, b"new\n")

        assert _mode(dst) == VAULT_FILE_MODE, f"re-generated page is {_modestr(dst)}"

    async def test_reindex_wiki_page_body_keeps_vault_mode(
        self, api_client: AsyncClient, api_env: dict[str, Any]
    ) -> None:
        """
        ``reindex_wiki_page_body`` is the in-place rewrite behind wikilink enrichment; it
        used its own open-coded mkstemp+replace.
        """
        from app.ingest.orchestrator import _load_page, reindex_wiki_page_body

        _page_id, wiki_file = await _ingest_wiki_entity(
            api_env,
            filename="enrich_mode.md",
            content="---\ntype: entity\ntitle: Enrich Mode\nsources: []\n---\n\nPlain body.\n",
        )
        os.chmod(wiki_file, 0o644)

        rel_path = str(wiki_file.relative_to(api_env["vault_root"]))
        page = await _load_page(rel_path)
        assert page is not None, f"page row missing for {rel_path}"
        new_text = (
            "---\ntype: entity\ntitle: Enrich Mode\nsources: []\n---\n\n"
            "Body with an [[Other Page]] link.\n"
        )
        await reindex_wiki_page_body(
            page=page,
            new_file_text=new_text,
            body_for_embedding="Body with an [[Other Page]] link.",
            bump=False,
        )

        assert wiki_file.read_text(encoding="utf-8") == new_text
        assert _mode(wiki_file) == VAULT_FILE_MODE, f"enriched page is {_modestr(wiki_file)}"
        assert list(wiki_file.parent.glob("*.enrich_tmp")) == []


# ── Ingress: upload, from-text, clip ─────────────────────────────────────────


class TestSourceIngressMode:
    async def test_upload_lands_at_vault_mode(self, upload_env: dict[str, Any]) -> None:
        client = upload_env["client"]
        sources_dir: Path = upload_env["sources_dir"]

        resp = await client.post(
            "/ingest/upload",
            files={"file": ("mode_note.md", io.BytesIO(b"# Mode\n"), "text/markdown")},
        )
        assert resp.status_code == 202, resp.text

        dst = sources_dir / "mode_note.md"
        assert dst.exists()
        assert _mode(dst) == VAULT_FILE_MODE, f"uploaded source is {_modestr(dst)}"
        assert list(sources_dir.glob("*.upload_tmp")) == []

    async def test_from_text_lands_at_vault_mode(self, upload_env: dict[str, Any]) -> None:
        client = upload_env["client"]
        sources_dir: Path = upload_env["sources_dir"]

        resp = await client.post(
            "/ingest/from-text",
            json={"text": "Pasted body.\n", "source_hint": "Pasted Note"},
        )
        assert resp.status_code == 202, resp.text

        dst = (upload_env["vault_root"] / resp.json()["file_path"]).resolve()
        assert dst.exists()
        assert _mode(dst) == VAULT_FILE_MODE, f"pasted source is {_modestr(dst)}"
        assert list(sources_dir.glob("*.fromtext_tmp")) == []

    async def test_clip_lands_at_vault_mode(self, clip_env: dict[str, Any]) -> None:
        client = clip_env["client"]
        sources_dir: Path = clip_env["sources_dir"]

        resp = await client.post(
            "/clip",
            json=_VALID_BODY,
            headers={
                "Authorization": f"Bearer {_VALID_TOKEN}",
                "Origin": "chrome-extension://fakeextensionid",
            },
        )
        assert resp.status_code == 202, resp.text

        dst = (clip_env["vault_root"] / resp.json()["file_path"]).resolve()
        assert dst.exists()
        assert _mode(dst) == VAULT_FILE_MODE, f"clipped source is {_modestr(dst)}"
        assert list(sources_dir.glob("*.clip_tmp")) == []
