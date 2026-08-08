create or replace view bi.dim_mes_historico
with (security_barrier = true) as
with eventos as (
    select data_finalizacao as data_evento
    from bi.fato_historico_conclusao
    where foi_finalizado
      and data_finalizacao is not null

    union

    select data_entrega as data_evento
    from bi.fato_historico_conclusao
    where foi_entregue
      and data_entrega is not null

    union

    select data_retirada as data_evento
    from bi.fato_historico_conclusao
    where foi_retirado
      and data_retirada is not null
),
meses as (
    select distinct date_trunc('month', data_evento)::date as data_mes
    from eventos
)
select
    data_mes,
    to_char(data_mes, 'YYYY-MM') as ano_mes,
    extract(year from data_mes)::integer as ano,
    extract(month from data_mes)::integer as mes_numero
from meses
order by data_mes;

comment on view bi.dim_mes_historico is
    'Meses que possuem ao menos uma finalizacao, entrega ou retirada; usado nos filtros historicos do Power BI.';

grant select on bi.dim_mes_historico to powerbi_reader;
