# project_service.py
from extensions import db
from models.knowledge import Knowledge

class KnowledgeService:
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
