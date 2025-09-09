# project_service.py
from extensions import db
from models.knowledge import Knowledge

class KnowledgeService:

    @staticmethod
    def create_from_plain(project_id, user_id, title, content, category=None, active=True, order=0):
        """フォームを介さず、値を直接受け取ってKnowledgeを作成するヘルパ。
        Docker/フロントのAJAXからの簡易登録で使用。
        """
        knowledge = Knowledge(
            project_id=project_id,
            user_id=user_id,
            title=title,
            category=(category or ''),
            content=content,
            active=active,
            order=order or 0,
        )
        db.session.add(knowledge)
        db.session.commit()
        return knowledge
    @staticmethod
    def get_all_by_project(project_id):
      return Knowledge.query.filter_by(project_id=project_id).order_by(Knowledge.order).all()


    @staticmethod
    def get(knowledge_id):
        return Knowledge.query.get(knowledge_id)


    @staticmethod
    def create(form, project_id, user_id):
        knowledge = Knowledge(
        project_id=project_id,
        user_id = user_id,
        title=form.title.data,
        category=form.category.data,
        content=form.content.data,
        active=form.active.data,
        order=form.order.data or 0,
        )
        db.session.add(knowledge)
        db.session.commit()
        return knowledge


    @staticmethod
    def update(knowledge, form):
        knowledge.title = form.title.data
        knowledge.category = form.category.data
        knowledge.content = form.content.data
        knowledge.active = form.active.data
        knowledge.order = form.order.data or 0
        db.session.commit()
        return knowledge


    @staticmethod
    def delete(knowledge):
        db.session.delete(knowledge)
        db.session.commit()
