from flask import Blueprint, render_template, request, redirect, url_for, flash, Response, stream_with_context, jsonify, send_file, abort
from flask_login import login_required, current_user
from forms.doc_form import DocForm
from services.doc_service import DocService
from services.gpt_provider import GptProvider
from services.diff_service import DiffService
from pathlib import Path
import os
import re
import uuid
from werkzeug.utils import secure_filename

from flask import current_app


docs_bp = Blueprint("docs", __name__)

ALLOWED_UPLOAD_EXTS = {
    # 必須セット（フェーズ1）
    "txt", "md", "markdown", "pdf", "docx", "pptx", "xlsx",
    "csv", "json", "yaml", "yml", "html", "htm",
    # コード系（テキストとして取り扱い）
    "py", "js", "ts", "java", "php", "go", "rb", "cs", "sh", "sql", "css"
}

MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5MB/ファイル（必要に応じて調整）


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


def _ext_ok(filename: str) -> bool:
    """拡張子チェック（元のファイル名から判定）"""
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    return ext in ALLOWED_UPLOAD_EXTS


def _ensure_upload_dir(project_id: int, user_id: int) -> Path:
    base = Path.cwd() / "uploads" / str(user_id) / str(project_id)
    base.mkdir(parents=True, exist_ok=True)
    return base


@docs_bp.route("/<int:project_id>", methods=["GET", "POST"])
@login_required
def index(project_id: int):
    form = DocForm()
    svc = DocService()

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
        current_left=current_commit,
        current_right=current_commit,
        pos=pos,
        has_prev=has_prev,
        has_next=has_next,
        total=total,
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
    必須拡張子のみを対象とした、簡易ファイルアップロードAPI。
    - 保存先: ./uploads/<user_id>/<project_id>/<uuid>_<secure_stem>.<ext>
    - 返却: { ok, files: [{name, size, ext, text_preview, stored_path}] }
      stored_path は将来の再利用用（このAPIでは使わない）。
    - 本APIは「抽出」までは行わず、まずはテキストとして読み取りを試みる実装（必須範囲）。
    - 将来: PDF/Officeは専用抽出（extract_service）に切替。
    """
    if 'files' not in request.files:
        return jsonify({"ok": False, "error": "no files"}), 400

    results = []
    upload_dir = _ensure_upload_dir(project_id, current_user.user_id)

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
        if size > MAX_UPLOAD_BYTES:
            results.append({"name": orig_name, "ok": False, "error": "too_large"})
            continue

        # 保存名を安全に生成（拡張子は元のものを保持）
        ext_raw = orig_name.rsplit('.', 1)[-1].lower() if '.' in orig_name else ''
        secure_stem = secure_filename(Path(orig_name).stem) or 'file'
        uid = uuid.uuid4().hex[:8]
        save_name = f"{uid}_{secure_stem}.{ext_raw}" if ext_raw else f"{uid}_{secure_stem}"
        abs_path = upload_dir / save_name
        fs.save(abs_path)

        # 簡易テキスト化: UTF-8優先、失敗時はignore
        text_content = ''
        try:
            with open(abs_path, 'r', encoding='utf-8', errors='ignore') as f:
                text_content = f.read()
        except Exception:
            text_content = ''

        preview = text_content[:500]
        results.append({
            "ok": True,
            "name": orig_name,
            "size": size,
            "ext": ext_raw,
            "text_preview": preview,
            "stored_path": str(abs_path),
        })

    return jsonify({"ok": True, "files": results})


@docs_bp.route("/<int:project_id>/stream", methods=["POST"])
@login_required
def stream_generate(project_id: int):
    print("stream generate")
    data = request.get_json(silent=True) or {}
    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"error": "prompt is required"}), 400

    provider = GptProvider()
    svc = DocService()

    def generate():
        yield ""
        for piece in provider.stream_with_history(project_id=project_id, prompt=prompt, svc=svc, history_limit=20):
            yield piece

    return Response(stream_with_context(generate()), mimetype="text/plain; charset=utf-8")

# --- 追加: コード生成(API) ---
@docs_bp.route("/<int:project_id>/codegen", methods=["POST"])
@login_required
def codegen(project_id: int):
    data = request.get_json(silent=True) or {}
    spec = (data.get("spec_markdown") or "").strip()
    project_name = (data.get("project_name") or "generated_project").strip()
    if not spec:
        return jsonify({"ok": False, "error": "spec_markdown is required"}), 400

    provider = GptProvider()
    svc = DocService()

    result = provider.generate_project_with_tools(
        project_id=project_id,
        spec_markdown=spec,
        svc=svc,
        project_name=project_name,
        create_zip=True,
        history_limit=50,
    )

    summary = result.content or ""
    zip_path = None
    m = re.search(r"zip_path=([^\s]+)", summary)
    if m:
        zip_path = m.group(1)

    if not zip_path:
        return jsonify({"ok": True, "zip_url": None, "summary": summary})

    try:
        p = _safe_file_within_allowed_roots(zip_path)
    except Exception:
        return jsonify({"ok": True, "zip_url": None, "summary": summary})

    dl_url = url_for("docs.download_generated", path=str(p), _external=False)
    return jsonify({"ok": True, "zip_url": dl_url, "summary": summary})

# --- 追加: 生成ZIPの安全ダウンロード ---
@docs_bp.route("/download/generated")
@login_required
def download_generated():
    path = request.args.get("path", "")
    if not path:
        abort(400)
    p = _safe_file_within_allowed_roots(path)
    return send_file(p, as_attachment=True, download_name=p.name)



@docs_bp.route("/<int:project_id>/delete/<int:memo_id>", methods=["POST"])
@login_required
def delete_memo(project_id: int, memo_id: int):
    svc = DocService()
    svc.delete_memo(project_id, memo_id)

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

# docs_controller.py
@docs_bp.route("/<int:project_id>/stream_tool", methods=["POST"])
@login_required
def stream_generate_tool(project_id: int):
    print("stream_generate_tool")
    data = request.get_json(silent=True) or {}
    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"error": "prompt is required"}), 400

    provider = GptProvider()
    svc = DocService()

    def generate():
        yield ""
        for piece in provider.stream_with_history_and_tool(
            project_id=project_id,
            prompt=prompt,
            svc=svc,
            history_limit=20,
        ):
            yield piece

    return Response(stream_with_context(generate()), mimetype="text/plain; charset=utf-8")


@docs_bp.route("/<int:project_id>/diff/latest", methods=["GET"])
@login_required
def latest_diff(project_id: int):
    """
    直近のバックアップと現行ファイルの差分一覧をJSONで返す簡易API。
    現状は base_dir をプロジェクトカレント配下に固定し、全体から探索する。
    将来的にはプロジェクトのルートを記録してそこに限定する。
    """
    # 探索ベース: カレント配下
    base_dir = Path.cwd()
    svc = DiffService(base_dir=base_dir)
    files = svc.latest_diffs(limit_files=100)
    payload = {
        "project_id": project_id,
        "commit_id": None,
        "generated_at": None,
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
