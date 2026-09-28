from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from draftboard.data.nffl_rollover_backup import (
    AUTO_BACKUP_PREFIX,
    create_nffl_rollover_backup,
)


class NfflRolloverBackupTests(unittest.TestCase):
    def test_missing_pg_dump_fails_closed(self) -> None:
        with TemporaryDirectory() as tmp:
            with patch(
                "draftboard.data.nffl_rollover_backup.shutil.which",
                return_value=None,
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "requires pg_dump",
                ):
                    create_nffl_rollover_backup(
                        dsn=(
                            "postgresql://user:secret@db:5432/mlf"
                        ),
                        source_season_year=2026,
                        target_season_year=2027,
                        backup_dir=tmp,
                    )

            self.assertEqual(
                list(Path(tmp).glob("*.dump")),
                [],
            )

    def test_success_validates_and_prunes_only_automatic_dumps(
        self,
    ) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)

            manual = (
                root
                / "nffl_pre_contract_publication_20260909_162652.dump"
            )
            manual.write_bytes(b"manual")

            for index in range(4):
                path = (
                    root
                    / (
                        f"{AUTO_BACKUP_PREFIX}"
                        f"2022_to_2023_old_{index}.dump"
                    )
                )
                path.write_bytes(
                    b"old-auto-backup"
                )
                os.utime(
                    path,
                    (
                        index + 1,
                        index + 1,
                    ),
                )

            calls: list[tuple[list[str], dict]] = []

            def fake_run(
                cmd,
                **kwargs,
            ):
                command = list(cmd)
                calls.append(
                    (
                        command,
                        dict(kwargs),
                    )
                )

                if command[0] == "/usr/bin/pg_dump":
                    output_path = Path(
                        command[
                            command.index("--file") + 1
                        ]
                    )
                    output_path.write_bytes(
                        b"x" * 4096
                    )

                    return subprocess.CompletedProcess(
                        command,
                        0,
                        stdout="",
                        stderr="",
                    )

                if command[0] == "/usr/bin/pg_restore":
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        stdout="archive-listing",
                        stderr="",
                    )

                raise AssertionError(
                    f"Unexpected command: {command}"
                )

            def fake_which(name: str) -> str | None:
                if name in {
                    "pg_dump",
                    "pg_restore",
                }:
                    return f"/usr/bin/{name}"
                return None

            with patch(
                "draftboard.data.nffl_rollover_backup.shutil.which",
                side_effect=fake_which,
            ), patch(
                "draftboard.data.nffl_rollover_backup.subprocess.run",
                side_effect=fake_run,
            ):
                result = create_nffl_rollover_backup(
                    dsn=(
                        "postgresql://user:secret@db:5432/mlf"
                    ),
                    source_season_year=2026,
                    target_season_year=2027,
                    backup_dir=root,
                    retention_count=3,
                    now=datetime(
                        2026,
                        9,
                        28,
                        16,
                        30,
                        0,
                        tzinfo=timezone.utc,
                    ),
                )

            self.assertTrue(
                result.path.exists()
            )

            self.assertGreaterEqual(
                result.size_bytes,
                4096,
            )

            automatic = list(
                root.glob(
                    f"{AUTO_BACKUP_PREFIX}*.dump"
                )
            )

            self.assertEqual(
                len(automatic),
                3,
            )

            self.assertTrue(
                manual.exists()
            )

            self.assertEqual(
                len(result.removed_files),
                2,
            )

            dump_cmd, dump_kwargs = calls[0]

            self.assertNotIn(
                "secret",
                " ".join(dump_cmd),
            )

            self.assertEqual(
                dump_kwargs["env"]["PGPASSWORD"],
                "secret",
            )

            self.assertIn(
                "--format=custom",
                dump_cmd,
            )

            self.assertEqual(
                calls[1][0][1],
                "--list",
            )

    def test_failed_dump_leaves_no_partial_archive(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)

            def fake_which(name: str) -> str | None:
                return f"/usr/bin/{name}"

            def fake_run(
                cmd,
                **kwargs,
            ):
                return subprocess.CompletedProcess(
                    list(cmd),
                    2,
                    stdout="",
                    stderr="synthetic dump failure",
                )

            with patch(
                "draftboard.data.nffl_rollover_backup.shutil.which",
                side_effect=fake_which,
            ), patch(
                "draftboard.data.nffl_rollover_backup.subprocess.run",
                side_effect=fake_run,
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "synthetic dump failure",
                ):
                    create_nffl_rollover_backup(
                        dsn=(
                            "postgresql://user:secret@db:5432/mlf"
                        ),
                        source_season_year=2026,
                        target_season_year=2027,
                        backup_dir=root,
                    )

            self.assertEqual(
                list(root.iterdir()),
                [],
            )


if __name__ == "__main__":
    unittest.main()