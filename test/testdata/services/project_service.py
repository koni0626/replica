# project_service.py
from extensions import db
from models.projects import Projects

class ProjectService(object):
    def __init__(self):
        pass

    def create_project(self, project_name: str, description: str, doc_path: str) -> Projects:
        project = Projects(project_name=project_name, description=description, doc_path=doc_path)
        db.session.add(project)
        db.session.commit()
        return project

    def fetch_all_projects(self):
        return Projects.query.order_by(Projects.project_id.desc()).all()

    # 追加：ID 取得
    def fetch_by_id(self, project_id: int) -> Projects | None:
        return Projects.query.get(project_id)

    # 追加：更新
    def update_project(self, project_id: int, project_name: str, description: str, doc_path: str) -> Projects:
        project = Projects.query.get(project_id)
        if not project:
            return None
        project.project_name = project_name
        project.description  = description
        project.doc_path     = doc_path
        db.session.commit()
        return project
