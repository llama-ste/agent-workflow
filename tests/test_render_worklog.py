import unittest

from scripts.render_worklog import build_meta, render_html, split_front_matter, split_sections

SAMPLE = """---
date: 2026-09-17
project: shop-web
title: 테스트 제목
type: decision
outcome: 완료
links: [PR #1, PR #2]
---

## 배경

**굵은** 문장과 [링크](https://example.com).

## 남은 것

- 첫째
- 둘째

![그림](assets/a.png)
"""


class RenderWorklogTests(unittest.TestCase):
    def test_front_matter_parses_scalars_and_list(self):
        fm, raw, body = split_front_matter(SAMPLE)
        self.assertEqual(fm["title"], "테스트 제목")
        self.assertEqual(fm["links"], ["PR #1", "PR #2"])
        self.assertIn("date: 2026-09-17", raw)
        self.assertTrue(body.strip().startswith("## 배경"))

    def test_sections_split_by_heading(self):
        _, _, body = split_front_matter(SAMPLE)
        heads = [head for head, _ in split_sections(body)]
        self.assertEqual(heads, ["배경", "남은 것"])

    def test_render_html_contains_expected_pieces(self):
        out = render_html(SAMPLE)
        self.assertIn("<title>테스트 제목</title>", out)
        self.assertIn("shop-web", out)                      # 프로젝트 칩
        self.assertIn("<strong>굵은</strong>", out)          # 굵게
        self.assertIn('href="https://example.com"', out)     # 링크
        self.assertIn("<li>첫째</li>", out)                  # 목록
        self.assertIn("<figure><img", out)                   # 이미지
        self.assertIn('<span class="num">01</span>', out)    # 섹션 번호

    def test_code_fence_ordered_list_and_inline_code(self):
        source = (
            "---\ntitle: T\n---\n\n## H\n\n1. 첫째\n2. 둘째\n\n"
            "설정은 `NOTION_TOKEN` 으로 읽는다.\n\n"
            "```mermaid\nflowchart TD\n\n  A --> B\n```\n\n다음 문단.\n"
        )
        out = render_html(source)
        self.assertIn("<ol><li>첫째</li><li>둘째</li></ol>", out)      # 번호 목록
        self.assertIn("<code>NOTION_TOKEN</code>", out)                # 인라인 코드
        self.assertIn('<pre><code class="language-mermaid">', out)     # 코드펜스
        self.assertIn("flowchart TD", out)                             # 펜스 내용 보존
        self.assertIn("<p>다음 문단.</p>", out)                         # 펜스 내 빈 줄에 안 끊김
        self.assertNotIn("```", out)                                   # 펜스 기호가 본문에 안 남음

    def test_inline_code_content_is_not_reinterpreted(self):
        out = render_html("---\ntitle: T\n---\n\n## H\n\n`**not bold**` 확인\n")
        self.assertIn("<code>**not bold**</code>", out)
        self.assertNotIn("<strong>not bold</strong>", out)

    def test_escapes_html_in_content(self):
        out = render_html("---\ntitle: <x>\n---\n\n## H\n\n<script>bad</script>\n")
        self.assertNotIn("<script>bad</script>", out)
        self.assertIn("&lt;script&gt;", out)

    def test_meta_marks_done_outcome(self):
        fm, _, _ = split_front_matter(SAMPLE)
        self.assertIn("badge-good", build_meta(fm))


if __name__ == "__main__":
    unittest.main()
