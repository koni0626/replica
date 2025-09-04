from flask import Blueprint, render_template, request, redirect, url_for, flash, Response, stream_with_context, jsonify, send_file, abort
from flask_login import current_user
from forms.doc_form import DocForm
from services.doc_service import DocService
from services.gpt_provider import GptProvider
from pathlib import Path
import os
import re

docs_bp = Blueprint("docs", __name__)

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

@docs_bp.route("/<int:project_id>", methods=["GET", "POST"])
def index(project_id: int):
    form = DocForm()
    svc = DocService()

    total = svc.count_by_project(project_id)

    left_pos  = max(request.args.get("left_pos",  0, type=int) or 0, 0)
    right_pos = max(request.args.get("right_pos", 0, type=int) or 0, 0)
    if total > 0:
        left_pos  = min(left_pos,  total - 1)
        right_pos = min(right_pos, total - 1)

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
            return redirect(url_for("docs.index", project_id=project_id, left_pos=0, right_pos=1))

    current_left  = svc.nth_by_project(project_id, left_pos)
    current_right = svc.nth_by_project(project_id, right_pos)
    left_has_prev  = (left_pos + 1)  < total
    left_has_next  = left_pos > 0
    right_has_prev = (right_pos + 1) < total
    right_has_next = right_pos > 0

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

@docs_bp.route("/<int:project_id>/stream", methods=["POST"])
def stream_generate(project_id: int):
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

@docs_bp.route("/<int:project_id>/delete/<int:memo_id>", methods=["POST"])
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

# --- 追加: コード生成(API) ---
@docs_bp.route("/<int:project_id>/codegen", methods=["POST"])
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
def download_generated():
    path = request.args.get("path", "")
    if not path:
        abort(400)
    p = _safe_file_within_allowed_roots(path)
    return send_file(p, as_attachment=True, download_name=p.name)
