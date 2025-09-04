from models.users import Users, db

class UserService:
    @staticmethod
    def add_user(username, email, password):
        user = Users(username=username, email=email)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

    @staticmethod
    def get_user_by_username(username):
        return Users.query.filter_by(username=username).first()

    @staticmethod
    def delete_user(user_id):
        user = Users.query.get(user_id)
        if user:
            db.session.delete(user)
            db.session.commit()

    @staticmethod
    def get_all_users():
        return Users.query.all()