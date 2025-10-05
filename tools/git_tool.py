import os
import json
import subprocess
from pathlib import Path, PurePosixPath
from typing import List, Dict, Optional
from langchain_core.tools import tool


# 既存の repo ルート解決（レガシー・未使用化）
def _resolve_repo_root(repo_path: str) -> str:
    p = Path(repo_path).expanduser().resolve()
    if p.is_file():
        p = p.parent
    # .git を上方探索
    cur = p
    for _ in range(10):  # 10階層まで
        if (cur / '.git').exists():
            return str(cur)
        if cur.parent == cur:
            break
        cur = cur.parent
    # 最後に git rev-parse --show-toplevel を試す
    try:
        out = subprocess.run(
            ['git', 'rev-parse', '--show-toplevel'],
            cwd=str(p),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=10
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return str(p)


def _run_git(args: List[str], cwd: str, timeout: int = 20):
    env = os.environ.copy()
    env.setdefault('LC_ALL', 'C.UTF-8')
    env.setdefault('LANG', 'C.UTF-8')
    proc = subprocess.run(
        ['git'] + args,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding='utf-8',
        errors='replace',
        timeout=timeout,
        env=env,
    )
    return proc


def _parse_name_status_z(payload: str) -> List[Dict[str, str]]:
    # パターンA/B の両方を解釈
    entries = payload.split('\x00')
    i = 0
    out = []
    while i < len(entries):
        rec = entries[i]
        if not rec:
            i += 1
            continue
        if '\t' in rec:
            status, rest = rec.split('\t', 1)
            s0 = status[:1]
            if s0 in ('R', 'C'):
                # rename/copy
                if '\t' in rest:
                    old_path, new_path = rest.split('\t', 1)
                else:
                    old_path = rest
                    new_path = entries[i + 1] if i + 1 < len(entries) else ''
                    i += 1
                out.append({'status': s0, 'path': new_path, 'old_path': old_path})
            else:
                out.append({'status': s0, 'path': rest})
            i += 1
        else:
            status = rec
            s0 = status[:1]
            if s0 in ('R', 'C'):
                old_path = entries[i + 1] if i + 1 < len(entries) else ''
                new_path = entries[i + 2] if i + 2 < len(entries) else ''
                out.append({'status': s0, 'path': new_path, 'old_path': old_path})
                i += 3
            else:
                path = entries[i + 1] if i + 1 < len(entries) else ''
                if path:
                    out.append({'status': s0, 'path': path})
                i += 2
    return out


# 追加: doc_path を必ず使うためのヘルパ群
try:
    # 既存 tools.tools の doc_path 解決を流用
    from tools.tools import _resolve_doc_path as _resolve_doc_path  # type: ignore
except Exception:
    # フォールバック: services.project_service を用いて doc_path を解決
    def _resolve_doc_path(project_id: int) -> Path:
        from services.project_service import ProjectService  # 遅延インポートで依存を最小化
        ps = ProjectService()
        proj = ps.fetch_by_id(project_id)
        if not proj or not getattr(proj, "doc_path", None):
            raise ValueError("doc_path_not_set")
        base = Path(proj.doc_path).expanduser().resolve()
        if (not base.exists()) or (not base.is_dir()):
            raise ValueError("invalid_doc_path")
        return base


def _ensure_under_doc_path(p: Path, doc_path: Path) -> Path:
    """p を正規化し、doc_path 配下であることを強制。外なら ValueError。"""
    rp = Path(p).expanduser().resolve()
    try:
        _ = rp.relative_to(doc_path)
    except Exception:
        raise ValueError(f"path must be under doc_path (got: {rp}, doc_path: {doc_path})")
    return rp


def _resolve_repo_root_for_project(project_id: int, repo_path: Optional[str]) -> str:
    """
    必ず doc_path をリポジトリルートとして返す。
    repo_path が与えられた場合も doc_path 配下であることのみ検証する。
    特別扱い: repo_path が None/""/"."/"repo" の場合は doc_path を返す。
    相対パスは doc_path 起点で解決し、絶対パスは doc_path 配下であることを検証する。
    """
    doc_path = Path(_resolve_doc_path(project_id)).expanduser().resolve()
    if not doc_path.exists() or not doc_path.is_dir():
        raise ValueError(f"doc_path not found or not directory: {doc_path}")

    if not repo_path or repo_path in (".", "repo"):
        return str(doc_path)

    p = Path(repo_path)
    if not p.is_absolute():
        p = (doc_path / p)
    _ensure_under_doc_path(p, doc_path)  # 妥当性チェックのみ

    return str(doc_path)


def _sanitize_pathspecs_under_doc_path(doc_path: Path, pathspecs: Optional[List[str]]) -> Optional[List[str]]:
    """
    pathspecs を doc_path 相対 POSIX に正規化。
    絶対/相対いずれも doc_path 配下でないものはエラー。
    """
    if not pathspecs:
        return None
    out: List[str] = []
    for ps in pathspecs:
        p = Path(ps)
        if not p.is_absolute():
            p = (doc_path / p)
        rp = _ensure_under_doc_path(p, doc_path)
        out.append(PurePosixPath(rp.relative_to(doc_path)).as_posix())
    return out


@tool
def git_diff_files(
        repo_path: str,
        base: str,
        head: str,
        detect_renames: bool = True,
        detect_copies: bool = True,
        find_renames_threshold: Optional[int] = None,
        pathspecs: Optional[List[str]] = None,
        timeout_seconds: int = 20,
        project_id: Optional[int] = None,
) -> str:
    """
    任意の base..head の修正ファイル一覧（ステータス付き）を取得します。
    実行は doc_path（project_id で解決）を CWD として行います。
    戻り: JSON 文字列 { ok, base, head, cwd, files:[{status,path,old_path?}], stderr, exit_code }
    """
    if project_id is None:
        return json.dumps({'ok': False, 'error': 'project_id is required'}, ensure_ascii=False)

    cwd = _resolve_repo_root_for_project(project_id, repo_path)
    args = ['diff', '--name-status', '-z']
    if detect_renames:
        if find_renames_threshold is not None:
            args.append(f'-M{int(find_renames_threshold)}')
        else:
            args.append('-M')
    if detect_copies:
        if find_renames_threshold is not None:
            args.append(f'-C{int(find_renames_threshold)}')
        else:
            args.append('-C')
    args.append(f'{base}..{head}')

    doc_path = Path(cwd)
    sps = _sanitize_pathspecs_under_doc_path(doc_path, pathspecs)
    if sps:
        args.append('--')
        args.extend(sps)

    proc = _run_git(args, cwd=cwd, timeout=timeout_seconds)
    out: Dict[str, object] = {
        'ok': proc.returncode == 0,
        'cmd': ['git'] + args,
        'cwd': cwd,
        'base': base,
        'head': head,
        'exit_code': int(proc.returncode),
        'stderr': proc.stderr.strip(),
        'files': []
    }
    if proc.returncode != 0:
        return json.dumps(out, ensure_ascii=False)

    files = _parse_name_status_z(proc.stdout)
    out['files'] = files
    return json.dumps(out, ensure_ascii=False)


@tool
def git_diff_patch(
        repo_path: str,
        base: str,
        head: str,
        path: str,
        context_lines: int = 3,
        detect_renames: bool = True,
        detect_copies: bool = True,
        find_renames_threshold: Optional[int] = None,
        timeout_seconds: int = 20,
        project_id: Optional[int] = None,
) -> str:
    """
    任意の base..head、特定の path に対する差分のパッチテキストを取得します。
    実行は doc_path（project_id で解決）を CWD として行います。
    戻り: JSON { ok, base, head, path, patch_text, is_binary, stderr, exit_code }
    """
    if project_id is None:
        return json.dumps({'ok': False, 'error': 'project_id is required'}, ensure_ascii=False)

    cwd = _resolve_repo_root_for_project(project_id, repo_path)
    args = ['diff', f'-U{int(context_lines)}', '-M' if detect_renames else '', '-C' if detect_copies else '']
    # 空文字は除去
    args = [a for a in args if a]
    if find_renames_threshold is not None:
        if detect_renames:
            args.append(f'-M{int(find_renames_threshold)}')
        if detect_copies:
            args.append(f'-C{int(find_renames_threshold)}')
    args.append(f'{base}..{head}')
    args.extend(['--', path])

    proc = _run_git(args, cwd=cwd, timeout=timeout_seconds)
    out: Dict[str, object] = {
        'ok': proc.returncode == 0,
        'cmd': ['git'] + args,
        'cwd': cwd,
        'base': base,
        'head': head,
        'path': path,
        'exit_code': int(proc.returncode),
        'stderr': proc.stderr.strip(),
        'patch_text': '',
        'is_binary': False,
    }
    if proc.returncode != 0:
        return json.dumps(out, ensure_ascii=False)

    text = proc.stdout
    out['patch_text'] = text
    # 簡易判定: バイナリ扱いの時に 'Binary files ... differ' が含まれる
    if 'Binary files ' in text and ' differ' in text:
        out['is_binary'] = True
    return json.dumps(out, ensure_ascii=False)


@tool
def git_list_branches(repo_path: str, timeout_seconds: int = 10, project_id: Optional[int] = None) -> str:
    """ローカルブランチ一覧を返す。JSON { ok, branches, cmd, cwd }。CWD は doc_path 固定。"""
    if project_id is None:
        return json.dumps({'ok': False, 'error': 'project_id is required'}, ensure_ascii=False)
    cwd = _resolve_repo_root_for_project(project_id, repo_path)
    proc = _run_git(['branch', '--format=%(refname:short)'], cwd=cwd, timeout=timeout_seconds)
    out = {
        'ok': proc.returncode == 0,
        'cmd': ['git', 'branch', '--format=%(refname:short)'],
        'cwd': cwd,
        'branches': [],
        'stderr': proc.stderr.strip(),
        'exit_code': int(proc.returncode)
    }
    if proc.returncode == 0:
        branches = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
        out['branches'] = branches
    return json.dumps(out, ensure_ascii=False)


@tool
def git_current_branch(repo_path: str, timeout_seconds: int = 10, project_id: Optional[int] = None) -> str:
    """現在のブランチ名を返す。JSON { ok, branch }。CWD は doc_path 固定。"""
    if project_id is None:
        return json.dumps({'ok': False, 'error': 'project_id is required'}, ensure_ascii=False)
    cwd = _resolve_repo_root_for_project(project_id, repo_path)
    proc = _run_git(['rev-parse', '--abbrev-ref', 'HEAD'], cwd=cwd, timeout=timeout_seconds)
    out = {
        'ok': proc.returncode == 0,
        'cmd': ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
        'cwd': cwd,
        'branch': proc.stdout.strip(),
        'stderr': proc.stderr.strip(),
        'exit_code': int(proc.returncode)
    }
    return json.dumps(out, ensure_ascii=False)


@tool
def git_log_range(repo_path: str, base: str, head: str, max_count: int = 50, timeout_seconds: int = 20,
                  project_id: Optional[int] = None) -> str:
    """base..head のコミットログ概要を返す。JSON { ok, commits:[{sha,author,date,subject}] }。CWD は doc_path 固定。"""
    if project_id is None:
        return json.dumps({'ok': False, 'error': 'project_id is required'}, ensure_ascii=False)
    cwd = _resolve_repo_root_for_project(project_id, repo_path)
    fmt = '%H%x00%an%x00%ad%x00%s'
    args = ['log', f'--max-count={int(max_count)}', f'--pretty=format:{fmt}', '-z', f'{base}..{head}']
    proc = _run_git(args, cwd=cwd, timeout=timeout_seconds)
    out = {
        'ok': proc.returncode == 0,
        'cmd': ['git'] + args,
        'cwd': cwd,
        'commits': [],
        'stderr': proc.stderr.strip(),
        'exit_code': int(proc.returncode)
    }
    if proc.returncode == 0:
        toks = proc.stdout.split('\x00')
        # 4トークン単位
        commits = []
        for i in range(0, len(toks) - 3, 4):
            sha, author, date, subject = toks[i:i + 4]
            if not sha:
                continue
            commits.append({'sha': sha, 'author': author, 'date': date, 'subject': subject})
        out['commits'] = commits
    return json.dumps(out, ensure_ascii=False)


@tool
def git_show_file(repo_path: str, rev: str, path: str, timeout_seconds: int = 15,
                  project_id: Optional[int] = None) -> str:
    """指定コミットのファイル内容を取得。JSON { ok, content, is_binary }。CWD は doc_path 固定。"""
    if project_id is None:
        return json.dumps({'ok': False, 'error': 'project_id is required'}, ensure_ascii=False)
    cwd = _resolve_repo_root_for_project(project_id, repo_path)
    args = ['show', f'{rev}:{path}']
    proc = _run_git(args, cwd=cwd, timeout=timeout_seconds)
    out = {
        'ok': proc.returncode == 0,
        'cmd': ['git'] + args,
        'cwd': cwd,
        'rev': rev,
        'path': path,
        'content': '',
        'is_binary': False,
        'stderr': proc.stderr.strip(),
        'exit_code': int(proc.returncode)
    }
    if proc.returncode != 0:
        return json.dumps(out, ensure_ascii=False)
    text = proc.stdout
    out['content'] = text
    # バイナリっぽい簡易判定
    if '\x00' in text:
        out['is_binary'] = True
    return json.dumps(out, ensure_ascii=False)


@tool
def git_status_porcelain(repo_path: str, timeout_seconds: int = 10, project_id: Optional[int] = None) -> str:
    """ワークツリーの変更一覧（porcelain v1 -z）。JSON { ok, entries:[{xy,path,orig_path?}] }。CWD は doc_path 固定。"""
    if project_id is None:
        return json.dumps({'ok': False, 'error': 'project_id is required'}, ensure_ascii=False)
    cwd = _resolve_repo_root_for_project(project_id, repo_path)
    proc = _run_git(['status', '--porcelain', '-z'], cwd=cwd, timeout=timeout_seconds)
    out = {
        'ok': proc.returncode == 0,
        'cmd': ['git', 'status', '--porcelain', '-z'],
        'cwd': cwd,
        'entries': [],
        'stderr': proc.stderr.strip(),
        'exit_code': int(proc.returncode)
    }
    if proc.returncode != 0:
        return json.dumps(out, ensure_ascii=False)
    toks = proc.stdout.split('\x00')
    entries = []
    for t in toks:
        if not t:
            continue
        # 先頭2文字が XY、残りがパス（rename は "R <sp> old -> new" 簡易処理）
        if len(t) > 3 and t[2] == ' ':
            xy = t[:2]
            rest = t[3:]
            if ' -> ' in rest:
                old, new = rest.split(' -> ', 1)
                entries.append({'xy': xy, 'path': new, 'orig_path': old})
            else:
                entries.append({'xy': xy, 'path': rest})
    out['entries'] = entries
    return json.dumps(out, ensure_ascii=False)


@tool
def git_rev_parse(repo_path: str, rev: str, timeout_seconds: int = 10, project_id: Optional[int] = None) -> str:
    """rev を SHA に解決。JSON { ok, sha }。CWD は doc_path 固定。"""
    if project_id is None:
        return json.dumps({'ok': False, 'error': 'project_id is required'}, ensure_ascii=False)
    cwd = _resolve_repo_root_for_project(project_id, repo_path)
    proc = _run_git(['rev-parse', rev], cwd=cwd, timeout=timeout_seconds)
    out = {
        'ok': proc.returncode == 0,
        'cmd': ['git', 'rev-parse', rev],
        'cwd': cwd,
        'rev': rev,
        'sha': proc.stdout.strip(),
        'stderr': proc.stderr.strip(),
        'exit_code': int(proc.returncode)
    }
    return json.dumps(out, ensure_ascii=False)


@tool
def git_repo_root(repo_path: str, timeout_seconds: int = 10, project_id: Optional[int] = None) -> str:
    """repo_path から見つけたリポジトリルートを返す。JSON { ok, repo_root }。doc_path を返却。"""
    if project_id is None:
        return json.dumps({'ok': False, 'error': 'project_id is required'}, ensure_ascii=False)
    cwd = _resolve_repo_root_for_project(project_id, repo_path)
    out = {'ok': True, 'repo_root': cwd}
    return json.dumps(out, ensure_ascii=False)


@tool
def git_diff_own_changes_files(
        repo_path: str,
        base_ref: str = 'origin/develop',
        head_ref: str = 'HEAD',
        pathspecs: Optional[List[str]] = None,
        detect_renames: bool = True,
        detect_copies: bool = True,
        find_renames_threshold: Optional[int] = None,
        timeout_seconds: int = 20,
        project_id: Optional[int] = None,
) -> str:
    """
    自ブランチ(=HEAD)で“自分が加えた変更だけ”のファイル一覧を返します。
    実質コマンド: git diff --name-only $(git merge-base <base_ref> <head_ref>)..<head_ref>
    実行は doc_path（project_id で解決）を CWD として行います。
    戻り: JSON 文字列 { ok, cmd, cwd, base_ref, head_ref, merge_base, files:[...], stderr, exit_code }
    """
    if project_id is None:
        return json.dumps({'ok': False, 'error': 'project_id is required'}, ensure_ascii=False)

    cwd = _resolve_repo_root_for_project(project_id, repo_path)

    # merge-base を解決
    mb_proc = _run_git(['merge-base', base_ref, head_ref], cwd=cwd, timeout=timeout_seconds)
    out: Dict[str, object] = {
        'ok': False,
        'cmd': None,
        'cwd': cwd,
        'base_ref': base_ref,
        'head_ref': head_ref,
        'merge_base': '',
        'files': [],
        'stderr': mb_proc.stderr.strip(),
        'exit_code': int(mb_proc.returncode),
    }
    if mb_proc.returncode != 0:
        return json.dumps(out, ensure_ascii=False)
    merge_base = mb_proc.stdout.strip()
    out['merge_base'] = merge_base

    # 実際の diff --name-only
    args = ['diff', '--name-only', '-z']
    if detect_renames:
        if find_renames_threshold is not None:
            args.append(f'-M{int(find_renames_threshold)}')
        else:
            args.append('-M')
    if detect_copies:
        if find_renames_threshold is not None:
            args.append(f'-C{int(find_renames_threshold)}')
        else:
            args.append('-C')
    args.append(f'{merge_base}..{head_ref}')

    doc_path = Path(cwd)
    sps = _sanitize_pathspecs_under_doc_path(doc_path, pathspecs)
    if sps:
        args.append('--')
        args.extend(sps)

    proc = _run_git(args, cwd=cwd, timeout=timeout_seconds)
    out['cmd'] = ['git'] + args
    out['stderr'] = proc.stderr.strip()
    out['exit_code'] = int(proc.returncode)
    if proc.returncode != 0:
        return json.dumps(out, ensure_ascii=False)

    files = [t for t in proc.stdout.split('\x00') if t]
    out['files'] = files
    out['ok'] = True
    return json.dumps(out, ensure_ascii=False)
