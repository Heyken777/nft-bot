from django.conf import settings


class LedgerRouter:
    def db_for_read(self, model, **hints):
        if model._meta.db_table == 'balance_ledger':
            return 'ledger_db' if settings.PG_ENABLED else 'default'
        return None

    def db_for_write(self, model, **hints):
        if model._meta.db_table == 'balance_ledger':
            return 'ledger_db' if settings.PG_ENABLED else 'default'
        return None

    def allow_relation(self, obj1, obj2, **hints):
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if db == 'ledger_db':
            return app_label == 'novix_admin' and model_name == 'balanceledger'
        if model_name == 'balanceledger':
            return True
        return None