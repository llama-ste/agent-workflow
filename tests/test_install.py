import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import install
from scripts.install import BEGIN, END, ROOT, TARGETS, block, check_tool, install_tool, merge, render


class InstallTests(unittest.TestCase):
    def test_render_includes_common_adapter_and_absolute_paths(self):
        for tool in TARGETS:
            rendered = render(tool)
            self.assertIn("# 공통 AI 작업 규칙", rendered)
            self.assertIn((ROOT / "tools" / tool / "instructions.md").read_text(encoding="utf-8"), rendered)
            self.assertIn(f"`{ROOT / 'workflows' / 'review.md'}`", rendered)
            # 상대 경로 참조는 남지 않고 모두 절대 경로로 치환되어야 한다.
            self.assertNotIn("`workflows/review.md`", rendered)

    def test_doc_references_resolve_and_appear_as_absolute_paths(self):
        # 문서가 참조하는 workflows/·guides/ 경로가 실제로 존재하고, 생성 블록에 절대 경로로 들어가는지 확인한다.
        sources = [ROOT / "AGENTS.md", *ROOT.glob("workflows/*.md"), *ROOT.glob("guides/*.md")]
        for tool in TARGETS:
            rendered = render(tool)
            for source in sources:
                for reference in re.findall(r"`((?:workflows|guides)/[\w-]+\.md)`", source.read_text(encoding="utf-8")):
                    self.assertTrue((ROOT / reference).is_file(), f"Missing reference in {source}: {reference}")
                    self.assertIn(str(ROOT / reference), rendered)

    def test_merge_is_idempotent_and_preserves_outside_content(self):
        user = "# 내 기존 전역 규칙\n\n손으로 쓴 내용\n"
        once = merge(user, block("codex"))
        twice = merge(once, block("codex"))
        self.assertEqual(once, twice)
        self.assertIn("손으로 쓴 내용", once)
        self.assertEqual(once.count(BEGIN), 1)
        self.assertEqual(once.count(END), 1)


class InstallIOTests(unittest.TestCase):
    # 실제 파일 I/O 경로(백업·디스크 병합·멱등·제거)를 임시 타깃으로 검증한다.
    def _target(self, directory):
        return Path(directory) / "AGENTS.md"

    def test_install_preserves_user_content_and_backs_up(self):
        with tempfile.TemporaryDirectory() as directory:
            target = self._target(directory)
            target.write_text("# 내 규칙\n\n손으로 쓴 내용\n", encoding="utf-8")
            with mock.patch.dict(install.TARGETS, {"codex": target}):
                install_tool("codex", dry_run=False, uninstall=False, show=False)
            text = target.read_text(encoding="utf-8")
            self.assertIn("손으로 쓴 내용", text)
            self.assertIn(BEGIN, text)
            self.assertIn(END, text)
            backups = list(Path(directory).glob("AGENTS.md.bak-*"))
            self.assertEqual(len(backups), 1)
            self.assertIn("손으로 쓴 내용", backups[0].read_text(encoding="utf-8"))

    def test_reinstall_is_idempotent_without_new_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = self._target(directory)
            with mock.patch.dict(install.TARGETS, {"codex": target}):
                install_tool("codex", False, False, False)
                first = target.read_text(encoding="utf-8")
                install_tool("codex", False, False, False)
                second = target.read_text(encoding="utf-8")
            self.assertEqual(first, second)
            # 새로 만든 파일(기존 내용 없음)은 백업하지 않는다.
            self.assertEqual(list(Path(directory).glob("*.bak-*")), [])

    def test_uninstall_removes_block_and_keeps_user_content(self):
        with tempfile.TemporaryDirectory() as directory:
            target = self._target(directory)
            target.write_text("# 내 규칙\n\n지킬 내용\n", encoding="utf-8")
            with mock.patch.dict(install.TARGETS, {"codex": target}):
                install_tool("codex", False, False, False)
                install_tool("codex", dry_run=False, uninstall=True, show=False)
            text = target.read_text(encoding="utf-8")
            self.assertIn("지킬 내용", text)
            self.assertNotIn(BEGIN, text)

    def test_check_detects_healthy_and_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            target = self._target(directory)
            with mock.patch.dict(install.TARGETS, {"codex": target}):
                self.assertFalse(check_tool("codex"))  # 미연결
                install_tool("codex", False, False, False)
                self.assertTrue(check_tool("codex"))   # 정상


if __name__ == "__main__":
    unittest.main()
