import json
from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required
from flask_login import current_user
from forms.knowledge_form import KnowledgeForm
from services.knowledge_service import KnowledgeService


knowledge_bp = Blueprint('knowledge', __name__)

from services.project_service import ProjectService

@knowledge_bp.route('/api/create_from_prompt', methods=['POST'])
@login_required
def api_create_from_prompt():
    data = request.get_json(silent=True) or {}
    title = (data.get('title') or '').strip()
    content = (data.get('content') or '').strip()
    project_id = data.get('project_id')
    if not (project_id and title and content):
        return {"ok": False, "message": "project_id, title, content は必須です"}, 400
    try:
        k = KnowledgeService.create_from_plain(project_id=project_id, user_id=current_user.user_id,
                                              title=title, content=content,
                                              category=data.get('category'), active=True, order=0)
        return {"ok": True, "knowledge_id": k.knowledge_id}
    except Exception as e:
        return {"ok": False, "message": str(e)}, 500


@knowledge_bp.route('/<int:project_id>')
@login_required
def index(project_id):
    knowledge = KnowledgeService.get_all_by_project(project_id)
    pj = ProjectService().fetch_by_id(project_id)
    project_name = pj.project_name if pj else f"Project #{project_id}"
    # 事前のJSONダンプは行わず、そのままテンプレートに渡す（テンプレート側で |tojson を使う）
    return render_template('knowledge/index.html', knowledge=knowledge, project_id=project_id, project_name=project_name)


@knowledge_bp.route('/create/<int:project_id>', methods=['GET', 'POST'])
@login_required
def create(project_id):
    form = KnowledgeForm()
    pj = ProjectService().fetch_by_id(project_id)
    project_name = pj.project_name if pj else f"Project #{project_id}"
    if form.validate_on_submit():
        KnowledgeService.create(form, project_id, current_user.user_id)
        flash('ナレッジを追加しました', 'success')
        return redirect(url_for('knowledge.index', project_id=project_id))
    return render_template('knowledge/create.html', form=form, project_id=project_id, project_name=project_name)


@knowledge_bp.route('/edit/<int:project_id>/<int:knowledge_id>', methods=['GET', 'POST'])
@login_required
def edit(project_id, knowledge_id):
    knowledge = KnowledgeService.get(knowledge_id)
    form = KnowledgeForm(obj=knowledge)
    pj = ProjectService().fetch_by_id(project_id)
    project_name = pj.project_name if pj else f"Project #{project_id}"
    if form.validate_on_submit():
        KnowledgeService.update(knowledge, form)
        flash('ナレッジを更新しました', 'success')
        return redirect(url_for('knowledge.index', project_id=project_id))
    return render_template('knowledge/edit.html', form=form, project_id=project_id, knowledge_id=knowledge_id, project_name=project_name)


@knowledge_bp.route('/delete/<int:project_id>/<int:knowledge_id>', methods=['POST'])
@login_required
def delete(project_id, knowledge_id):
    knowledge = KnowledgeService.get(knowledge_id)
    KnowledgeService.delete(knowledge)
    flash('ナレッジを削除しました', 'info')

    return redirect(url_for('knowledge.index', project_id=project_id))
