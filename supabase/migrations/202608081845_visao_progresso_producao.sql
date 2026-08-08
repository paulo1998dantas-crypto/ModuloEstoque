create or replace view bi.fato_progresso_producao
with (security_barrier = true) as
with etapas as (
    select s.work_order_stage_id::text as evento_id, s.work_order_id, s.termino::date as data_evento, s.termino as data_hora,
        'ETAPA CONCLUÍDA'::text as tipo_evento,
        case upper(trim(s.etapa))
            when 'A/C' then 'AR CONDICIONADO' when 'AC' then 'AR CONDICIONADO' when 'DESMONT' then 'DESMONTAGEM'
            when 'REVEST' then 'REVESTIMENTO' when 'BCO' then 'BANCOS' when 'ELÉTRICA' then 'ELÉTRICA'
            when 'ELETRICA' then 'ELÉTRICA' when 'EXPE' then 'EXPEDIÇÃO' when 'LIBERAÇÃO' then 'LIBERAÇÃO'
            when 'LIBERACAO' then 'LIBERAÇÃO' when 'PREP' then 'PREPARAÇÃO' when 'SERRA' then 'SERRALHERIA'
            when 'PLOTAGEM' then 'PLOTAGEM' when 'VIDROS' then 'VIDROS' when 'ACESSÓRIO' then 'ACESSÓRIOS'
            when 'ACESSORIO' then 'ACESSÓRIOS' else coalesce(nullif(trim(s.etapa), ''), 'NÃO INFORMADO')
        end as setor,
        coalesce(nullif(trim(s.etapa), ''), 'NÃO INFORMADO') as etapa
    from bi.fato_etapas_producao s
    where s.aplicavel and s.termino is not null
),
finalizacoes as (
    select h.work_order_id::text || ':FINALIZACAO', h.work_order_id, h.data_finalizacao,
        h.data_finalizacao::timestamp at time zone 'America/Sao_Paulo', 'VEÍCULO FINALIZADO'::text, 'FINALIZAÇÃO'::text, 'FINALIZAÇÃO'::text
    from bi.fato_historico_conclusao h
    where h.foi_finalizado and h.data_finalizacao is not null
),
entregas as (
    select h.work_order_id::text || ':ENTREGA', h.work_order_id, h.data_entrega,
        h.data_entrega::timestamp at time zone 'America/Sao_Paulo', 'VEÍCULO ENTREGUE'::text, 'ENTREGA'::text, 'ENTREGA'::text
    from bi.fato_historico_conclusao h
    where h.foi_entregue and h.data_entrega is not null
),
eventos as (
    select * from etapas union all select * from finalizacoes union all select * from entregas
)
select evento_id, work_order_id, data_evento, data_hora, tipo_evento, setor, etapa,
    extract(year from data_evento)::integer as ano,
    to_char(data_evento, 'YYYY-MM') as ano_mes,
    date_trunc('week', data_evento)::date as semana_inicio,
    to_char(data_evento, 'IYYY') || '-S' || lpad(extract(week from data_evento)::text, 2, '0') as semana_ano
from eventos;

comment on view bi.fato_progresso_producao is
    'Eventos concluídos de produção: etapa, finalização e entrega, com setor normalizado e períodos semanal/mensal/anual.';

grant select on bi.fato_progresso_producao to powerbi_reader;
