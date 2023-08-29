import django.db.transaction

orig_get_connection = django.db.transaction.get_connection


def hacked_get_connection(*a, **kw):
    return orig_get_connection('actual_db')


django.db.transaction.get_connection = hacked_get_connection


class DBRouter:
    def db_for_read(self, model, **hints):
        return 'actual_db'

    def db_for_write(self, model, **hints):
        return 'actual_db'

    def allow_relation(self, obj1, obj2, **hints):
        return True

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        return True
