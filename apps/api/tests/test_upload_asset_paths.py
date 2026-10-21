"""教材资源出口只能读当前批次目录内的图片与中间产物。

校验逻辑放在 ``infrastructure.files.upload_registry``，这里直接用真实临时目录验证：
越界目标文件是真实存在且内容可读的，所以一旦校验失效测试会读到内容而不是 404。
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from infrastructure.files.upload_registry import (
    resolve_inside_upload_root,
    safe_path_segment,
)


class UploadAssetPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "upload"
        self.batch = self.root / "assets" / "batch-1"
        self.batch.mkdir(parents=True)
        (self.batch / "q1.png").write_bytes(b"png")
        (self.batch / "source.md").write_text("批次内的中间产物", encoding="utf-8")
        # 以下文件都在批次目录之外，一旦越界就会真的被读到。
        (self.root / "assets" / "q1.png").write_bytes(b"sibling")
        (self.root / "assets" / "source.md").write_text("兄弟目录", encoding="utf-8")
        (self.root / "outside.png").write_bytes(b"outside")
        (Path(self.temporary.name) / "secret.png").write_bytes(b"secret")

    def resolve(self, *segments: str) -> Path | None:
        return resolve_inside_upload_root(self.root / "assets", *segments)

    def test_user_reads_a_question_image_inside_the_uploaded_batch(self) -> None:
        resolved = self.resolve("batch-1", "q1.png")

        self.assertEqual(resolved, (self.batch / "q1.png").resolve())

    def test_user_reads_a_review_artifact_inside_the_uploaded_batch(self) -> None:
        resolved = self.resolve("batch-1", "source.md")

        self.assertEqual(resolved, (self.batch / "source.md").resolve())

    def test_user_cannot_escape_the_batch_with_dot_dot_windows_or_absolute_paths(self) -> None:
        for segments in (
            ("..", "q1.png"),
            ("..", "source.md"),
            ("../..", "secret.png"),
            ("batch-1", "../q1.png"),
            ("batch-1", r"..\..\secret.png"),
            (r"..\..", "secret.png"),
            ("batch-1", r"C:\Windows\win.ini"),
            ("batch-1", "/etc/passwd"),
            ("/", "q1.png"),
            ("", "q1.png"),
            ("batch-1", ".."),
            ("batch-1", "."),
            (str(self.root / "assets"), "source.md"),
            (str(Path(self.temporary.name)), "secret.png"),
            ("batch-1", "q1.png\x00.png"),
        ):
            with self.subTest(segments=segments):
                self.assertIsNone(self.resolve(*segments))

    def test_user_cannot_escape_through_percent_encoded_segments(self) -> None:
        for segments in (
            ("%2e%2e", "secret.png"),
            ("batch-1", "%2e%2e%2fq1.png"),
            ("%252e%252e", "secret.png"),
            ("batch-1", r"%2e%2e%5cq1.png"),
        ):
            with self.subTest(segments=segments):
                self.assertIsNone(self.resolve(*segments))

    def test_user_cannot_read_sibling_directories_of_a_batch(self) -> None:
        # 兄弟目录里真的有同名文件，拒绝必须来自校验而不是“文件不存在”。
        self.assertIsNone(self.resolve("..", "q1.png"))
        # 不存在的批次不会越界；路由用 is_file() 把它变成 404。
        missing = self.resolve("missing-batch", "q1.png")
        self.assertIsNotNone(missing)
        self.assertFalse(missing.is_file())

    def test_valid_segments_are_returned_unchanged(self) -> None:
        for value in ("batch-1", "q1.png", "source.md", "a-b_c.1.png"):
            with self.subTest(value=value):
                self.assertEqual(safe_path_segment(value), value)

    def test_user_cannot_follow_a_symlink_to_a_sibling_with_the_same_directory_prefix(self) -> None:
        # Given an existing sibling whose name shares the trusted root's prefix
        sibling = self.root / "assets-other"
        sibling.mkdir()
        secret = sibling / "secret.png"
        secret.write_bytes(b"outside-the-assets-root")
        link = self.batch / "linked.png"
        try:
            link.symlink_to(secret)
        except OSError as error:
            if getattr(error, "winerror", None) == 1314:
                self.skipTest("Windows 创建符号链接需要开发者模式或相应权限")
            raise

        # When the requested name is valid but its real target is outside the root
        # Then a string prefix shared by the sibling must not permit the read
        self.assertIsNone(self.resolve("batch-1", "linked.png"))


if __name__ == "__main__":
    unittest.main()
