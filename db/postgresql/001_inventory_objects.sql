-- PostgreSQL reference objects for the course project.
-- These objects match the Django migration inventory/migrations/0001_postgresql_database_objects.py

create or replace view inventory_equipment_stock_view as
select
    e.id,
    e.name,
    e.inventory_number,
    e.status,
    e.is_consumable,
    e.quantity_total,
    e.quantity_available,
    e.low_stock_threshold,
    c.name as category_name,
    s.name as supplier_name,
    w.name as workplace_name,
    cb.code as cabinet_code,
    (e.quantity_available <= e.low_stock_threshold) as is_low_stock
from assets_equipment e
left join core_equipmentcategory c on c.id = e.category_id
left join core_supplier s on s.id = e.supplier_id
left join core_workplace w on w.id = e.workplace_id
left join core_cabinet cb on cb.id = e.cabinet_id
where e.deleted_at is null;

create or replace view inventory_request_summary_view as
select
    r.status,
    r.request_kind,
    coalesce(w.name, 'Unassigned') as workplace_name,
    count(*) as request_count,
    coalesce(sum(r.quantity), 0) as total_quantity
from operations_equipmentrequest r
left join core_workplace w on w.id = r.workplace_id
where r.deleted_at is null
group by r.status, r.request_kind, coalesce(w.name, 'Unassigned');

create or replace view inventory_active_checkout_view as
select
    c.id,
    c.taken_at,
    c.due_at,
    c.quantity,
    e.name as equipment_name,
    e.inventory_number,
    u.username as taken_by_username,
    w.name as workplace_name,
    (c.due_at is not null and c.due_at < now()) as is_overdue
from assets_equipmentcheckout c
left join assets_equipment e on e.id = c.equipment_id
left join auth_user u on u.id = c.taken_by_id
left join core_workplace w on w.id = c.workplace_id
where c.deleted_at is null
  and c.returned_at is null;

create table if not exists inventory_db_audit_event (
    id bigserial primary key,
    table_name varchar(120) not null,
    operation varchar(16) not null,
    row_pk varchar(64),
    actor_id bigint null references auth_user(id) on delete set null,
    old_data jsonb,
    new_data jsonb,
    changed_at timestamptz not null default now()
);

create or replace function inventory_capture_db_audit()
returns trigger
language plpgsql
as $$
declare
    actor_text text;
    actor_value bigint;
begin
    actor_text := nullif(current_setting('app.current_actor_id', true), '');
    actor_value := case when actor_text is null then null else actor_text::bigint end;

    insert into inventory_db_audit_event (
        table_name,
        operation,
        row_pk,
        actor_id,
        old_data,
        new_data
    )
    values (
        TG_TABLE_NAME,
        TG_OP,
        coalesce(NEW.id::text, OLD.id::text),
        actor_value,
        case when TG_OP = 'INSERT' then null else to_jsonb(OLD) end,
        case when TG_OP = 'DELETE' then null else to_jsonb(NEW) end
    );

    if TG_OP = 'DELETE' then
        return OLD;
    end if;
    return NEW;
end;
$$;

drop trigger if exists inventory_audit_equipment on assets_equipment;
create trigger inventory_audit_equipment
after insert or update or delete on assets_equipment
for each row execute function inventory_capture_db_audit();

drop trigger if exists inventory_audit_adjustment on assets_inventoryadjustment;
create trigger inventory_audit_adjustment
after insert or update or delete on assets_inventoryadjustment
for each row execute function inventory_capture_db_audit();

drop trigger if exists inventory_audit_checkout on assets_equipmentcheckout;
create trigger inventory_audit_checkout
after insert or update or delete on assets_equipmentcheckout
for each row execute function inventory_capture_db_audit();

drop trigger if exists inventory_audit_request on operations_equipmentrequest;
create trigger inventory_audit_request
after insert or update or delete on operations_equipmentrequest
for each row execute function inventory_capture_db_audit();

drop trigger if exists inventory_audit_usage on operations_materialusage;
create trigger inventory_audit_usage
after insert or update or delete on operations_materialusage
for each row execute function inventory_capture_db_audit();

drop trigger if exists inventory_audit_timer on operations_worktimer;
create trigger inventory_audit_timer
after insert or update or delete on operations_worktimer
for each row execute function inventory_capture_db_audit();

create or replace procedure reject_stale_requests(p_actor_id integer, p_stale_days integer)
language plpgsql
as $$
begin
    update operations_equipmentrequest
    set status = 'rejected',
        processed_by_id = p_actor_id,
        processed_at = now(),
        comment = case
            when coalesce(comment, '') = '' then
                'Rejected automatically by admin procedure after ' || p_stale_days::text || ' day(s).'
            else
                comment || E'\n' || 'Rejected automatically by admin procedure after ' || p_stale_days::text || ' day(s).'
        end
    where status = 'pending'
      and requested_at < now() - make_interval(days => p_stale_days);
end;
$$;

create or replace procedure finish_abandoned_timers(p_actor_id integer, p_stale_hours integer)
language plpgsql
as $$
begin
    update operations_worktimer
    set ended_at = now(),
        note = case
            when coalesce(note, '') = '' then
                'Finished automatically by admin procedure after ' || p_stale_hours::text || ' hour(s).'
            else
                note || E'\n' || 'Finished automatically by admin procedure after ' || p_stale_hours::text || ' hour(s).'
        end
    where ended_at is null
      and started_at < now() - make_interval(hours => p_stale_hours);
end;
$$;

create or replace procedure restock_low_stock_consumables(p_actor_id integer)
language plpgsql
as $$
declare
    rec record;
    v_delta integer;
begin
    for rec in
        select id, quantity_available, quantity_total, low_stock_threshold
        from assets_equipment
        where is_consumable = true
          and deleted_at is null
          and low_stock_threshold > 0
          and quantity_available < low_stock_threshold
    loop
        v_delta := rec.low_stock_threshold - rec.quantity_available;

        update assets_equipment
        set quantity_total = quantity_total + v_delta,
            quantity_available = quantity_available + v_delta,
            updated_at = now()
        where id = rec.id;

        insert into assets_inventoryadjustment (equipment_id, delta, reason, created_at, created_by_id, deleted_at)
        values (
            rec.id,
            v_delta,
            'Automatic restock to low-stock threshold by admin procedure.',
            now(),
            p_actor_id,
            null
        );
    end loop;
end;
$$;
