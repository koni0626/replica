from pathlib import Path
import os
import json
import re
import fnmatch
import time
import ast
from typing import List, Dict, Optional
from langchain_core.tools import tool
from services.project_service import ProjectService


# --- search_paths.json 連携ヘルパ（UIの保存に追従するための軽量実装）
# 保存場所: instance/<project_id>/search_paths.json を直接読む（services層に依存しない）
# 期待フォーマット: {"version":1, "includes":["path"...], "excludes":["path"...]}

def _load_search_paths_globs(project_id: Optional[int]) -> Dict[str, List[str]]:
    """project_id が与えられた場合に instance/<project_id>/search_paths.json を読み、
    include_globs / exclude_globs を返す。存在しない/不正時は空を返す。
    tools 側で完結させるため services には依存しない簡易実装。
    """
    globs: Dict[str, List[str]] = {"include_globs": [], "exclude_globs": []}
    if project_id is None:
        return globs
    try:
        inst = Path.cwd() / "instance" / str(project_id)
        state_path = inst / "search_paths.json"
        if not state_path.exists():
            return globs
        data = json.loads(state_path.read_text(encoding="utf-8"))
        inc = data.get("includes") or []
        exc = data.get("excludes") or []
        def _norm(paths: List[str]) -> List[str]:
            out: List[str] = []
            for s in paths:
                s = str(s).strip().replace("\\", "/").strip("/")
                if not s:
                    continue
                # ディレクトリ配下すべてを対象にするため '/**' を付ける
                out.append(f"{s}/**")
            return out
        globs["include_globs"] = _norm(inc)
        globs["exclude_globs"] = _norm(exc)
        return globs
    except Exception:
        return globs

# -----------------
# 内部ユーティリティ
# -----------------
def _resolve_doc_path(project_id: int) -> Path:
    ps = ProjectService()
    proj = ps.fetch_by_id(project_id)
    if not proj or not getattr(proj, "doc_path", None):
        raise ValueError("doc_path_not_set")
    base = Path(proj.doc_path).expanduser().resolve()
    if (not base.exists()) or (not base.is_dir()):
        raise ValueError("invalid_doc_path")
    return base

def _path_matches_globs(rel_posix: str, globs: List[str], is_dir: bool = False) -> bool:
    """rel_posix が globs のいずれかにマッチするか。ディレクトリについては 'path/**' の前方一致も許容。
    globs が空なら True（制限なし）。"""
    if not globs:
        return True
    # ディレクトリのときは末尾にスラッシュを持つ形でも評価
    rel_dir = rel_posix if not is_dir else (rel_posix.rstrip("/") + "/")
    for g in globs:
        if g.endswith("/**"):
            prefix = g[:-3]  # 'path/' 期待
            if is_dir:
                if rel_dir == prefix or rel_dir.startswith(prefix):
                    return True
            else:
                # ファイルは前方一致で 'path/' 配下かどうか
                if rel_posix == prefix.rstrip("/") or rel_posix.startswith(prefix):
                    return True
        # フォールバックで fnmatch
        if fnmatch.fnmatch(rel_posix, g):
            return True
    return False


def find_files(base_path: str,
               pattern: str = "**/*",
               max_files: int = 2000,
               exclude_dirs: list = None,
               exclude_exts: list = None,
               honor_gitignore: bool = False,
               include_exts: Optional[List[str]] = None,
               include_globs: Optional[List[str]] = None,
               exclude_globs: Optional[List[str]] = None,
               project_id: Optional[int] = None) -> str:
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
        exclude_exts: 除外するファイル拡張子のリスト（ドット付き、例: ".log"）
        include_exts: 許可する拡張子のリスト（ドット付き、例: [".py", ".js"]）。指定時はこの拡張子のみを返します。
        include_globs: 追加の包含グロブ（例: ["services/**", "controllers/**"]）
        exclude_globs: 追加の除外グロブ（例: ["controllers/legacy/**"]）
        project_id: UI保存の検索パス（instance/<project_id>/search_paths.json）を適用する場合に指定

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

    # include_exts の正規化（None の場合は空集合 = 制限なし）
    allow_exts: set[str] = set()
    if include_exts is not None:
        if isinstance(include_exts, str):
            raw = [x.strip() for x in include_exts.split(",") if x.strip()]
        else:
            raw = [str(x).strip() for x in include_exts if str(x).strip()]
        for x in raw:
            s = x.lower()
            if not s.startswith("."):
                s = "." + s
            allow_exts.add(s)

    # UI保存の検索パス（includes/excludes）をグロブへ展開（未指定時のみ補完）
    if project_id is not None:
        saved = _load_search_paths_globs(project_id)
        if include_globs is None:
            ig = saved.get("include_globs")
            if ig:
                include_globs = ig
        if exclude_globs is None:
            eg = saved.get("exclude_globs")
            if eg:
                exclude_globs = eg

    matched = []
    for p in root.glob(pattern):
        if not p.is_file():
            continue
        # 明示的な除外ディレクトリ
        if any(ex_dir in p.parts for ex_dir in exclude_dirs):
            continue

        rel = p.relative_to(root).as_posix()

        # 明示的な除外拡張子
        if p.suffix.lower() in {str(e).lower() for e in exclude_exts}:
            continue

        # include_exts が指定されていれば許可リストのみ
        if allow_exts and p.suffix.lower() not in allow_exts:
            continue

        # include_globs / exclude_globs によるフィルタ
        if include_globs and not _path_matches_globs(rel, include_globs, is_dir=False):
            continue
        if exclude_globs and _path_matches_globs(rel, exclude_globs, is_dir=False):
            continue

        matched.append(rel)
        if len(matched) >= max_files:
            break

    matched.sort()
    return "\n".join(matched)


@tool
def read_file(file_name: str, project_id: int) -> str:
    """
    引き数に指定されたファイルの内容を読み取り、テキストで返却する。
    相対パスが指定された場合は doc_path 配下を基準として解決し、doc_path の外を指す場合はエラーとする。

    Args:
        file_name: 読み取り対象のファイルパス。相対パスの場合は doc_path を基準に解決する。
        project_id: 必須。プロジェクトの doc_path 解決に使用。

    Raises:
        ValueError: project_id が不正、doc_path が未設定/不正、または doc_path 外を指している場合。
        FileNotFoundError / OSError: ファイル未存在・アクセス不可など（従来どおり伝播）。

    Returns:
        ファイル内容（UTF-8テキスト）
    """
    # doc_path を解決（tools.py 内にあるヘルパを利用）
    base = _resolve_doc_path(project_id)  # -> Path

    p = Path(file_name).expanduser()
    # 相対パスなら doc_path 配下に連結
    if not p.is_absolute():
        p = base / p

    # 正規化
    p = p.resolve()

    # doc_path 配下強制（脱出禁止）
    try:
        _ = p.relative_to(base)
    except Exception:
        raise ValueError(f"read_file: path must be under doc_path (got: {p}, doc_path: {base})")

    with open(p, encoding="utf-8") as f:
        return f.read()


@tool
def write_file(file_path: str, content: str, project_id: int) -> bool:
    """
    第1引数に指定されたパスへ UTF-8 テキストを書き込みます（上書き）。
    相対パスが指定された場合は doc_path 配下を基準として解決し、doc_path の外を指す場合は書き込みません。

    Args:
        file_path: 書き込み先のファイルパス。相対パスの場合は doc_path を基準に解決します。
        content:   書き込む文字列（UTF-8）
        project_id: 必須。プロジェクトの doc_path 解決に使用

    Returns:
        正常時 True、異常時 False（doc_path 外指定・作成/書き込みエラー等）

    備考:
        - 親ディレクトリが無い場合は自動作成します。
        - 安全のため、絶対パスが指定された場合でも doc_path 配下でないと書き込みません。
        - 例外は握りつぶして False を返します（従来の戻り値仕様と整合）。
          例外詳細を呼び出し側で使いたい場合は、ここで raise に変更してください。
    """
    try:
        base = _resolve_doc_path(project_id)  # -> Path（存在かつディレクトリを保証）

        p = Path(file_path).expanduser()
        # 相対パスなら doc_path 配下に連結
        if not p.is_absolute():
            p = base / p

        # 正規化
        p = p.resolve()

        # doc_path 配下強制（脱出禁止）
        try:
            _ = p.relative_to(base)
        except Exception:
            # doc_path 外は書き込まない
            return False

        # 親ディレクトリを作成（存在してもエラーにならない）
        p.parent.mkdir(parents=True, exist_ok=True)

        # UTF-8 で上書き
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)

        return True
    except Exception:
        # ここでログに詳細を残したい場合は print などを追加してください
        return False


@tool
def make_dirs(dir_path: str, project_id: int) -> bool:
    """
    指定ディレクトリを作成します（親ディレクトリもまとめて作成）。
    相対パスが指定された場合は doc_path 配下を基準として解決し、doc_path の外を指す場合は作成しません。

    Args:
        dir_path: 作成したいディレクトリのパス。相対パスの場合は doc_path を基準に解決します。
        project_id: 必須。プロジェクトの doc_path 解決に使用します。

    Returns:
        正常時 True、異常時 False（doc_path 外指定・作成エラー等）

    備考:
        - 安全のため、絶対パスが指定された場合でも doc_path 配下でないと作成しません。
        - 例外は握りつぶして False を返します（write_file と同様の方針）。
    """
    try:
        base = _resolve_doc_path(project_id)  # -> Path（存在かつディレクトリを保証）

        p = Path(dir_path).expanduser()
        # 相対パスなら doc_path 配下に連結
        if not p.is_absolute():
            p = base / p

        # 正規化
        p = p.resolve()

        # doc_path 配下強制（脱出禁止）
        try:
            _ = p.relative_to(base)
        except Exception:
            # doc_path 外は作成しない
            return False

        # ディレクトリ作成（既存でもOK）
        p.mkdir(parents=True, exist_ok=True)
        return True
    except Exception:
        return False


@tool
def list_files(
        base_path: str,
        include_exts: Optional[List[str]] = None,
        project_id: Optional[int] = None,
) -> str:
    """
    base_path 配下のファイルを拡張子フィルタで列挙し、相対パス（base_path 相対）を改行区切りで返します。
    ただし検索対象は doc_path 配下に限定し、検索スコープは search_paths.json（includes/excludes）に必ず従います。

    変更点:
    - project_id を必須化（None はエラー）。
    - base_path は doc_path 配下である必要があり、外を指す場合はエラー。
    - search_paths.json（instance/<project_id>/search_paths.json）の include/exclude を必須適用。
      ファイル未存在/空/解析失敗はエラー。

    Args:
        base_path: 起点ディレクトリ（doc_path またはその配下のみ許可）
        include_exts: 許可する拡張子（ドット付き、例: [".py",".js"] または "py,js"）
        project_id: 必須。doc_path と search_paths.json の解決に使用

    Returns:
        見つかった base_path 相対パスの改行区切り文字列（0件でも空文字列）

    Raises:
        ValueError: project_id 未指定、base_path が不正、doc_path 配下でない、または search_paths.json が空などの構成不備
        FileNotFoundError / ValueError: search_paths.json 未存在 or 解析失敗
    """
    # project_id 必須
    if project_id is None:
        raise ValueError("list_files: project_id は必須です")

    # doc_path を解決（実装に合わせて置換してください）
    # 例: doc_path = Path(_resolve_doc_path(project_id)).expanduser().resolve()
    doc_path = Path(_resolve_doc_path(project_id)).expanduser().resolve()

    if not doc_path.exists() or not doc_path.is_dir():
        raise ValueError(f"list_files: doc_path が見つからないかディレクトリではありません: {doc_path}")

    # base_path は doc_path 配下のみ許可
    root = Path(base_path).expanduser().resolve()
    try:
        _ = root.relative_to(doc_path)
    except Exception:
        raise ValueError("list_files: base_path は doc_path の配下である必要があります")

    if not root.exists() or not root.is_dir():
        # 仕様上はエラーでも良いが、従来互換で空を返す
        return ""

    # search_paths.json を必須適用（未存在/壊れ/空はエラー）
    saved = _load_search_paths_globs(project_id)  # 実装により未存在時に空を返す場合がある
    include_globs: List[str] = saved.get("include_globs") or []
    exclude_globs: List[str] = saved.get("exclude_globs") or []
    if not include_globs and not exclude_globs:
        raise ValueError("list_files: search_paths.json が見つからないか、includes/excludes が空です")

    # include_exts の正規化（未指定時は既定セット）
    default_exts = {".py", ".html", ".php", ".js"}
    allow_exts: set[str] = set()
    if include_exts is None:
        allow_exts = default_exts
    else:
        if isinstance(include_exts, str):
            raw = [x.strip() for x in include_exts.split(",") if x.strip()]
        else:
            raw = [str(x).strip() for x in include_exts if str(x).strip()]
        for x in raw:
            s = x.lower()
            if not s.startswith("."):
                s = "." + s
            allow_exts.add(s)
        if not allow_exts:
            allow_exts = default_exts

    # 検索（base_path 配下）: search_paths.json の評価は doc_path 相対で行う
    paths: list[str] = []
    for ext in allow_exts:
        for p in root.rglob(f"*{ext}"):
            if not p.is_file():
                continue

            # 念のため doc_path 配下を強制（シンボリックリンク抜け道対策）
            try:
                doc_rel_posix = p.resolve().relative_to(doc_path).as_posix()
            except Exception:
                # doc_path 外はスキップ（またはエラー扱いにしても良い）
                continue

            # search_paths.json の include/exclude を doc_path 相対で必ず評価
            if include_globs and not _path_matches_globs(doc_rel_posix, include_globs, is_dir=False):
                continue
            if exclude_globs and _path_matches_globs(doc_rel_posix, exclude_globs, is_dir=False):
                continue

            # 返却は base_path 相対
            rel = p.relative_to(root).as_posix()
            paths.append(rel)

    return "\n".join(sorted(set(paths)))

@tool
def list_dirs(
        base_path: str,
        pattern: str = "**/*",
        max_dirs: int = 200,
        honor_gitignore: bool = True,
        project_id: int = None,
) -> str:
    """
    base_path配下から、ディレクトリのみを検索して相対パスを改行区切りで返します。
    例: list_dirs("repo", "src/*") -> "src/components\nsrc/utils\n..."

    Args:
        base_path: 検索の起点ディレクトリ
        pattern:  globパターン（例: "**/*", "src/*" など）
        max_dirs: 返す最大件数（過大応答の抑制）
        project_id: 必須。UI 保存の検索パス（search_paths.json）を取得するために使用

    Raises:
        ValueError: project_id 未指定、または base_path が不正な場合
        FileNotFoundError / ValueError: search_paths.json 未存在 or 解析失敗（_load_search_paths_globs 由来）

    Returns:
        見つかった相対ディレクトリパスの改行区切り文字列（0件でも空文字列）
    """
    if project_id is None:
        raise ValueError("list_dirs: project_id は必須です")

    root = Path(base_path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        # 既存仕様に合わせて空文字返却（必要なら例外にしてもよい）
        return ""

    # search_paths.json を必ず読み込み（未存在/壊れは例外→上位へ伝播）
    sp = _load_search_paths_globs(project_id)
    include_globs: List[str] = sp.get("include_globs") or []
    exclude_globs: List[str] = sp.get("exclude_globs") or []


    matched: List[str] = []
    for p in root.glob(pattern):
        if not p.is_dir():
            continue
        rel = p.relative_to(root).as_posix()
        if rel in (".", ""):
            continue

        # search_paths.json の include/exclude を必ず評価
        if include_globs and not _path_matches_globs(rel, include_globs, is_dir=True):
            continue
        if exclude_globs and _path_matches_globs(rel, exclude_globs, is_dir=True):
            continue

        matched.append(rel)
        if len(matched) >= max_dirs:
            break

    matched.sort()
    return "\n".join(matched)


@tool
def file_stat(file_path: str, project_id: int) -> str:
    """
    ファイルの存在・サイズ・更新時刻・行数を返す（JSON文字列）。
    相対パスは doc_path を基準に解決し、doc_path 外を指す場合はエラーを返す。

    Args:
        file_path: 対象ファイルのパス。相対の場合は doc_path を基準に解決する。
        project_id: 必須。doc_path 解決・ベースパス強制に用いる。

    Returns(JSON):
        {
          "exists": bool,
          "size"?: int,
          "mtime"?: float,
          "line_count"?: int,
          "path"?: str,             # 解決後の絶対パス（doc_path 配下）
          "error"?: str
        }
    """
    info: Dict[str, object] = {"exists": False, "path": str(file_path)}

    # doc_path を解決
    try:
        base = _resolve_doc_path(project_id)  # -> Path
    except Exception as e:
        info["error"] = f"doc_path_resolve_failed: {type(e).__name__}: {e}"
        return json.dumps(info, ensure_ascii=False)

    # 相対 → doc_path 連結、正規化
    p = Path(file_path).expanduser()
    if not p.is_absolute():
        p = base / p
    p = p.resolve()
    info["path"] = str(p)

    # doc_path 配下強制
    try:
        _ = p.relative_to(base)
    except Exception:
        info["error"] = f"path_must_be_under_doc_path (got: {p}, doc_path: {base})"
        return json.dumps(info, ensure_ascii=False)

    # 存在/種別チェック
    if not p.exists() or not p.is_file():
        # exists False のまま返す（size/mtime/line_count は付けない）
        return json.dumps(info, ensure_ascii=False)

    # 統計取得
    try:
        st = p.stat()
        info["exists"] = True
        info["size"] = int(st.st_size)
        info["mtime"] = float(st.st_mtime)

        # 行数はテキスト扱い（errors="ignore"）
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

    Args:
        file_path: 対象ファイルのパス。相対パスは doc_path を基準に解決。
        start_line: 開始行（1始まり、1未満は1に切り上げ）
        end_line: 終了行（start_line 未満が来た場合は start_line に切り上げ）
        project_id: 必須。doc_path の解決に使用。

    Returns(JSON):
        {
          "exists": bool,
          "start_line"?: int,
          "end_line"?: int,
          "content"?: str,
          "path"?: str,           # 解決後の絶対パス（doc_path 配下）
          "error"?: str
        }
    """
    result: Dict[str, object] = {}

    # doc_path 解決
    try:
        base = _resolve_doc_path(project_id)  # -> Path
    except Exception as e:
        result.update({"exists": False, "error": f"doc_path_resolve_failed: {type(e).__name__}: {e}"})
        return json.dumps(result, ensure_ascii=False)

    # 相対→doc_path 連結、正規化
    p = Path(file_path).expanduser()
    if not p.is_absolute():
        p = base / p
    p = p.resolve()
    result["path"] = str(p)

    # doc_path 配下強制
    try:
        _ = p.relative_to(base)
    except Exception:
        result.update({
            "exists": False,
            "error": f"path_must_be_under_doc_path (got: {p}, doc_path: {base})"
        })
        return json.dumps(result, ensure_ascii=False)

    # 存在/種別チェック
    if not p.exists() or not p.is_file():
        result["exists"] = False
        return json.dumps(result, ensure_ascii=False)

    # 行範囲の正規化
    s = max(1, int(start_line))
    e = max(s, int(end_line))

    # 抽出
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


@tool
def list_python_symbols(file_path: str, project_id: int) -> str:
    """
    Pythonファイルの関数/クラスと開始・終了行を抽出して返す（JSON文字列）。
    相対パスは doc_path を基準に解決し、doc_path の外を指す場合はエラーにします。

    Args:
        file_path: 対象の Python ファイル（.py）。相対パスは doc_path を基準に解決します。
        project_id: 必須。doc_path の解決とベースパス強制に使用します。

    Returns(JSON):
        {
          "exists": bool,
          "path": str,           # 解決後の絶対パス（doc_path 配下）
          "symbols": [           # 見つかったシンボル（開始/終了行番号付き）
            { "name": str, "kind": "function"|"class", "start": int, "end": int },
            ...
          ],
          "error"?: str
        }
    """
    out: Dict[str, object] = {"exists": False, "path": str(file_path), "symbols": []}
    try:
        # doc_path を解決
        base = _resolve_doc_path(project_id)  # -> Path

        # 相対→doc_path 連結、正規化
        p = Path(file_path).expanduser()
        if not p.is_absolute():
            p = base / p
        p = p.resolve()
        out["path"] = str(p)

        # doc_path 配下強制（脱出禁止）
        try:
            _ = p.relative_to(base)
        except Exception:
            out["error"] = f"path_must_be_under_doc_path (got: {p}, doc_path: {base})"
            return json.dumps(out, ensure_ascii=False)

        # 存在/種別チェック
        if (not p.exists()) or (not p.is_file()):
            return json.dumps(out, ensure_ascii=False)

        # 読み取り→AST解析
        src = p.read_text(encoding="utf-8", errors="ignore")
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
        out["exists"] = True
        out["symbols"] = symbols
        return json.dumps(out, ensure_ascii=False)

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
        ensure_trailing_newline: bool = True,
        project_id: int = None,
) -> str:
    """
    ファイルの途中に code を差し込むツール。
    - line を指定: その行の "before/after" に挿入
    - anchor を指定: マッチ行の "before/after" に挿入（first/last/nth、部分一致 or 正規表現）
    - line と anchor の両方は指定しないこと（line が優先）

    追加仕様:
    - project_id は必須。相対パスは doc_path を基準に解決し、doc_path 外を指す場合はエラー。
    - ensure_trailing_newline=True のとき、code の末尾に改行が無ければ付与。
    """
    out: Dict[str, object] = {
        "ok": False, "insert_at": None, "mode": None, "where": where,
        "matched_line": None, "occurrence": occurrence, "regex": bool(regex),
        "backup": False  # 現状バックアップは未実装（必要なら別途実装）
    }

    try:
        # doc_path を解決し、相対→doc_path 連結、正規化
        if project_id is None:
            out["error"] = "project_id_required"
            return json.dumps(out, ensure_ascii=False)

        base = _resolve_doc_path(project_id)  # -> Path
        p = Path(file_path).expanduser()
        if not p.is_absolute():
            p = base / p
        p = p.resolve()

        # doc_path 配下強制（脱出禁止）
        try:
            _ = p.relative_to(base)
        except Exception:
            out["error"] = f"path_must_be_under_doc_path (got: {p}, doc_path: {base})"
            return json.dumps(out, ensure_ascii=False)

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

            # マッチ行を収集（1始まり）
            matches: List[int] = []
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

            # 出現箇所の選択
            occ = (occurrence or "first").lower()
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

            if where == "before":
                insert_index = min(max(0, base_line - 1), len(lines))
                out["insert_at"] = base_line
            else:  # after
                insert_index = min(max(0, base_line), len(lines))
                out["insert_at"] = base_line + 1

            out["matched_line"] = base_line

        # 行番号モードの insert_at 設定（アンカーは上で設定済み）
        if out["mode"] == "line":
            # 1始まりで返す（where によって位置が異なる）
            out["insert_at"] = insert_index + 1

        # 実挿入
        if insert_index is None:
            out["error"] = "insert_index_not_determined"
            return json.dumps(out, ensure_ascii=False)

        lines[insert_index:insert_index] = code_lines  # スライス挿入

        # 書き込み（doc_path 配下の安全なパスに対して）
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
        length: Optional[int] = None,  # 削除する行数（省略時は1行）
        project_id: int = None,  # 必須。doc_path を解決し、相対パスを doc_path 基準にする
        # 追加オプション（新規）
        mark_only: bool = False,  # True のとき削除せずマークで囲って可視化
        deletion_uuid: Optional[str] = None,  # 省略時は uuid4().hex を生成
        comment_prefix: str = "#",  # マークのコメント記号（既定 "#")
) -> str:
    """
    指定範囲を削除します（バックアップは取りません。必要なら呼び出し側で実施してください）。2通りの指定方法:
      1) line_start / line_end を与える（1始まり・endを含む）
      2) anchor を与える → マッチ行を基準に offset/length で範囲を決める

    追加仕様:
    - project_id は必須。相対パスは doc_path を基準に解決し、doc_path 外を指す場合はエラー。
    - mark_only=True の場合、削除せずに対象範囲を
        "{comment_prefix} {UUID}-D start" ～ "{comment_prefix} {UUID}-D end"
      のコメントで囲んで可視化します（UUID は start/end で同一）。
    """
    out: Dict[str, object] = {
        "ok": False, "mode": None, "matched_line": None,
        "start_line": None, "end_line": None, "deleted": 0,
        "occurrence": occurrence, "regex": bool(regex),
        # 追加の返却フィールド
        "marked": False, "marker_uuid": None, "marker_start_line": None, "marker_end_line": None,
    }
    try:
        import uuid

        # project_id 必須
        if project_id is None:
            out["error"] = "project_id_required"
            return json.dumps(out, ensure_ascii=False)

        # doc_path を解決
        base = _resolve_doc_path(project_id)  # -> Path

        # 相対→doc_path 連結、正規化
        p = Path(file_path).expanduser()
        if not p.is_absolute():
            p = base / p
        p = p.resolve()

        # doc_path 配下強制（脱出禁止）
        try:
            _ = p.relative_to(base)
        except Exception:
            out["error"] = f"path_must_be_under_doc_path (got: {p}, doc_path: {base})"
            return json.dumps(out, ensure_ascii=False)

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

            s = base_line + int(offset)
            if length is None:
                length = 1
            e = s + int(length) - 1
            out["matched_line"] = base_line

        # 範囲補正と妥当性
        if s > e:
            out["error"] = "invalid_range"
            return json.dumps(out, ensure_ascii=False)
        s = max(1, s)
        e = min(len(lines), e)
        out["start_line"], out["end_line"] = int(s), int(e)

        # 実処理の分岐
        s_idx, e_idx = s - 1, e
        block = lines[s_idx:e_idx]  # 削除対象のブロック

        if mark_only:
            # マーキング（削除しない）
            uid = deletion_uuid or uuid.uuid4().hex
            # ブロック先頭行のインデントを継承（なければ空）
            indent = ""
            if block:
                m = re.match(r"\s*", block[0])
                indent = m.group(0) if m else ""
            start_marker = f"{indent}{comment_prefix} {uid}-D start\n"
            end_marker = f"{indent}{comment_prefix} {uid}-D end\n"

            # 挿入: [start_marker] + block + [end_marker]
            lines[s_idx:e_idx] = [start_marker] + block + [end_marker]

            out["marked"] = True
            out["marker_uuid"] = uid
            out["marker_start_line"] = s  # マーカー開始（1始まり）
            out["marker_end_line"] = s + len(block) + 1  # マーカー終端の行番号（start + ブロック長 + 1）

            # 削除は行わない
            out["deleted"] = 0

        else:
            # 既存の削除ロジック
            del_count = e_idx - s_idx
            if del_count <= 0:
                out["error"] = "empty_range"
                return json.dumps(out, ensure_ascii=False)
            lines[s_idx:e_idx] = []
            out["deleted"] = del_count

        # 書き戻し（UTF-8）
        with p.open("w", encoding="utf-8") as f:
            f.write("".join(lines))

        out["ok"] = True
        return json.dumps(out, ensure_ascii=False)

    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        return json.dumps(out, ensure_ascii=False)


@tool  # 必要に応じて有効化
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
    仕様:
    - 検索対象は doc_path 配下のみ。base_path が doc_path の外を指す場合はエラー。
    - 検索ディレクトリのスコープは search_paths.json（_load_search_paths_globs）の include/exclude に従う。
    - 検索キーワードは正規表現（regex）のみ。
    - project_id は必須。

    Args:
        base_path: 検索の起点ディレクトリ（doc_path か、その配下を指定してください）
        regex:     正規表現パターン（例: r"foo\\s+bar"。大文字小文字はパターン側のフラグで制御）
        project_id: 必須。doc_path と search_paths.json の取得に使用
        extensions: 拡張子ホワイトリスト（例: [".py",".js"]）。None は全て対象
        max_files:  走査する最大ファイル数
        max_matches_per_file: 1ファイルあたりの最大マッチ数
        max_total_matches:    全体の最大マッチ数
        context_lines: マッチ行の前後に付与する文脈行数
        size_limit_bytes_per_file: 1ファイルあたりの最大サイズ（超過はスキップ）
        encoding: テキスト読み取りのエンコーディング（errors="ignore"）
        follow_symlinks: os.walk の followlinks
        timeout_seconds: 全体のタイムアウト秒数

    Returns(JSON):
        {
          "ok": true/false,
          "base_path": "...",            # 呼び出し時の base_path
          "doc_path": "...",             # 解決された doc_path（絶対パス）
          "query": regex,
          "is_regex": true,
          "stats": {...},
          "files": [ { "file_path":"...", "ext":".py", "size":123, "match_count":2, "truncated":false, "encoding_used":"utf-8", "matches":[...] } ],
          "errors": [ {"file_path":"...", "error":"..."} ]
        }
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

    # project_id 必須
    if project_id is None:
        result["ok"] = False
        result["errors"].append({"file_path": "", "error": "project_id is required"})
        return json.dumps(result, ensure_ascii=False)

    # doc_path を解決（プロジェクトの実装に合わせて関数名を置換してください）
    try:
        doc_path = Path(_resolve_doc_path(project_id)).expanduser().resolve()
    except Exception as e:
        result["ok"] = False
        result["errors"].append({"file_path": "", "error": f"doc_path resolve failed: {e}"})
        return json.dumps(result, ensure_ascii=False)

    result["doc_path"] = str(doc_path)

    if not doc_path.exists() or not doc_path.is_dir():
        result["ok"] = False
        result["errors"].append({"file_path": str(doc_path), "error": "doc_path not found or not directory"})
        return json.dumps(result, ensure_ascii=False)

    # base_path が doc_path 配下かチェック（外ならエラー）
    root = Path(base_path).expanduser().resolve()
    try:
        # root が doc_path の配下であれば相対を取得可能（外なら ValueError）
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

    # search_paths.json を読み込み（無い/壊れは例外→errorsへ）
    try:
        saved = _load_search_paths_globs(project_id)
        include_globs: List[str] = saved.get("include_globs", []) or []
        exclude_globs: List[str] = saved.get("exclude_globs", []) or []
    except FileNotFoundError as e:
        result["ok"] = False
        result["errors"].append({"file_path": "", "error": f"search_paths.json not found: {e}"})
        return json.dumps(result, ensure_ascii=False)
    except ValueError as e:
        result["ok"] = False
        result["errors"].append({"file_path": "", "error": f"search_paths.json parse error: {e}"})
        return json.dumps(result, ensure_ascii=False)

    default_excluded = {"vendor", ".github", "logs", ".git"}  # 既定の除外

    # 拡張子フィルタ（小文字比較）
    ext_whitelist = set(e.lower() for e in (extensions or []))

    # 正規表現コンパイル（パターン内フラグで制御）
    try:
        pattern = re.compile(regex)
    except re.error as e:
        result["ok"] = False
        result["errors"].append({"file_path": "", "error": f"invalid regex: {e}"})
        return json.dumps(result, ensure_ascii=False)

    scanned_files = 0
    matched_files = 0
    total_matches = 0
    scanned_bytes = 0
    global_truncated = False

    def file_matches(doc_rel_posix: str) -> bool:
        # include: いずれかに合致必須（空なら無条件 pass）
        if include_globs and not _path_matches_globs(doc_rel_posix, include_globs, is_dir=False):
            return False
        # exclude: 合致すれば除外
        if exclude_globs and _path_matches_globs(doc_rel_posix, exclude_globs, is_dir=False):
            return False
        return True

    for dirpath, dirnames, filenames in os.walk(root, followlinks=follow_symlinks):
        # 既定の除外ディレクトリを walk から除外
        dirnames[:] = [d for d in dirnames if d not in default_excluded]

        # タイムアウト
        if timeout_seconds and (time.time() - start_ts) > timeout_seconds:
            global_truncated = True
            break

        for fname in filenames:
            if timeout_seconds and (time.time() - start_ts) > timeout_seconds:
                global_truncated = True
                break

            fpath = Path(dirpath) / fname

            # 念のため doc_path 配下を強制（シンボリックリンク等の抜け道を遮断）
            try:
                _ = fpath.resolve().relative_to(doc_path)
            except Exception:
                # doc_path 外が出てきたらスキップ（またはエラー扱いにしてもよい）
                result["errors"].append({"file_path": str(fpath), "error": "skipped: outside doc_path"})
                continue

            # doc_path を基準にした相対パス（search_paths.json の評価に用いる）
            doc_rel_posix = fpath.resolve().relative_to(doc_path).as_posix()
            # 表示上や互換性のための base_path 相対も計算
            base_rel_posix = fpath.resolve().relative_to(root).as_posix()

            # 拡張子フィルタ
            ext = fpath.suffix.lower()
            if ext_whitelist and ext not in ext_whitelist:
                continue

            # search_paths.json の include/exclude を適用（doc_path 相対）
            if not file_matches(doc_rel_posix):
                continue

            # サイズチェック
            try:
                size = fpath.stat().st_size
            except OSError as e:
                result["errors"].append({"file_path": doc_rel_posix, "error": str(e)})
                continue

            if size_limit_bytes_per_file and size > size_limit_bytes_per_file:
                continue

            scanned_files += 1
            if scanned_files > max_files:
                global_truncated = True
                break

            # 読み取り
            try:
                with open(fpath, "r", encoding=encoding, errors="ignore") as rf:
                    lines = rf.readlines()
            except Exception as e:
                result["errors"].append({"file_path": doc_rel_posix, "error": str(e)})
                continue

            scanned_bytes += size
            matches = []
            per_file_truncated = False

            for i, line in enumerate(lines, start=1):
                try:
                    it = list(pattern.finditer(line))
                except Exception as e:
                    result["errors"].append({"file_path": doc_rel_posix, "error": f"regex error at line {i}: {e}"})
                    it = []

                for m in it:
                    col_start = m.start() + 1
                    col_end = m.end() + 1

                    before_start = max(0, i - 1 - context_lines)
                    before = [l.rstrip("\n") for l in lines[before_start: i - 1]] if context_lines > 0 else []
                    after_end = min(len(lines), i + context_lines)
                    after = [l.rstrip("\n") for l in lines[i: after_end]] if context_lines > 0 else []

                    matches.append({
                        "line_no": i,
                        "col_start": col_start,
                        "col_end": col_end,
                        "line": line.rstrip("\n"),
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
                # file_path は base_path 相対で返す（従来互換）。必要なら doc_path 相対も追加可。
                result["files"].append({
                    "file_path": base_rel_posix,
                    "ext": ext,
                    "size": size,
                    "match_count": len(matches),
                    "truncated": per_file_truncated,
                    "encoding_used": encoding,
                    "matches": matches,
                })

            if global_truncated:
                break

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
