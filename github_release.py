#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub Release 자동화 스크립트 (VComHub)

기능:
1. release/release 폴더 또는 VERSION.txt에서 가장 최신 버전을 자동으로 감지
2. 해당 버전의 실행 파일(.exe)과 SBOM(.sbom.json)을 릴리스 에셋으로 지정
3. README.md에서 해당 버전의 히스토리(Changelog)를 자동 추출하여 릴리스 노트로 구성
4. VERSION.txt의 SHA256 체크섬 및 빌드 메타데이터를 노트에 첨부
5. gh CLI를 통해 지정된 GitHub 저장소에 즉시 릴리스 배포
"""

import os
import sys
import re
import argparse
import subprocess
import tempfile
from pathlib import Path

# Windows 콘솔 인코딩 방어
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

DEFAULT_REPO = "c0z0c-release/VComHub"


def find_release_dir() -> Path:
    """release 디렉토리의 루트 경로를 찾습니다."""
    script_dir = Path(__file__).resolve().parent

    # 1. 스크립트 자체가 release 디렉토리에 있는 경우 (release/github_release.py)
    if (script_dir / "VERSION.txt").is_file() and (script_dir / "README.md").is_file():
        return script_dir

    # 2. 현재 작업 디렉토리가 VComHub 루트이고 하위에 release/ 가 있는 경우
    cwd = Path.cwd()
    if (cwd / "release" / "VERSION.txt").is_file():
        return cwd / "release"

    if (cwd / "VERSION.txt").is_file():
        return cwd

    # 3. 스크립트 하위 디렉토리 검색
    if (script_dir / "release" / "VERSION.txt").is_file():
        return script_dir / "release"

    return script_dir


def check_gh_cli() -> None:
    """gh CLI 설치 및 로그인 상태를 확인합니다."""
    try:
        subprocess.run(["gh", "--version"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", check=True)
    except (subprocess.SubprocessError, FileNotFoundError):
        print("[ERROR] GitHub CLI('gh')가 설치되어 있지 않거나 PATH에 없습니다.")
        print("        설치 안내: https://cli.github.com/")
        sys.exit(1)

    auth_res = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
    if auth_res.returncode != 0:
        print("[ERROR] GitHub CLI 로그인이 필요합니다.")
        print("        다음 명령어를 실행하여 먼저 로그인해 주세요:")
        print("        gh auth login")
        sys.exit(1)


def parse_version_key(v_str: str):
    """버전 정렬용 튜플 변환 (예: '0.6.27' -> (0, 6, 27))"""
    nums = re.findall(r"\d+", v_str)
    return tuple(map(int, nums)) if nums else (0,)


def detect_latest_version(release_dir: Path) -> str:
    """VERSION.txt 또는 release/release/ 디렉토리에서 최신 버전을 찾습니다."""
    version_file = release_dir / "VERSION.txt"
    if version_file.is_file():
        try:
            with open(version_file, "r", encoding="utf-8") as f:
                first_line = f.readline().strip()
                if re.match(r"^\d+\.\d+\.\d+", first_line):
                    return first_line
        except Exception:
            pass

    # release/release 폴더 내 파일 검색
    artifact_dir = release_dir / "release"
    if artifact_dir.is_dir():
        versions = []
        for p in artifact_dir.glob("vcomhub-*.exe"):
            m = re.search(r"vcomhub-([0-9\.]+)\.exe$", p.name, re.IGNORECASE)
            if m:
                versions.append(m.group(1))
        if versions:
            versions.sort(key=parse_version_key)
            return versions[-1]

    raise RuntimeError("최신 버전을 감지할 수 없습니다. VERSION.txt 또는 release/release/*.exe 파일을 확인하세요.")


def extract_changelog_from_readme(readme_path: Path, version: str) -> str:
    """README.md에서 지정된 버전의 변경 내역을 추출합니다."""
    if not readme_path.is_file():
        return ""

    try:
        content = readme_path.read_text(encoding="utf-8")
    except Exception:
        return ""

    # ### 0.6.20 ... 패턴 검색 (영어/한국어 등 여러 섹션이 있으면 모두 수집)
    pattern = rf"(?m)^###\s+{re.escape(version)}(?:\s+[^\n]*)?\n([\s\S]*?)(?=^###\s+|^##\s+|\Z)"
    matches = re.findall(pattern, content)

    if not matches:
        return ""

    sections = []
    for body in matches:
        cleaned = body.strip()
        if cleaned:
            sections.append(cleaned)

    if len(sections) == 1:
        return f"### What's Changed\n\n{sections[0]}"
    elif len(sections) >= 2:
        return f"### What's Changed (English)\n\n{sections[0]}\n\n---\n### 변경 사항 (한국어)\n\n{sections[1]}"

    return ""


def get_version_txt_info(version_file: Path) -> str:
    """VERSION.txt에서 메타데이터와 SHA256 체크섬을 읽어 마크다운으로 변환합니다."""
    if not version_file.is_file():
        return ""

    try:
        lines = [line.strip() for line in version_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        if len(lines) <= 1:
            return ""

        # lines[0]은 버전 번호
        info_lines = []
        for line in lines[1:]:
            info_lines.append(f"- `{line}`")

        return "### Verification & Metadata\n\n" + "\n".join(info_lines)
    except Exception:
        return ""


def build_release_notes(release_dir: Path, version: str) -> str:
    """릴리스 노트 본문을 생성합니다."""
    readme_path = release_dir / "README.md"
    version_file = release_dir / "VERSION.txt"

    changelog = extract_changelog_from_readme(readme_path, version)
    meta_info = get_version_txt_info(version_file)

    notes_parts = []
    if changelog:
        notes_parts.append(changelog)
    else:
        notes_parts.append(f"### Release v{version}\n\nVComHub v{version} release build.")
        notes_parts.append(
            "> [!NOTE]\n"
            f"> 이 버전(v{version})에 대한 상세 변경 내역은 아직 `README.md`에 추가되지 않았습니다."
        )

    if meta_info:
        notes_parts.append(meta_info)

    # 하단 Source code 안내 코멘트 추가
    source_code_notice = (
        "---\n"
        "> [!NOTE]\n"
        f"> **다운로드 안내 / Download Notice:**\n"
        f"> - VComHub를 실행하려면 아래 **Assets** 목록에서 **`vcomhub-{version}.exe`** 파일을 다운로드하세요.\n"
        "> - 하단에 표시되는 **`Source code (zip)`** 및 **`Source code (tar.gz)`** 항목은 GitHub 시스템이 자동 생성한 저장소 스냅샷 아카이브입니다. 실행 프로그램(.exe)이 아니므로 일반적인 프로그램 사용에는 필요하지 않습니다.\n"
        ">\n"
        f"> *(To run VComHub, please download **`vcomhub-{version}.exe`** from the Assets list below. The auto-generated `Source code (zip/tar.gz)` files are repository snapshots without the executable binary and are not needed for running the application.)*"
    )
    notes_parts.append(source_code_notice)

    return "\n\n".join(notes_parts)


def main():
    parser = argparse.ArgumentParser(description="GitHub Release 자동 생성 및 배포 도구")
    parser.add_argument("--version", "-v", help="배포할 버전 (미지정 시 최신 버전 자동 감지)")
    parser.add_argument("--repo", "-r", default=DEFAULT_REPO, help=f"GitHub 저장소 (기본값: {DEFAULT_REPO})")
    parser.add_argument("--dry-run", action="store_true", help="실제 업로드 없이 배포 정보와 노트를 미리 확인")
    parser.add_argument("--draft", action="store_true", help="초안(Draft) 상태로 생성 (기본값은 즉시 배포)")
    parser.add_argument("--prerelease", action="store_true", help="시험판(Pre-release)으로 등록")
    args = parser.parse_args()

    release_dir = find_release_dir()
    print(f"[INFO] 기준 디렉토리: {release_dir}")

    # 버전 감지
    version = args.version or detect_latest_version(release_dir)
    tag = f"v{version}" if not version.startswith("v") else version
    raw_version = version.lstrip("v")

    print(f"[INFO] 배포 태그: {tag} (버전: {raw_version})")
    print(f"[INFO] 대상 저장소: {args.repo}")

    # 업로드할 파일 확인
    artifact_dir = release_dir / "release"
    exe_file = artifact_dir / f"vcomhub-{raw_version}.exe"
    sbom_file = artifact_dir / f"vcomhub-{raw_version}.sbom.json"

    assets = []
    if exe_file.is_file():
        assets.append(exe_file)
    else:
        print(f"[ERROR] 실행 파일이 존재하지 않습니다: {exe_file}")
        sys.exit(1)

    if sbom_file.is_file():
        assets.append(sbom_file)
    else:
        print(f"[WARN] SBOM 파일이 없습니다: {sbom_file} (생략됨)")

    # 릴리스 노트 생성
    notes = build_release_notes(release_dir, raw_version)

    print("\n" + "=" * 60)
    print("[PREVIEW] 생성된 릴리스 노트:")
    print("=" * 60)
    print(notes)
    print("=" * 60)

    print("\n[INFO] 업로드 대상 파일:")
    for a in assets:
        size_mb = a.stat().st_size / (1024 * 1024)
        print(f"  - {a.name} ({size_mb:.2f} MB)")

    if args.dry_run:
        print("\n[DRY-RUN] 실제 배포를 실행하지 않고 테스트를 마쳤습니다.")
        return

    # gh CLI 점검
    check_gh_cli()

    # 임시 파일에 릴리스 노트 작성 (인코딩/이스케이프 안전 처리)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as tf:
        tf.write(notes)
        notes_file_path = tf.name

    try:
        cmd = [
            "gh", "release", "create", tag,
            *[str(a) for a in assets],
            "--repo", args.repo,
            "--title", tag,
            "--notes-file", notes_file_path
        ]
        if args.draft:
            cmd.append("--draft")
        if args.prerelease:
            cmd.append("--prerelease")

        mode_str = "Draft 생성" if args.draft else "즉시 배포"
        print(f"\n[EXEC] GitHub Release {mode_str} 진행 중...")
        print(f"       명령어: {' '.join(cmd[:6])} ...")

        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
        if result.returncode == 0:
            print("\n[SUCCESS] 배포가 성공적으로 완료되었습니다!")
            if result.stdout.strip():
                print(f"          릴리스 URL: {result.stdout.strip()}")
            else:
                print(f"          릴리스 페이지: https://github.com/{args.repo}/releases/tag/{tag}")
        else:
            print(f"\n[FAIL] 배포 실패 (종료 코드 {result.returncode}):")
            print(result.stderr.strip() or result.stdout.strip())
            sys.exit(result.returncode)
    finally:
        if os.path.exists(notes_file_path):
            try:
                os.remove(notes_file_path)
            except OSError:
                pass


if __name__ == "__main__":
    main()

