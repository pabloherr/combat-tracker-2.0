"""PG temporales (D&D 5e).

Viven en la ficha (`sheet.hp_temp`), no en la vida: no suben el máximo, no se
curan, absorben el daño antes que los PG y se van con un descanso largo. Los
tocan tanto la ficha como el combate, así que la cuenta vive acá.
"""

import json

MAX = 999


def leer(sheet: dict) -> int:
    """Los temporales de una ficha. El PDF los deja como texto."""
    try:
        return max(0, int(str((sheet or {}).get("hp_temp") or "0").strip() or 0))
    except (TypeError, ValueError):
        return 0


def escribir(conn, char_id: int, sheet: dict, n: int) -> int:
    n = max(0, min(MAX, n))
    sheet["hp_temp"] = n
    conn.execute("UPDATE characters SET sheet=? WHERE id=?",
                 (json.dumps(sheet), char_id))
    return n


def absorber(conn, char_id: int, dano: int) -> int:
    """Gasta los temporales que alcancen y devuelve el daño que sigue de pie.

    En Cosmere nunca hay temporales, así que devuelve el daño entero."""
    if dano <= 0 or not char_id:
        return max(0, dano)
    row = conn.execute("SELECT sheet FROM characters WHERE id=?", (char_id,)).fetchone()
    if not row:
        return dano
    sheet = json.loads(row["sheet"] or "{}")
    temp = leer(sheet)
    if not temp:
        return dano
    gastado = min(temp, dano)
    escribir(conn, char_id, sheet, temp - gastado)
    return dano - gastado
