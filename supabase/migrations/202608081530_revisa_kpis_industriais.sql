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

comment on view bi.dim_ordem_servico is
    'Dimensao de O.S. com categoria de servico e SLA tecnico de producao por linha.';

comment on view bi.fato_historico_conclusao is
    'Historico MES por veiculo/O.S., separando finalizacao, entrega e retirada e aplicando SLA tecnico por linha.';

comment on view bi.fato_necessidades_os is
    'Necessidade real por O.S. e SKU, com categoria de servico e flag de indisponibilidade para novo empenho.';
