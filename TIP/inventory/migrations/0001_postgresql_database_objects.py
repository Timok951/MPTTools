from django.db import migrations


VIEW_SQL = [
    """
    CREATE OR REPLACE VIEW inventory_equipment_stock_view AS
    SELECT
        e.id,
        e.name,
        e.inventory_number,
        e.status,
        e.is_consumable,
        e.quantity_total,
        e.quantity_available,
        e.low_stock_threshold,
        c.name AS category_name,
        w.name AS workplace_name,
        cb.code AS cabinet_code,
        (e.quantity_available <= e.low_stock_threshold) AS is_low_stock
    FROM assets_equipment e
    LEFT JOIN core_equipmentcategory c ON c.id = e.category_id
    LEFT JOIN core_workplace w ON w.id = e.workplace_id
    LEFT JOIN core_cabinet cb ON cb.id = e.cabinet_id
    WHERE e.deleted_at IS NULL;
    """,
    """
    CREATE OR REPLACE VIEW inventory_request_summary_view AS
    SELECT
        r.status,
        r.request_kind,
        COALESCE(w.name, 'Unassigned') AS workplace_name,
        COUNT(*) AS request_count,
        COALESCE(SUM(r.quantity), 0) AS total_quantity
    FROM operations_equipmentrequest r
    LEFT JOIN core_workplace w ON w.id = r.workplace_id
    WHERE r.deleted_at IS NULL
    GROUP BY r.status, r.request_kind, COALESCE(w.name, 'Unassigned');
    """,
    """
    CREATE OR REPLACE VIEW inventory_active_checkout_view AS
    SELECT
        c.id,
        c.taken_at,
        c.due_at,
        c.quantity,
        e.name AS equipment_name,
        e.inventory_number,
        u.username AS taken_by_username,
        w.name AS workplace_name,
        (c.due_at IS NOT NULL AND c.due_at < NOW()) AS is_overdue
    FROM assets_equipmentcheckout c
    LEFT JOIN assets_equipment e ON e.id = c.equipment_id
    LEFT JOIN auth_user u ON u.id = c.taken_by_id
    LEFT JOIN core_workplace w ON w.id = c.workplace_id
    WHERE c.deleted_at IS NULL
      AND c.returned_at IS NULL;
    """,
]

AUDIT_SQL = [
    """
    CREATE TABLE IF NOT EXISTS inventory_db_audit_event (
        id BIGSERIAL PRIMARY KEY,
        table_name VARCHAR(120) NOT NULL,
        operation VARCHAR(16) NOT NULL,
        row_pk VARCHAR(64),
        actor_id BIGINT NULL REFERENCES auth_user(id) ON DELETE SET NULL,
        old_data JSONB,
        new_data JSONB,
        changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """,
    """
    CREATE OR REPLACE FUNCTION inventory_capture_db_audit()
    RETURNS TRIGGER
    LANGUAGE plpgsql
    AS $$
    DECLARE
        actor_text TEXT;
        actor_value BIGINT;
    BEGIN
        actor_text := NULLIF(current_setting('app.current_actor_id', true), '');
        actor_value := CASE WHEN actor_text IS NULL THEN NULL ELSE actor_text::BIGINT END;

        INSERT INTO inventory_db_audit_event (
            table_name,
            operation,
            row_pk,
            actor_id,
            old_data,
            new_data
        )
        VALUES (
            TG_TABLE_NAME,
            TG_OP,
            COALESCE(NEW.id::TEXT, OLD.id::TEXT),
            actor_value,
            CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE to_jsonb(OLD) END,
            CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE to_jsonb(NEW) END
        );

        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END;
    $$;
    """,
    "DROP TRIGGER IF EXISTS inventory_audit_equipment ON assets_equipment;",
    """
    CREATE TRIGGER inventory_audit_equipment
    AFTER INSERT OR UPDATE OR DELETE ON assets_equipment
    FOR EACH ROW EXECUTE FUNCTION inventory_capture_db_audit();
    """,
    "DROP TRIGGER IF EXISTS inventory_audit_adjustment ON assets_inventoryadjustment;",
    """
    CREATE TRIGGER inventory_audit_adjustment
    AFTER INSERT OR UPDATE OR DELETE ON assets_inventoryadjustment
    FOR EACH ROW EXECUTE FUNCTION inventory_capture_db_audit();
    """,
    "DROP TRIGGER IF EXISTS inventory_audit_checkout ON assets_equipmentcheckout;",
    """
    CREATE TRIGGER inventory_audit_checkout
    AFTER INSERT OR UPDATE OR DELETE ON assets_equipmentcheckout
    FOR EACH ROW EXECUTE FUNCTION inventory_capture_db_audit();
    """,
    "DROP TRIGGER IF EXISTS inventory_audit_request ON operations_equipmentrequest;",
    """
    CREATE TRIGGER inventory_audit_request
    AFTER INSERT OR UPDATE OR DELETE ON operations_equipmentrequest
    FOR EACH ROW EXECUTE FUNCTION inventory_capture_db_audit();
    """,
    "DROP TRIGGER IF EXISTS inventory_audit_usage ON operations_materialusage;",
    """
    CREATE TRIGGER inventory_audit_usage
    AFTER INSERT OR UPDATE OR DELETE ON operations_materialusage
    FOR EACH ROW EXECUTE FUNCTION inventory_capture_db_audit();
    """,
    "DROP TRIGGER IF EXISTS inventory_audit_timer ON operations_worktimer;",
    """
    CREATE TRIGGER inventory_audit_timer
    AFTER INSERT OR UPDATE OR DELETE ON operations_worktimer
    FOR EACH ROW EXECUTE FUNCTION inventory_capture_db_audit();
    """,
]

PROCEDURE_SQL = [
    """
    CREATE OR REPLACE PROCEDURE reject_stale_requests(p_actor_id integer, p_stale_days integer)
    LANGUAGE plpgsql
    AS $$
    BEGIN
        UPDATE operations_equipmentrequest
        SET status = 'rejected',
            processed_by_id = p_actor_id,
            processed_at = NOW(),
            comment = CASE
                WHEN COALESCE(comment, '') = '' THEN
                    'Rejected automatically by admin procedure after ' || p_stale_days::text || ' day(s).'
                ELSE
                    comment || E'\\n' || 'Rejected automatically by admin procedure after ' || p_stale_days::text || ' day(s).'
            END
        WHERE status = 'pending'
          AND requested_at < NOW() - make_interval(days => p_stale_days);
    END;
    $$;
    """,
    """
    CREATE OR REPLACE PROCEDURE finish_abandoned_timers(p_actor_id integer, p_stale_hours integer)
    LANGUAGE plpgsql
    AS $$
    BEGIN
        UPDATE operations_worktimer
        SET ended_at = NOW(),
            note = CASE
                WHEN COALESCE(note, '') = '' THEN
                    'Finished automatically by admin procedure after ' || p_stale_hours::text || ' hour(s).'
                ELSE
                    note || E'\\n' || 'Finished automatically by admin procedure after ' || p_stale_hours::text || ' hour(s).'
            END
        WHERE ended_at IS NULL
          AND started_at < NOW() - make_interval(hours => p_stale_hours);
    END;
    $$;
    """,
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
    """,
]

REVERSE_SQL = [
    "DROP VIEW IF EXISTS inventory_active_checkout_view;",
    "DROP VIEW IF EXISTS inventory_request_summary_view;",
    "DROP VIEW IF EXISTS inventory_equipment_stock_view;",
    "DROP TRIGGER IF EXISTS inventory_audit_timer ON operations_worktimer;",
    "DROP TRIGGER IF EXISTS inventory_audit_usage ON operations_materialusage;",
    "DROP TRIGGER IF EXISTS inventory_audit_request ON operations_equipmentrequest;",
    "DROP TRIGGER IF EXISTS inventory_audit_checkout ON assets_equipmentcheckout;",
    "DROP TRIGGER IF EXISTS inventory_audit_adjustment ON assets_inventoryadjustment;",
    "DROP TRIGGER IF EXISTS inventory_audit_equipment ON assets_equipment;",
    "DROP FUNCTION IF EXISTS inventory_capture_db_audit();",
    "DROP TABLE IF EXISTS inventory_db_audit_event;",
    "DROP PROCEDURE IF EXISTS restock_low_stock_consumables(integer);",
    "DROP PROCEDURE IF EXISTS finish_abandoned_timers(integer, integer);",
    "DROP PROCEDURE IF EXISTS reject_stale_requests(integer, integer);",
]


def create_postgresql_objects(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    for statement in VIEW_SQL + AUDIT_SQL + PROCEDURE_SQL:
        schema_editor.execute(statement)


def drop_postgresql_objects(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    for statement in REVERSE_SQL:
        schema_editor.execute(statement)


class Migration(migrations.Migration):
    dependencies = [
        ("assets", "0005_alter_equipment_options_and_more"),
        ("audit", "0002_portal_and_i18n_meta"),
        ("core", "0008_alter_cabinet_options_and_more"),
        ("operations", "0004_alter_equipmentrequest_options_and_more"),
    ]

    operations = [
        migrations.RunPython(create_postgresql_objects, drop_postgresql_objects),
    ]
