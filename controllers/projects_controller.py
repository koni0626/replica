# project_controller.py
from flask import Blueprint, render_template, redirect, url_for, flash, request
from services.project_service import ProjectService
from forms.project_form import ProjectRegisterForm
from flask_login import login_required

project_bp = Blueprint("projects", __name__)

@project_bp.route("/", methods=["GET"])
@login_required
def index():
    pj_sv = ProjectService()
    projects = pj_sv.fetch_all_projects()
    return render_template("projects/index.html", projects=projects)

# 新規作成
@project_bp.route("/new", methods=["GET", "POST"])
@login_required
def create():
    form = ProjectRegisterForm()
    if form.validate_on_submit():
        pj_sv = ProjectService()
        pj_sv.create_project(
            project_name=form.project_name.data,
            description=form.description.data,
            doc_path=form.doc_path.data,
        )
        flash("プロジェクトを登録しました。", "success")
        return redirect(url_for("projects.index"))
    return render_template("projects/form.html", form=form, mode="create")

# 編集
@project_bp.route("/<int:project_id>/edit", methods=["GET", "POST"])
@login_required
def edit(project_id: int):
    pj_sv = ProjectService()
    project = pj_sv.fetch_by_id(project_id)
    if not project:
        flash("対象のプロジェクトが見つかりません。", "warning")
        return redirect(url_for("projects.index"))

    form = ProjectRegisterForm(obj=project)

    if form.validate_on_submit():
        # ZIPがアップロードされているか確認
        file = request.files.get("doc_zip")
        extracted_path = None
        if file and file.filename:
            try:
                extracted_path = pj_sv.save_and_extract_doc_zip(file, project_id)
            except Exception as e:
                flash(f"ZIPの処理に失敗しました: {e}", "danger")
                return render_template("projects/form.html", form=form, mode="edit", project=project)

        pj_sv.update_project(
            project_id=project.project_id,
            project_name=form.project_name.data,
            description=form.description.data,
            doc_path=extracted_path if extracted_path is not None else form.doc_path.data,
        )
        flash("プロジェクトを更新しました。", "success")
        return redirect(url_for("projects.index"))

    return render_template("projects/form.html", form=form, mode="edit", project=project)

@project_bp.route('/duplicate/<int:project_id>', methods=['POST'])
@login_required
def duplicate_project(project_id):
    new_name = request.form.get('new_name')
    if not new_name:
        flash('新しいプロジェクト名を入力してください', 'error')
        return redirect(url_for('projects.index'))

    try:
        ProjectService.duplicate_project(project_id, new_name)
        flash('プロジェクトを複製しました', 'success')
    except ValueError as e:
        flash(str(e), 'error')

    return redirect(url_for('projects.index'))

@project_bp.route('/delete/<int:project_id>', methods=['POST'])
@login_required
def delete_project(project_id):
    try:
        ProjectService.delete_project(project_id)
        flash('プロジェクトを削除しました', 'success')
    except ValueError as e:
        flash(str(e), 'error')

    return redirect(url_for('projects.index'))
