"""worklog 마크다운(프론트매터 + 서사)을 보기 좋은 HTML로 렌더링한다.

사용:
    python3 scripts/render_worklog.py <입력.md> [-o 출력.html] [--embed]

- 의존성 없이 표준 라이브러리만 사용한다.
- 지원 문법: 프론트매터(key: value, links: [..]), `## 섹션`, 문단, `**굵게**`,
  `[링크](url)`, `![대체텍스트](이미지)`, `- 목록`, `1. 번호 목록`,
  인라인 코드, ``` 코드블록.
- --embed: 이미지를 data URI로 인라인해 단일 파일로 만든다(블로그·공유용).
"""

import argparse
import base64
import html
import mimetypes
import re
from pathlib import Path


def split_front_matter(text):
    # (프론트매터 dict, 원본 프론트매터 텍스트, 본문)을 돌려준다.
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, "", text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            raw = "\n".join(lines[1:i])
            return parse_yaml_lite(lines[1:i]), raw, "\n".join(lines[i + 1:])
    return {}, "", text


def parse_yaml_lite(lines):
    data = {}
    for line in lines:
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            data[key] = [v.strip() for v in inner.split(",")] if inner else []
        else:
            data[key] = value
    return data


def split_sections(body):
    # `## 제목` 기준으로 (제목, 내용) 목록을 만든다. 첫 제목 이전 내용은 버린다.
    sections, head, buf = [], None, []
    for line in body.splitlines():
        if line.startswith("## "):
            if head is not None:
                sections.append((head, "\n".join(buf).strip()))
            head, buf = line[3:].strip(), []
        else:
            buf.append(line)
    if head is not None:
        sections.append((head, "\n".join(buf).strip()))
    return sections


IMAGE_RE = re.compile(r"^!\[(.*?)\]\((.+?)\)\s*$")
BULLET_RE = re.compile(r"^\s*-\s+")
NUMBER_RE = re.compile(r"^\s*\d+\.\s+")


def render_inline(text):
    # 인라인 코드를 먼저 빼두어 그 안의 기호가 굵게·링크로 해석되지 않게 한다.
    codes = []

    def stash(match):
        codes.append(html.escape(match.group(1)))
        return f"\x00{len(codes) - 1}\x00"

    text = re.sub(r"`([^`]+)`", stash, text)
    text = html.escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\[(.+?)\]\((.+?)\)", r'<a href="\2">\1</a>', text)
    return re.sub(r"\x00(\d+)\x00", lambda m: f"<code>{codes[int(m.group(1))]}</code>", text)


def image_src(src, embed, base_dir):
    if not embed or src.startswith(("http://", "https://", "data:")):
        return src
    path = (base_dir / src) if base_dir else Path(src)
    try:
        mime = mimetypes.guess_type(str(path))[0] or "image/png"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{encoded}"
    except OSError:
        return src  # 파일을 못 읽으면 원본 경로를 유지한다.


def collect_list_items(lines, marker):
    # 목록 항목을 모은다. 들여쓴 연속 줄은 앞 항목에 이어 붙인다.
    # 목록이 아닌 줄이 섞이면 None을 돌려준다.
    items, current = [], None
    for line in lines:
        if marker.match(line):
            if current is not None:
                items.append(current)
            current = marker.sub("", line).strip()
        elif current is not None and line.strip():
            current += " " + line.strip()
        else:
            return None
    if current is not None:
        items.append(current)
    return items or None


def render_chunk(block, embed, base_dir):
    lines = block.splitlines()
    image = IMAGE_RE.match(block)
    if image:
        alt, src = image.group(1), image_src(image.group(2), embed, base_dir)
        caption = f"<figcaption>{html.escape(alt)}</figcaption>" if alt else ""
        return f'<figure><img src="{html.escape(src)}" alt="{html.escape(alt)}">{caption}</figure>'
    for marker, tag in ((BULLET_RE, "ul"), (NUMBER_RE, "ol")):
        items = collect_list_items(lines, marker)
        if items:
            body = "".join(f"<li>{render_inline(item)}</li>" for item in items)
            return f"<{tag}>{body}</{tag}>"
    return f"<p>{render_inline(block)}</p>"


def render_blocks(content, embed, base_dir):
    # 코드펜스는 내부에 빈 줄이 있을 수 있어 한 줄씩 훑으며 처리한다.
    parts, buffer = [], []
    lines = content.strip().splitlines()

    def flush():
        if buffer:
            parts.append(render_chunk("\n".join(buffer).strip(), embed, base_dir))
            buffer.clear()

    index = 0
    while index < len(lines):
        line = lines[index]
        if line.lstrip().startswith("```"):
            flush()
            language = line.strip().strip("`").strip()
            index += 1
            code = []
            while index < len(lines) and not lines[index].lstrip().startswith("```"):
                code.append(lines[index])
                index += 1
            index += 1  # 닫는 펜스
            attribute = f' class="language-{html.escape(language)}"' if language else ""
            parts.append(f"<pre><code{attribute}>{html.escape(chr(10).join(code))}</code></pre>")
            continue
        if line.strip():
            buffer.append(line)
        else:
            flush()
        index += 1
    flush()
    return "\n".join(part for part in parts if part)


def chip(label, value=None, cls=""):
    inner = f"<b>{html.escape(label)}</b> {html.escape(value)}" if value else html.escape(label)
    return f'<span class="chip {cls}">{inner}</span>'


def build_meta(fm):
    chips = []
    if fm.get("project"):
        chips.append(chip("project", fm["project"]))
    if fm.get("date"):
        chips.append(chip("date", fm["date"]))
    if fm.get("type"):
        chips.append(chip(fm["type"], cls="badge-type"))
    if fm.get("scope"):
        chips.append(chip("scope", fm["scope"]))
    if fm.get("outcome"):
        chips.append(chip(fm["outcome"], cls="badge-good" if fm["outcome"] == "완료" else ""))
    for link in fm.get("links", []):
        chips.append(chip(link))
    return "\n".join(chips)


def render_html(md_text, embed=False, base_dir=None):
    fm, raw_fm, body = split_front_matter(md_text)
    title = fm.get("title", "작업 기록")
    decision = fm.get("decision", "")

    decision_html = ""
    if decision:
        decision_html = (
            '<div class="decision"><div class="k">결정</div>'
            f'<div class="v">{html.escape(decision)}</div></div>'
        )

    front_html = ""
    if raw_fm:
        front_html = (
            '<div class="frontmatter"><div class="bar"><span></span><span></span>'
            '<span></span><span class="label">worklog.md</span></div>'
            f"<pre>---\n{html.escape(raw_fm)}\n---</pre></div>"
        )

    sections_html = []
    for index, (head, content) in enumerate(split_sections(body), start=1):
        sections_html.append(
            f'<section><h2><span class="num">{index:02d}</span> {html.escape(head)}</h2>'
            f"{render_blocks(content, embed, base_dir)}</section>"
        )

    return TEMPLATE.format(
        title=html.escape(title),
        decision=decision_html,
        meta=build_meta(fm),
        frontmatter=front_html,
        sections="\n".join(sections_html),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", type=Path, help="worklog 마크다운 파일")
    parser.add_argument("-o", "--output", type=Path, help="출력 HTML 경로 (기본: 입력과 같은 이름의 .html)")
    parser.add_argument("--embed", action="store_true", help="이미지를 data URI로 인라인해 단일 파일로 생성")
    args = parser.parse_args()

    md_text = args.input.read_text(encoding="utf-8")
    output = args.output or args.input.with_suffix(".html")
    output.write_text(render_html(md_text, embed=args.embed, base_dir=args.input.resolve().parent), encoding="utf-8")
    print(f"생성 완료: {output}")


TEMPLATE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root {{
  --bg:#eef1f2; --surface:#fff; --surface-2:#f4f6f6; --ink:#14201f; --ink-soft:#46514f;
  --ink-faint:#6f7a78; --line:#dde3e2; --line-soft:#e8ecec; --accent:#0f766e;
  --accent-wash:#e9f3f1; --good:#157f5c; --good-wash:#e4f3ec; --amber:#9a6a15;
  --shadow:0 1px 2px rgba(20,32,31,.06),0 8px 24px -12px rgba(20,32,31,.18); --radius:14px;
  --serif:"Fraunces",Georgia,serif; --sans:"IBM Plex Sans",system-ui,sans-serif; --mono:"IBM Plex Mono",ui-monospace,monospace;
}}
@media (prefers-color-scheme:dark) {{ :root:not([data-theme="light"]) {{
  --bg:#0e1413; --surface:#151d1c; --surface-2:#1b2523; --ink:#e8eeec; --ink-soft:#b0bbb8;
  --ink-faint:#7f8a87; --line:#263230; --line-soft:#1f2a28; --accent:#4cc0af; --accent-wash:#16302c;
  --good:#56c58f; --good-wash:#142c22; --amber:#d2a24e;
  --shadow:0 1px 2px rgba(0,0,0,.3),0 10px 28px -14px rgba(0,0,0,.6);
}} }}
:root[data-theme="dark"] {{
  --bg:#0e1413; --surface:#151d1c; --surface-2:#1b2523; --ink:#e8eeec; --ink-soft:#b0bbb8;
  --ink-faint:#7f8a87; --line:#263230; --line-soft:#1f2a28; --accent:#4cc0af; --accent-wash:#16302c;
  --good:#56c58f; --good-wash:#142c22; --amber:#d2a24e;
  --shadow:0 1px 2px rgba(0,0,0,.3),0 10px 28px -14px rgba(0,0,0,.6);
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink); font-family:var(--sans); line-height:1.6; -webkit-font-smoothing:antialiased; }}
img {{ max-width:100%; }}
.wrap {{ max-width:760px; margin:0 auto; padding:16px; padding-block:clamp(24px,5vw,56px); }}
.eyebrow {{ font-family:var(--mono); font-size:12px; letter-spacing:.14em; text-transform:uppercase; color:var(--accent); display:flex; align-items:center; gap:8px; margin-bottom:14px; }}
.eyebrow .dot {{ width:6px; height:6px; border-radius:50%; background:var(--accent); }}
h1 {{ font-family:var(--serif); font-weight:600; font-size:clamp(30px,6vw,46px); line-height:1.08; letter-spacing:-.01em; text-wrap:balance; margin:0 0 20px; }}
.decision {{ display:flex; gap:14px; align-items:flex-start; background:var(--accent-wash); border:1px solid color-mix(in srgb,var(--accent) 25%,transparent); border-radius:var(--radius); padding:16px 18px; margin-bottom:26px; }}
.decision .k {{ font-family:var(--mono); font-size:11px; letter-spacing:.1em; text-transform:uppercase; color:var(--accent); padding-top:3px; white-space:nowrap; }}
.decision .v {{ font-size:16.5px; font-weight:500; }}
.meta {{ display:flex; flex-wrap:wrap; gap:8px; margin-bottom:26px; }}
.chip {{ font-family:var(--mono); font-size:12px; color:var(--ink-soft); background:var(--surface); border:1px solid var(--line); border-radius:999px; padding:4px 11px; display:inline-flex; gap:6px; align-items:center; }}
.chip b {{ color:var(--ink-faint); font-weight:500; }}
.chip.badge-good {{ color:var(--good); background:var(--good-wash); border-color:color-mix(in srgb,var(--good) 30%,transparent); }}
.chip.badge-type {{ color:var(--accent); background:var(--accent-wash); border-color:color-mix(in srgb,var(--accent) 30%,transparent); }}
.frontmatter {{ background:var(--surface); border:1px solid var(--line); border-radius:var(--radius); box-shadow:var(--shadow); overflow:hidden; margin-bottom:34px; }}
.frontmatter .bar {{ display:flex; gap:7px; align-items:center; padding:10px 14px; border-bottom:1px solid var(--line-soft); background:var(--surface-2); }}
.frontmatter .bar span:not(.label) {{ width:10px; height:10px; border-radius:50%; background:var(--line); flex:none; }}
.frontmatter .bar .label {{ margin-left:6px; font-family:var(--mono); font-size:11px; color:var(--ink-faint); letter-spacing:.08em; }}
.frontmatter pre {{ margin:0; padding:14px 16px; font-family:var(--mono); font-size:12.5px; line-height:1.75; color:var(--ink-soft); overflow-x:auto; }}
section {{ margin-bottom:34px; }}
h2 {{ font-family:var(--sans); font-size:13px; font-weight:600; letter-spacing:.04em; text-transform:uppercase; color:var(--ink-faint); display:flex; align-items:baseline; gap:10px; margin:0 0 14px; }}
h2 .num {{ font-family:var(--mono); color:var(--accent); font-size:12px; }}
h2::after {{ content:""; flex:1; height:1px; background:var(--line-soft); }}
p {{ margin:0 0 12px; color:var(--ink-soft); max-width:66ch; }}
p strong {{ color:var(--ink); font-weight:600; }}
a {{ color:var(--accent); }}
ul {{ list-style:none; padding:0; margin:0 0 12px; display:grid; gap:8px; }}
ul li {{ display:flex; gap:10px; align-items:baseline; color:var(--ink-soft); }}
ul li::before {{ content:"—"; color:var(--amber); font-family:var(--mono); }}
ol {{ padding-left:1.3em; margin:0 0 12px; display:grid; gap:8px; }}
ol li {{ color:var(--ink-soft); padding-left:.2em; }}
ol li::marker {{ color:var(--accent); font-family:var(--mono); font-size:.9em; }}
code {{ font-family:var(--mono); font-size:.88em; background:var(--surface-2); border:1px solid var(--line-soft); border-radius:5px; padding:1px 5px; color:var(--ink); }}
pre {{ background:var(--surface-2); border:1px solid var(--line); border-radius:10px; padding:13px 15px; overflow-x:auto; margin:0 0 14px; }}
pre code {{ background:none; border:none; padding:0; font-size:12.5px; line-height:1.7; color:var(--ink-soft); }}
figure {{ margin:0 0 14px; }}
figure img {{ border:1px solid var(--line); border-radius:var(--radius); display:block; }}
figcaption {{ font-family:var(--mono); font-size:11.5px; color:var(--ink-faint); margin-top:8px; }}
</style>
</head>
<body>
<div class="wrap">
  <div class="eyebrow"><span class="dot"></span> 작업 기록 · worklog</div>
  <h1>{title}</h1>
  {decision}
  <div class="meta">{meta}</div>
  {frontmatter}
  {sections}
</div>
</body>
</html>
"""


if __name__ == "__main__":
    main()
