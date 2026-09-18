"""이 저장소의 공통 규칙을 어떤 AI 도구든 그 전역 지침 파일에 연결한다.

- 대상: TARGETS에 등록된 도구별 전역 지침 파일 (기본: Claude Code, Codex). 새 도구는 TARGETS에 추가한다.
- 관리 블록(마커 사이)만 생성·교체한다. 마커 밖의 기존 내용은 보존한다.
- 처음 연결하는 파일은 타임스탬프 백업을 만든다.
- 저장소를 수정하면 다시 실행해 전역 설정을 동기화한다. 저장소 위치가 바뀌면 다시 실행한다.
"""

import argparse
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BEGIN = "<!-- >>> ai-workflow (scripts/install.py가 관리하는 블록 · 직접 편집하지 말 것) >>> -->"
END = "<!-- <<< ai-workflow <<< -->"

# 도구별 전역 지침 파일. 새 AI 도구를 연결하려면 여기에 "<도구>": <전역 지침 경로>를 추가하고
# tools/<도구>/instructions.md에 어댑터를 만든다. 공통 규칙과 절차는 그대로 재사용된다.
TARGETS = {
    "claude-code": Path.home() / ".claude" / "CLAUDE.md",
    "codex": Path.home() / ".codex" / "AGENTS.md",
}


def doc_count():
    return sum(len(list((ROOT / folder).glob("*.md"))) for folder in ("workflows", "guides"))


def to_absolute(text):
    # 문서 참조(`workflows/x.md`, `guides/x.md`)를 절대 경로로 바꿔 전역 어디서든 읽을 수 있게 한다.
    return re.sub(
        r"`((?:workflows|guides)/[\w.-]+\.md)`",
        lambda m: f"`{ROOT / m.group(1)}`",
        text,
    )


def render(tool):
    common = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    adapter = (ROOT / "tools" / tool / "instructions.md").read_text(encoding="utf-8")
    return to_absolute(common) + "\n" + adapter


def block(tool):
    return f"{BEGIN}\n<!-- source: {ROOT} -->\n\n{render(tool)}\n{END}\n"


def merge(existing, new_block):
    if BEGIN in existing and END in existing:
        pre = existing[: existing.index(BEGIN)]
        post = existing[existing.index(END) + len(END):].lstrip("\n")
        return pre + new_block + (post if post else "")
    if existing.strip():
        return existing.rstrip() + "\n\n" + new_block
    return new_block


def extract_block(text):
    if BEGIN in text and END in text:
        return text[text.index(BEGIN): text.index(END) + len(END)]
    return None


def check_tool(tool):
    # 연결 상태를 점검한다. 정상이면 True. 저장소 이동·문서 누락·원본 드리프트를 잡는다.
    target = TARGETS[tool]
    current = extract_block(target.read_text(encoding="utf-8")) if target.exists() else None
    if current is None:
        print(f"[{tool}] ✗ 미연결 → install 필요: {target}")
        return False

    ok = True
    match = re.search(r"<!-- source: (.*?) -->", current)
    source = match.group(1).strip() if match else None
    if source != str(ROOT):
        print(f"[{tool}] ✗ 원본 위치 불일치 (블록={source} / 현재={ROOT}) → 재install 필요")
        ok = False

    missing = [p for p in re.findall(r"`(/[^`]+\.md)`", current) if not Path(p).exists()]
    if missing:
        print(f"[{tool}] ✗ 참조 문서 {len(missing)}개 누락:")
        for path in missing:
            print(f"    - {path}")
        ok = False

    if ok and current.rstrip("\n") != block(tool).rstrip("\n"):
        print(f"[{tool}] ✗ 원본과 내용 불일치(드리프트) → 재install로 동기화 필요")
        ok = False

    if ok:
        print(f"[{tool}] ✓ 정상: {target}")
    return ok


def install_tool(tool, dry_run, uninstall, show):
    target = TARGETS[tool]
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    had_block = BEGIN in existing and END in existing
    new_block = None if uninstall else block(tool)

    if uninstall:
        if not had_block:
            print(f"[{tool}] 관리 블록 없음, 변경 없음: {target}")
            return
        pre = existing[: existing.index(BEGIN)]
        post = existing[existing.index(END) + len(END):].lstrip("\n")
        merged = (pre.rstrip() + "\n" if pre.strip() else "") + post
    else:
        merged = merge(existing, new_block)

    if merged == existing:
        print(f"[{tool}] 이미 최신 상태: {target}")
        return
    if dry_run:
        action = "관리 블록 제거" if uninstall else ("관리 블록 갱신" if had_block else "새로 연결")
        print(f"\n[{tool}] (dry-run) {action} 예정")
        print(f"  대상: {target}")
        if not uninstall:
            print(f"  주입 내용: 공통 규칙(AGENTS.md, 절차 경로 {doc_count()}개 포함) + {tool} 어댑터")
            print(f"  블록 크기: {len(new_block.splitlines())}줄")
        if existing and not had_block:
            print(f"  기존 파일 백업 예정 (기존 내용은 마커 밖에 그대로 보존)")
        elif not existing:
            print(f"  새 파일 생성 예정")
        if show and not uninstall:
            print("  ---- 주입될 관리 블록 ----")
            print("\n".join("  " + line for line in new_block.splitlines()))
            print("  ---- 끝 ----")
        elif not uninstall:
            print(f"  전체 내용을 보려면 --show 를 붙이세요.")
        return

    target.parent.mkdir(parents=True, exist_ok=True)
    # 관리 블록이 없던 기존 파일은 최초 연결 시 원본을 백업한다.
    if existing and not had_block:
        backup = target.with_name(target.name + f".bak-{datetime.now():%Y%m%d-%H%M%S}")
        backup.write_text(existing, encoding="utf-8")
        print(f"[{tool}] 기존 파일 백업: {backup}")
    target.write_text(merged, encoding="utf-8")
    print(f"[{tool}] {'관리 블록 제거' if uninstall else '연결 완료'}: {target}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tool", choices=list(TARGETS), help="한 도구만 처리 (기본: 전체)")
    parser.add_argument("--dry-run", action="store_true", help="변경 없이 무엇이 바뀔지만 출력")
    parser.add_argument("--show", action="store_true", help="dry-run 시 주입될 관리 블록 전체 내용을 함께 출력")
    parser.add_argument("--check", action="store_true", help="연결 상태 점검 (원본 위치·참조 문서·내용 동기화)")
    parser.add_argument("--uninstall", action="store_true", help="관리 블록 제거")
    args = parser.parse_args()

    tools = [args.tool] if args.tool else list(TARGETS)

    if args.check:
        results = [check_tool(tool) for tool in tools]
        raise SystemExit(0 if all(results) else 1)

    for tool in tools:
        install_tool(tool, args.dry_run, args.uninstall, args.show)

    if not args.dry_run and not args.uninstall:
        print("\n기존에 손으로 작성한 전역 규칙이 있다면 관리 블록과 중복되지 않는지 백업본과 비교해 확인하세요.")


if __name__ == "__main__":
    main()
