from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Any, Iterable

from services.project_service import ProjectService


class SearchPathService:
    """
    検索パス（include/exclude）の永続化と取得、ツリー生成を担当。
    保存場所: instance/<project_id>/search_paths.json

    フォーマット v2（新仕様: includes はファイルのホワイトリスト）
    {
      "version": 2,
      "includes": ["controllers/user_controller.py", "services/user_service.py"],
      "excludes": ["controllers/legacy"]
    }

    v1（後方互換: includes はディレクトリを含む）
    {
      "version": 1,
      "includes": ["controllers", "services"],
      "excludes": ["controllers/legacy"]
    }
    """

    VERSION = 2

    def _instance_dir(self, project_id: int) -> Path:
        base = Path.cwd() / "instance" / str(project_id)
        base.mkdir(parents=True, exist_ok=True)
        return base

    def _state_path(self, project_id: int) -> Path:
        return self._instance_dir(project_id) / "search_paths.json"

    def load_state(self, project_id: int) -> Dict[str, Any]:
        p = self._state_path(project_id)
        REQUIRED_EXCLUDES = {".git", "vendor"}

        def _norm(s: str) -> str:
            return str(s).strip().replace("\\", "/").strip("/")

        if not p.exists():
            return {"version": self.VERSION, "includes": [], "excludes": sorted(list(REQUIRED_EXCLUDES))}
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            inc = data.get("includes") or []
            exc = data.get("excludes") or []
            if not isinstance(inc, list) or not isinstance(exc, list):
                return {"version": self.VERSION, "includes": [], "excludes": sorted(list(REQUIRED_EXCLUDES))}
            # 正規化
            inc_norm = [_norm(x) for x in inc if _norm(x)]
            exc_norm = [_norm(x) for x in exc if _norm(x)]
            # includes から .git / vendor を除外
            inc_norm = [x for x in inc_norm if
                        x not in REQUIRED_EXCLUDES and not any(x.startswith(f"{rx}/") for rx in REQUIRED_EXCLUDES)]
            # excludes へ強制追加
            exc_norm.extend(REQUIRED_EXCLUDES)
            return {
                "version": int(data.get("version") or 1),
                "includes": sorted(list(dict.fromkeys(inc_norm))),
                "excludes": sorted(list(dict.fromkeys(exc_norm))),
            }
        except Exception:
            # 壊れている場合は初期状態（必須除外のみ）
            return {"version": self.VERSION, "includes": [], "excludes": sorted(list(REQUIRED_EXCLUDES))}

    def _doc_base(self, project_id: int) -> Path:
        ps = ProjectService()
        proj = ps.fetch_by_id(project_id)
        if not proj or not getattr(proj, "doc_path", None):
            raise ValueError("doc_path_not_set")
        base = Path(proj.doc_path).expanduser().resolve()
        if not base.exists() or not base.is_dir():
            raise ValueError("invalid_doc_path")
        return base

    def _iter_files_under(self, base: Path, rel: str) -> Iterable[str]:
        """rel がディレクトリなら再帰でファイルを列挙、ファイルならそのまま返す（base 相対 POSIX）。
        .git / vendor / .github / logs / node_modules / .venv / __pycache__ / .idea は除外。
        存在しないパスは無視。
        """
        EXCLUDED_NAMES = {".git", "vendor", ".github", "logs", "node_modules", ".venv", "__pycache__", ".idea"}
        rel = str(rel).replace("\\", "/").strip("/")
        if not rel:
            return []
        target = (base / rel).resolve()
        try:
            target.relative_to(base)
        except Exception:
            return []
        if not target.exists():
            return []
        if target.is_file():
            try:
                return [target.relative_to(base).as_posix()]
            except Exception:
                return []
        out: list[str] = []
        if target.is_dir():
            for dirpath, dirnames, filenames in os.walk(target, followlinks=False):
                # 除外名の枝刈り
                dirnames[:] = [d for d in dirnames if d not in EXCLUDED_NAMES]
                for fn in filenames:
                    p = Path(dirpath) / fn
                    try:
                        out.append(p.resolve().relative_to(base).as_posix())
                    except Exception:
                        continue
        return out

    def save_state(self, project_id: int, includes: List[str], excludes: List[str]) -> Dict[str, Any]:
        """
        ツリー設定を保存する。
        - includes/excludes を正規化（パス区切りを / に統一、前後の / を除去）
        - .git / vendor / .idea は常に excludes に含める（強制）
        - includes は“ファイルのみ”として保存（ディレクトリが来た場合は配下ファイルへ展開）
        """
        REQUIRED_EXCLUDES = {".git", "vendor", ".idea"}

        def _norm(s: str) -> str:
            return str(s).strip().replace("\\", "/").strip("/")

        # 正規化
        inc_raw = [x for x in (includes or []) if str(x).strip()]
        exc_raw = [x for x in (excludes or []) if str(x).strip()]
        inc_norm = [_norm(x) for x in inc_raw if _norm(x)]
        exc_norm = [_norm(x) for x in exc_raw if _norm(x)]
        # includes から .git / vendor / .idea を除外（トップレベル表記のみ強制排除）
        inc_norm = [x for x in inc_norm if
                    x not in REQUIRED_EXCLUDES and not any(x.startswith(f"{rx}/") for rx in REQUIRED_EXCLUDES)]
        # excludes に .git / vendor / .idea を必ず追加
        exc_norm.extend(REQUIRED_EXCLUDES)

        # includes をファイルに正規化（v2仕様）
        base = self._doc_base(project_id)
        file_set: set[str] = set()
        for item in inc_norm:
            for f in self._iter_files_under(base, item):
                file_set.add(f)
        inc_final = sorted(list(file_set))
        exc_final = sorted(list(dict.fromkeys(exc_norm)))

        state = {
            "version": self.VERSION,
            "includes": inc_final,
            "excludes": exc_final,
        }
        p = self._state_path(project_id)
        p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        return state

    def build_tree(self, project_id: int, rel: str = "") -> List[Dict[str, Any]]:
        """
        doc_path 配下の "直下1階層のみ" を返す（Lazy Load 用）。
        - ディレクトリだけでなくファイルも返す
        - 巨大/不要ディレクトリは除外（.git / vendor / .github / logs / node_modules / .venv / __pycache__ / .idea）
        - rel を指定すると、そのサブディレクトリ直下のみを返す
        返却ノード:
          ディレクトリ: { type: 'dir', name, rel, has_children }
          ファイル    : { type: 'file', name, path, selected }
        selected は現状の includes に含まれるか（v1 includes=ディレクトリ時は祖先一致で true）
        """
        base = self._doc_base(project_id)
        state = self.load_state(project_id)
        version = int(state.get("version") or 1)
        inc_list: list[str] = state.get("includes", []) or []
        exc_list: list[str] = state.get("excludes", []) or []

        EXCLUDED_NAMES = {".git", "vendor", ".github", "logs", "node_modules", ".venv", "__pycache__", ".idea"}

        def is_excluded_path(p: Path) -> bool:
            try:
                parts = p.resolve().relative_to(base).parts
            except Exception:
                # base 外は対象外
                return True
            return any(part in EXCLUDED_NAMES for part in parts)

        def is_selected_file(rel_file: str) -> bool:
            rel_file = str(rel_file).replace("\\", "/").strip("/")
            if not rel_file:
                return False
            # excludes は優先して除外
            if any(rel_file == e or rel_file.startswith(e + "/") for e in exc_list):
                return False
            if version >= 2:
                return rel_file in inc_list
            # v1: includes にディレクトリが含まれている想定 — 祖先一致で採用
            return any(rel_file == i or rel_file.startswith(i + "/") for i in inc_list)

        # 開始ディレクトリを決定
        start = base
        rel_norm = (rel or "").strip().replace("\\", "/").strip("/")
        if rel_norm:
            candidate = (base / rel_norm).resolve()
            try:
                candidate.relative_to(base)
            except Exception:
                candidate = base
            start = base if is_excluded_path(candidate) else candidate

        if not start.exists() or not start.is_dir():
            start = base

        dirs: List[Dict[str, Any]] = []
        files: List[Dict[str, Any]] = []
        try:
            with os.scandir(start) as it:
                for entry in it:
                    name = entry.name
                    abs_p = Path(entry.path).resolve()
                    if name in EXCLUDED_NAMES or is_excluded_path(abs_p):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        # has_children（子ディレクトリ or ファイルの存在で判断）
                        has_children = False
                        try:
                            with os.scandir(abs_p) as it2:
                                for ch in it2:
                                    if ch.name in EXCLUDED_NAMES:
                                        continue
                                    ch_abs = Path(ch.path).resolve()
                                    if not is_excluded_path(ch_abs):
                                        has_children = True
                                        break
                        except Exception:
                            has_children = False
                        rel_child = str(abs_p.relative_to(base)).replace("\\", "/") if start != base else name
                        dirs.append({
                            "type": "dir",
                            "name": name,
                            "rel": rel_child,
                            "has_children": has_children,
                        })
                    elif entry.is_file(follow_symlinks=False):
                        try:
                            rel_file = str(abs_p.relative_to(base)).replace("\\", "/")
                        except Exception:
                            continue
                        files.append({
                            "type": "file",
                            "name": name,
                            "path": rel_file,
                            "selected": is_selected_file(rel_file),
                        })
        except Exception:
            # アクセス不可などは空配列
            return []

        # 名前順で安定化（ディレクトリ→ファイルの順）
        dirs.sort(key=lambda x: x["name"].lower())
        files.sort(key=lambda x: x["name"].lower())
        return dirs + files

    @staticmethod
    def to_globs_from_state(state: Dict[str, Any]) -> Dict[str, List[str]]:
        """
        互換ヘルパ（従来の search_grep 用）。
        v2では includes がファイル列になるが、従来コード互換のため "path/**" へ変換する。
        （ただし新仕様ではツール側で includes のファイル集合を直接参照する実装へ移行する想定）
        """
        inc = []
        exc = []
        for s in state.get("includes", []) or []:
            s = str(s).strip().replace("\\", "/").strip("/")
            if s:
                # ファイル指定でもディレクトリ指定でも、とにかく prefix/** にして返す（後方互換）
                # 本当の制限は fs_modules.scan_tree で includes ファイル集合へ限定される
                inc.append(f"{s}/**")
        for s in state.get("excludes", []) or []:
            s = str(s).strip().replace("\\", "/").strip("/")
            if s:
                exc.append(f"{s}/**")
        return {"include_globs": inc, "exclude_globs": exc}
