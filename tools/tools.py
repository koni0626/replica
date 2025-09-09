from pathlib import Path
import os
import json
import re
import fnmatch
from typing import List, Dict, Optional
from langchain_core.tools import tool


# ----------------------------
# .gitignore 対応の簡易ヘルパ
# ----------------------------

def _load_gitignore_patterns(root: Path) -> list:
    """root/.gitignore を読み込み、コメント/空行を除いたパターンの配列を返す（簡易実装）。"""
    pats: list[str] = []
    gi = root / ".gitignore"
    if not gi.exists():
        return pats
    try:
        with open(gi, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                s = (line or "").strip()
                if not s or s.startswith("#"):
                    continue
                pats.append(s)
    except Exception:
        # 読み取り失敗時は無視
        pass
    return pats


def _match_gitignore_pattern(rel: str, pat: str) -> bool:
    """.gitignore の主要パターンのみ簡易対応してマッチ判定を返す。- 先頭の '!'（否定）はここでは処理しない（呼び出し側で順序トグル）
    - 先頭の '/' はルートアンカー（rel 全体に対して評価）
    - 末尾の '/' はディレクトリ指定（プレフィックス一致）
    - その他はどこにでもマッチしうるパターンとして fnmatch（basename も評価）
    これは Git の完全な挙動ではありませんが、一般的なユースケース（node_modules, dist, *.log など）をカバーします。"""
    posix = rel.replace("\\", "/")

    # 否定はここでは無視（上位で処理）
    neg = pat.startswith("!")
    if neg:
        pat = pat[1:]

    # ルートアンカー
    anchored = pat.startswith("/")
    if anchored:
        pat = pat[1:]

    # ディレクトリ指定（末尾スラッシュ）
    dir_only = pat.endswith("/")
    if dir_only:
        pat = pat.rstrip("/")

    # ディレクトリ指定はプレフィックス一致
    if dir_only:
        if anchored:
            return posix == pat or posix.startswith(pat + "/")
        else:
            # どこかに含まれていればOK
            return ("/" + posix).find("/" + pat + "/") != -1 or posix.startswith(pat + "/") or posix == pat

    # ファイル/汎用パターン
    if anchored:
        # ルート基準で fnmatch
        if fnmatch.fnmatch(posix, pat):
            return True
        # 明示的なプレフィックス一致も試す
        if posix.startswith(pat):
            return True
        return False

    # 非アンカー: どこにでも
    if fnmatch.fnmatch(posix, pat):
        return True
    # basename に対しても評価（例: *.log）
    base = posix.split("/")[-1]
    if fnmatch.fnmatch(base, pat):
        return True
    # サブパスに対して '**/pat' 相当のゆるい一致
    if "/" in posix and fnmatch.fnmatch(posix, f"**/{pat}"):
        return True
    return False


def _is_ignored_by_gitignore(rel: str, patterns: list[str]) -> bool:
    """.gitignore のパターン配列に基づいて rel（root からの相対POSIXパス）が無視対象か判定。否定（!pat）は順序通りにトグル適用します。"""
    ignored = False
    for raw in patterns:
        if not raw:
            continue
        neg = raw.startswith("!")
        if _match_gitignore_pattern(rel, raw):
            if neg:
                ignored = False
            else:
                ignored = True
    return ignored


@tool
def find_files(base_path: str, pattern: str = "**/*", max_files: int = 2000, exclude_dirs: list = None,
               exclude_exts: list = None, honor_gitignore: bool = False) -> str:
    """
    base_path配下から、globパターンでファイルを検索し、
    base_pathからの相対パスを改行区切りの文字列で返します。例:
      find_files("repo", "**/*.py")  ->  "app/main.py\nutils/io.py\n..."
      find_files("repo", "templates/**/*.html")

    Args:
        base_path: 検索の起点ディレクトリ
        pattern:  globパターン（例: "**/*.py", "src/**/*.php" など）
        max_files: 返す最大件数（過大応答の抑制）
        exclude_dirs: 除外するディレクトリのリスト
        exclude_exts: 除外するファイル拡張子のリスト
        honor_gitignore: True の場合、base_path 直下の .gitignore を読み込み、パターンにマッチするパスを除外します（簡易対応）。Returns:
        見つかった相対パスの改行区切り文字列（0件でも空文字列）
    """
    root = Path(base_path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return ""

    if exclude_dirs is None:
        exclude_dirs = []
    if exclude_exts is None:
        exclude_exts = []

    gitignore_patterns: list[str] = _load_gitignore_patterns(root) if honor_gitignore else []

    matched = []
    for p in root.glob(pattern):
        if p.is_file():
            rel = p.relative_to(root).as_posix()
            # .gitignore による除外
            if gitignore_patterns and _is_ignored_by_gitignore(rel, gitignore_patterns):
                continue
            # 明示的な除外ディレクトリ
            if any(ex_dir in p.parts for ex_dir in exclude_dirs):
                continue
            # 明示的な除外拡張子
            if p.suffix in exclude_exts:
                continue
            matched.append(rel)
            if len(matched) >= max_files:
                break
    matched.sort()
    return "\n".join(matched)


@tool
def read_file(file_name: str) -> str:
    """
    引き数に指定されたファイルの内容を読み取り、テキストで返却する。"""
    with open(file_name, encoding="utf-8") as f:
        text = f.read()
    return text


@tool
def write_file(file_path: str, content: str) -> bool:
    """
    第1引数に指定されたファイルに、指定された文字列を書き込みます。ディレクトリが無い場合は、作成します。:param file_path: ファイルのパス
    :param content: ファイルに書き込む文字列
    :return: 正常時True, 異常時Falseを返す
    """
    try:
        # 親ディレクトリを作成（存在してもエラーにならない）
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        # ファイルに書き込み（上書きモード）
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)

        return True
    except Exception as e:
        print(f"Error writing file {file_path}: {e}")
        return False


@tool
def make_dirs(dir_path: str) -> bool:
    """
    指定ディレクトリを作成します（親ディレクトリもまとめて作成）。既に存在する場合も True を返します。"""
    try:
        os.makedirs(dir_path, exist_ok=True)
        return True
    except Exception as e:
        print(f"[make_dirs] error: {e}")
        return False


@tool
def list_files(base_path: str) -> str:
    """
    base_path配下の全ファイルの相対パスを、改行区切りの文字列で返す。例:
      "dir/a.txt\nsrc/main.py\n..."

    Args:
        base_path: 起点ディレクトリ（相対/絶対どちらでも可）

    Raises:
        ValueError: base_path が存在しない or ディレクトリでない場合
    """
    root = Path(base_path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"base_path がディレクトリとして存在しません: {base_path}")

    paths = []
    for p in root.rglob("*"):
        if p.is_file() and p.suffix in {".py", ".html", ".php"}:
            rel = p.relative_to(root).as_posix()
            paths.append(rel)

    return "\n".join(sorted(paths))


# ここから追加ツール群
import ast


@tool
def file_stat(file_path: str) -> str:
    """ファイルの存在・サイズ・更新時刻・行数を返す（JSON文字列）。"""
    info: Dict[str, object] = {"exists": os.path.exists(file_path)}
    if not info["exists"]:
        return json.dumps(info, ensure_ascii=False)
    try:
        info["size"] = os.path.getsize(file_path)
        info["mtime"] = os.path.getmtime(file_path)
        line_count = 0
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            for _ in f:
                line_count += 1
        info["line_count"] = line_count
    except Exception as e:
        info["error"] = f"{type(e).__name__}: {e}"
    return json.dumps(info, ensure_ascii=False)


@tool
def read_file_range(file_path: str, start_line: int, end_line: int) -> str:
    """
    1始まりの行番号で[start_line, end_line]の内容を返す（JSON文字列）。範囲外は自動調整。"""
    result: Dict[str, object] = {"exists": os.path.exists(file_path)}
    if not result["exists"]:
        return json.dumps(result, ensure_ascii=False)
    start_line = max(1, int(start_line))
    end_line = max(start_line, int(end_line))
    lines: List[str] = []
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            for i, line in enumerate(f, start=1):
                if i < start_line:
                    continue
                if i > end_line:
                    break
                lines.append(line)
        result.update({
            "start_line": start_line,
            "end_line": end_line,
            "content": "".join(lines),
        })
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
    return json.dumps(result, ensure_ascii=False)


@tool
def list_python_symbols(file_path: str) -> str:
    """
    Pythonファイルの関数/クラスと開始・終了行を抽出して返す（JSON文字列）。"""
    out: Dict[str, object] = {"exists": os.path.exists(file_path), "symbols": []}
    if not out["exists"]:
        return json.dumps(out, ensure_ascii=False)
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            src = f.read()
        tree = ast.parse(src)
        symbols = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = node.name
                start = int(getattr(node, "lineno", 1))
                end = getattr(node, "end_lineno", None)
                if end is None:
                    end = start
                kind = "function" if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else "class"
                symbols.append({"name": name, "kind": kind, "start": start, "end": int(end)})
        symbols.sort(key=lambda s: s["start"])
        out["symbols"] = symbols
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return json.dumps(out, ensure_ascii=False)


@tool
def insert_code(
        file_path: str,
        code: str,
        line: Optional[int] = None,
        *,
        anchor: Optional[str] = None,
        where: str = "after",  # "before" | "after"
        occurrence: str = "first",  # "first" | "last" | "nth"
        nth: int = 1,  # occurrence="nth" のときのみ有効（1始まり）
        regex: bool = False,  # アンカーが正規表現かどうか
        ensure_trailing_newline: bool = True
) -> str:
    """
    ファイルの途中に code を差し込む（バックアップ付き）。- line を指定: その行の "before/after" に挿入
    - anchor を指定: その行の "before/after" に挿入（first/last/nth、部分一致 or 正規表現）
    ※ line と anchor の両方は指定しないこと（line が優先されます）
    """
    out: Dict[str, object] = {
        "ok": False, "insert_at": None, "mode": None, "where": where,
        "matched_line": None, "occurrence": occurrence, "regex": bool(regex),
        "backup": False
    }

    try:
        p = Path(file_path)
        if not p.exists():
            out["error"] = "file_not_found"
            return json.dumps(out, ensure_ascii=False)

        # 読み込み
        with p.open("r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()  # 改行込み

        if ensure_trailing_newline and code and not code.endswith("\n"):
            code += "\n"
        code_lines = code.splitlines(keepends=True)

        # 情報ノート（非常に大きな挿入）
        if anchor is not None and len(code_lines) > 50:
            out["note"] = "large_insert_consider_review"

        # 挿入位置の決定
        insert_index: Optional[int] = None  # 0始まりのスライス位置

        if line is not None:
            # --- 行番号指定 ---
            out["mode"] = "line"
            ln = max(1, int(line))

            if where not in ("before", "after"):
                out["error"] = "invalid_where"
                return json.dumps(out, ensure_ascii=False)

            if where == "before":
                insert_index = min(max(0, ln - 1), len(lines))
            else:  # after
                insert_index = min(max(0, ln), len(lines))

            out["matched_line"] = int(ln)

        else:
            # --- アンカー指定 ---
            out["mode"] = "anchor"
            if not anchor:
                out["error"] = "missing_line_or_anchor"
                return json.dumps(out, ensure_ascii=False)

            # マッチ行を集める
            matches: List[int] = []  # 1始まりの行番号
            if regex:
                pattern = re.compile(anchor)
                for idx, text in enumerate(lines, start=1):
                    if pattern.search(text):
                        matches.append(idx)
            else:
                for idx, text in enumerate(lines, start=1):
                    if anchor in text:
                        matches.append(idx)

            if not matches:
                out["error"] = "anchor_not_found"
                return json.dumps(out, ensure_ascii=False)

            # どの出現箇所を使うか
            occ = occurrence.lower()
            if occ == "first":
                base_line = matches[0]
            elif occ == "last":
                base_line = matches[-1]
            elif occ == "nth":
                if nth < 1 or nth > len(matches):
                    out["error"] = f"nth_out_of_range (1..{len(matches)})"
                    return json.dumps(out, ensure_ascii=False)
                base_line = matches[nth - 1]
            else:
                out["error"] = "invalid_occurrence"
                return json.dumps(out, ensure_ascii=False)

            if where not in ("before", "after"):
                out["error"] = "invalid_where"
                return json.dumps(out, ensure_ascii=False)

            # 0始まり index
            if where == "before":
                insert_index = min(max(0, base_line - 1), len(lines))
                out["insert_at"] = base_line  # before の場合、挿入位置は基準行の位置
            else:  # after
                insert_index = min(max(0, base_line), len(lines))
                out["insert_at"] = base_line + 1  # after の場合、基準行の次

            out["matched_line"] = base_line

        # 行番号モードの insert_at 設定（アンカーモードは上で設定済み）
        if out["mode"] == "line":
            # 1始まりで返す（where によって位置が異なる）
            if where == "before":
                out["insert_at"] = insert_index + 1
            else:  # after
                out["insert_at"] = insert_index + 1  # after は「次の行」の位置に挿入

        # 実挿入
        if insert_index is None:
            out["error"] = "insert_index_not_determined"
            return json.dumps(out, ensure_ascii=False)

        lines[insert_index:insert_index] = code_lines  # スライス挿入

        # 書き込み
        content = "".join(lines)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        out["ok"] = True
        return json.dumps(out, ensure_ascii=False)

    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        return json.dumps(out, ensure_ascii=False)


@tool
def update_code(
        file_path: str,
        code: str,
        line_start: Optional[int] = None,
        line_end: Optional[int] = None,
        *,
        anchor: Optional[str] = None,  # アンカーで範囲を決める場合の基準行（部分一致 or 正規表現）
        occurrence: str = "first",  # "first" | "last" | "nth"
        nth: int = 1,  # occurrence="nth" のときのみ（1始まり）
        regex: bool = False,
        offset: int = 0,  # アンカー基準行からの開始位置のずれ（+n行目から）
        length: Optional[int] = None,  # 置換する行数（省略時は1行）
        ensure_trailing_newline: bool = True
) -> str:
    """
    指定範囲を code で置換します（バックアップは取りません。必要なら呼び出し側で実施してください）。2通りの指定方法:
      1) line_start / line_end を与える（1始まり・endを含む）
      2) anchor を与える → マッチ行を基準に offset/length で範囲を決める

    Level 1 ガード:
      - anchor 指定で code が複数行かつ length 未指定の場合、曖昧さによる事故を防ぐためエラーを返す。"""
    out: Dict[str, object] = {
        "ok": False, "mode": None, "matched_line": None,
        "start_line": None, "end_line": None,
        "occurrence": occurrence, "regex": bool(regex)
    }
    try:
        p = Path(file_path)
        if not p.exists():
            out["error"] = "file_not_found"
            return json.dumps(out, ensure_ascii=False)

        with p.open("r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()

        if ensure_trailing_newline and code and not code.endswith("\n"):
            code += "\n"
        code_lines = code.splitlines(keepends=True)

        # Level 1 Guard: 複数行 code × anchor × length 未指定 → エラーにする
        if line_start is None and anchor is not None:
            if length is None and len(code_lines) > 1:
                out["error"] = "ambiguous_length_use_length_or_single_line_code"
                out[
                    "hint"] = "When using anchor with multi-line code, specify length to match the number of lines, or use line_start/line_end."
                return json.dumps(out, ensure_ascii=False)

        # 範囲決定
        if line_start is not None:
            out["mode"] = "line_range"
            s = max(1, int(line_start))
            e = int(line_end) if line_end is not None else s
        else:
            out["mode"] = "anchor_range"
            if not anchor:
                out["error"] = "missing_range"
                return json.dumps(out, ensure_ascii=False)

            # アンカー行を収集
            matches: List[int] = []
            if regex:
                pat = re.compile(anchor)
                for idx, text in enumerate(lines, start=1):
                    if pat.search(text):
                        matches.append(idx)
            else:
                for idx, text in enumerate(lines, start=1):
                    if anchor in text:
                        matches.append(idx)
            if not matches:
                out["error"] = "anchor_not_found"
                return json.dumps(out, ensure_ascii=False)

            occ = (occurrence or "first").lower()
            if occ == "first":
                base = matches[0]
            elif occ == "last":
                base = matches[-1]
            elif occ == "nth":
                if nth < 1 or nth > len(matches):
                    out["error"] = f"nth_out_of_range (1..{len(matches)})"
                    return json.dumps(out, ensure_ascii=False)
                base = matches[nth - 1]
            else:
                out["error"] = "invalid_occurrence"
                return json.dumps(out, ensure_ascii=False)

            s = base + int(offset)
            if length is None:
                length = 1
            e = s + int(length) - 1
            out["matched_line"] = base

        # 範囲補正
        if s > e:
            out["error"] = "invalid_range"
            return json.dumps(out, ensure_ascii=False)
        s = max(1, s)
        e = min(len(lines), e)
        out["start_line"], out["end_line"] = int(s), int(e)

        # 実置換
        s_idx, e_idx = s - 1, e
        lines[s_idx:e_idx] = code_lines

        with p.open("w", encoding="utf-8") as f:
            f.write("".join(lines))

        out["ok"] = True
        return json.dumps(out, ensure_ascii=False)

    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        return json.dumps(out, ensure_ascii=False)


@tool
def delete_code(
        file_path: str,
        line_start: Optional[int] = None,
        line_end: Optional[int] = None,
        *,
        anchor: Optional[str] = None,  # アンカーで範囲を決める場合の基準行（部分一致 or 正規表現）
        occurrence: str = "first",  # "first" | "last" | "nth"
        nth: int = 1,  # occurrence="nth" のときのみ（1始まり）
        regex: bool = False,
        offset: int = 0,  # アンカー基準行からの開始位置のずれ
        length: Optional[int] = None  # 削除する行数（省略時は1行）
) -> str:
    """
    指定範囲を削除します（バックアップは取りません。必要なら呼び出し側で実施してください）。2通りの指定方法:
      1) line_start / line_end を与える（1始まり・endを含む）
      2) anchor を与える → マッチ行を基準に offset/length で範囲を決める
    """
    out: Dict[str, object] = {
        "ok": False, "mode": None, "matched_line": None,
        "start_line": None, "end_line": None, "deleted": 0,
        "occurrence": occurrence, "regex": bool(regex)
    }
    try:
        p = Path(file_path)
        if not p.exists():
            out["error"] = "file_not_found"
            return json.dumps(out, ensure_ascii=False)

        with p.open("r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()

        # 範囲決定
        if line_start is not None:
            out["mode"] = "line_range"
            s = max(1, int(line_start))
            e = int(line_end) if line_end is not None else s
        else:
            out["mode"] = "anchor_range"
            if not anchor:
                out["error"] = "missing_range"
                return json.dumps(out, ensure_ascii=False)

            matches: List[int] = []
            if regex:
                pat = re.compile(anchor)
                for idx, text in enumerate(lines, start=1):
                    if pat.search(text):
                        matches.append(idx)
            else:
                for idx, text in enumerate(lines, start=1):
                    if anchor in text:
                        matches.append(idx)
            if not matches:
                out["error"] = "anchor_not_found"
                return json.dumps(out, ensure_ascii=False)

            occ = (occurrence or "first").lower()
            if occ == "first":
                base = matches[0]
            elif occ == "last":
                base = matches[-1]
            elif occ == "nth":
                if nth < 1 or nth > len(matches):
                    out["error"] = f"nth_out_of_range (1..{len(matches)})"
                    return json.dumps(out, ensure_ascii=False)
                base = matches[nth - 1]
            else:
                out["error"] = "invalid_occurrence"
                return json.dumps(out, ensure_ascii=False)

            s = base + int(offset)
            if length is None:
                length = 1
            e = s + int(length) - 1
            out["matched_line"] = base

        # 範囲補正
        if s > e:
            out["error"] = "invalid_range"
            return json.dumps(out, ensure_ascii=False)
        s = max(1, s)
        e = min(len(lines), e)
        out["start_line"], out["end_line"] = int(s), int(e)

        # 実削除
        s_idx, e_idx = s - 1, e
        del_count = e_idx - s_idx
        lines[s_idx:e_idx] = []
        out["deleted"] = del_count

        with p.open("w", encoding="utf-8") as f:
            f.write("".join(lines))

        out["ok"] = True
        return json.dumps(out, ensure_ascii=False)

    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        return json.dumps(out, ensure_ascii=False)


@tool
def search_grep(
    base_path: str,
    query: str,
    is_regex: bool = False,
    case_sensitive: bool = False,
    whole_word: bool = False,
    extensions: Optional[list] = None,
    include_globs: Optional[list] = None,
    exclude_globs: Optional[list] = None,
    exclude_dirs: Optional[list] = None,
    honor_gitignore: bool = True,
    skip_binary: bool = True,
    size_limit_bytes_per_file: int = 5_000_000,
    max_files: int = 5000,
    max_matches_per_file: int = 50,
    max_total_matches: int = 2000,
    context_lines: int = 2,
    encoding: str = "utf-8",
    path_match_only: bool = False,
    follow_symlinks: bool = False,
    timeout_seconds: int = 10,
) -> str:
    """
    ディレクトリ配下のテキストを grep 風に検索するツール。- .gitignore を尊重（honor_gitignore=True のとき）
    - 拡張子やglob、除外ディレクトリでフィルタ
    - マッチ箇所の行番号・列位置・前後コンテキストを返却
    """
    import time as _time

    t0 = _time.time()
    root = Path(base_path).expanduser().resolve()
    out: Dict[str, object] = {
        "ok": False,
        "base_path": str(root),
        "query": query,
        "is_regex": bool(is_regex),
        "case_sensitive": bool(case_sensitive),
        "whole_word": bool(whole_word),
        "stats": {"scanned_files": 0, "matched_files": 0, "total_matches": 0, "scanned_bytes": 0, "duration_ms": 0,
                  "truncated": False},
        "files": [],
        "errors": []
    }

    if not root.exists() or not root.is_dir():
        out["errors"].append({"file_path": str(root), "error": "base_path_not_dir"})
        return json.dumps(out, ensure_ascii=False)

    # 既定除外（ナレッジベース準拠）
    if exclude_dirs is None:
        exclude_dirs = ["vendor", ".github", "logs", ".git"]
    include_globs = include_globs or []
    exclude_globs = exclude_globs or []
    extensions = [e.lower() for e in (extensions or [])]

    # .gitignore パターン
    gitignore_patterns: list[str] = _load_gitignore_patterns(root) if honor_gitignore else []

    # クエリを用意
    flags = 0 if case_sensitive else re.IGNORECASE
    if is_regex:
        pat_src = query
    else:
        pat_src = re.escape(query)
    if whole_word:
        pat_src = r"\b" + pat_src + r"\b"
    try:
        pattern = re.compile(pat_src, flags)
    except re.error as e:
        out["errors"].append({"file_path": "<pattern>", "error": f"invalid_regex: {e}"})
        return json.dumps(out, ensure_ascii=False)

    # ファイル列挙
    scanned_files = 0
    matched_files = 0
    total_matches = 0
    scanned_bytes = 0
    files_out: List[Dict[str, object]] = []
    truncated_global = False

    def _match_globs(rel_posix: str, globs: List[str]) -> bool:
        if not globs:
            return True
        return any(fnmatch.fnmatch(rel_posix, g) for g in globs)

    try:
        for dirpath, dirnames, filenames in os.walk(root, followlinks=bool(follow_symlinks)):
            # 除外ディレクトリをスキップ（インプレースでフィルタ）
            dirnames[:] = [d for d in dirnames if d not in exclude_dirs]

            # タイムアウト
            if _time.time() - t0 > timeout_seconds:
                out["errors"].append({"file_path": str(root), "error": "timeout"})
                truncated_global = True
                break

            for fn in filenames:
                if scanned_files >= max_files:
                    truncated_global = True
                    break

                p = Path(dirpath) / fn
                if not p.is_file():
                    continue

                rel = p.relative_to(root).as_posix()

                # .gitignore
                if gitignore_patterns and _is_ignored_by_gitignore(rel, gitignore_patterns):
                    continue

                # glob 包含/除外
                if not _match_globs(rel, include_globs):
                    continue
                if exclude_globs and any(fnmatch.fnmatch(rel, g) for g in exclude_globs):
                    continue

                # 拡張子
                ext = p.suffix.lower()
                if extensions and ext not in extensions:
                    continue

                # サイズ
                try:
                    size = p.stat().st_size
                except Exception as e:
                    out["errors"].append({"file_path": rel, "error": f"stat_error: {e}"})
                    continue
                if size_limit_bytes_per_file and size > int(size_limit_bytes_per_file):
                    continue

                # パス名のみマッチ
                if path_match_only:
                    m = pattern.search(rel)
                    if m:
                        files_out.append({
                            "file_path": rel, "ext": ext, "size": int(size),
                            "match_count": 1, "truncated": False, "encoding_used": None,
                            "matches": [{
                                "line_no": 0, "col_start": m.start() + 1, "col_end": m.end() + 1,
                                "line": rel, "context_before": [], "context_after": []
                            }]
                        })
                        matched_files += 1
                        total_matches += 1
                    scanned_files += 1
                    scanned_bytes += int(size)
                    if total_matches >= max_total_matches:
                        truncated_global = True
                        break
                    continue

                # バイナリスキップ（簡易判定）
                if skip_binary:
                    try:
                        with open(p, "rb") as bf:
                            head = bf.read(4096)
                        if b"\0" in head:
                            scanned_files += 1
                            scanned_bytes += int(size)
                            continue
                    except Exception as e:
                        out["errors"].append({"file_path": rel, "error": f"open_error: {e}"})
                        continue

                # テキスト検索
                try:
                    with open(p, "r", encoding=encoding, errors="ignore") as tf:
                        lines = tf.readlines()
                except Exception as e:
                    out["errors"].append({"file_path": rel, "error": f"read_error: {e}"})
                    continue

                scanned_files += 1
                scanned_bytes += int(size)

                matches = []
                file_truncated = False
                for i, line in enumerate(lines, start=1):
                    if total_matches >= max_total_matches:
                        truncated_global = True
                        break
                    if len(matches) >= max_matches_per_file:
                        file_truncated = True
                        break

                    for m in pattern.finditer(line):
                        # 位置（1始まり）
                        col_s, col_e = m.start() + 1, m.end() + 1
                        # 前後文脈
                        s = max(1, i - context_lines);
                        e = min(len(lines), i + context_lines)
                        ctx_before = [l.rstrip("\n") for l in lines[s - 1:i - 1]] if context_lines else []
                        ctx_after = [l.rstrip("\n") for l in lines[i:e]] if context_lines else []
                        matches.append({
                            "line_no": i,
                            "col_start": col_s,
                            "col_end": col_e,
                            "line": line.rstrip("\n"),
                            "context_before": ctx_before,
                            "context_after": ctx_after,
                        })
                        total_matches += 1
                        if total_matches >= max_total_matches:
                            truncated_global = True
                            break
                        if len(matches) >= max_matches_per_file:
                            file_truncated = True
                            break
                    if file_truncated or truncated_global:
                        break

                if matches:
                    files_out.append({
                        "file_path": rel,
                        "ext": ext,
                        "size": int(size),
                        "match_count": len(matches),
                        "truncated": file_truncated,
                        "encoding_used": encoding,
                        "matches": matches,
                    })
                    matched_files += 1

            if scanned_files >= max_files or truncated_global:
                break

    except Exception as e:
        out["errors"].append({"file_path": str(root), "error": f"walk_error: {e}"})

    dur_ms = int((_time.time() - t0) * 1000)
    out["stats"] = {
        "scanned_files": scanned_files,
        "matched_files": matched_files,
        "total_matches": total_matches,
        "scanned_bytes": int(scanned_bytes),
        "duration_ms": dur_ms,
        "truncated": bool(truncated_global or scanned_files >= max_files or total_matches >= max_total_matches),
    }
    out["files"] = files_out
    out["ok"] = True
    return json.dumps(out, ensure_ascii=False)


@tool
def replace_in_line(
        file_path: str,
        anchor: str,
        find: str,
        replace: str,
        occurrence: str = "first",  # first | last | nth
        nth: int = 1,
        regex: bool = False,
        ensure_trailing_newline: bool = True,
) -> str:
    """
    1行の中だけで置換を行う安全なユーティリティ。- アンカーで対象行を特定し、その1行の中で find→replace を実行します。- 複数行ブロックには影響しません（update_code の誤用防止に有効）。"""
    out: Dict[str, object] = {
        "ok": False,
        "matched_line": None,
        "occurrence": occurrence,
        "nth": int(nth),
        "regex": bool(regex),
        "replacements": 0,
    }
    try:
        p = Path(file_path)
        if not p.exists():
            out["error"] = "file_not_found"
            return json.dumps(out, ensure_ascii=False)

        with p.open("r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()

        # 対象行の決定（アンカーによる行特定）
        matches: List[int] = []  # 1始まり
        if regex:
            pat = re.compile(anchor)
            for i, text in enumerate(lines, start=1):
                if pat.search(text):
                    matches.append(i)
        else:
            for i, text in enumerate(lines, start=1):
                if anchor in text:
                    matches.append(i)
        if not matches:
            out["error"] = "anchor_not_found"
            return json.dumps(out, ensure_ascii=False)

        occ = (occurrence or "first").lower()
        if occ == "first":
            line_no = matches[0]
        elif occ == "last":
            line_no = matches[-1]
        elif occ == "nth":
            if nth < 1 or nth > len(matches):
                out["error"] = f"nth_out_of_range (1..{len(matches)})"
                return json.dumps(out, ensure_ascii=False)
            line_no = matches[nth - 1]
        else:
            out["error"] = "invalid_occurrence"
            return json.dumps(out, ensure_ascii=False)

        # 1行内置換
        idx = line_no - 1
        old = lines[idx]
        if regex:
            try:
                fpat = re.compile(find)
            except re.error as e:
                out["error"] = f"invalid_regex: {e}"
                return json.dumps(out, ensure_ascii=False)
            new, count = fpat.subn(replace, old)
        else:
            count = old.count(find)
            new = old.replace(find, replace)

        # 置換なしでも ok として返却
        if count == 0:
            out["matched_line"] = int(line_no)
            out["ok"] = True
            return json.dumps(out, ensure_ascii=False)

        # 末尾改行維持
        if ensure_trailing_newline and not new.endswith("\n"):
            new = new + "\n"

        lines[idx] = new
        with p.open("w", encoding="utf-8") as f:
            f.write("".join(lines))

        out["matched_line"] = int(line_no)
        out["replacements"] = int(count)
        out["ok"] = True
        return json.dumps(out, ensure_ascii=False)

    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        return json.dumps(out, ensure_ascii=False)