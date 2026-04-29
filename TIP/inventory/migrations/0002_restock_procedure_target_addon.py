from django.db import migrations

# Replaces single-argument procedure with (actor_id, addon default 0).
RESTOCK_SQL = """
CREATE OR REPLACE PROCEDURE restock_low_stock_consumables(p_actor_id integer, p_addon integer DEFAULT 0)
LANGUAGE plpgsql
AS $$
DECLARE
    rec RECORD;
    v_delta integer;
    v_target integer;
    v_reason text;
BEGIN
    FOR rec IN
        SELECT id, quantity_available, quantity_total, low_stock_threshold
        FROM assets_equipment
        WHERE is_consumable = true
          AND deleted_at IS NULL
          AND low_stock_threshold > 0
          AND quantity_available < low_stock_threshold
    LOOP
        v_target := rec.low_stock_threshold + COALESCE(p_addon, 0);
        v_delta := v_target - rec.quantity_available;
        IF v_delta <= 0 THEN
            CONTINUE;
        END IF;

        IF COALESCE(p_addon, 0) > 0 THEN
            v_reason := 'Automatic restock to low-stock threshold plus ' || p_addon::text || ' by admin procedure.';
        ELSE
            v_reason := 'Automatic restock to low-stock threshold by admin procedure.';
        END IF;

        UPDATE assets_equipment
        SET quantity_total = quantity_total + v_delta,
            quantity_available = quantity_available + v_delta,
            updated_at = NOW()
        WHERE id = rec.id;

        INSERT INTO assets_inventoryadjustment (
            equipment_id,
            delta,
            reason,
            created_at,
            created_by_id,
            deleted_at
        )
        VALUES (
            rec.id,
            v_delta,
            v_reason,
            NOW(),
            p_actor_id,
            NULL
        );
    END LOOP;
END;
$$;
"""


def _apply_forwards(schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute("DROP PROCEDURE IF EXISTS restock_low_stock_consumables(integer);")
    schema_editor.execute("DROP PROCEDURE IF EXISTS restock_low_stock_consumables(integer, integer);")
    schema_editor.execute(RESTOCK_SQL)


def _apply_backwards(schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute("DROP PROCEDURE IF EXISTS restock_low_stock_consumables(integer, integer);")
    schema_editor.execute(
        """
        CREATE OR REPLACE PROCEDURE restock_low_stock_consumables(p_actor_id integer)
        LANGUAGE plpgsql
        AS $$
        DECLARE
            rec RECORD;
            v_delta integer;
        BEGIN
            FOR rec IN
                SELECT id, quantity_available, quantity_total, low_stock_threshold
                FROM assets_equipment
                WHERE is_consumable = true
                  AND deleted_at IS NULL
                  AND low_stock_threshold > 0
                  AND quantity_available < low_stock_threshold
            LOOP
                v_delta := rec.low_stock_threshold - rec.quantity_available;

                UPDATE assets_equipment
                SET quantity_total = quantity_total + v_delta,
                    quantity_available = quantity_available + v_delta,
                    updated_at = NOW()
                WHERE id = rec.id;

                INSERT INTO assets_inventoryadjustment (
                    equipment_id,
                    delta,
                    reason,
                    created_at,
                    created_by_id,
                    deleted_at
                )
                VALUES (
                    rec.id,
                    v_delta,
                    'Automatic restock to low-stock threshold by admin procedure.',
                    NOW(),
                    p_actor_id,
                    NULL
                );
            END LOOP;
        END;
        $$;
        """
    )


def forwards(apps, schema_editor):
    _apply_forwards(schema_editor)


def backwards(apps, schema_editor):
    _apply_backwards(schema_editor)


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0001_postgresql_database_objects"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
