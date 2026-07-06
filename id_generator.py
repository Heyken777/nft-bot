import secrets

# 32 символа: A-Z без O/I + 2-9 без 0/1
ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'


def generate_deal_code(cursor, table='deals', column='deal_code'):
    while True:
        code = ''.join(secrets.choice(ALPHABET) for _ in range(8))
        cursor.execute(f"SELECT 1 FROM {table} WHERE {column}=?", (code,))
        if not cursor.fetchone():
            return code
