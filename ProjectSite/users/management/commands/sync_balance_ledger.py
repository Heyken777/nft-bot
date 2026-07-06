import sqlite3
from django.core.management.base import BaseCommand
from django.conf import settings
from django.db import connections


BATCH_SIZE = 500


class Command(BaseCommand):
    help = 'Синхронизирует balance_ledger из SQLite в PostgreSQL'

    def handle(self, *args, **options):
        if not settings.PG_ENABLED:
            self.stdout.write(self.style.WARNING('PostgreSQL не настроен (PG_ENABLED=False). Синхронизация отключена.'))
            return

        sqlite_path = settings.DATABASES['default']['NAME']
        try:
            sl_conn = sqlite3.connect(str(sqlite_path), timeout=10)
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Ошибка подключения к SQLite: {e}'))
            return

        sl_conn.row_factory = sqlite3.Row
        sl_cur = sl_conn.cursor()

        pg_conn = connections['ledger_db']
        pg_cur = pg_conn.cursor()

        sl_cur.execute("SELECT COALESCE(MAX(id), 0) FROM balance_ledger")
        max_id_row = sl_cur.fetchone()
        max_sqlite_id = max_id_row[0] if max_id_row else 0

        pg_cur.execute("SELECT COALESCE(MAX(id), 0) FROM balance_ledger")
        max_pg_id = pg_cur.fetchone()[0]

        if max_sqlite_id <= max_pg_id:
            self.stdout.write(self.style.SUCCESS('Базы уже синхронизированы. Новых записей нет.'))
            sl_conn.close()
            return

        sl_cur.execute(
            "SELECT * FROM balance_ledger WHERE id > ? ORDER BY id ASC",
            (max_pg_id,)
        )

        rows = sl_cur.fetchall()
        total = len(rows)
        inserted = 0

        cols = ['user_id', 'currency', 'amount_delta', 'balance_before', 'balance_after',
                'operation_type', 'reference_id', 'initiated_by', 'note', 'created_at']
        placeholders = ','.join(['%s'] * len(cols))
        col_names = ','.join(cols)
        insert_sql = f"INSERT INTO balance_ledger ({col_names}) VALUES ({placeholders})"

        for i in range(0, total, BATCH_SIZE):
            batch = rows[i:i + BATCH_SIZE]
            values = []
            for row in batch:
                values.append(tuple(row[c] for c in cols))
            pg_cur.executemany(insert_sql, values)
            pg_conn.commit()
            inserted += len(batch)
            self.stdout.write(f'  → Синхронизировано {inserted}/{total}')

        sl_conn.close()
        self.stdout.write(self.style.SUCCESS(f'Готово: {inserted} записей скопировано в PostgreSQL.'))