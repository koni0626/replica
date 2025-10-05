from __future__ import annotations
from pathlib import Path
import os
import fnmatch
import json
import re
from typing import List, Optional, Set, Literal, Dict

# 外部サービス（doc_path 解決に使用）
from services.project_service import ProjectService

# 既定のディレクトリ除外（巨大・不要なツリーを走査しないため）
EXCLUDED_NAMES_DEFAULT: Set[str] = {
    ".git", "vendor", ".github", "logs", "node_modules", ".venv", "__pycache__", ".idea"
}

# -----------------
# search_paths.json 読み込み（v2: includes は“ファイルのホワイトリスト”）
# -----------------

def load_search_paths_state(project_id: Optional[int]) -> Dict[str, List[str]]:
    """
    instance/<project_id>/search_paths.json を読み、正規化済みの includes/excludes を返す。
    返却:{"includes": [...], "excludes": [...]}（両方とも POSIX 相対パスの配列）
    project_id 無指定や未存在時は空配列を返す。
    """
    state = {"includes": [], "excludes": []}
    if project_id is None:
        return state
    try:
        inst = Path.cwd() / "instance" / str(project_id)
        sp = inst / "search_paths.json"
        if not sp.exists():
            return state
        data = json.loads(sp.read_text(encoding="utf-8"))
        ver = int(data.get("version") or 1)
        inc = data.get("includes") or []
        exc = data.get("excludes") or []

        def _norm_list(lst):
            out = []
            for s in lst:
                s = str(s).strip().replace("\\", "/").strip("/")
                if s:
                    out.append(s)
            return out

        inc_n = _norm_list(inc)
        exc_n = _norm_list(exc)
        # v1 の includes（ディレクトリ含む）は一旦そのまま返す（呼び出し側で祖先許容する箇所があれば考慮）。
        # v2 ではファイルのみが入っている想定。
        state["includes"] = inc_n
        state["excludes"] = exc_n
    except Exception:
        return {"includes": [], "excludes": []}
    return state

# 従来互換: globs 形式も提供（ただし新仕様では基本未使用）

def load_search_paths_globs(project_id: Optional[int]) -> dict:
    """
    includes/excludes を globs として返す（後方互換）。
    返却:{"include_globs": ["path/**",...], "exclude_globs": ["path/**",...]}
    """
    globs = {"include_globs": [], "exclude_globs": []}
    state = load_search_paths_state(project_id)

    def _to_globs(paths: List[str]) -> List[str]:
        out: List[str] = []
        for s in paths or []:
            s = str(s).strip().replace("\\", "/").strip("/")
            if s:
                out.append(f"{s}/**")
        return out

    globs["include_globs"] = _to_globs(state.get("includes") or [])
    globs["exclude_globs"] = _to_globs(state.get("excludes") or [])
    return globs

# -----------------
# doc_path 解決
# -----------------

def resolve_doc_path(project_id: int) -> Path:
    ps = ProjectService()
    proj = ps.fetch_by_id(project_id)
    if not proj or not getattr(proj, "doc_path", None):
        raise ValueError("doc_path_not_set")
    base = Path(proj.doc_path).expanduser().resolve()
    if (not base.exists()) or (not base.is_dir()):
        raise ValueError("invalid_doc_path")
    return base

# -----------------
# 共通ユーティリティ
# -----------------

def path_matches_globs(rel_posix: str, globs: List[str], is_dir: bool = False) -> bool:
    if not globs:
        return True
    rel_dir = rel_posix if not is_dir else (rel_posix.rstrip("/") + "/")
    for g in globs:
        if g.endswith("/**"):
            prefix = g[:-3]
            if is_dir:
                if rel_dir == prefix or rel_dir.startswith(prefix):
                    return True
            else:
                if rel_posix == prefix.rstrip("/") or rel_posix.startswith(prefix):
                    return True
        if fnmatch.fnmatch(rel_posix, g):
            return True
    return False


def normalize_exts(exts, default_set: Optional[Set[str]] = None) -> Set[str]:
    if exts is None:
        return set(default_set or [])
    if isinstance(exts, str):
        raw = [x.strip() for x in exts.split(",") if x.strip()]
    else:
        raw = [str(x).strip() for x in exts if str(x).strip()]
    out: Set[str] = set()
    for x in raw:
        s = x.lower()
        if not s.startswith("."):
            s = "." + s
        out.add(s)
    return out or set(default_set or [])


def rel_posix(p: Path, base: Path) -> str:
    return p.resolve().relative_to(base).as_posix()


def pattern_match(rel: str, pattern: Optional[str]) -> bool:
    if not pattern:
        return True
    return fnmatch.fnmatch(rel, pattern)


def includes_pattern_match(rel: str, pattern: Optional[str]) -> bool:
    """
    includes に保存された相対パス（doc_path 相対）に対するマッチ。
    互換のため、先頭が "**/" のパターンはそのプレフィックスを取り除いたものでもマッチ可とする。
    特別扱い: pattern == "**/*" は全許可。
    """
    if not pattern or pattern == "**/*":
        return True
    if fnmatch.fnmatch(rel, pattern):
        return True
    if pattern.startswith("**/"):
        alt = pattern[3:]
        if fnmatch.fnmatch(rel, alt):
            return True
    return False

# -----------------
# 統合スキャナ（新仕様: includes は“ファイル集合のホワイトリスト”）
# -----------------

def scan_tree(
    mode: Literal["files", "dirs"],
    base_path: str,
    project_id: Optional[int],
    *,
    # スコープ・安全ガード
    require_project: bool = False,
    require_search_paths: bool = False,
    # パターン・拡張子・件数
    pattern: Optional[str] = None,
    include_exts: Optional[List[str]] = None,
    exclude_exts: Optional[List[str]] = None,
    max_items: Optional[int] = None,
    # search_paths.json（globsは後方互換のため残すが、新仕様では使わない）
    include_globs: Optional[List[str]] = None,
    exclude_globs: Optional[List[str]] = None,
    # 走査制御
    extra_excluded_dirs: Optional[Set[str]] = None,
    allow_ancestor_for_include: bool = False,
    # 追加: パターンの適用先を "includes" に切替（True のとき、pattern は includes の相対パスに対してマッチ）
    pattern_on_includes: bool = False,
    # 追加: search_paths.json の excludes を無視する（.git/vendor等の既定除外は維持）
    ignore_excludes: bool = True,
) -> List[str]:
    """
    find_files / list_files / list_dirs 用の共通走査エンジン（新仕様）。
    - 検索対象は search_paths.json の includes に列挙された“ファイル”のみ。
    - ディレクトリ走査は基本行わず、includes の親ディレクトリ集合から返す（mode=="dirs"）。
    - 但し allow_ancestor_for_include=True の場合、includes に含まれる「ディレクトリ」を祖先として配下のファイルを再帰列挙対象に含める（後方互換）。
    - pattern_on_includes=True の場合、pattern は base_path 相対ではなく「includes に保存された相対パス（doc_path 相対）」に対して適用する。
    - base_path 相対の POSIX 文字列配列を返す。
    - 特別扱い: pattern == "**/*" の場合は「全許可」と解釈し、トップレベル（スラッシュを含まない）も除外しない。
    - ignore_excludes=True の場合、search_paths.json の excludes は評価しない（既定の巨大ディレクトリ除外は維持）。
    """
    # doc_path 解決
    doc_path: Optional[Path] = None
    if project_id is not None:
        doc_path = resolve_doc_path(project_id).expanduser().resolve()
    elif require_project:
        raise ValueError("project_id is required")

    # base_path の解決（相対が来た場合は doc_path 基準で解決／'repo' は doc_path のエイリアスとして扱う）
    bp_raw = str(base_path or "").strip()
    p = Path(bp_raw).expanduser()
    if doc_path is not None:
        if not p.is_absolute():
            if bp_raw in ("", ".", "repo"):
                root = doc_path
            else:
                root = (doc_path / p).resolve()
        else:
            root = p.resolve()
        # ガード: base_path は doc_path 配下のみ許容
        try:
            _ = root.relative_to(doc_path)
        except Exception:
            raise ValueError(f"base_path must be under doc_path (base_path={root}, doc_path={doc_path})")
    else:
        root = p.resolve()

    if not root.exists() or not root.is_dir():
        return []

    # 新仕様: ファイル集合のロード
    state = load_search_paths_state(project_id)
    includes_files = state.get("includes") or []
    includes_set = set(includes_files)
    excludes = set(state.get("excludes") or [])
    if ignore_excludes:
        excludes = set()

    if require_search_paths and not includes_files:
        # 明示的に何も選択されていなければ結果なし
        return []

    # 既定除外（親ディレクトリ計算時などに使用）
    excluded_names = set(EXCLUDED_NAMES_DEFAULT)
    if extra_excluded_dirs:
        excluded_names |= set(extra_excluded_dirs)

    # パターン・拡張子の事前正規化
    allow_exts = normalize_exts(include_exts, default_set=None if mode == "files" else None)
    deny_exts = normalize_exts(exclude_exts, default_set=set())

    out: List[str] = []
    seen: Set[str] = set()

    def add_file(abs_f: Path):
        # excludes（祖先一致）
        try:
            rel_from_doc = abs_f.resolve().relative_to(doc_path or root).as_posix()
        except Exception:
            return
        # ルール: 明示的に includes に入っているファイルは、excludes の祖先一致より優先する（explicit include wins）
        explicit = rel_from_doc in includes_set
        if excludes and (not explicit) and any(rel_from_doc == e or rel_from_doc.startswith(e + "/") for e in excludes):
            return
        # base_path 配下のみ
        try:
            rel_from_root = abs_f.resolve().relative_to(root).as_posix()
        except Exception:
            return
        ext = abs_f.suffix.lower()
        if deny_exts and ext in deny_exts:
            return
        if allow_exts and ext not in allow_exts:
            return
        # pattern の適用（includes ではなく base_path 相対に対して）
        if pattern and not pattern_on_includes:
            # 特別扱い: "**/*" は全許可（トップレベルも含める）
            if pattern != "**/*" and not pattern_match(rel_from_root, pattern):
                return
        if rel_from_root not in seen:
            seen.add(rel_from_root)
            out.append(rel_from_root)

    if mode == "files":
        # includes の各要素を処理
        for rel in includes_files:
            rel = str(rel).replace("\\", "/").strip("/")
            if not rel:
                continue
            # pattern を includes のパスに対して適用するオプション
            if pattern and pattern_on_includes:
                # 特別扱い: "**/*" は全許可（トップレベルも含める）
                if not includes_pattern_match(rel, pattern):
                    continue
            abs_t = (doc_path or root) / rel
            if abs_t.is_file():
                add_file(abs_t)
                if max_items and len(out) >= max_items:
                    return sorted(out)
            elif abs_t.is_dir() and allow_ancestor_for_include:
                # ディレクトリ祖先を許容する場合は再帰列挙
                for dirpath, dirnames, filenames in os.walk(abs_t, followlinks=False):
                    # 既定除外の枝刈り
                    dirnames[:] = [d for d in dirnames if d not in excluded_names]
                    for fn in filenames:
                        f = Path(dirpath) / fn
                        add_file(f)
                        if max_items and len(out) >= max_items:
                            return sorted(out)
        return sorted(out)

    # mode == "dirs": includes の親ディレクトリ集合
    parents: Set[str] = set()

    def add_parent_of(p: Path):
        try:
            parent = p.resolve().parent
            rel_parent = parent.relative_to(root).as_posix()
        except Exception:
            return
        # 既定除外ディレクトリは除外
        if any(part in excluded_names for part in Path(rel_parent).parts):
            return
        # ディレクトリに対するパターンは、通常 base_path 相対のディレクトリに対して
        if pattern and not pattern_on_includes:
            # 既定の "**/*" は「全ディレクトリ許可」とみなす（トップレベル名にもマッチさせる）
            if pattern != "**/*" and not pattern_match(rel_parent, pattern):
                return
        parents.add(rel_parent)

    for rel in includes_files:
        rel = str(rel).replace("\\", "/").strip("/")
        if not rel:
            continue
        # pattern を includes のパスに対して適用するオプション
        if pattern and pattern_on_includes:
            # 特別扱い: "**/*" は全許可（トップレベルも含める）
            if not includes_pattern_match(rel, pattern):
                continue
        abs_t = (doc_path or root) / rel
        if abs_t.is_file():
            add_parent_of(abs_t)
        elif abs_t.is_dir() and allow_ancestor_for_include:
            # ディレクトリ祖先を許容する場合は配下ファイルの親を追加
            for dirpath, dirnames, filenames in os.walk(abs_t, followlinks=False):
                dirnames[:] = [d for d in dirnames if d not in excluded_names]
                for fn in filenames:
                    f = Path(dirpath) / fn
                    add_parent_of(f)
                    if max_items and len(parents) >= max_items:
                        break

    out = sorted(list(parents))
    if max_items and len(out) > max_items:
        out = out[:max_items]
    return out
