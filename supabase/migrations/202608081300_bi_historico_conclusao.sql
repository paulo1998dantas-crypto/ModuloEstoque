-- Histórico consultivo do MES para a análise de conclusão e entrega no Power BI.
-- Uma linha por veículo/O.S.; nenhuma tabela operacional é alterada.

create or replace view bi.fato_historico_conclusao
with (security_barrier = true) as
with historico_status as (
    select
        h.work_order_id,
        min(h.created_at) filter (where h.novo_status = 'FINALIZADA') as primeira_finalizacao_historica
    from public.erp_work_order_status_history h
    group by h.work_order_id
), base as (
    select
        w.id as work_order_id,
        w.vehicle_entry_id,
        e.vehicle_id,
        w.numero_os,
        e.item_number as item,
        v.chassi,
        right(v.chassi, 8) as chassi_exibicao,
        coalesce(v.modelo, '') as modelo,
        coalesce(nullif(trim(w.cliente_nome), ''), nullif(trim(e.cliente_nome), ''), 'NÃO INFORMADO') as cliente,
        case
            when upper(trim(coalesce(w.mercado, ''))) in ('LICITACAO', 'LICITAÇÃO') then 'LICITAÇÃO'
            when upper(trim(coalesce(w.mercado, ''))) = 'VAREJO' then 'VAREJO'
            else 'NÃO INFORMADO'
        end as mercado,
        coalesce(nullif(upper(trim(w.linha)), ''), 'NÃO INFORMADO') as linha_produto,
        coalesce(w.tipo_veiculo, '') as tipo_veiculo,
        coalesce(w.transformacao, '') as transformacao,
        w.status,
        w.data_aprovacao,
        e.data_chegada,
        greatest(w.data_aprovacao, e.data_chegada::date) as data_inicio_producao,
        case
            when w.data_aprovacao is not null
             and (e.data_chegada is null or w.data_aprovacao >= e.data_chegada::date)
                then 'APROVAÇÃO DA PROPOSTA'
            when e.data_chegada is not null then 'CHEGADA DO VEÍCULO'
            else 'SEM REFERÊNCIA'
        end as origem_inicio_producao,
        coalesce(w.termino_producao, w.finalizado_at, hs.primeira_finalizacao_historica) as data_finalizacao_em,
        case
            when w.termino_producao is not null then 'TÉRMINO DA PRODUÇÃO'
            when w.finalizado_at is not null then 'FINALIZAÇÃO DA O.S.'
            when hs.primeira_finalizacao_historica is not null then 'HISTÓRICO DE STATUS'
            else 'SEM DATA'
        end as origem_data_finalizacao,
        w.data_entrega as data_entrega_em,
        coalesce(seq.data_entrega_vigente, w.data_comercial_prevista, w.data_comercial_calculada) as prazo_finalizacao,
        w.updated_at
    from public.erp_work_orders w
    join public.erp_vehicle_entries e on e.id = w.vehicle_entry_id
    join public.erp_vehicles v on v.id = e.vehicle_id
    left join public.erp_work_order_sequences seq
        on seq.work_order_id = w.id and seq.ativo = true
    left join historico_status hs on hs.work_order_id = w.id
), calculado as (
    select
        b.*,
        b.data_finalizacao_em::date as data_finalizacao,
        b.data_entrega_em::date as data_entrega,
        case
            when b.data_finalizacao_em is not null
             and b.data_inicio_producao is not null
             and b.data_finalizacao_em::date >= b.data_inicio_producao
                then b.data_finalizacao_em::date - b.data_inicio_producao
            else null
        end as dias_producao,
        b.data_finalizacao_em is not null
            and b.data_inicio_producao is not null
            and b.data_finalizacao_em::date < b.data_inicio_producao as duracao_invalida
    from base b
)
select
    c.work_order_id,
    c.vehicle_entry_id,
    c.vehicle_id,
    c.numero_os,
    c.item,
    c.chassi,
    c.chassi_exibicao,
    c.modelo,
    c.cliente,
    c.mercado,
    c.linha_produto,
    c.tipo_veiculo,
    c.transformacao,
    c.status,
    c.data_aprovacao,
    c.data_chegada,
    c.data_inicio_producao,
    c.origem_inicio_producao,
    c.data_finalizacao,
    c.data_finalizacao_em,
    c.origem_data_finalizacao,
    c.data_entrega,
    c.data_entrega_em,
    c.prazo_finalizacao,
    c.dias_producao,
    c.duracao_invalida,
    c.data_finalizacao is not null as foi_finalizado,
    c.data_entrega is not null as foi_entregue,
    c.data_finalizacao is not null
        and c.prazo_finalizacao is not null
        and c.data_finalizacao > c.prazo_finalizacao as finalizado_atraso,
    case
        when c.data_finalizacao is null and c.status in ('FINALIZADA', 'ENTREGUE', 'RETIRADA')
            then 'CONCLUÍDO SEM DATA FINAL'
        when c.data_finalizacao is null then 'NÃO FINALIZADO'
        when c.prazo_finalizacao is null then 'FINALIZADO SEM PRAZO'
        when c.data_finalizacao > c.prazo_finalizacao then 'FINALIZADO EM ATRASO'
        else 'FINALIZADO NO PRAZO'
    end as situacao_finalizacao,
    case
        when c.data_finalizacao is not null
         and c.prazo_finalizacao is not null
         and c.data_finalizacao > c.prazo_finalizacao
            then c.data_finalizacao - c.prazo_finalizacao
        else 0
    end as dias_atraso_finalizacao,
    c.updated_at
from calculado c;

comment on view bi.fato_historico_conclusao is
    'Historico MES por veiculo/O.S.: conclusao, entrega, prazo e tempo de producao desde a maior data entre aprovacao e chegada.';

revoke all on bi.fato_historico_conclusao from public, anon, authenticated;
grant select on bi.fato_historico_conclusao to powerbi_reader;
