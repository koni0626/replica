from pathlib import Path
import os
from langchain_core.tools import tool

@tool
def find_files(base_path: str, pattern: str = "**/*", max_files: int = 2000, exclude_dirs: list = None, exclude_exts: list = None) -> str:
    """
    base_path配下から、globパターンでファイルを検索し、
    base_pathからの相対パスを改行区切りの文字列で返します。

    例:
      find_files("repo", "**/*.py")  ->  "app/main.py\nutils/io.py\n..."
      find_files("repo", "templates/**/*.html")

    Args:
        base_path: 検索の起点ディレクトリ
        pattern:  globパターン（例: "**/*.py", "src/**/*.php" など）
        max_files: 返す最大件数（過大応答の抑制）
        exclude_dirs: 除外するディレクトリのリスト
        exclude_exts: 除外するファイル拡張子のリスト

    Returns:
        見つかった相対パスの改行区切り文字列（0件でも空文字列）
    """
    root = Path(base_path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return ""

    if exclude_dirs is None:
        exclude_dirs = []
    if exclude_exts is None:
        exclude_exts = []

    matched = []
    for p in root.glob(pattern):
        if p.is_file():
            # 除外ディレクトリに含まれているかチェック
            if any(ex_dir in p.parts for ex_dir in exclude_dirs):
                continue
            # 除外拡張子に含まれているかチェック
            if p.suffix in exclude_exts:
                continue
            matched.append(p.relative_to(root).as_posix())
            if len(matched) >= max_files:
                break
    matched.sort()
    return "\n".join(matched)

@tool
def read_file(file_name: str) -> str:
    """
    引き数に指定されたファイルの内容を読み取り、テキストで返却する。
    """
    with open(file_name, encoding="utf-8") as f:
        text = f.read()

    return text

@tool
def write_file(file_path: str, content: str) -> bool:
    """
    第1引数に指定されたファイルに、指定された文字列を書き込みます。
    ディレクトリが無い場合は、作成します。

    :param file_path: ファイルのパス
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
        # ログ出力なども適宜ここで行う
        print(f"Error writing file {file_path}: {e}")
        return False

@tool
def make_dirs(dir_path: str) -> bool:
    """
    指定ディレクトリを作成します（親ディレクトリもまとめて作成）。
    既に存在する場合も True を返します。

    Args:
        dir_path: 作成したいディレクトリのパス

    Returns:
        正常時 True、失敗時 False
    """
    try:
        os.makedirs(dir_path, exist_ok=True)
        return True
    except Exception as e:
        print(f"[make_dirs] error: {e}")
        return False

@tool
def list_files(base_path: str) -> str:
    """
    base_path配下の全ファイルの相対パスを、改行区切りの文字列で返す。
    例:
      "dir/a.txt\nsrc/main.py\n..."

    Args:
        base_path: 起点ディレクトリ（相対/絶対どちらでも可）

    Raises:
        ValueError: base_path が存在しない or ディレクトリでない場合
    """
    root = Path(base_path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"base_path がディレクトリとして存在しません: {base_path}")

    # 再帰で全ファイルを収集（隠しファイルも含む）
    paths = []
    for p in root.rglob("*"):
        if p.is_file() and p.suffix in {".py", ".html", ".php"}:
            rel = p.relative_to(root).as_posix()
            paths.append(rel)

    # 安定化のためソートしてから改行結合
    print(paths)
    return "\n".join(sorted(paths))

# ここから追加ツール
import shutil
from datetime import datetime
import json
import re
from typing import List, Dict, Tuple
import ast

@tool
def backup_then_write(file_path: str, content: str, timestamp_format: str = "%Y%m%d%H%M%S", backup_prefix: str = "bk_") -> bool:
    """
    指定ファイルを上書きする前に、同ディレクトリにバックアップを作成してから書き込みます。

    バックアップファイル名: {現在時刻}{backup_prefix}{元ファイル名}
      例) index.html -> 20250101123045bk_index.html

    Args:
        file_path: 上書き対象のファイルパス（例: C:\\...\\templates\\index.html）
        content:   上書き後の内容（UTF-8で保存）
        timestamp_format: 日時のフォーマット（デフォルト: %Y%m%d%H%M%S）
        backup_prefix: バックアップ名の接頭辞（デフォルト: "bk_")

    Returns:
        成功時 True、失敗時 False
    """
    try:
        dir_path = os.path.dirname(file_path)
        os.makedirs(dir_path, exist_ok=True)

        # 既存ファイルがある場合はバックアップ
        if os.path.exists(file_path):
            ts = datetime.now().strftime(timestamp_format)
            base = os.path.basename(file_path)
            backup_name = f"{ts}{backup_prefix}{base}"
            backup_path = os.path.join(dir_path, backup_name)
            shutil.copy2(file_path, backup_path)

        # 新しい内容で上書き
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)

        return True
    except Exception as e:
        print(f"[backup_then_write] error for {file_path}: {e}")
        return False

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
    1始まりの行番号で[start_line, end_line]の内容を返す（JSON文字列）。
    範囲外は自動調整。
    """
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
    Pythonファイルの関数/クラスと開始・終了行を抽出して返す（JSON文字列）。
    """
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
def apply_text_edits_with_backup(file_path: str, edits_json: str) -> str:
    """
    小さなテキスト置換（find/replace）集合を適用し、事前にバックアップを作成する。
    edits_json: JSON配列 [[{"find": "old", "replace": "new", "occurrence": "first|all|nth", "nth": 1}], ...]
    戻り値は {ok, applied} のJSON文字列。
    """
    try:
        edits = json.loads(edits_json or "[]") if isinstance(edits_json, str) else (edits_json or [])
    except Exception as e:
        return json.dumps({"ok": False, "error": f"invalid_json: {e}"}, ensure_ascii=False)

    if not os.path.exists(file_path):
        return json.dumps({"ok": False, "error": "file_not_found"}, ensure_ascii=False)

    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()

        def replace_once_at_nth(s: str, old: str, new: str, nth: int) -> Tuple[str, int]:
            idx = -1
            count = 0
            start = 0
            while True:
                idx = s.find(old, start)
                if idx == -1:
                    return s, 0
                count += 1
                if count == nth:
                    return s[:idx] + new + s[idx + len(old):], 1
                start = idx + len(old)

        total = 0
        for e in edits:
            old = e.get("find", "") if isinstance(e, dict) else ""
            new = e.get("replace", "") if isinstance(e, dict) else ""
            occ = (e.get("occurrence", "first") if isinstance(e, dict) else "first").lower()
            if not old:
                continue
            if occ == "all":
                n = text.count(old)
                if n > 0:
                    text = text.replace(old, new)
                    total += n
            elif occ == "nth":
                nth = int(e.get("nth", 1)) if isinstance(e, dict) else 1
                text, applied = replace_once_at_nth(text, old, new, nth)
                total += applied
            else:  # first
                text, applied = replace_once_at_nth(text, old, new, 1)
                total += applied

        # バックアップ＋書き込み
        ok = backup_then_write(file_path=file_path, content=text)
        return json.dumps({"ok": bool(ok), "applied": int(total)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)
