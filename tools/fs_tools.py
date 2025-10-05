from pathlib import Path
import os
import json
import re
import fnmatch
import time
from typing import List, Dict, Optional
from langchain_core.tools import tool

# 共通モジュール（走査・ガード・正規化ロジックを集約）
from tools.fs_modules import (
    resolve_doc_path,
    load_search_paths_globs,
    path_matches_globs,
    normalize_exts,
    rel_posix,
    scan_tree,
    EXCLUDED_NAMES_DEFAULT,
)


# =====================
# ファイル検索/列挙系
# =====================

@tool
def find_files(base_path: str,
               pattern: str = "**/*",
               max_files: int = 2000,
               exclude_dirs: list = None,
               exclude_exts: list = None,
               include_exts: Optional[List[str]] = None,
               include_globs: Optional[List[str]] = None,
               exclude_globs: Optional[List[str]] = None,
               project_id: Optional[int] = None,
               # 追加: pattern を includes の相対パスに対して適用する
               pattern_on_includes: bool = False) -> str:
    """
    base_path配下から、globパターンでファイルを検索し、base_pathからの相対パスを改行区切りの文字列で返します。

    重要（新仕様）:
    - 検索対象は「検索パス設定（search_paths.json）の includes にチェックされた“ファイル”のみ」。
    - project_id は必須。base_path は当該プロジェクトの doc_path 配下である必要があります。
    - ディレクトリ段階での枝刈り（.git / vendor / .github / logs / node_modules / .venv / __pycache__）は共通ロジックに準拠。
    - includes/excludes の globs は使用せず、includes のファイル集合をフィルタして返します。
    - pattern_on_includes=True の場合、pattern は base_path 相対ではなく「includes に保存された相対パス（doc_path 相対）」に対してマッチします。
    - excludes は無視します（ignore_excludes=True）。明示的に includes に入っているものを優先します。
    """
    if project_id is None:
        raise ValueError("find_files: project_id は必須です（検索対象は検索パスにチェックされたファイルのみ）")

    results = scan_tree(
        mode="files",
        base_path=base_path,
        project_id=project_id,
        pattern=pattern,
        include_exts=include_exts,
        exclude_exts=exclude_exts,
        max_items=max_files,
        # globs は使わず、search_paths.json の includes（ファイル集合）に限定
        extra_excluded_dirs=set(exclude_dirs or []),
        require_project=True,
        require_search_paths=True,
        allow_ancestor_for_include=False,
        pattern_on_includes=pattern_on_includes,
        # 追加: excludes を見ない
        ignore_excludes=True,
    )
    return "\n".join(results)


@tool
def list_files(
        base_path: str,
        include_exts: Optional[List[str]] = None,
        project_id: Optional[int] = None,
        # 追加: 直下のみ（非再帰）に限定するオプション
        shallow: bool = False,
) -> str:
    """
    base_path 配下のファイルを拡張子フィルタで列挙し、相対パス（base_path 相対）を改行区切りで返します。
    ただし検索対象は doc_path 配下に限定し、検索スコープは search_paths.json（includes）に必ず従います。

    変更点:
    - project_id を必須化（None はエラー）。
    - base_path は doc_path 配下である必要があり、外を指す場合はエラー。
    - search_paths.json の excludes は無視します（ignore_excludes=True）。
    - .git / vendor / .github / logs / node_modules / .venv / __pycache__ は探索から除外。
    - shallow=True の場合は「base_path 直下のファイルのみ」（サブディレクトリ配下は除外）。
    """
    if project_id is None:
        raise ValueError("list_files: project_id は必須です")

    # 既定の拡張子セット（未指定時）
    default_exts = {".py", ".pyi",
                    ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
                    ".php",
                    ".phtml", ".html", ".htm", ".css", ".scss", ".less",
                    ".vue", ".svelte", ".json", ".yaml", ".yml", ".toml",
                    ".ini", ".env", ".md", ".mdx", ".txt", ".csv", ".tsv",
                    ".sql", ".xml", ".sh", ".bat", ".ps1", ".properties",
                    ".cfg", ".conf"}

    allow_exts = normalize_exts(include_exts, default_set=default_exts)

    results = scan_tree(
        mode="files",
        base_path=base_path,
        project_id=project_id,
        include_exts=list(allow_exts),
        require_project=True,
        require_search_paths=True,
        allow_ancestor_for_include=False,
        ignore_excludes=True,
    )

    if shallow:
        # base_path 直下（"/" を含まない相対パス）のみ
        results = [p for p in results if "/" not in p]

    return "\n".join(results)


@tool
def list_dirs(
        base_path: str,
        pattern: str = "**/*",
        max_dirs: int = 200,
        honor_gitignore: bool = True,  # 互換のため残置（現時点では未使用）
        project_id: int = None,
        # 追加: pattern を includes の相対パスに対して適用する
        pattern_on_includes: bool = False,
        # 追加: 直下のみ（非再帰）に限定するオプション
        shallow: bool = False,
) -> str:
    """
    base_path配下から、ディレクトリのみを検索して相対パスを改行区切りで返します。
    - 検索対象は doc_path 配下に限定し、search_paths.json（includes）に従って走査範囲を枝刈りします。
    - .git / vendor など巨大ディレクトリは、ディレクトリ段階で除外します。

    例: list_dirs("repo", "src/*") -> "src/components\nsrc/utils\n..."

    Args:
        base_path: 検索の起点ディレクトリ（doc_path またはその配下のみ許可）
        pattern:  globパターン（例: "**/*", "src/*" など） — 通常は base_path 相対のディレクトリパスに適用
        max_dirs: 返す最大件数（過大応答の抑制）
        project_id: 必須。UI 保存の検索パス（search_paths.json）を取得するために使用
        pattern_on_includes: True の場合、pattern は includes の相対パスに対して適用
        shallow: True の場合、base_path 直下のディレクトリのみ返す

    Returns:
        見つかった相対ディレクトリパスの改行区切り文字列（0件でも空文字列）
    """
    if project_id is None:
        raise ValueError("list_dirs: project_id は必須です")

    results = scan_tree(
        mode="dirs",
        base_path=base_path,
        project_id=project_id,
        pattern=pattern,
        max_items=max_dirs,
        require_project=True,
        require_search_paths=True,
        allow_ancestor_for_include=True,
        pattern_on_includes=pattern_on_includes,
        ignore_excludes=True,
    )

    if shallow:
        # base_path 直下のディレクトリのみ（"/" を含まず、"." は除外）
        results = [d for d in results if d and d != "." and "/" not in d]

    return "\n".join(results)


# ============
# FS 基本操作
# ============

@tool
def read_file(file_name: str, project_id: int) -> str:
    """
    引き数に指定されたファイルの内容を読み取り、テキストで返却する。
    相対パスが指定された場合は doc_path を基準に解決し、doc_path の外を指す場合はエラーとする。
    """
    base = resolve_doc_path(project_id)

    p = Path(file_name).expanduser()
    if not p.is_absolute():
        p = base / p
    p = p.resolve()

    try:
        _ = p.relative_to(base)
    except Exception:
        raise ValueError(f"read_file: path must be under doc_path (got: {p}, doc_path: {base})")

    with open(p, encoding="utf-8") as f:
        return f.read()


# 補助: search_paths.json の includes へファイル（doc_path 相対 POSIX）を追加
# 失敗しても write_file 自体は成功扱いにするため、例外は握り潰します。
def _add_to_search_includes(project_id: int, rel_path: str) -> None:
    try:
        inst = Path.cwd() / "instance" / str(project_id)
        inst.mkdir(parents=True, exist_ok=True)
        sp = inst / "search_paths.json"
        if sp.exists():
            try:
                data = json.loads(sp.read_text(encoding="utf-8")) or {}
            except Exception:
                data = {}
        else:
            data = {}
        includes = list(data.get("includes") or [])
        excludes = list(data.get("excludes") or [])
        rel_norm = str(rel_path).replace("\\", "/").strip("/")
        if rel_norm and rel_norm not in includes:
            includes.append(rel_norm)
        # バージョンは v2 を既定とする
        try:
            ver = int(data.get("version") or 2)
        except Exception:
            ver = 2
        data["version"] = ver
        data["includes"] = includes
        data["excludes"] = excludes
        sp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


@tool
def write_file(file_path: str, content: str, project_id: int) -> bool:
    """
    第1引数に指定されたパスへ UTF-8 テキストを書き込みます（上書き）。
    相対パスが指定された場合は doc_path 配下を基準として解決し、doc_path の外を指す場合は書き込みません。

    追記: 新規作成時は instance/<project_id>/search_paths.json の includes に
    当該ファイル（doc_path 相対 POSIX）を自動追加します。
    """
    try:
        base = resolve_doc_path(project_id)
        p = Path(file_path).expanduser()
        if not p.is_absolute():
            p = base / p
        p = p.resolve()
        try:
            _ = p.relative_to(base)
        except Exception:
            return False
        existed_before = p.exists()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
        if not existed_before:
            try:
                rel_from_doc = p.relative_to(base).as_posix()
                _add_to_search_includes(project_id, rel_from_doc)
            except Exception:
                pass
        return True
    except Exception:
        return False


@tool
def make_dirs(dir_path: str, project_id: int) -> bool:
    """
    指定ディレクトリを作成します（親ディレクトリもまとめて作成）。
    相対パスが指定された場合は doc_path 配下を基準として解決し、doc_path の外を指す場合は作成しません。
    """
    try:
        base = resolve_doc_path(project_id)
        p = Path(dir_path).expanduser()
        if not p.is_absolute():
            p = base / p
        p = p.resolve()
        try:
            _ = p.relative_to(base)
        except Exception:
            return False
        p.mkdir(parents=True, exist_ok=True)
        return True
    except Exception:
        return False


# ============
# メタ情報系
# ============

@tool
def file_stat(file_path: str, project_id: int) -> str:
    """
    ファイルの存在・サイズ・更新時刻・行数を返す（JSON文字列）。
    相対パスは doc_path を基準に解決し、doc_path 外を指す場合はエラーを返す。
    """
    info: Dict[str, object] = {"exists": False, "path": str(file_path)}

    try:
        base = resolve_doc_path(project_id)
    except Exception as e:
        info["error"] = f"doc_path_resolve_failed: {type(e).__name__}: {e}"
        return json.dumps(info, ensure_ascii=False)

    p = Path(file_path).expanduser()
    if not p.is_absolute():
        p = base / p
    p = p.resolve()
    info["path"] = str(p)

    try:
        _ = p.relative_to(base)
    except Exception:
        info["error"] = f"path_must_be_under_doc_path (got: {p}, doc_path: {base})"
        return json.dumps(info, ensure_ascii=False)

    if not p.exists() or not p.is_file():
        return json.dumps(info, ensure_ascii=False)

    try:
        st = p.stat()
        info["exists"] = True
        info["size"] = int(st.st_size)
        info["mtime"] = float(st.st_mtime)
        line_count = 0
        with p.open("r", encoding="utf-8", errors="ignore") as f:
            for _ in f:
                line_count += 1
        info["line_count"] = int(line_count)
    except Exception as e:
        info["error"] = f"{type(e).__name__}: {e}"

    return json.dumps(info, ensure_ascii=False)


@tool
def read_file_range(file_path: str, start_line: int, end_line: int, project_id: int) -> str:
    """
    1始まりの行番号で [start_line, end_line] の内容を返す（JSON文字列）。
    相対パスは doc_path を基準に解決し、doc_path の外を指す場合はエラーにする。
    範囲外は自動調整（start>=1、end>=start）。存在しない場合は exists=False を返す。
    """
    result: Dict[str, object] = {}

    try:
        base = resolve_doc_path(project_id)
    except Exception as e:
        result.update({"exists": False, "error": f"doc_path_resolve_failed: {type(e).__name__}: {e}"})
        return json.dumps(result, ensure_ascii=False)

    p = Path(file_path).expanduser()
    if not p.is_absolute():
        p = base / p
    p = p.resolve()
    result["path"] = str(p)

    try:
        _ = p.relative_to(base)
    except Exception:
        result.update({
            "exists": False,
            "error": f"path_must_be_under_doc_path (got: {p}, doc_path: {base})"
        })
        return json.dumps(result, ensure_ascii=False)

    if not p.exists() or not p.is_file():
        result["exists"] = False
        return json.dumps(result, ensure_ascii=False)

    s = max(1, int(start_line))
    e = max(s, int(end_line))

    lines: List[str] = []
    try:
        with p.open("r", encoding="utf-8", errors="ignore") as f:
            for i, line in enumerate(f, start=1):
                if i < s:
                    continue
                if i > e:
                    break
                lines.append(line)
        result.update({
            "exists": True,
            "start_line": s,
            "end_line": e,
            "content": "".join(lines),
        })
    except Exception as ex:
        result.update({
            "exists": False,
            "error": f"{type(ex).__name__}: {ex}"
        })

    return json.dumps(result, ensure_ascii=False)


# ==============
# Grep 相当検索
# ==============

@tool
def search_grep(
        base_path: str,
        regex: str,
        project_id: int,
        extensions: Optional[List[str]] = None,
        max_files: int = 5000,
        max_matches_per_file: int = 50,
        max_total_matches: int = 2000,
        context_lines: int = 2,
        size_limit_bytes_per_file: int = 5_000_000,
        encoding: str = "utf-8",
        follow_symlinks: bool = False,
        timeout_seconds: int = 10,
) -> str:
    """
    ディレクトリ配下を正規表現で検索します（簡易版）。
    - 検索対象は doc_path 配下のみ。base_path が doc_path の外を指す場合はエラー。
    - 検索ディレクトリのスコープは search_paths.json の includes に準拠し、excludes は無視します。
    - スコープ評価は fs_modules.scan_tree に委譲し、include 優先（祖先許容）で統一。
    """
    start_ts = time.time()
    result = {
        "ok": True,
        "base_path": base_path,
        "doc_path": "",
        "query": regex,
        "is_regex": True,
        "stats": {
            "scanned_files": 0,
            "matched_files": 0,
            "total_matches": 0,
            "scanned_bytes": 0,
            "duration_ms": 0,
            "truncated": False,
        },
        "files": [],
        "errors": [],
    }

    if project_id is None:
        result["ok"] = False
        result["errors"].append({"file_path": "", "error": "project_id is required"})
        return json.dumps(result, ensure_ascii=False)

    try:
        doc_path = Path(resolve_doc_path(project_id)).expanduser().resolve()
    except Exception as e:
        result["ok"] = False
        result["errors"].append({"file_path": "", "error": f"doc_path resolve failed: {e}"})
        return json.dumps(result, ensure_ascii=False)

    result["doc_path"] = str(doc_path)

    root = Path(base_path).expanduser().resolve()
    try:
        _ = root.relative_to(doc_path)
    except Exception:
        result["ok"] = False
        result["errors"].append({
            "file_path": str(root),
            "error": "base_path must be under doc_path",
        })
        return json.dumps(result, ensure_ascii=False)

    if not root.exists() or not root.is_dir():
        result["ok"] = False
        result["errors"].append({"file_path": str(root), "error": "base_path not found or not directory"})
        return json.dumps(result, ensure_ascii=False)

    # 正規表現コンパイル
    try:
        pattern = re.compile(regex)
    except re.error as e:
        result["ok"] = False
        result["errors"].append({"file_path": "", "error": f"invalid regex: {e}"})
        return json.dumps(result, ensure_ascii=False)

    # 候補ファイルを共通スキャナで収集（include 優先・祖先許容・search_paths.jsonに準拠）
    try:
        candidates: List[str] = scan_tree(
            mode="files",
            base_path=str(root),
            project_id=project_id,
            include_exts=extensions,  # None の場合は全拡張子
            require_project=True,
            require_search_paths=True,
            allow_ancestor_for_include=True,
            max_items=max_files,
            ignore_excludes=True,
        )
    except Exception as e:
        result["ok"] = False
        result["errors"].append({"file_path": str(root), "error": f"scan_tree error: {e}"})
        return json.dumps(result, ensure_ascii=False)

    scanned_files = 0
    matched_files = 0
    total_matches = 0
    scanned_bytes = 0
    global_truncated = False

    # 1行テキストの上限（minified対策）
    LINE_SNIPPET_MAX = 500

    for rel in candidates:
        if timeout_seconds and (time.time() - start_ts) > timeout_seconds:
            global_truncated = True
            break

        fpath = (root / rel).resolve()
        if not fpath.exists() or not fpath.is_file():
            continue

        try:
            size = fpath.stat().st_size
        except OSError as e:
            result["errors"].append({"file_path": rel, "error": str(e)})
            continue

        if size_limit_bytes_per_file and size > size_limit_bytes_per_file:
            continue

        scanned_files += 1
        if scanned_files > max_files:
            global_truncated = True
            break

        try:
            with open(fpath, "r", encoding=encoding, errors="ignore") as rf:
                lines = rf.readlines()
        except Exception as e:
            result["errors"].append({"file_path": rel, "error": str(e)})
            continue

        scanned_bytes += size
        matches = []
        per_file_truncated = False

        for i, line in enumerate(lines, start=1):
            try:
                it = list(pattern.finditer(line))
            except Exception as e:
                result["errors"].append({"file_path": rel, "error": f"regex error at line {i}: {e}"})
                it = []

            for m in it:
                col_start = m.start() + 1
                col_end = m.end() + 1

                before_start = max(0, i - 1 - context_lines)
                before = [l.rstrip("\n")[:LINE_SNIPPET_MAX] + ("...(truncated)" if len(l.rstrip("\n")) > LINE_SNIPPET_MAX else "") for l in lines[before_start: i - 1]] if context_lines > 0 else []
                after_end = min(len(lines), i + context_lines)
                after = [l.rstrip("\n")[:LINE_SNIPPET_MAX] + ("...(truncated)" if len(l.rstrip("\n")) > LINE_SNIPPET_MAX else "") for l in lines[i: after_end]] if context_lines > 0 else []

                display_line = line.rstrip("\n")
                if len(display_line) > LINE_SNIPPET_MAX:
                    display_line = display_line[:LINE_SNIPPET_MAX] + "...(truncated)"

                matches.append({
                    "line_no": i,
                    "col_start": col_start,
                    "col_end": col_end,
                    "line": display_line,
                    "context_before": before,
                    "context_after": after,
                })

                total_matches += 1
                if max_total_matches and total_matches >= max_total_matches:
                    per_file_truncated = True
                    global_truncated = True
                    break

                if max_matches_per_file and len(matches) >= max_matches_per_file:
                    per_file_truncated = True
                    break

            if per_file_truncated:
                break

        if matches:
            matched_files += 1
            result["files"].append({
                "file_path": rel,
                "ext": fpath.suffix.lower(),
                "size": size,
                "match_count": len(matches),
                "truncated": per_file_truncated,
                "encoding_used": encoding,
                "matches": matches,
            })

        if global_truncated:
            break

    duration_ms = int((time.time() - start_ts) * 1000)
    result["stats"].update({
        "scanned_files": scanned_files,
        "matched_files": matched_files,
        "total_matches": total_matches,
        "scanned_bytes": scanned_bytes,
        "duration_ms": duration_ms,
        "truncated": global_truncated,
    })

    return json.dumps(result, ensure_ascii=False)
