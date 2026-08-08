-- Camada consultiva para Power BI - ERP JI Montadora
-- Fonte de verdade: Supabase compartilhado rodtxswtqbsbtukmvobn.
-- Este script cria somente schema, role NOLOGIN e views. Nao altera dados.

create schema if not exists bi;

comment on schema bi is
    'Camada consultiva do Power BI. Nao expor este schema na Data API.';

revoke all on schema bi from public;
revoke all on schema bi from anon;
revoke all on schema bi from authenticated;

do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'powerbi_reader') then
        create role powerbi_reader nologin;
    end if;
end
$$;

grant usage on schema bi to powerbi_reader;

create or replace view bi.dim_sku
with (security_barrier = true) as
select
    s.id as sku_id,
    s.sku as codigo,
    s.descricao,
    coalesce(s.unidade, '') as unidade,
    coalesce(s.grupo, '') as grupo,
    coalesce(s.categoria, '') as categoria,
    coalesce(s.localizacao, '') as localizacao,
    coalesce(s.estoque_minimo, 0)::numeric as estoque_minimo,
    s.active as ativo,
    s.created_at,
    s.updated_at
from public.skus s;

comment on view bi.dim_sku is
    'Dimensao canonica de SKU usada em todas as paginas do BI.';

create or replace view bi.dim_ordem_servico
with (security_barrier = true) as
select
    w.id as work_order_id,
    w.numero_os,
    e.item_number as item,
    e.id as vehicle_entry_id,
    v.id as vehicle_id,
    v.chassi,
    right(v.chassi, 8) as chassi_exibicao,
    coalesce(v.marca, '') as marca,
    coalesce(v.modelo, '') as modelo,
    coalesce(v.versao, '') as versao,
    coalesce(v.mmv, '') as mmv,
    coalesce(nullif(trim(w.cliente_nome), ''), nullif(trim(e.cliente_nome), ''), '') as cliente,
    coalesce(w.municipio, '') as municipio,
    coalesce(w.uf, '') as uf,
    coalesce(w.vendedor, '') as vendedor,
    coalesce(w.mercado, '') as mercado,
    coalesce(w.tipo_servico, '') as tipo_servico,
    coalesce(w.tipo_veiculo, '') as tipo_veiculo,
    coalesce(w.linha, '') as linha,
    coalesce(w.transformacao_codigo, '') as transformacao_codigo,
    coalesce(w.transformacao, '') as transformacao,
    w.status,
    coalesce(w.technical_status, 'ABERTA') as technical_status,
    coalesce(w.stage_configuration_status, 'PENDENTE') as stage_configuration_status,
    e.data_chegada,
    w.data_aprovacao,
    w.ativado_at,
    w.data_comercial_prevista,
    w.data_comercial_calculada,
    coalesce(seq.data_entrega_vigente, w.data_comercial_prevista, w.data_comercial_calculada) as data_entrega_vigente,
    seq.semana_planejada,
    seq.sequencia,
    seq.prioridade_manual,
    w.termino_producao,
    w.data_entrega,
    w.created_at,
    w.updated_at,
    coalesce(st.etapas_total, 0) as etapas_total,
    coalesce(st.etapas_aplicaveis, 0) as etapas_aplicaveis,
    coalesce(st.etapas_concluidas, 0) as etapas_concluidas,
    coalesce(st.etapas_em_andamento, 0) as etapas_em_andamento,
    coalesce(st.etapas_pendentes, 0) as etapas_pendentes,
    case
        when coalesce(st.etapas_aplicaveis, 0) = 0 then 0::numeric
        else round(100 * st.etapas_concluidas::numeric / st.etapas_aplicaveis, 2)
    end as percentual_avanco,
    w.status in ('ATIVA', 'EM_PRODUÇÃO') as em_wip,
    case
        when w.status = 'ATIVA' then 'PATIO'
        when w.status = 'EM_PRODUÇÃO' then 'PRODUCAO'
        when w.status in ('FINALIZADA', 'ENTREGUE', 'RETIRADA') then 'CONCLUIDA'
        else 'FORA_WIP'
    end as fase_wip,
    case
        when w.status in ('ATIVA', 'EM_PRODUÇÃO')
         and greatest(w.data_aprovacao, e.data_chegada::date) is not null
         and current_date > greatest(w.data_aprovacao, e.data_chegada::date)
             + case when upper(trim(coalesce(w.linha, ''))) in ('LE', 'LAE') then 45 else 30 end
        then true else false
    end as entrega_atrasada,
    case
        when w.status in ('ATIVA', 'EM_PRODUÇÃO') then
            current_date - greatest(w.data_aprovacao, e.data_chegada::date)
        else null
    end as dias_no_wip,
    case
        when upper(trim(coalesce(w.tipo_servico, ''))) in ('TRANSFORMAÇÃO', 'TRANSFORMACAO') then 'TRANSFORMAÇÃO'
        when upper(trim(coalesce(w.tipo_servico, ''))) in ('PÓS-VENDA', 'POS-VENDA', 'PÓS VENDA', 'POS VENDA') then 'PÓS-VENDA'
        when upper(trim(coalesce(w.tipo_servico, ''))) in (
            'INSTALAÇÃO_DE_ACESSÓRIO', 'INSTALACAO_DE_ACESSORIO',
            'INSTALAÇÃO DE ACESSÓRIO', 'INSTALACAO DE ACESSORIO'
        ) then 'INSTALAÇÃO DE ACESSÓRIO'
        when nullif(trim(coalesce(w.tipo_servico, '')), '') is null then 'NÃO INFORMADO'
        else 'OUTROS'
    end as categoria_servico,
    coalesce(nullif(upper(trim(w.linha)), ''), 'NÃO INFORMADO') as linha_produto,
    greatest(w.data_aprovacao, e.data_chegada::date) as data_inicio_producao,
    case when upper(trim(coalesce(w.linha, ''))) in ('LE', 'LAE') then 45 else 30 end as prazo_producao_dias,
    greatest(w.data_aprovacao, e.data_chegada::date)
        + case when upper(trim(coalesce(w.linha, ''))) in ('LE', 'LAE') then 45 else 30 end
        as data_limite_producao,
    case
        when w.status in ('ATIVA', 'EM_PRODUÇÃO')
         and greatest(w.data_aprovacao, e.data_chegada::date) is not null
         and current_date > greatest(w.data_aprovacao, e.data_chegada::date)
             + case when upper(trim(coalesce(w.linha, ''))) in ('LE', 'LAE') then 45 else 30 end
        then true else false
    end as producao_atrasada,
    case
        when w.status in ('ATIVA', 'EM_PRODUÇÃO')
         and greatest(w.data_aprovacao, e.data_chegada::date) is not null
         and current_date > greatest(w.data_aprovacao, e.data_chegada::date)
             + case when upper(trim(coalesce(w.linha, ''))) in ('LE', 'LAE') then 45 else 30 end
        then current_date - (
            greatest(w.data_aprovacao, e.data_chegada::date)
            + case when upper(trim(coalesce(w.linha, ''))) in ('LE', 'LAE') then 45 else 30 end
        )
        else 0
    end as dias_atraso_producao
from public.erp_work_orders w
join public.erp_vehicle_entries e on e.id = w.vehicle_entry_id
join public.erp_vehicles v on v.id = e.vehicle_id
left join public.erp_work_order_sequences seq
    on seq.work_order_id = w.id and seq.ativo = true
left join lateral (
    select
        count(*) as etapas_total,
        count(*) filter (where s.aplicavel) as etapas_aplicaveis,
        count(*) filter (where s.aplicavel and s.status = 'CONCLUÍDA') as etapas_concluidas,
        count(*) filter (where s.aplicavel and s.status = 'EM_ANDAMENTO') as etapas_em_andamento,
        count(*) filter (where s.aplicavel and s.status in ('PENDENTE', 'LIBERADA')) as etapas_pendentes
    from public.erp_work_order_stages s
    where s.work_order_id = w.id
) st on true;

comment on view bi.dim_ordem_servico is
    'Dimensao de O.S. com identificacao do veiculo, cliente, WIP e progresso de etapas.';

create or replace view bi.fato_historico_conclusao
with (security_barrier = true) as
with historico_status as (
    select
        h.work_order_id,
        min(h.created_at) filter (where h.novo_status = 'FINALIZADA') as primeira_finalizacao_historica,
        min(h.created_at) filter (where h.novo_status = 'RETIRADA') as primeira_retirada_historica
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
        coalesce(w.tipo_servico, '') as tipo_servico,
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
        greatest(w.data_aprovacao, e.data_chegada::date)
            + case when upper(trim(coalesce(w.linha, ''))) in ('LE', 'LAE') then 45 else 30 end
            as prazo_finalizacao,
        hs.primeira_retirada_historica as data_retirada_em,
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
    c.status in ('FINALIZADA', 'ENTREGUE') and c.data_finalizacao is not null as foi_finalizado,
    c.status = 'ENTREGUE' and c.data_entrega is not null as foi_entregue,
    c.status in ('FINALIZADA', 'ENTREGUE')
        and c.data_finalizacao is not null
        and c.prazo_finalizacao is not null
        and c.data_finalizacao > c.prazo_finalizacao as finalizado_atraso,
    case
        when c.status = 'RETIRADA' then 'RETIRADO'
        when c.data_finalizacao is null and c.status in ('FINALIZADA', 'ENTREGUE')
            then 'CONCLUÍDO SEM DATA FINAL'
        when c.data_finalizacao is null then 'NÃO FINALIZADO'
        when c.prazo_finalizacao is null then 'FINALIZADO SEM DATA INICIAL'
        when c.data_finalizacao > c.prazo_finalizacao then 'FINALIZADO EM ATRASO'
        else 'FINALIZADO NO PRAZO'
    end as situacao_finalizacao,
    case
        when c.status in ('FINALIZADA', 'ENTREGUE')
         and c.data_finalizacao is not null
         and c.prazo_finalizacao is not null
         and c.data_finalizacao > c.prazo_finalizacao
            then c.data_finalizacao - c.prazo_finalizacao
        else 0
    end as dias_atraso_finalizacao,
    c.updated_at,
    coalesce(c.tipo_servico, '') as tipo_servico,
    case
        when upper(trim(coalesce(c.tipo_servico, ''))) in ('TRANSFORMAÇÃO', 'TRANSFORMACAO') then 'TRANSFORMAÇÃO'
        when upper(trim(coalesce(c.tipo_servico, ''))) in ('PÓS-VENDA', 'POS-VENDA', 'PÓS VENDA', 'POS VENDA') then 'PÓS-VENDA'
        when upper(trim(coalesce(c.tipo_servico, ''))) in (
            'INSTALAÇÃO_DE_ACESSÓRIO', 'INSTALACAO_DE_ACESSORIO',
            'INSTALAÇÃO DE ACESSÓRIO', 'INSTALACAO DE ACESSORIO'
        ) then 'INSTALAÇÃO DE ACESSÓRIO'
        when nullif(trim(coalesce(c.tipo_servico, '')), '') is null then 'NÃO INFORMADO'
        else 'OUTROS'
    end as categoria_servico,
    case when upper(trim(coalesce(c.linha_produto, ''))) in ('LE', 'LAE') then 45 else 30 end
        as prazo_producao_dias,
    c.prazo_finalizacao as data_limite_producao,
    c.data_retirada_em::date as data_retirada,
    c.status = 'RETIRADA' as foi_retirado
from calculado c;

comment on view bi.fato_historico_conclusao is
    'Historico MES por veiculo/O.S.: conclusao, entrega, prazo e tempo de producao desde a maior data entre aprovacao e chegada.';

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

create or replace view bi.fato_etapas_producao
with (security_barrier = true) as
select
    s.id as work_order_stage_id,
    s.work_order_id,
    w.numero_os,
    e.item_number as item,
    right(v.chassi, 8) as chassi_exibicao,
    coalesce(nullif(trim(w.cliente_nome), ''), nullif(trim(e.cliente_nome), ''), '') as cliente,
    s.stage_code as etapa,
    s.ordem,
    s.aplicavel,
    s.parametrizado,
    s.status,
    s.semana_planejada,
    s.data_planejada,
    s.sequencia_planejada,
    coalesce(s.responsavel, '') as responsavel,
    coalesce(s.localizacao, '') as localizacao,
    s.inicio,
    s.termino,
    case
        when s.inicio is not null and s.termino is not null
        then extract(epoch from (s.termino - s.inicio)) / 3600.0
        else null
    end as duracao_horas,
    coalesce(s.observacoes, '') as observacoes
from public.erp_work_order_stages s
join public.erp_work_orders w on w.id = s.work_order_id
join public.erp_vehicle_entries e on e.id = w.vehicle_entry_id
join public.erp_vehicles v on v.id = e.vehicle_id;

comment on view bi.fato_etapas_producao is
    'Uma linha por etapa da O.S., para WIP, avancos, filas e tempos de producao.';

create or replace view bi.fato_progresso_producao
with (security_barrier = true) as
with etapas as (
    select
        s.work_order_stage_id::text as evento_id,
        s.work_order_id,
        s.termino::date as data_evento,
        s.termino as data_hora,
        'ETAPA CONCLUÍDA'::text as tipo_evento,
        case upper(trim(s.etapa))
            when 'A/C' then 'AR CONDICIONADO'
            when 'AC' then 'AR CONDICIONADO'
            when 'DESMONT' then 'DESMONTAGEM'
            when 'REVEST' then 'REVESTIMENTO'
            when 'BCO' then 'BANCOS'
            when 'ELÉTRICA' then 'ELÉTRICA'
            when 'ELETRICA' then 'ELÉTRICA'
            when 'EXPE' then 'EXPEDIÇÃO'
            when 'LIBERAÇÃO' then 'LIBERAÇÃO'
            when 'LIBERACAO' then 'LIBERAÇÃO'
            when 'PREP' then 'PREPARAÇÃO'
            when 'SERRA' then 'SERRALHERIA'
            when 'PLOTAGEM' then 'PLOTAGEM'
            when 'VIDROS' then 'VIDROS'
            when 'ACESSÓRIO' then 'ACESSÓRIOS'
            when 'ACESSORIO' then 'ACESSÓRIOS'
            else coalesce(nullif(trim(s.etapa), ''), 'NÃO INFORMADO')
        end as setor,
        coalesce(nullif(trim(s.etapa), ''), 'NÃO INFORMADO') as etapa
    from bi.fato_etapas_producao s
    where s.aplicavel
      and s.termino is not null
),
finalizacoes as (
    select
        h.work_order_id::text || ':FINALIZACAO' as evento_id,
        h.work_order_id,
        h.data_finalizacao as data_evento,
        h.data_finalizacao::timestamp at time zone 'America/Sao_Paulo' as data_hora,
        'VEÍCULO FINALIZADO'::text as tipo_evento,
        'FINALIZAÇÃO'::text as setor,
        'FINALIZAÇÃO'::text as etapa
    from bi.fato_historico_conclusao h
    where h.foi_finalizado
      and h.data_finalizacao is not null
),
entregas as (
    select
        h.work_order_id::text || ':ENTREGA' as evento_id,
        h.work_order_id,
        h.data_entrega as data_evento,
        h.data_entrega::timestamp at time zone 'America/Sao_Paulo' as data_hora,
        'VEÍCULO ENTREGUE'::text as tipo_evento,
        'ENTREGA'::text as setor,
        'ENTREGA'::text as etapa
    from bi.fato_historico_conclusao h
    where h.foi_entregue
      and h.data_entrega is not null
),
eventos as (
    select * from etapas
    union all
    select * from finalizacoes
    union all
    select * from entregas
)
select
    evento_id,
    work_order_id,
    data_evento,
    data_hora,
    tipo_evento,
    setor,
    etapa,
    extract(year from data_evento)::integer as ano,
    to_char(data_evento, 'YYYY-MM') as ano_mes,
    date_trunc('week', data_evento)::date as semana_inicio,
    to_char(data_evento, 'IYYY') || '-S' || lpad(extract(week from data_evento)::text, 2, '0') as semana_ano
from eventos;

comment on view bi.fato_progresso_producao is
    'Eventos concluídos de produção: etapa, finalização e entrega, com setor normalizado e períodos semanal/mensal/anual.';

create or replace view bi.fato_movimentacoes_estoque
with (security_barrier = true) as
select
    m.id as movement_id,
    m.created_at as data_movimento_utc,
    (m.created_at at time zone 'UTC' at time zone 'America/Sao_Paulo') as data_movimento,
    (m.created_at at time zone 'UTC' at time zone 'America/Sao_Paulo')::date as data,
    m.sku_id,
    s.sku as codigo,
    s.descricao,
    coalesce(s.unidade, '') as unidade,
    coalesce(s.grupo, '') as grupo,
    coalesce(s.categoria, '') as categoria,
    m.tipo,
    m.quantidade,
    m.saldo_anterior,
    m.saldo_posterior,
    m.saldo_posterior - m.saldo_anterior as variacao_saldo,
    m.movement_status,
    m.work_order_id,
    w.numero_os,
    e.item_number as item,
    right(v.chassi, 8) as chassi_exibicao,
    coalesce(m.context_kind, '') as contexto,
    coalesce(m.setor, '') as setor,
    coalesce(m.reference_text, '') as referencia,
    coalesce(m.documento, '') as documento,
    coalesce(m.source_type, '') as origem,
    m.related_movement_id,
    m.parent_movement_id,
    m.operation_id,
    coalesce(m.observacao, '') as observacao
from public.movements m
join public.skus s on s.id = m.sku_id
left join public.erp_work_orders w on w.id = m.work_order_id
left join public.erp_vehicle_entries e on e.id = w.vehicle_entry_id
left join public.erp_vehicles v on v.id = e.vehicle_id;

comment on view bi.fato_movimentacoes_estoque is
    'Livro de movimentos com contexto de SKU, O.S. e variacao real do saldo.';

create or replace view bi.fato_empenhos_abertos
with (security_barrier = true) as
with baixas as (
    select
        b.related_movement_id as commitment_id,
        sum(b.quantidade) as quantidade_baixada
    from public.movements b
    where b.tipo = 'BAIXA'
      and b.movement_status = 'ATIVA'
      and b.related_movement_id is not null
    group by b.related_movement_id
)
select
    m.id as commitment_id,
    m.created_at as criado_em_utc,
    (m.created_at at time zone 'UTC' at time zone 'America/Sao_Paulo') as criado_em,
    m.tipo,
    m.sku_id,
    s.sku as codigo,
    s.descricao,
    coalesce(s.unidade, '') as unidade,
    coalesce(s.grupo, '') as grupo,
    coalesce(s.categoria, '') as categoria,
    m.quantidade as quantidade_original,
    coalesce(b.quantidade_baixada, 0) as quantidade_baixada,
    greatest(m.quantidade - coalesce(b.quantidade_baixada, 0), 0) as quantidade_pendente,
    m.work_order_id,
    w.numero_os,
    e.item_number as item,
    coalesce(nullif(trim(w.cliente_nome), ''), nullif(trim(e.cliente_nome), ''), '') as cliente,
    case when m.work_order_id is null then 'FLUXO_COMPARTILHADO' else 'VINCULADO_OS' end as tipo_vinculo,
    coalesce(m.setor, '') as setor,
    coalesce(m.reference_text, '') as referencia,
    coalesce(m.documento, '') as documento,
    coalesce(m.observacao, '') as observacao
from public.movements m
join public.skus s on s.id = m.sku_id
left join baixas b on b.commitment_id = m.id
left join public.erp_work_orders w on w.id = m.work_order_id
left join public.erp_vehicle_entries e on e.id = w.vehicle_entry_id
where m.tipo in ('EMPENHO', 'SAIDA')
  and m.movement_status = 'ATIVA'
  and greatest(m.quantidade - coalesce(b.quantidade_baixada, 0), 0) > 0;

comment on view bi.fato_empenhos_abertos is
    'Uma linha por empenho ainda aberto. Evita repetir e somar o mesmo saldo de fluxo em varias O.S.';

create or replace view bi.fato_estoque_atual
with (security_barrier = true) as
with baixas as (
    select
        b.related_movement_id as commitment_id,
        sum(b.quantidade) as quantidade_baixada
    from public.movements b
    where b.tipo = 'BAIXA'
      and b.movement_status = 'ATIVA'
      and b.related_movement_id is not null
    group by b.related_movement_id
), empenhos as (
    select
        m.id,
        m.sku_id,
        m.work_order_id,
        greatest(m.quantidade - coalesce(b.quantidade_baixada, 0), 0) as saldo_empenho
    from public.movements m
    left join baixas b on b.commitment_id = m.id
    where m.tipo in ('EMPENHO', 'SAIDA')
      and m.movement_status = 'ATIVA'
), empenhos_sku as (
    select
        sku_id,
        sum(saldo_empenho) as empenhado_total,
        sum(saldo_empenho) filter (where work_order_id is not null) as empenhado_os,
        sum(saldo_empenho) filter (where work_order_id is null) as empenhado_fluxo,
        count(*) filter (where saldo_empenho > 0) as empenhos_ativos
    from empenhos
    where saldo_empenho > 0
    group by sku_id
)
select
    s.id as sku_id,
    s.sku as codigo,
    s.descricao,
    coalesce(s.unidade, '') as unidade,
    coalesce(s.grupo, '') as grupo,
    coalesce(s.categoria, '') as categoria,
    coalesce(s.localizacao, '') as localizacao,
    coalesce(s.estoque_minimo, 0)::numeric as estoque_minimo,
    coalesce(sb.saldo_atual, 0)::numeric as estoque_atual,
    coalesce(es.empenhado_total, 0)::numeric as empenhado_total,
    coalesce(es.empenhado_os, 0)::numeric as empenhado_os,
    coalesce(es.empenhado_fluxo, 0)::numeric as empenhado_fluxo,
    greatest(coalesce(sb.saldo_atual, 0) - coalesce(es.empenhado_total, 0), 0)::numeric as estoque_disponivel,
    (coalesce(sb.saldo_atual, 0) - coalesce(es.empenhado_total, 0))::numeric as estoque_disponivel_contabil,
    coalesce(es.empenhos_ativos, 0) as empenhos_ativos,
    case
        when coalesce(sb.saldo_atual, 0) <= 0 then 'ZERADO'
        when coalesce(sb.saldo_atual, 0) - coalesce(es.empenhado_total, 0) < 0 then 'SALDO_COMPROMETIDO'
        when s.estoque_minimo is not null
         and coalesce(sb.saldo_atual, 0) - coalesce(es.empenhado_total, 0) <= s.estoque_minimo then 'BAIXO'
        else 'OK'
    end as status_estoque,
    sb.updated_at as saldo_atualizado_em,
    s.active as sku_ativo
from public.skus s
left join public.stock_balances sb on sb.sku_id = s.id
left join empenhos_sku es on es.sku_id = s.id;

comment on view bi.fato_estoque_atual is
    'Snapshot por SKU: saldo fisico, empenho pendente e saldo disponivel.';

create or replace view bi.fato_inventarios
with (security_barrier = true) as
select
    c.id as inventory_count_id,
    c.session_id,
    ses.status as status_sessao,
    ses.opened_at,
    ses.closed_at,
    c.counted_at,
    c.counted_at::date as data_contagem,
    c.sku_id,
    s.sku as codigo,
    s.descricao,
    coalesce(s.unidade, '') as unidade,
    coalesce(s.grupo, '') as grupo,
    coalesce(s.categoria, '') as categoria,
    c.saldo_sistema,
    c.quantidade_contada,
    c.diferenca,
    case
        when c.diferenca = 0 then 'SEM_DIVERGENCIA'
        when c.diferenca > 0 then 'SOBRA'
        else 'FALTA'
    end as status_divergencia
from public.inventory_counts c
join public.inventory_sessions ses on ses.id = c.session_id
join public.skus s on s.id = c.sku_id;

comment on view bi.fato_inventarios is
    'Contagens de inventario e divergencias por SKU e sessao.';

create or replace view bi.fato_necessidades_os
with (security_barrier = true) as
with recursive
active_orders as (
    select
        w.id as work_order_id,
        w.numero_os,
        w.status as status_os,
        w.technical_status,
        w.cliente_nome,
        w.linha,
        w.transformacao,
        w.tipo_servico,
        w.data_comercial_prevista,
        coalesce(seq.data_entrega_vigente, w.data_comercial_prevista, w.data_comercial_calculada) as data_entrega_vigente,
        e.item_number as item,
        e.cliente_nome as cliente_entrada,
        v.chassi,
        d.id as document_id,
        d.composicao
    from public.erp_work_orders w
    join public.erp_vehicle_entries e on e.id = w.vehicle_entry_id
    join public.erp_vehicles v on v.id = e.vehicle_id
    left join public.erp_work_order_sequences seq
        on seq.work_order_id = w.id and seq.ativo = true
    left join lateral (
        select doc.id, doc.composicao
        from public.suprimentos_documentos doc
        where doc.tipo = 'os'
          and (doc.erp_work_order_id = w.id or doc.numero = w.numero_os)
        order by (doc.erp_work_order_id = w.id) desc, doc.updated_at desc
        limit 1
    ) d on true
    where coalesce(w.technical_status, 'ABERTA') = 'ABERTA'
      and upper(coalesce(w.status, '')) not in
          ('FINALIZADA', 'ENTREGUE', 'RETIRADA', 'CANCELADA', 'ARQUIVADA')
), composition_rows as (
    select
        ao.*,
        upper(trim(line.value ->> 'codigo')) as codigo,
        coalesce(nullif(trim(line.value ->> 'descricao'), ''), '') as descricao_linha,
        coalesce(nullif(trim(line.value ->> 'unidade'), ''), '') as unidade_linha,
        coalesce(nullif(trim(line.value ->> 'setor'), ''), '') as setor,
        coalesce(nullif(trim(line.value ->> 'item'), ''), '') as item_pai,
        case
            when coalesce(line.value ->> 'level', '0') ~ '^-?[0-9]+$'
            then (line.value ->> 'level')::integer else 0
        end as nivel_bom,
        case
            when replace(coalesce(line.value ->> 'qtd', line.value ->> 'quantidade', '0'), ',', '.')
                 ~ '^[+-]?[0-9]+([.][0-9]+)?$'
            then replace(coalesce(line.value ->> 'qtd', line.value ->> 'quantidade', '0'), ',', '.')::numeric
            else 0::numeric
        end as quantidade
    from active_orders ao
    cross join lateral jsonb_array_elements(coalesce(ao.composicao, '[]'::jsonb)) line(value)
), required as (
    select
        c.work_order_id,
        c.numero_os,
        c.status_os,
        c.technical_status,
        c.item,
        c.chassi,
        coalesce(nullif(trim(c.cliente_nome), ''), nullif(trim(c.cliente_entrada), ''), '') as cliente,
        coalesce(c.linha, '') as linha,
        coalesce(c.transformacao, '') as transformacao,
        coalesce(c.tipo_servico, '') as tipo_servico,
        c.data_comercial_prevista,
        c.data_entrega_vigente,
        c.document_id,
        c.codigo,
        max(c.descricao_linha) as descricao_linha,
        max(c.unidade_linha) as unidade_linha,
        string_agg(distinct c.setor, ' / ' order by c.setor) filter (where c.setor <> '') as setor,
        min(c.nivel_bom) as nivel_bom,
        string_agg(distinct c.item_pai, ', ' order by c.item_pai) filter (where c.item_pai <> '') as itens_pai,
        sum(c.quantidade) as quantidade_necessaria
    from composition_rows c
    where c.codigo <> '' and c.quantidade > 0
    group by
        c.work_order_id, c.numero_os, c.status_os, c.technical_status, c.item,
        c.chassi, c.cliente_nome, c.cliente_entrada, c.linha, c.transformacao, c.tipo_servico,
        c.data_comercial_prevista, c.data_entrega_vigente, c.document_id, c.codigo
), bom_paths as (
    select
        s.id as source_sku_id,
        s.id as covered_sku_id,
        1::numeric as fator,
        array[s.id] as caminho,
        0 as profundidade
    from public.skus s
    union all
    select
        p.source_sku_id,
        b.component_sku_id,
        p.fator * b.quantidade,
        p.caminho || b.component_sku_id,
        p.profundidade + 1
    from bom_paths p
    join public.bom_components b on b.item_sku_id = p.covered_sku_id
    where not b.component_sku_id = any(p.caminho)
      and p.profundidade < 20
), bom_closure as (
    select source_sku_id, covered_sku_id, sum(fator) as fator
    from bom_paths
    group by source_sku_id, covered_sku_id
), coverage as (
    select
        m.work_order_id,
        bc.covered_sku_id,
        sum(m.quantidade * bc.fator) as quantidade_coberta
    from public.movements m
    join bom_closure bc on bc.source_sku_id = m.sku_id
    where m.work_order_id is not null
      and m.movement_status = 'ATIVA'
      and (
          m.tipo in ('EMPENHO', 'SAIDA')
          or (
              m.tipo = 'BAIXA'
              and not exists (
                  select 1
                  from public.movements parent
                  where parent.id = m.related_movement_id
                    and parent.movement_status = 'ATIVA'
                    and parent.tipo in ('EMPENHO', 'SAIDA')
                    and parent.work_order_id = m.work_order_id
              )
          )
      )
    group by m.work_order_id, bc.covered_sku_id
)
select
    r.work_order_id,
    r.numero_os,
    r.item,
    r.chassi,
    right(r.chassi, 8) as chassi_exibicao,
    r.cliente,
    r.linha,
    r.transformacao,
    r.status_os,
    r.technical_status,
    r.data_comercial_prevista,
    r.data_entrega_vigente,
    r.document_id,
    s.id as sku_id,
    r.codigo,
    coalesce(nullif(r.descricao_linha, ''), s.descricao, '') as descricao,
    coalesce(nullif(r.unidade_linha, ''), s.unidade, '') as unidade,
    coalesce(r.setor, '') as setor,
    r.nivel_bom,
    coalesce(r.itens_pai, '') as itens_pai,
    r.quantidade_necessaria,
    least(r.quantidade_necessaria, greatest(coalesce(c.quantidade_coberta, 0), 0)) as quantidade_coberta,
    greatest(r.quantidade_necessaria - greatest(coalesce(c.quantidade_coberta, 0), 0), 0) as quantidade_pendente,
    case
        when greatest(r.quantidade_necessaria - greatest(coalesce(c.quantidade_coberta, 0), 0), 0) > 0
        then 'PENDENTE' else 'COBERTA'
    end as status_necessidade,
    r.tipo_servico,
    case
        when upper(trim(coalesce(r.tipo_servico, ''))) in ('TRANSFORMAÇÃO', 'TRANSFORMACAO') then 'TRANSFORMAÇÃO'
        when upper(trim(coalesce(r.tipo_servico, ''))) in ('PÓS-VENDA', 'POS-VENDA', 'PÓS VENDA', 'POS VENDA') then 'PÓS-VENDA'
        when upper(trim(coalesce(r.tipo_servico, ''))) in (
            'INSTALAÇÃO_DE_ACESSÓRIO', 'INSTALACAO_DE_ACESSORIO',
            'INSTALAÇÃO DE ACESSÓRIO', 'INSTALACAO DE ACESSORIO'
        ) then 'INSTALAÇÃO DE ACESSÓRIO'
        when nullif(trim(coalesce(r.tipo_servico, '')), '') is null then 'NÃO INFORMADO'
        else 'OUTROS'
    end as categoria_servico,
    coalesce(es.estoque_disponivel, 0) as estoque_disponivel,
    greatest(r.quantidade_necessaria - greatest(coalesce(c.quantidade_coberta, 0), 0), 0) > 0
        and coalesce(es.estoque_disponivel, 0) <= 0 as sem_estoque_disponivel
from required r
left join public.skus s on upper(trim(s.sku)) = r.codigo
left join coverage c on c.work_order_id = r.work_order_id and c.covered_sku_id = s.id
left join bi.fato_estoque_atual es on es.sku_id = s.id;

comment on view bi.fato_necessidades_os is
    'Necessidade real por O.S. e SKU. Empenho do conjunto cobre sua arvore BOM; baixa filha nao duplica cobertura.';

create or replace view bi.fato_compras_transito
with (security_barrier = true) as
select
    o.id as purchase_order_id,
    l.id as purchase_order_line_id,
    o.numero_oc,
    l.numero_linha,
    o.fornecedor_id,
    coalesce(o.fornecedor_nome, '') as fornecedor,
    coalesce(o.categoria, '') as categoria_compra,
    o.data_emissao,
    o.status as status_oc,
    coalesce(o.technical_status, 'ABERTA') as technical_status,
    coalesce(o.financial_status, 'PENDENTE') as financial_status,
    l.status as status_linha,
    l.sku_id,
    l.sku_codigo as codigo,
    coalesce(s.descricao, l.descricao_original, '') as descricao,
    coalesce(l.unidade, s.unidade, '') as unidade,
    l.quantidade_pedida,
    l.quantidade_recebida,
    greatest(l.quantidade_pedida - l.quantidade_recebida, 0) as quantidade_pendente,
    l.valor_unitario_pedido,
    l.quantidade_pedida * l.valor_unitario_pedido as valor_pedido,
    greatest(l.quantidade_pedida - l.quantidade_recebida, 0) * l.valor_unitario_pedido as valor_pendente,
    coalesce(l.data_necessidade, o.data_necessidade) as data_necessidade,
    coalesce(l.data_necessidade, o.data_necessidade) - current_date as dias_para_remessa,
    case
        when coalesce(l.data_necessidade, o.data_necessidade) is null then 'SEM DATA'
        when coalesce(l.data_necessidade, o.data_necessidade) < current_date then 'ATRASADA'
        when coalesce(l.data_necessidade, o.data_necessidade) = current_date then 'VENCE HOJE'
        else 'A VENCER'
    end as situacao_transito,
    coalesce(nullif(trim(l.destino), ''), nullif(trim(o.destino), ''), '') as destino,
    l.work_order_id,
    w.numero_os,
    e.item_number as item,
    (
        o.status in ('EMITIDA', 'PARCIALMENTE_RECEBIDA')
        and coalesce(o.technical_status, 'ABERTA') <> 'CONCLUIDA'
        and l.status in ('PENDENTE', 'PARCIALMENTE_RECEBIDA')
        and greatest(l.quantidade_pedida - l.quantidade_recebida, 0) > 0
    ) as em_transito
from public.erp_purchase_order_lines l
join public.erp_purchase_orders o on o.id = l.purchase_order_id
left join public.skus s on s.id = l.sku_id
left join public.erp_work_orders w on w.id = l.work_order_id
left join public.erp_vehicle_entries e on e.id = w.vehicle_entry_id;

comment on view bi.fato_compras_transito is
    'Uma linha por item de O.C., com saldo fisico pendente e classificacao de prazo.';

create or replace view bi.fato_recebimentos_inspecao
with (security_barrier = true) as
select
    r.id as goods_receipt_id,
    l.id as goods_receipt_line_id,
    r.purchase_order_id,
    pol.id as purchase_order_line_id,
    o.numero_oc,
    r.data_recebimento,
    r.data_recebimento::date as data,
    r.origem,
    coalesce(r.fornecedor_nome, o.fornecedor_nome, '') as fornecedor,
    coalesce(r.numero_nf, '') as numero_nf,
    coalesce(r.operador, '') as operador,
    r.status as status_recebimento,
    r.confirmed_at,
    r.reversed_at,
    l.sku_id,
    l.sku_codigo as codigo,
    coalesce(s.descricao, pol.descricao_original, '') as descricao,
    coalesce(pol.unidade, s.unidade, '') as unidade,
    l.quantidade_esperada,
    l.quantidade_recebida_anterior,
    l.saldo_pendente,
    l.quantidade_fisica,
    l.quantidade_aprovada,
    l.quantidade_condicional,
    l.quantidade_rejeitada,
    coalesce(l.resultado_inspecao, '') as resultado_inspecao,
    coalesce(l.justificativa_divergencia, '') as justificativa_divergencia,
    coalesce(l.lote, '') as lote,
    coalesce(l.serie, '') as serie,
    l.validade,
    coalesce(l.localizacao, '') as localizacao,
    l.certificado_exigido,
    l.certificado_apresentado,
    l.validade_certificado,
    l.valor_unitario_pedido,
    l.valor_unitario_real
from public.erp_goods_receipt_lines l
join public.erp_goods_receipts r on r.id = l.goods_receipt_id
left join public.erp_purchase_order_lines pol on pol.id = l.purchase_order_line_id
left join public.erp_purchase_orders o on o.id = r.purchase_order_id
left join public.skus s on s.id = l.sku_id;

comment on view bi.fato_recebimentos_inspecao is
    'Recebimentos e resultados de inspecao por linha, sem gerar ou alterar saldo.';

create or replace view bi.fato_forecast
with (security_barrier = true) as
select
    f.id as forecast_id,
    f.numero_forecast,
    f.codigo as codigo_forecast,
    f.tipo_demanda,
    f.status,
    f.proposta_numero,
    coalesce(f.cliente_nome, '') as cliente,
    coalesce(f.vendedor, '') as vendedor,
    coalesce(f.mercado, '') as mercado,
    f.data_confirmacao,
    f.data_prevista_chegada,
    f.data_entrega_prevista,
    f.quantidade_planejada,
    f.probabilidade,
    f.quantidade_planejada * f.probabilidade / 100.0 as quantidade_ponderada,
    coalesce(f.tipo_servico, '') as tipo_servico,
    coalesce(f.tipo_veiculo, '') as tipo_veiculo,
    coalesce(f.linha, '') as linha,
    coalesce(f.transformacao_codigo, '') as transformacao_codigo,
    coalesce(f.transformacao, '') as transformacao,
    coalesce(f.produto_planejado_sku, '') as produto_planejado_sku,
    coalesce(f.produto_planejado_descricao, '') as produto_planejado_descricao,
    f.vehicle_entry_id,
    f.work_order_id,
    coalesce(x.itens, 0) as itens,
    coalesce(x.necessidades, 0) as necessidades,
    coalesce(x.necessidades, 0) > 0 as possui_estrutura_materiais,
    f.created_at,
    f.updated_at
from public.suprimentos_forecasts f
left join lateral (
    select
        (select count(*) from public.suprimentos_forecast_itens i where i.forecast_id = f.id) as itens,
        (select count(*) from public.suprimentos_forecast_necessidades n where n.forecast_id = f.id) as necessidades
) x on true;

comment on view bi.fato_forecast is
    'Forecast firme e preditivo; identifica registros ainda sem estrutura de materiais.';

create or replace view bi.fato_forecast_necessidades
with (security_barrier = true) as
select
    n.id as forecast_need_id,
    n.forecast_id,
    f.codigo as codigo_forecast,
    f.tipo_demanda,
    f.status as status_forecast,
    f.cliente_nome as cliente,
    f.data_prevista_chegada,
    f.data_entrega_prevista,
    f.probabilidade,
    n.forecast_item_id,
    n.sku_id,
    n.sku_codigo as codigo,
    n.descricao,
    n.unidade,
    n.quantidade_planejada,
    n.quantidade_planejada * f.probabilidade / 100.0 as quantidade_ponderada,
    n.nivel_maximo,
    n.origem,
    n.caminho_bom,
    n.created_at
from public.suprimentos_forecast_necessidades n
join public.suprimentos_forecasts f on f.id = n.forecast_id;

comment on view bi.fato_forecast_necessidades is
    'Necessidades de materiais explodidas dos Forecasts.';

create or replace view bi.fato_mrp
with (security_barrier = true) as
with necessidade_os as (
    select
        sku_id,
        codigo,
        sum(quantidade_pendente) as necessidade_os_pendente,
        count(distinct work_order_id) filter (where quantidade_pendente > 0) as ordens_pendentes
    from bi.fato_necessidades_os
    group by sku_id, codigo
), forecast as (
    select
        sku_id,
        codigo,
        sum(quantidade_planejada) filter (
            where status_forecast = 'ATIVO' and tipo_demanda = 'AGUARDANDO_CHEGADA'
        ) as forecast_firme,
        sum(quantidade_planejada) filter (
            where status_forecast = 'ATIVO' and tipo_demanda = 'PREVISAO_DEMANDA'
        ) as forecast_preditivo_bruto,
        sum(quantidade_ponderada) filter (
            where status_forecast = 'ATIVO' and tipo_demanda = 'PREVISAO_DEMANDA'
        ) as forecast_preditivo_ponderado
    from bi.fato_forecast_necessidades
    group by sku_id, codigo
), transito as (
    select
        sku_id,
        codigo,
        sum(quantidade_pendente) filter (where em_transito) as quantidade_transito,
        count(distinct purchase_order_id) filter (where em_transito) as ordens_compra_abertas,
        min(data_necessidade) filter (where em_transito) as proxima_remessa
    from bi.fato_compras_transito
    group by sku_id, codigo
)
select
    s.sku_id,
    s.codigo,
    s.descricao,
    s.unidade,
    s.grupo,
    s.categoria,
    s.localizacao,
    s.estoque_minimo,
    coalesce(e.estoque_atual, 0) as estoque_atual,
    coalesce(e.empenhado_total, 0) as empenhado_total,
    coalesce(e.empenhado_os, 0) as empenhado_os,
    coalesce(e.empenhado_fluxo, 0) as empenhado_fluxo,
    coalesce(e.estoque_disponivel, 0) as estoque_disponivel,
    coalesce(n.necessidade_os_pendente, 0) as necessidade_os_pendente,
    coalesce(f.forecast_firme, 0) as forecast_firme,
    coalesce(f.forecast_preditivo_bruto, 0) as forecast_preditivo_bruto,
    coalesce(f.forecast_preditivo_ponderado, 0) as forecast_preditivo_ponderado,
    (
        coalesce(n.necessidade_os_pendente, 0)
        + coalesce(f.forecast_firme, 0)
        + coalesce(f.forecast_preditivo_ponderado, 0)
    ) as necessidade_total,
    coalesce(t.quantidade_transito, 0) as quantidade_transito,
    (
        coalesce(e.estoque_disponivel, 0)
        + coalesce(t.quantidade_transito, 0)
        - coalesce(n.necessidade_os_pendente, 0)
        - coalesce(f.forecast_firme, 0)
        - coalesce(f.forecast_preditivo_ponderado, 0)
    ) as saldo_projetado,
    greatest(
        coalesce(n.necessidade_os_pendente, 0)
        + coalesce(f.forecast_firme, 0)
        + coalesce(f.forecast_preditivo_ponderado, 0)
        - coalesce(e.estoque_disponivel, 0)
        - coalesce(t.quantidade_transito, 0),
        0
    ) as necessidade_compra,
    greatest(
        coalesce(n.necessidade_os_pendente, 0)
        + coalesce(f.forecast_firme, 0)
        + coalesce(f.forecast_preditivo_ponderado, 0)
        + coalesce(s.estoque_minimo, 0)
        - coalesce(e.estoque_disponivel, 0)
        - coalesce(t.quantidade_transito, 0),
        0
    ) as necessidade_compra_com_estoque_minimo,
    coalesce(n.ordens_pendentes, 0) as ordens_pendentes,
    coalesce(t.ordens_compra_abertas, 0) as ordens_compra_abertas,
    t.proxima_remessa,
    case
        when greatest(
            coalesce(n.necessidade_os_pendente, 0)
            + coalesce(f.forecast_firme, 0)
            + coalesce(f.forecast_preditivo_ponderado, 0)
            - coalesce(e.estoque_disponivel, 0)
            - coalesce(t.quantidade_transito, 0), 0
        ) > 0 then 'COMPRAR'
        when coalesce(n.necessidade_os_pendente, 0)
           + coalesce(f.forecast_firme, 0)
           + coalesce(f.forecast_preditivo_ponderado, 0) > 0 then 'COBERTO'
        else 'SEM_DEMANDA'
    end as status_mrp,
    now() as atualizado_em
from bi.dim_sku s
left join bi.fato_estoque_atual e on e.sku_id = s.sku_id
left join necessidade_os n on n.sku_id = s.sku_id
left join forecast f on f.sku_id = s.sku_id
left join transito t on t.sku_id = s.sku_id;

comment on view bi.fato_mrp is
    'MRP I por SKU: necessidade pendente + Forecast versus estoque disponivel e transito.';

revoke all on all tables in schema bi from public;
revoke all on all tables in schema bi from anon;
revoke all on all tables in schema bi from authenticated;
grant select on all tables in schema bi to powerbi_reader;

alter default privileges in schema bi revoke all on tables from public;
alter default privileges in schema bi revoke all on tables from anon;
alter default privileges in schema bi revoke all on tables from authenticated;
alter default privileges in schema bi grant select on tables to powerbi_reader;
