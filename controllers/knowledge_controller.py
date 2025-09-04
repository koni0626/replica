import json
from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required
from flask_login import current_user
from forms.knowledge_form import KnowledgeForm
from services.knowledge_service import KnowledgeService


knowledge_bp = Blueprint('knowledge', __name__)


@knowledge_bp.route('/<int:project_id>')
@login_required
def index(project_id):
    knowledge = KnowledgeService.get_all_by_project(project_id)
    # 事前のJSONダンプは行わず、そのままテンプレートに渡す（テンプレート側で |tojson を使う）
    return render_template('knowledge/index.html', knowledge=knowledge, project_id=project_id)


@knowledge_bp.route('/create/<int:project_id>', methods=['GET', 'POST'])
@login_required
def create(project_id):
    form = KnowledgeForm()
    if form.validate_on_submit():
        KnowledgeService.create(form, project_id, current_user.user_id)
        flash('ナレッジを追加しました', 'success')
        return redirect(url_for('knowledge.index', project_id=project_id))
    return render_template('knowledge/create.html', form=form, project_id=project_id)


@knowledge_bp.route('/edit/<int:project_id>/<int:knowledge_id>', methods=['GET', 'POST'])
@login_required
def edit(project_id, knowledge_id):
    knowledge = KnowledgeService.get(knowledge_id)
    form = KnowledgeForm(obj=knowledge)
    if form.validate_on_submit():
        KnowledgeService.update(knowledge, form)
        flash('ナレッジを更新しました', 'success')
        return redirect(url_for('knowledge.index', project_id=project_id))
    return render_template('knowledge/edit.html', form=form, project_id=project_id, knowledge_id=knowledge_id)


@knowledge_bp.route('/delete/<int:knowledge_id>', methods=['POST'])
@login_required
def delete(knowledge_id):
    knowledge = KnowledgeService.get(knowledge_id)
    KnowledgeService.delete(knowledge)
    flash('ナレッジを削除しました', 'info')

    # URLのクエリ（?project_id=...）またはオブジェクトから project_id を取得
    project_id = request.args.get('project_id', type=int)
    if not project_id and hasattr(knowledge, 'project_id'):
        project_id = getattr(knowledge, 'project_id')

    return redirect(url_for('knowledge.index', project_id=project_id))
