-- Permit only the ADMIN-authorized permanent purge path to delete immutable
-- request events. The allowance is transaction-local and bound to one request.
begin;
set local lock_timeout = '5s';
set local statement_timeout = '60s';
create or replace function public.erp_preserve_purchase_request_history() returns trigger
language plpgsql as $$
begin
 if TG_OP = 'DELETE'
    and current_setting('erp.purchase_request_purge_id', true) = OLD.request_id then
  return OLD;
 end if;
 raise exception 'Histórico de solicitações é imutável; registre um novo evento.';
end;
$$;
commit;
