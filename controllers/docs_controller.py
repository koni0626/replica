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


docs_bp = Blueprint("docs", __name__)

ALLOWED_UPLOAD_EXTS = {
    # 必須セット（フェーズ1）
    "txt", "md", "markdown",
    "csv", "json", "yaml", "yml", "html", "htm", "docx", "pptx", "xlsx", "pdf",
    # コード系（テキストとして取り扱い）
    "py", "js", "ts", "java", "php", "go", "rb", "cs", "sh", "sql", "css"
}

MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5MB/ファイル（必要に応じて調整）


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


# ==========================
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
    return ext in ALLOWED_UPLOAD_EXTS


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
        if size > MAX_UPLOAD_BYTES:
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


@docs_bp.route("/<int:project_id>/stream", methods=["POST"])
@login_required
def stream_generate(project_id: int):
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
        for piece in provider.stream_with_history(project_id=project_id, prompt=final_prompt, svc=svc, history_limit=20):
            yield piece

    return Response(stream_with_context(generate()), mimetype="text/plain; charset=utf-8")


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
    直近のバックアップと現行ファイルの差分一覧をJSONで返す簡易API。
    現状は base_dir をプロジェクトカレント配下に固定し、全体から探索する。
    将来的にはプロジェクトのルートを記録してそこに限定する。
    """
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
