from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Any

from services.project_service import ProjectService


class SearchPathService:
    """
    検索パス（include/exclude）の永続化と取得、ツリー生成を担当。
    保存場所: instance/<project_id>/search_paths.json
    フォーマット:
    {
      "version": 1,
      "includes": ["controllers", "services"],
      "excludes": ["controllers/legacy"]
    }
    """

    VERSION = 1

    def _instance_dir(self, project_id: int) -> Path:
        base = Path.cwd() / "instance" / str(project_id)
        base.mkdir(parents=True, exist_ok=True)
        return base

    def _state_path(self, project_id: int) -> Path:
        return self._instance_dir(project_id) / "search_paths.json"

    def load_state(self, project_id: int) -> Dict[str, Any]:
        p = self._state_path(project_id)
        if not p.exists():
            return {"version": self.VERSION, "includes": [], "excludes": []}
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            inc = data.get("includes") or []
            exc = data.get("excludes") or []
            if not isinstance(inc, list) or not isinstance(exc, list):
                return {"version": self.VERSION, "includes": [], "excludes": []}
            return {"version": int(data.get("version") or self.VERSION), "includes": inc, "excludes": exc}
        except Exception:
            # 壊れている場合は初期状態を返す
            return {"version": self.VERSION, "includes": [], "excludes": []}

    def save_state(self, project_id: int, includes: List[str], excludes: List[str]) -> Dict[str, Any]:
        state = {
            "version": self.VERSION,
            "includes": sorted(list(dict.fromkeys(includes or []))),
            "excludes": sorted(list(dict.fromkeys(excludes or []))),
        }
        p = self._state_path(project_id)
        p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        return state

    def _doc_base(self, project_id: int) -> Path:
        ps = ProjectService()
        proj = ps.fetch_by_id(project_id)
        if not proj or not getattr(proj, "doc_path", None):
            raise ValueError("doc_path_not_set")
        base = Path(proj.doc_path).expanduser().resolve()
        if not base.exists() or not base.is_dir():
            raise ValueError("invalid_doc_path")
        return base

    def build_tree(self, project_id: int, rel: str = "") -> List[Dict[str, Any]]:
        """
        doc_path 配下のディレクトリツリーを返す（配列）。
        - vendor/.github/logs/tmp は除外（深さ無制限でサブツリーごと除外）
        - search_paths.json の excludes も除外（先頭一致でサブツリーごと除外）
        - rel（相対パス）でサブツリー取得にも対応
        返却ノード: { name, rel, children: [...] }
        """
        base = self._doc_base(project_id)
        start = base
        rel_norm = (rel or "").strip().replace("\\", "/")
        if rel_norm:
            start = (base / rel_norm).resolve()
            try:
                start.relative_to(base)
            except Exception:
                start = base
        if not start.exists() or not start.is_dir():
            start = base

        # 除外パスの準備（prefix一致で除外）。ドキュメント化されている既定の除外 + ユーザー設定
        state = self.load_state(project_id)
        exclude_prefixes: List[str] = []
        default_excludes = ["vendor", ".github", "logs", "tmp"]
        # state の excludes は相対パス前提。正規化して prefix 用に末尾スラッシュ付与
        for s in (state.get("excludes") or []) + default_excludes:
            s = str(s).strip().replace("\\", "/").strip("/")
            if not s:
                continue
            exclude_prefixes.append(s + "/")

        def is_excluded(rel_path: str) -> bool:
            rp = rel_path.replace("\\", "/").strip("/")
            rp_slash = rp + "/"
            for pre in exclude_prefixes:
                if rp_slash.startswith(pre):
                    return True
            return False

        def _walk(dir_path: Path, prefix: str = "") -> List[Dict[str, Any]]:
            out: List[Dict[str, Any]] = []
            # scandir でファイルを列挙しつつ、ディレクトリのみを効率的に抽出
            try:
                with os.scandir(dir_path) as it:
                    dirs = []
                    for entry in it:
                        try:
                            if not entry.is_dir(follow_symlinks=False):
                                continue
                        except Exception:
                            continue
                        name = entry.name
                        rel_child = f"{prefix}{name}" if not prefix else f"{prefix}{name}"
                        if is_excluded(rel_child):
                            continue
                        dirs.append((name, entry.path))
            except Exception:
                dirs = []
            # 名前順にソート（大文字小文字を無視）
            dirs.sort(key=lambda t: t[0].lower())

            for name, path_str in dirs:
                rel_child = f"{prefix}{name}" if not prefix else f"{prefix}{name}"
                children = _walk(Path(path_str), rel_child + "/")
                out.append({
                    "name": name,
                    "rel": rel_child.rstrip("/"),
                    "children": children,
                })
            return out

        # rel 指定がある場合は、その直下のツリーのみ返す（Lazy Load用途）。
        prefix = "" if start == base else (str(start.relative_to(base)).replace("\\", "/").rstrip("/") + "/")
        # 開始地点が除外配下の場合は、上位の呼び出し側が rel を誤っているので空ツリーを返す
        if prefix and is_excluded(prefix.rstrip("/")):
            return []
        return _walk(start, prefix)

    @staticmethod
    def to_globs_from_state(state: Dict[str, Any]) -> Dict[str, List[str]]:
        """
        保存状態から search_grep 向けの include_globs / exclude_globs を生成。
        - includes は path/** へ
        - excludes は path/** へ（exclude_globs として使用推奨）
        空なら空配列
        """
        inc = []
        exc = []
        for s in state.get("includes", []) or []:
            s = str(s).strip().replace("\\", "/").strip("/")
            if s:
                inc.append(f"{s}/**")
        for s in state.get("excludes", []) or []:
            s = str(s).strip().replace("\\", "/").strip("/")
            if s:
                exc.append(f"{s}/**")
        return {"include_globs": inc, "exclude_globs": exc}
