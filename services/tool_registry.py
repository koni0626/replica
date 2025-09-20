from __future__ import annotations

from pathlib import Path
from typing import Dict, Any

from services.project_service import ProjectService
from services.search_path_service import SearchPathService
from tools import tools as fs_tools
from tools import php_tools
from tools import git_tool
from tools import network_tool
from tools import rag_tools

# 共有のツールマップ（LangChainのToolオブジェクトを想定）
TOOL_MAP: Dict[str, Any] = {
    # FS/Text
    "find_files": fs_tools.find_files,
    "write_file": fs_tools.write_file,
    "read_file": fs_tools.read_file,
    "list_files": fs_tools.list_files,
    "list_dirs": fs_tools.list_dirs,
    "make_dirs": fs_tools.make_dirs,
    "file_stat": fs_tools.file_stat,
    "read_file_range": fs_tools.read_file_range,
    "list_python_symbols": fs_tools.list_python_symbols,
    "search_grep": fs_tools.search_grep,
    # RAG
    "rag_build_index": rag_tools.rag_build_index,
    "rag_update_index": rag_tools.rag_update_index,
    "rag_index_text": rag_tools.rag_index_text,
    "rag_query_text": rag_tools.rag_query_text,
    # PHP
    "php_locate_functions": php_tools.php_locate_functions,
    "php_insert_after_function_end": php_tools.php_insert_after_function_end,
    "php_replace_function_body": php_tools.php_replace_function_body,
    "php_list_symbols": php_tools.php_list_symbols,
    "php_detect_namespace_and_uses": php_tools.php_detect_namespace_and_uses,
    "php_add_strict_types_declare": php_tools.php_add_strict_types_declare,
    "php_insert_use_statement": php_tools.php_insert_use_statement,
    "php_add_method_to_class": php_tools.php_add_method_to_class,
    "php_replace_method_body": php_tools.php_replace_method_body,
    "php_lint": php_tools.php_lint,
    # ネットワーク
    "fetch_url_text": network_tool.fetch_url_text,
    "fetch_url_links": network_tool.fetch_url_links,
    # Git
    "git_diff_files": git_tool.git_diff_files,
    "git_diff_patch": git_tool.git_diff_patch,
    "git_list_branches": git_tool.git_list_branches,
    "git_current_branch": git_tool.git_current_branch,
    "git_log_range": git_tool.git_log_range,
    "git_show_file": git_tool.git_show_file,
    "git_status_porcelain": git_tool.git_status_porcelain,
    "git_rev_parse": git_tool.git_rev_parse,
    "git_repo_root": git_tool.git_repo_root,
    "git_diff_own_changes_files": git_tool.git_diff_own_changes_files,
}

# base_path を強制したいツール（doc_path 配下に制限する）
TOOLS_REQUIRE_BASE_PATH = {"find_files", "list_files", "list_dirs", "search_grep"}


def project_base_dir(project_id: int) -> Path:
    ps = ProjectService()
    proj = ps.fetch_by_id(project_id)
    if not proj or not getattr(proj, "doc_path", None):
        raise ValueError("doc_path_not_set")
    base = Path(proj.doc_path).expanduser().resolve()
    if (not base.exists()) or (not base.is_dir()):
        raise ValueError("invalid_doc_path")
    return base


def resolve_search_base(base_dir: Path, requested: str | None) -> Path:
    s = (requested or "").strip()
    if not s:
        return base_dir
    s = s.replace("\\", "/")
    if s.startswith("/"):
        s = s[1:]
    if s.lower().startswith("docs/"):
        s = s[5:]
    if ":" in s:
        return base_dir
    parts = [p for p in s.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        return base_dir
    sub = (base_dir / "/".join(parts)).resolve()
    try:
        sub.relative_to(base_dir)
    except Exception:
        return base_dir
    if sub.exists() and sub.is_dir():
        return sub
    return base_dir


# 保存された検索パス（UI指定）があれば、それを優先してグロブを注入するためのヘルパ
# 呼び出し側（例）: Agents で search_grep 実行前にこれを参照して include/exclude を上書き

def load_saved_search_globs(project_id: int) -> dict[str, list[str]]:
    try:
        state = SearchPathService().load_state(project_id)
        return SearchPathService.to_globs_from_state(state)
    except Exception:
        return {"include_globs": [], "exclude_globs": []}
