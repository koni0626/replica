from flask import Blueprint, render_template, request, redirect, url_for, flash, Response, stream_with_context, jsonify, send_file, abort
from flask_login import login_required, current_user
from forms.doc_form import DocForm
from services.doc_service import DocService
from services.gpt_provider import GptProvider
from services.diff_service import DiffService
from services.extract_service import ExtractService
from pathlib import Path
import os
import re
import uuid
from werkzeug.utils import secure_filename

from flask import current_app
from services.project_service import ProjectService, ALLOWED_THEMES


docs_bp = Blueprint("docs", __name__)

from services.search_path_service import SearchPathService

# ==========================
# 生成ZIPの安全ダウンロード用（既存）
# ==========================

def _allowed_roots() -> list[Path]:
    roots: list[Path] = []

    # 環境変数の明示指定
    env = os.getenv("APP_GENERATED_DIR") or os.getenv("GENERATED_BASE_DIR")
    if env:
        try:
            roots.append(Path(env).expanduser().resolve())
        except Exception:
            pass

    # 代表的な候補（存在するもののみ）
    for cand in [
        Path("/mnt/data/generated"),
        Path.home() / "GeneratedArtifacts",
        Path.cwd() / "generated",
        Path(os.getenv("TMP", os.getenv("TEMP", "/tmp"))) / "generated",
    ]:
        try:
            p = cand.expanduser().resolve()
            if p.exists():
                roots.append(p)
        except Exception:
            continue

    # 重複除去
    uniq = []
    seen = set()
    for r in roots:
        s = str(r)
        if s not in seen:
            seen.add(s)
            uniq.append(r)
    return uniq


def _safe_file_within_allowed_roots(abs_path: str | Path) -> Path:
    p = Path(abs_path).resolve()
    roots = _allowed_roots()
    if not p.is_file():
        abort(404)
    if not any(root in p.parents for root in roots):
        abort(400, description="invalid path")
    return p
# メディア（添付ファイル）関連
# ==========================

def _media_dir(project_id: int, user_id: int) -> Path:
    """media/<user_id>/<project_id> を返す（無ければ作成）。"""
    base = Path.cwd() / "media" / str(user_id) / str(project_id)
    base.mkdir(parents=True, exist_ok=True)
    return base


def _safe_media_file(p: str | Path, project_id: int, user_id: int) -> Path:
    """クライアントから渡されたパスが media/<user>/<project> 配下かを確認する。"""
    path = Path(p).resolve()
    root = _media_dir(project_id, user_id)
    if not path.is_file():
        abort(404)
    # ルートディレクトリに含まれているか
    if root not in path.parents:
        abort(400, description="invalid media path")
    return path


def _ext_ok(filename: str) -> bool:
    """拡張子チェック（元のファイル名から判定）"""
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if not ext:
        return False
    allowed = set(current_app.config.get("ALLOWED_UPLOAD_EXTS", []))
    return ext in allowed


@docs_bp.route("/<int:project_id>", methods=["GET", "POST"])
@login_required
def index(project_id: int):
    form = DocForm()
    svc = DocService()

    # 追加: プロジェクト名を取得
    pj = ProjectService().fetch_by_id(project_id)
    project_name = pj.project_name if pj else f"Project #{project_id}"
    theme_class = getattr(pj, 'theme', 'theme-sky') if pj else 'theme-sky'
    if theme_class not in ALLOWED_THEMES:
        theme_class = 'theme-sky'

    total = svc.count_by_project(project_id)

    pos = max(request.args.get("pos", 0, type=int) or 0, 0)
    if total > 0:
        pos = min(pos, total - 1)

    if form.validate_on_submit() and form.submit_commit.data:
        content = (form.generated_content.data or "").strip()
        if not content:
            flash("生成結果が空です。先に『生成』してください。", "warning")
        else:
            svc.commit(
                project_id=project_id,
                user_id=current_user.user_id,
                prompt=form.prompt.data,
                content=content,
            )
            flash("Docsを保存しました。", "success")
            return redirect(url_for("docs.index", project_id=project_id, pos=0))

    current_commit = svc.nth_by_project(project_id, pos)
    has_prev = (pos + 1) < total
    has_next = pos > 0

    return render_template(
        "docs/index.html",
        form=form,
        project_id=project_id,
        project_name=project_name,
        current_left=current_commit,
        current_right=current_commit,
        pos=pos,
        has_prev=has_prev,
        has_next=has_next,
        total=total,
        theme_class=theme_class,
    )

@docs_bp.route("/save_note/<int:doc_id>", methods=["POST"])
@login_required
def save_note(doc_id):
    data = request.get_json()
    note = data.get('note')
    svc = DocService()
    if svc.save_note(doc_id, note):
        return jsonify({'success': True})
    return jsonify({'success': False}), 400


@docs_bp.route("/upload/<int:project_id>", methods=["POST"])
@login_required
def upload(project_id: int):
    """
    必須拡張子＋Office/PDFを対象とした、ファイルアップロードAPI。
    - 保存先: ./media/<user_id>/<project_id>/<uuid>_<secure_stem>.<ext>
    - 返却: { ok, files: [{name, size, ext, text_preview, stored_path}] }
      stored_path はクライアントに返すが、サーバ受信時に必ず media ルート配下か検証する。
    - プレビューは ExtractService で最大500文字を抽出（失敗時はUTF-8テキスト読みにフォールバック）。
    """
    if 'files' not in request.files:
        return jsonify({"ok": False, "error": "no files"}), 400

    results = []
    media_dir = _media_dir(project_id, current_user.user_id)

    for fs in request.files.getlist('files'):
        orig_name = fs.filename or ''
        if not orig_name:
            continue
        # 判定は元のファイル名で行う（日本語名などでも正しく拡張子判定）
        if not _ext_ok(orig_name):
            results.append({"name": orig_name, "ok": False, "error": "unsupported_extension"})
            continue

        # サイズチェック
        fs.stream.seek(0, os.SEEK_END)
        size = fs.stream.tell()
        fs.stream.seek(0)
        max_bytes = int(current_app.config.get("MAX_UPLOAD_BYTES", 10 * 1024 * 1024))
        if size > max_bytes:
            results.append({"name": orig_name, "ok": False, "error": "too_large"})
            continue

        # 保存名を安全に生成（拡張子は元のものを保持）
        ext_raw = orig_name.rsplit('.', 1)[-1].lower() if '.' in orig_name else ''
        secure_stem = secure_filename(Path(orig_name).stem) or 'file'
        uid = uuid.uuid4().hex[:8]
        save_name = f"{uid}_{secure_stem}.{ext_raw}" if ext_raw else f"{uid}_{secure_stem}"
        abs_path = media_dir / save_name
        fs.save(abs_path)

        # プレビュー用の抽出（拡張子に応じてテキスト抽出）
        try:
            preview = ExtractService.extract_text(abs_path, ext=ext_raw, limit=500)
            if not preview:
                # フォールバック: プレーンテキストで最大500文字
                preview = abs_path.read_text(encoding='utf-8', errors='ignore')[:500]
        except Exception:
            preview = ''

        results.append({
            "ok": True,
            "name": orig_name,
            "size": size,
            "ext": ext_raw,
            "text_preview": preview,
            "stored_path": str(abs_path),
        })

    return jsonify({"ok": True, "files": results})


def _build_attachments_text(project_id: int, user_id: int, paths: list[str], per_file_limit: int = 100_000) -> str:
    """
    添付された media 内のファイルを安全に読み、LLMへ渡すテキストを構築する。
    - 各ファイル先頭 per_file_limit 文字まで（ExtractServiceでテキスト抽出）
    - 章区切りとして "---" とファイル名ラベルを付与
    """
    if not paths:
        return ""
    parts: list[str] = []
    for p in paths:
        try:
            abs_path = _safe_media_file(p, project_id, user_id)
            name = abs_path.name
            ext = abs_path.suffix.lower().lstrip('.')
            snippet = ExtractService.extract_text(abs_path, ext=ext, limit=per_file_limit)
            parts.append(f"\n\n---\n[添付ファイル:{name}]\n{snippet}")
        except Exception:
            # 読み取りに失敗したファイルはスキップ
            continue
    return "".join(parts)


@docs_bp.route("/<int:project_id>/delete/<int:memo_id>", methods=["POST"])
@login_required
def delete_history(project_id: int, memo_id: int):
    svc = DocService()
    svc.delete_history(project_id, memo_id)

    left_pos  = max(request.args.get("left_pos", 0, type=int) or 0, 0)
    right_pos = max(request.args.get("right_pos", 1, type=int) or 0, 0)

    total = svc.count_by_project(project_id)
    if total > 0:
        left_pos  = min(left_pos,  total - 1)
        right_pos = min(right_pos, total - 1)

    current_left  = svc.nth_by_project(project_id, left_pos)
    current_right = svc.nth_by_project(project_id, right_pos)
    left_has_prev  = (left_pos + 1)  < total
    left_has_next  = left_pos > 0
    right_has_prev = (right_pos + 1) < total
    right_has_next = right_pos > 0

    form = DocForm()
    return render_template(
        "docs/index.html",
        form=form,
        project_id=project_id,
        current_left=current_left,
        current_right=current_right,
        left_pos=left_pos, right_pos=right_pos,
        left_has_prev=left_has_prev, left_has_next=left_has_next,
        right_has_prev=right_has_prev, right_has_next=right_has_next,
        total=total,
    )


@docs_bp.route("/<int:project_id>/stream_tool", methods=["POST"])
@login_required
def stream_generate_tool(project_id: int):
    data = request.get_json(silent=True) or {}
    prompt = (data.get("prompt") or "").strip()
    attachments = data.get("attachments") or []
    if not prompt:
        return jsonify({"error": "prompt is required"}), 400

    # 添付（media配下のファイル）を読み込み、プロンプトにサーバ側で追記
    att_text = _build_attachments_text(project_id, current_user.user_id, attachments)
    final_prompt = prompt + att_text

    provider = GptProvider()
    svc = DocService()

    def generate():
        yield ""
        for piece in provider.stream_with_history_and_tool(
            project_id=project_id,
            prompt=final_prompt,
            svc=svc,
            history_limit=20,
        ):
            yield piece

    return Response(stream_with_context(generate()), mimetype="text/plain; charset=utf-8")

@docs_bp.route("/<int:project_id>/diff/latest", methods=["GET"])
@login_required
def latest_diff(project_id: int):
    """
    Git 管理の doc_path から差分（git diff）を取得して返す。
    未設定/不正/非Git の場合はエラーJSON（カレントへのフォールバックはしない）。
    クエリ ?staged=1 でステージ済み差分。
    """
    from services.project_service import ProjectService
    ps = ProjectService()
    proj = ps.fetch_by_id(project_id)
    if not proj or not getattr(proj, 'doc_path', None):
        return jsonify({"ok": False, "error": "doc_path_not_set", "message": "このプロジェクトのdoc_pathが設定されていません。プロジェクト詳細で設定してください。"}), 400

    base_dir = Path(proj.doc_path).expanduser().resolve()
    if (not base_dir.exists()) or (not base_dir.is_dir()):
        return jsonify({"ok": False, "error": "invalid_doc_path", "message": "doc_pathが存在しないかディレクトリではありません。プロジェクト詳細で正しいパスを設定してください。"}), 400

    staged = (request.args.get("staged", "0") == "1")

    svc = DiffService(base_dir=base_dir)
    try:
        files = svc.latest_git_diffs(staged=staged, include_untracked=True, max_files=100)
    except ValueError as e:
        err = str(e)
        if err == 'not_a_git_repo':
            return jsonify({"ok": False, "error": err, "message": ".git が見つかりません。doc_path は Git リポジトリである必要があります。"}), 400
        return jsonify({"ok": False, "error": err, "message": "git diff の取得に失敗しました。"}), 400

    payload = {
        "ok": True,
        "project_id": project_id,
        "files": [
            {
                "path": f.path,
                "status": f.status,
                "patch": f.patch,
                "size": f.size,
                "truncated": f.truncated,
            }
            for f in files
        ],
    }
    return jsonify(payload)

@docs_bp.route("/<int:project_id>/search_paths", methods=["GET"])
@login_required
def search_paths(project_id: int):
    pj = ProjectService().fetch_by_id(project_id)
    if not pj or not getattr(pj, 'doc_path', None):
        flash("このプロジェクトのdoc_pathが設定されていません。プロジェクト詳細で設定してください。", "warning")
        return redirect(url_for('docs.index', project_id=project_id))
    theme_class = getattr(pj, 'theme', 'theme-sky')
    if theme_class not in ALLOWED_THEMES:
        theme_class = 'theme-sky'
    return render_template('docs/search_paths.html', project_id=project_id, doc_path=pj.doc_path, theme_class=theme_class)


@docs_bp.route("/<int:project_id>/search_tree", methods=["GET"])
@login_required
def search_tree(project_id: int):
    try:
        rel = (request.args.get('rel') or '').strip()
        tree = SearchPathService().build_tree(project_id, rel=rel)
        return jsonify(tree)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@docs_bp.route("/<int:project_id>/search_paths_state", methods=["GET", "POST"])
@login_required
def search_paths_state(project_id: int):
    if request.method == "GET":
        state = SearchPathService().load_state(project_id)
        return jsonify(state)

    data = request.get_json(silent=True) or {}
    includes = data.get('includes') or []
    excludes = data.get('excludes') or []
    # 正規化（文字列配列のみ許可）
    inc = [str(x).strip().replace('\\\\', '/').strip('/') for x in includes if isinstance(x, (str,))]
    exc = [str(x).strip().replace('\\\\', '/').strip('/') for x in excludes if isinstance(x, (str,))]
    saved = SearchPathService().save_state(project_id, inc, exc)
    return jsonify({"ok": True, **saved})
