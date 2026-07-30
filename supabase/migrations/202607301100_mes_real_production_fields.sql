-- Campos operacionais preservados no relatório diário real do MES.
-- Migração estritamente aditiva: não altera registros, saldos ou movimentos.

begin;
set local lock_timeout = 5000;
set local statement_timeout = 60000;

alter table if exists public.erp_work_orders
    add column if not exists data_comercial_calculada date null,
    add column if not exists modelo_configuracao text not null default '',
    add column if not exists info text not null default '',
    add column if not exists bo text not null default '',
    add column if not exists observacoes_controle_producao text not null default '',
    add column if not exists observacoes_gerais text not null default '',
    add column if not exists sequenciamento_legacy text not null default '',
    add column if not exists pedido_compras_legacy text not null default '',
    add column if not exists numero_sequencia_legacy text not null default '';

comment on column public.erp_work_orders.data_comercial_calculada is
    'Prazo comercial calculado a partir da maior data entre chegada e aprovação, conforme parametrização da linha.';
comment on column public.erp_work_orders.modelo_configuracao is
    'Configuração comercial legada (por exemplo PACK); não substitui marca/modelo/versão do veículo.';
comment on column public.erp_work_orders.pedido_compras_legacy is
    'Referência textual histórica. Novos vínculos de compras usam erp_purchase_order_lines.work_order_id.';
comment on column public.erp_work_orders.numero_sequencia_legacy is
    'Número de sequência explicitamente informado na origem; nunca inferido pela posição da linha.';

commit;
