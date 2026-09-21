import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.sync_notion import build_blocks, build_properties, callout_block, file_block, rich_text

FRONT_MATTER = {
    "date": "2026-09-17",
    "project": "shop-web",
    "title": "테스트 제목",
    "type": "decision",
    "scope": "결제/할인",
    "decision": "한 줄 결론",
    "outcome": "완료",
    "links": ["PR #1", "PR #2"],
}

BODY = """## 배경

**굵은** 문장과 [링크](https://example.com).

## 남은 것

- 첫째
- 둘째

![로컬](assets/a.png)

![원격](https://example.com/b.png)
"""


class BuildPropertiesTests(unittest.TestCase):
    def test_maps_front_matter_to_notion_properties(self):
        properties = build_properties(FRONT_MATTER)
        self.assertEqual(properties["title"]["title"][0]["text"]["content"], "테스트 제목")
        self.assertEqual(properties["date"]["date"]["start"], "2026-09-17")
        self.assertEqual(properties["type"]["select"]["name"], "decision")
        self.assertEqual(properties["outcome"]["select"]["name"], "완료")
        self.assertEqual(properties["project"]["rich_text"][0]["text"]["content"], "shop-web")
        self.assertEqual(properties["links"]["rich_text"][0]["text"]["content"], "PR #1, PR #2")

    def test_omits_absent_fields(self):
        properties = build_properties({"title": "제목만"})
        self.assertEqual(set(properties), {"title"})


class RichTextTests(unittest.TestCase):
    def test_bold_and_link_become_annotations(self):
        tokens = rich_text("앞 **굵게** 뒤 [라벨](https://x.dev)")
        bold = [t for t in tokens if t.get("annotations", {}).get("bold")]
        linked = [t for t in tokens if t["text"].get("link")]
        self.assertEqual(bold[0]["text"]["content"], "굵게")
        self.assertEqual(linked[0]["text"]["link"]["url"], "https://x.dev")

    def test_plain_text_is_single_token(self):
        tokens = rich_text("그냥 문장")
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0]["text"]["content"], "그냥 문장")

    def test_inline_code_becomes_code_annotation(self):
        tokens = rich_text("설정은 `NOTION_TOKEN` 으로 읽는다")
        coded = [t for t in tokens if t.get("annotations", {}).get("code")]
        self.assertEqual(coded[0]["text"]["content"], "NOTION_TOKEN")


class BuildBlocksTests(unittest.TestCase):
    def test_sections_lists_and_remote_image(self):
        blocks = build_blocks(BODY)
        types = [b["type"] for b in blocks]
        self.assertEqual(types.count("heading_2"), 2)
        self.assertEqual(types.count("bulleted_list_item"), 2)
        self.assertEqual(types.count("paragraph"), 1)
        # 원격 이미지는 external 블록으로, 로컬 이미지는 보내지 않는다.
        images = [b for b in blocks if b["type"] == "image"]
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["image"]["external"]["url"], "https://example.com/b.png")

    def test_code_fence_and_ordered_list_become_blocks(self):
        blocks = build_blocks("## H\n\n1. 첫째\n2. 둘째\n\n```bash\necho hi\n\necho bye\n```\n\n끝.\n")
        types = [b["type"] for b in blocks]
        self.assertEqual(types.count("numbered_list_item"), 2)
        self.assertEqual(types.count("code"), 1)
        code = next(b for b in blocks if b["type"] == "code")
        self.assertEqual(code["code"]["language"], "bash")
        self.assertIn("echo bye", code["code"]["rich_text"][0]["text"]["content"])
        self.assertEqual(types.count("paragraph"), 1)  # 펜스 내 빈 줄에 안 끊김

    def test_local_image_is_skipped_without_base_dir(self):
        # base_dir이 없으면(업로드 불가) 로컬 이미지는 건너뛴다.
        blocks = build_blocks("## H\n\n![로컬](assets/a.png)\n")
        self.assertEqual([b["type"] for b in blocks].count("image"), 0)

    def test_local_image_is_uploaded_when_resolvable(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "assets").mkdir()
            (base / "assets" / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            with mock.patch("scripts.sync_notion.upload_file", return_value="up-1") as upload:
                blocks = build_blocks("## H\n\n![로컬](assets/a.png)\n", base)
            image = next(b for b in blocks if b["type"] == "image")
            self.assertEqual(image["image"]["file_upload"]["id"], "up-1")
            self.assertEqual(upload.call_args[0][2], "image/png")  # MIME 추론

    def test_tsx_is_a_known_language(self):
        blocks = build_blocks("## H\n\n```tsx\nconst a = 1;\n```\n")
        code = next(b for b in blocks if b["type"] == "code")
        self.assertEqual(code["code"]["language"], "tsx")

    def test_unknown_code_language_falls_back(self):
        blocks = build_blocks("## H\n\n```made-up-lang\nx\n```\n")
        code = next(b for b in blocks if b["type"] == "code")
        self.assertEqual(code["code"]["language"], "plain text")

    def test_sections_are_separated_by_divider(self):
        blocks = build_blocks(BODY)
        # 섹션이 2개면 그 사이에 구분선 1개가 들어간다.
        self.assertEqual([b["type"] for b in blocks].count("divider"), 1)
        self.assertEqual(blocks[0]["type"], "heading_2")  # 첫 섹션 앞에는 구분선이 없다


class AttachmentBlockTests(unittest.TestCase):
    def test_callout_carries_decision_and_icon(self):
        block = callout_block("한 줄 결론")
        self.assertEqual(block["type"], "callout")
        self.assertEqual(block["callout"]["icon"]["emoji"], "💡")
        self.assertEqual(block["callout"]["rich_text"][0]["text"]["content"], "한 줄 결론")

    def test_file_block_references_upload_id(self):
        block = file_block("upload-123", "worklog.html")
        self.assertEqual(block["file"]["type"], "file_upload")
        self.assertEqual(block["file"]["file_upload"]["id"], "upload-123")
        self.assertIn("worklog.html", block["file"]["caption"][0]["text"]["content"])


if __name__ == "__main__":
    unittest.main()
