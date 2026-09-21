"""worklog 마크다운을 Notion 데이터베이스로 보낸다.

준비:
    export NOTION_TOKEN=...        # Notion integration 토큰 (저장소에 넣지 않는다)
    python3 scripts/sync_notion.py --init-db <부모_페이지_ID>
    export NOTION_DB_ID=...        # 위 명령이 출력한 DB ID

사용:
    python3 scripts/sync_notion.py 기록.md

- 의존성 없이 표준 라이브러리만 사용한다.
- 프론트매터는 DB 속성으로, 본문은 페이지 블록으로 변환한다.
- 같은 title이 이미 있으면 새로 만들지 않고 갱신한다(upsert).
- 기록 옆의 로컬 이미지는 업로드해 페이지에 넣는다. 같은 원본이 HTML·Obsidian·Notion에서 모두 보인다.
"""

import argparse
import json
import mimetypes
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

try:  # 스크립트로 직접 실행할 때
    from render_worklog import collect_list_items, render_html, split_front_matter, split_sections
except ImportError:  # 저장소 루트에서 패키지 경로로 임포트할 때
    from scripts.render_worklog import collect_list_items, render_html, split_front_matter, split_sections

API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
TEXT_LIMIT = 2000  # Notion rich_text 한 조각의 최대 길이
CHILD_LIMIT = 100  # 한 요청에 붙일 수 있는 블록 수

TYPE_OPTIONS = ["feature", "bugfix", "refactor", "decision", "ops", "review"]
OUTCOME_OPTIONS = ["완료", "진행중", "보류"]
# Notion 코드 블록이 받는 언어 이름. 목록에 없으면 plain text로 보낸다.
NOTION_LANGUAGES = {
    "bash", "shell", "javascript", "typescript", "json", "yaml",
    "python", "sql", "html", "css", "scss", "diff", "markdown", "mermaid",
    "java", "go", "rust", "kotlin", "swift", "ruby", "php", "xml", "docker",
    "graphql", "toml", "plain text",
}

# Notion이 받지 않는 언어 이름은 가장 가까운 것으로 바꾼다. (tsx/jsx는 400을 낸다)
NOTION_LANGUAGE_ALIASES = {"tsx": "typescript", "jsx": "javascript"}

IMAGE_RE = re.compile(r"^!\[(.*?)\]\((.+?)\)\s*$")
INLINE_RE = re.compile(r"\*\*(.+?)\*\*|\[(.+?)\]\((.+?)\)|`([^`]+)`")
BULLET_RE = re.compile(r"^\s*-\s+")
NUMBER_RE = re.compile(r"^\s*\d+\.\s+")


def request(method, path, payload=None):
    token = os.environ.get("NOTION_TOKEN")
    if not token:
        raise SystemExit("NOTION_TOKEN 환경변수가 없습니다.")
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        f"{API}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")
        raise SystemExit(f"Notion API 오류 {error.code}: {detail}") from None


def upload_file(content, filename, content_type):
    # Notion 파일 업로드: 업로드 객체 생성 → 멀티파트 전송 → 첨부에 쓸 id 반환
    created = request("POST", "/file_uploads", {"filename": filename, "content_type": content_type})
    boundary = uuid4().hex
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode(),
        f"Content-Type: {content_type}\r\n\r\n".encode(),
        content,
        f"\r\n--{boundary}--\r\n".encode(),
    ])
    token = os.environ["NOTION_TOKEN"]
    upload = urllib.request.Request(
        created["upload_url"],
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    try:
        with urllib.request.urlopen(upload) as response:
            response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")
        raise SystemExit(f"파일 업로드 실패 {error.code}: {detail}") from None
    return created["id"]


def text_token(content, bold=False, link=None, code=False):
    token = {"type": "text", "text": {"content": content[:TEXT_LIMIT]}}
    if link:
        token["text"]["link"] = {"url": link}
    annotations = {}
    if bold:
        annotations["bold"] = True
    if code:
        annotations["code"] = True
    if annotations:
        token["annotations"] = annotations
    return token


def rich_text(text):
    # `**굵게**`, `[라벨](url)`, 인라인 코드를 변환한다. 나머지는 일반 텍스트로 둔다.
    tokens, pos = [], 0
    for match in INLINE_RE.finditer(text):
        if match.start() > pos:
            tokens.append(text_token(text[pos:match.start()]))
        if match.group(1):
            tokens.append(text_token(match.group(1), bold=True))
        elif match.group(2):
            tokens.append(text_token(match.group(2), link=match.group(3)))
        else:
            tokens.append(text_token(match.group(4), code=True))
        pos = match.end()
    if pos < len(text):
        tokens.append(text_token(text[pos:]))
    return tokens or [text_token("")]


def callout_block(text):
    return {"object": "block", "type": "callout", "callout": {
        "rich_text": rich_text(text),
        "icon": {"emoji": "💡"},
        "color": "blue_background",
    }}


def divider_block():
    return {"object": "block", "type": "divider", "divider": {}}


def file_block(upload_id, name):
    return {"object": "block", "type": "file", "file": {
        "type": "file_upload",
        "file_upload": {"id": upload_id},
        "caption": rich_text(f"{name} — 내려받아 열면 이미지·서식이 포함된 전체 기록을 볼 수 있다"),
    }}


def build_blocks(body, base_dir=None):
    blocks = []
    for index, (head, content) in enumerate(split_sections(body)):
        if index:  # 섹션 사이를 구분선으로 나눈다.
            blocks.append(divider_block())
        blocks.append({"object": "block", "type": "heading_2",
                       "heading_2": {"rich_text": rich_text(head)}})
        blocks.extend(section_blocks(content, base_dir))
    return blocks


def section_blocks(content, base_dir=None):
    # 코드펜스는 내부에 빈 줄이 있을 수 있어 한 줄씩 훑으며 처리한다.
    blocks, buffer = [], []
    lines = content.strip().splitlines()

    def flush():
        if buffer:
            blocks.extend(chunk_blocks("\n".join(buffer).strip(), base_dir))
            buffer.clear()

    index = 0
    while index < len(lines):
        line = lines[index]
        if line.lstrip().startswith("```"):
            flush()
            language = line.strip().strip("`").strip() or "plain text"
            language = NOTION_LANGUAGE_ALIASES.get(language, language)
            index += 1
            code = []
            while index < len(lines) and not lines[index].lstrip().startswith("```"):
                code.append(lines[index])
                index += 1
            index += 1  # 닫는 펜스
            blocks.append({"object": "block", "type": "code", "code": {
                "rich_text": [text_token("\n".join(code))],
                "language": language if language in NOTION_LANGUAGES else "plain text",
            }})
            continue
        if line.strip():
            buffer.append(line)
        else:
            flush()
        index += 1
    flush()
    return blocks


def chunk_blocks(chunk, base_dir=None):
    lines = chunk.splitlines()
    image = IMAGE_RE.match(chunk)
    if image:
        source = image.group(2)
        if source.startswith(("http://", "https://")):
            return [{"object": "block", "type": "image",
                     "image": {"type": "external", "external": {"url": source}}}]
        if base_dir:
            path = (Path(base_dir) / source).resolve()
            if path.is_file():
                mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                upload_id = upload_file(path.read_bytes(), path.name, mime)
                return [{"object": "block", "type": "image",
                         "image": {"type": "file_upload", "file_upload": {"id": upload_id}}}]
        return []  # 찾을 수 없는 이미지는 건너뛴다.
    for marker, kind in ((BULLET_RE, "bulleted_list_item"), (NUMBER_RE, "numbered_list_item")):
        items = collect_list_items(lines, marker)
        if items:
            return [{"object": "block", "type": kind, kind: {"rich_text": rich_text(item)}}
                    for item in items]
    return [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": rich_text(chunk)}}]


def build_properties(front_matter):
    properties = {"title": {"title": [text_token(front_matter.get("title", "제목 없음"))]}}
    if front_matter.get("date"):
        properties["date"] = {"date": {"start": front_matter["date"]}}
    for key in ("project", "scope", "decision"):
        if front_matter.get(key):
            properties[key] = {"rich_text": [text_token(front_matter[key])]}
    for key in ("type", "outcome"):
        if front_matter.get(key):
            properties[key] = {"select": {"name": front_matter[key]}}
    links = front_matter.get("links") or []
    if links:
        properties["links"] = {"rich_text": [text_token(", ".join(links))]}
    return properties


def create_database(parent_page_id):
    payload = {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "title": [text_token("작업 기록")],
        "properties": {
            "title": {"title": {}},
            "date": {"date": {}},
            "project": {"rich_text": {}},
            "type": {"select": {"options": [{"name": name} for name in TYPE_OPTIONS]}},
            "scope": {"rich_text": {}},
            "decision": {"rich_text": {}},
            "outcome": {"select": {"options": [{"name": name} for name in OUTCOME_OPTIONS]}},
            "links": {"rich_text": {}},
        },
    }
    return request("POST", "/databases", payload)


def find_page(database_id, title):
    result = request("POST", f"/databases/{database_id}/query", {
        "filter": {"property": "title", "title": {"equals": title}},
        "page_size": 1,
    })
    results = result.get("results") or []
    return results[0]["id"] if results else None


def replace_children(page_id, blocks):
    existing = request("GET", f"/blocks/{page_id}/children?page_size={CHILD_LIMIT}")
    for block in existing.get("results", []):
        request("DELETE", f"/blocks/{block['id']}")
    for start in range(0, len(blocks), CHILD_LIMIT):
        request("PATCH", f"/blocks/{page_id}/children",
                {"children": blocks[start:start + CHILD_LIMIT]})


def sync(md_path, database_id, with_html=False):
    md_path = Path(md_path)
    source = md_path.read_text(encoding="utf-8")
    front_matter, _, body = split_front_matter(source)
    title = front_matter.get("title", "제목 없음")
    properties = build_properties(front_matter)

    blocks = []
    if with_html:
        html = render_html(source, embed=True, base_dir=md_path.resolve().parent)
        filename = f"{md_path.stem}.html"
        upload_id = upload_file(html.encode("utf-8"), filename, "text/html")
        blocks.append(file_block(upload_id, filename))
    if front_matter.get("decision"):
        blocks.append(callout_block(front_matter["decision"]))
    if blocks:
        blocks.append(divider_block())
    blocks.extend(build_blocks(body, md_path.resolve().parent))

    page_id = find_page(database_id, title)
    if page_id:
        request("PATCH", f"/pages/{page_id}", {"properties": properties})
        replace_children(page_id, blocks)
        return page_id, "갱신"

    page = request("POST", "/pages", {
        "parent": {"database_id": database_id},
        "properties": properties,
        "children": blocks[:CHILD_LIMIT],
    })
    if len(blocks) > CHILD_LIMIT:
        for start in range(CHILD_LIMIT, len(blocks), CHILD_LIMIT):
            request("PATCH", f"/blocks/{page['id']}/children",
                    {"children": blocks[start:start + CHILD_LIMIT]})
    return page["id"], "생성"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", nargs="?", type=Path, help="worklog 마크다운 파일")
    parser.add_argument("--init-db", metavar="PAGE_ID", help="부모 페이지에 작업 기록 DB를 만들고 ID를 출력")
    parser.add_argument("--db", help="대상 DB ID (기본: NOTION_DB_ID 환경변수)")
    parser.add_argument("--with-html", action="store_true",
                        help="렌더링한 HTML을 페이지에 첨부한다(이미지 포함 전체 기록)")
    args = parser.parse_args()

    if args.init_db:
        database = create_database(args.init_db)
        print(f"DB 생성 완료: {database['id']}")
        print(f"  {database.get('url', '')}")
        print("\n아래를 환경에 추가하세요:")
        print(f"  export NOTION_DB_ID={database['id']}")
        return

    if not args.input:
        parser.error("동기화할 마크다운 파일을 지정하거나 --init-db를 사용하세요.")
    database_id = args.db or os.environ.get("NOTION_DB_ID")
    if not database_id:
        parser.error("NOTION_DB_ID 환경변수가 없습니다. --db로 지정하거나 --init-db를 먼저 실행하세요.")

    page_id, action = sync(args.input, database_id, with_html=args.with_html)
    print(f"{action} 완료: {page_id}")


if __name__ == "__main__":
    main()
