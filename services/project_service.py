# project_service.py
from extensions import db
from models.projects import Projects
from models.knowledge import Knowledge


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

    @staticmethod
    def duplicate_project(project_id: int, new_name: str) -> Projects:
        original_project = Projects.query.get(project_id)
        if not original_project:
            raise ValueError("プロジェクトが見つかりません")

        # プロジェクトの複製
        new_project = Projects(
            project_name=new_name,
            description=original_project.description,
            doc_path=None
        )
        db.session.add(new_project)
        db.session.commit()

        # Knowledgeの複製
        # Knowledgeの複製
        original_knowledges = Knowledge.query.filter_by(project_id=project_id).all()
        for knowledge in original_knowledges:
            new_knowledge = Knowledge(
                project_id=new_project.project_id,
                user_id=knowledge.user_id,  # 元のKnowledgeのuser_idをコピー
                title=knowledge.title,
                category=knowledge.category,
                content=knowledge.content,
                active=knowledge.active,
                order=knowledge.order,
                created_at=knowledge.created_at,
                updated_at=knowledge.updated_at
            )
            db.session.add(new_knowledge)

        db.session.commit()
        return new_project

    @staticmethod
    def delete_project(project_id: int) -> None:
        project = Projects.query.get(project_id)
        if not project:
            raise ValueError("プロジェクトが見つかりません")

        db.session.delete(project)
        db.session.commit()