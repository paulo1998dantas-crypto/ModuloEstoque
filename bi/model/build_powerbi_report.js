const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const reportRoot = path.resolve(
  __dirname,
  '..',
  'power-bi',
  'JI_Montadora_Operacional.Report',
  'definition',
  'pages'
);

if (!reportRoot.endsWith(path.join('JI_Montadora_Operacional.Report', 'definition', 'pages'))) {
  throw new Error(`Diretorio de destino inesperado: ${reportRoot}`);
}

const schemaBase = 'https://developer.microsoft.com/json-schemas/fabric/item/report/definition';
const id = (value) => crypto.createHash('sha1').update(value).digest('hex').slice(0, 20);
const filterId = (value) => `Filter${crypto.createHash('sha1').update(value).digest('hex').slice(0, 24)}`;

const COLORS = {
  ink: '#17212B',
  muted: '#667085',
  blue: '#1D4ED8',
  teal: '#0F766E',
  green: '#2E7D5B',
  amber: '#C6841A',
  red: '#C2413A',
  border: '#D8DEE8',
  canvas: '#F4F6F9',
  white: '#FFFFFF',
};

const literal = (value) => ({ expr: { Literal: { Value: value } } });
const stringLiteral = (value) => literal(`'${value.replaceAll("'", "''")}'`);
const colorLiteral = (value) => ({ solid: { color: stringLiteral(value) } });

const field = (entity, property, kind = 'Column') => ({
  [kind]: {
    Expression: { SourceRef: { Entity: entity } },
    Property: property,
  },
});

const projection = (spec) => ({
  field: field(spec.entity, spec.property, spec.kind || 'Column'),
  queryRef: `${spec.entity}.${spec.property}`,
  nativeQueryRef: spec.property,
});

const position = (x, y, width, height, tabOrder) => ({
  x,
  y,
  z: tabOrder,
  height,
  width,
  tabOrder,
});

function categoricalFilter(scope, entity, property, values) {
  const alias = `f${id(`${scope}:${entity}:${property}`).slice(0, 5)}`;
  return {
    name: filterId(`${scope}:filter:${entity}:${property}:${values.join('|')}`),
    field: field(entity, property),
    type: 'Categorical',
    filter: {
      Version: 2,
      From: [{ Name: alias, Entity: entity, Type: 0 }],
      Where: [{
        Condition: {
          In: {
            Expressions: [{ Column: { Expression: { SourceRef: { Source: alias } }, Property: property } }],
            Values: values.map((value) => [{ Literal: { Value: typeof value === 'boolean' ? String(value) : `'${String(value).replaceAll("'", "''")}'` } }]),
          },
        },
      }],
    },
    howCreated: 'User',
  };
}

const containerObjects = (title, options = {}) => ({
  title: [{
    properties: {
      show: literal(title ? 'true' : 'false'),
      ...(title ? {
        text: stringLiteral(title),
        fontFamily: stringLiteral('Segoe UI Semibold'),
        fontSize: literal('12D'),
        fontColor: colorLiteral(COLORS.ink),
      } : {}),
    },
  }],
  background: [{ properties: { show: literal('true'), color: colorLiteral(COLORS.white), transparency: literal('0D') } }],
  border: [{ properties: { show: literal('true'), color: colorLiteral(COLORS.border), radius: literal('8D'), width: literal('1D') } }],
  visualHeader: [{ properties: { show: literal('false') } }],
  padding: [{ properties: {
    top: literal(`${options.padding ?? 8}D`),
    bottom: literal(`${options.padding ?? 8}D`),
    left: literal(`${options.padding ?? 8}D`),
    right: literal(`${options.padding ?? 8}D`),
  } }],
});

function textbox(pageKey, key, title, subtitle, x, y, width, height, tabOrder) {
  const name = id(`${pageKey}:textbox:${key}`);
  const paragraphs = [{
    textRuns: [{
      value: title,
      textStyle: { fontFamily: 'Segoe UI Semibold', fontSize: '20px', color: COLORS.ink },
    }],
    horizontalTextAlignment: 'left',
  }];
  if (subtitle) {
    paragraphs.push({
      textRuns: [{
        value: subtitle,
        textStyle: { fontFamily: 'Segoe UI', fontSize: '10px', color: COLORS.muted },
      }],
      horizontalTextAlignment: 'left',
    });
  }
  return {
    name,
    json: {
      $schema: `${schemaBase}/visualContainer/2.11.0/schema.json`,
      name,
      position: position(x, y, width, height, tabOrder),
      visual: {
        visualType: 'textbox',
        objects: { general: [{ properties: { paragraphs } }] },
        visualContainerObjects: {
          background: [{ properties: { show: literal('false') } }],
          border: [{ properties: { show: literal('false') } }],
          visualHeader: [{ properties: { show: literal('false') } }],
          padding: [{ properties: { top: literal('0D'), bottom: literal('0D'), left: literal('0D'), right: literal('0D') } }],
        },
      },
    },
  };
}

function divider(pageKey, x, y, width, color, tabOrder) {
  const name = id(`${pageKey}:divider:${x}:${y}:${width}`);
  return {
    name,
    json: {
      $schema: `${schemaBase}/visualContainer/2.11.0/schema.json`,
      name,
      position: position(x, y, width, 4, tabOrder),
      visual: {
        visualType: 'shape',
        objects: {
          shape: [{ properties: { tileShape: stringLiteral('rectangle') } }],
          fill: [{ properties: { fillColor: colorLiteral(color), transparency: literal('0D') }, selector: { id: 'default' } }],
          outline: [{ properties: { show: literal('false') }, selector: { id: 'default' } }],
        },
        visualContainerObjects: {
          background: [{ properties: { show: literal('false') } }],
          border: [{ properties: { show: literal('false') } }],
          visualHeader: [{ properties: { show: literal('false') } }],
          padding: [{ properties: { top: literal('0D'), bottom: literal('0D'), left: literal('0D'), right: literal('0D') } }],
        },
      },
    },
  };
}

function slicer(pageKey, spec, x, tabOrder) {
  const name = id(`${pageKey}:slicer:${spec.entity}:${spec.property}`);
  return {
    name,
    json: {
      $schema: `${schemaBase}/visualContainer/2.11.0/schema.json`,
      name,
      position: position(x, 68, 200, 80, tabOrder),
      visual: {
        visualType: 'slicer',
        query: { queryState: { Values: { projections: [projection(spec)] } } },
        objects: {
          data: [{ properties: { mode: stringLiteral('Dropdown') } }],
          header: [{ properties: { show: literal('true'), text: stringLiteral(spec.label || spec.property) } }],
        },
        visualContainerObjects: containerObjects('', { padding: 0 }),
        drillFilterOtherVisuals: true,
      },
      filterConfig: {
        filters: [{
          name: filterId(`${pageKey}:slicer:${spec.entity}:${spec.property}`),
          field: field(spec.entity, spec.property),
          type: spec.type || 'Categorical',
        }],
      },
    },
  };
}

function kpiStrip(pageKey, measures, y, tabOrder) {
  const name = id(`${pageKey}:kpi-strip`);
  return {
    name,
    json: {
      $schema: `${schemaBase}/visualContainer/2.11.0/schema.json`,
      name,
      position: position(20, y, 1240, 132, tabOrder),
      visual: {
        visualType: 'cardVisual',
        query: {
          queryState: {
            Data: { projections: measures.map((property) => projection({ entity: 'fato_mrp', property, kind: 'Measure' })) },
          },
        },
        objects: {
          outline: [{ properties: { show: literal('false') }, selector: { id: 'default' } }],
          value: [{ properties: { fontSize: literal('20D'), bold: literal('true'), fontColor: colorLiteral(COLORS.ink) }, selector: { id: 'default' } }],
          label: [{ properties: { show: literal('true'), fontSize: literal('10D'), fontColor: colorLiteral(COLORS.muted) }, selector: { id: 'default' } }],
          cardCalloutArea: [{ properties: { show: literal('true'), paddingUniform: literal('6L'), rectangleRoundedCurve: literal('6L'), backgroundTransparency: literal('100D') } }],
          layout: [{ properties: { style: stringLiteral('Table'), customizeLines: literal('true'), gridlineWidth: literal('1D'), gridlineColor: colorLiteral(COLORS.border), gridlineTransparency: literal('0D') }, selector: { id: 'default' } }],
          padding: [{ properties: { paddingUniform: literal('4L') }, selector: { id: 'default' } }],
          spacing: [{ properties: { verticalSpacing: literal('0D') }, selector: { id: 'default' } }],
        },
        visualContainerObjects: containerObjects('', { padding: 4 }),
        drillFilterOtherVisuals: true,
      },
    },
  };
}

function barChart(pageKey, key, title, category, measure, x, y, width, height, color, tabOrder) {
  const name = id(`${pageKey}:chart:${key}`);
  return {
    name,
    json: {
      $schema: `${schemaBase}/visualContainer/2.11.0/schema.json`,
      name,
      position: position(x, y, width, height, tabOrder),
      visual: {
        visualType: 'clusteredBarChart',
        query: {
          queryState: {
            Category: { projections: [{ ...projection(category), active: true }] },
            Y: { projections: [projection({ entity: 'fato_mrp', property: measure, kind: 'Measure' })] },
          },
          sortDefinition: {
            sort: [{ field: field('fato_mrp', measure, 'Measure'), direction: 'Descending' }],
            isDefaultSort: true,
          },
        },
        objects: {
          dataPoint: [{ properties: { fill: colorLiteral(color), fillTransparency: literal('0D') } }],
          labels: [{ properties: { show: literal('true'), labelOverflow: literal('true'), optimizeLabelDisplay: literal('true') } }],
          categoryAxis: [{ properties: { labelColor: colorLiteral(COLORS.muted), titleText: stringLiteral('') } }],
          valueAxis: [{ properties: { start: literal('0D'), labelColor: colorLiteral(COLORS.muted), gridlineColor: colorLiteral('#E7EBF1'), gridlineStyle: stringLiteral('solid'), gridlineThickness: literal('1D') } }],
        },
        visualContainerObjects: containerObjects(title, { padding: 8 }),
        drillFilterOtherVisuals: true,
      },
    },
  };
}

function table(pageKey, key, title, columns, x, y, width, height, tabOrder, sort, filters = []) {
  const name = id(`${pageKey}:table:${key}`);
  const query = {
    queryState: { Values: { projections: columns.map(projection) } },
  };
  if (sort) {
    query.sortDefinition = {
      sort: [{ field: field(sort.entity, sort.property, sort.kind || 'Column'), direction: sort.direction || 'Ascending' }],
      isDefaultSort: true,
    };
  }
  return {
    name,
    json: {
      $schema: `${schemaBase}/visualContainer/2.11.0/schema.json`,
      name,
      position: position(x, y, width, height, tabOrder),
      visual: {
        visualType: 'tableEx',
        query,
        objects: {
          columnHeaders: [{ properties: {
            columnAdjustment: stringLiteral('growToFit'),
            autoSizeColumnWidth: literal('true'),
            backColor: colorLiteral('#EEF2F7'),
            fontColor: colorLiteral(COLORS.ink),
          } }],
          values: [{ properties: {
            backColorPrimary: colorLiteral(COLORS.white),
            backColorSecondary: colorLiteral('#F8FAFC'),
            fontColorPrimary: colorLiteral(COLORS.ink),
            fontColorSecondary: colorLiteral(COLORS.ink),
          } }],
          total: [{ properties: { totals: literal('false') } }],
        },
        visualContainerObjects: {
          ...containerObjects(title, { padding: 6 }),
          stylePreset: [{ properties: { name: stringLiteral('None') } }],
        },
        drillFilterOtherVisuals: true,
      },
      ...(filters.length ? { filterConfig: { filters: filters.map((spec) => categoricalFilter(`${pageKey}:${key}`, spec.entity, spec.property, spec.values)) } } : {}),
    },
  };
}

const col = (entity, property) => ({ entity, property, kind: 'Column' });
const measure = (property) => ({ entity: 'fato_mrp', property, kind: 'Measure' });

const pageSpecs = [
  {
    key: 'cockpit', displayName: '0. Cockpit Industrial', accent: COLORS.red,
    title: 'Cockpit Industrial', subtitle: 'Riscos e decisões imediatas | atualização no refresh do modelo | modo somente leitura',
    slicers: [{ ...col('dim_ordem_servico', 'cliente'), label: 'Cliente' }, { ...col('dim_sku', 'grupo'), label: 'Grupo de material' }],
    measures: ['O.S. no WIP', 'O.S. Atrasadas', 'O.S. com Material Pendente', 'SKUs Críticos', 'SKUs a Comprar', 'Linhas Atrasadas'],
    charts: [
      { key: 'os_atrasadas_cliente', title: 'O.S. atrasadas por cliente', category: col('dim_ordem_servico', 'cliente'), measure: 'O.S. Atrasadas', color: COLORS.red },
      { key: 'status_mrp', title: 'Cobertura do MRP — SKUs com demanda', category: col('fato_mrp', 'status_mrp'), measure: 'SKUs MRP em Demanda', color: COLORS.blue },
    ],
    table: {
      title: 'Ações prioritárias — materiais',
      columns: [col('dim_sku', 'codigo'), col('dim_sku', 'descricao'), col('dim_sku', 'unidade'), measure('Prioridade MRP'), measure('Próxima Necessidade'), measure('O.S. Impactadas pelo SKU'), measure('Necessidade Compra (U.M.)')],
      sort: { entity: 'fato_mrp', property: 'Ordem Prioridade MRP', kind: 'Measure', direction: 'Ascending' },
      filters: [{ entity: 'fato_mrp', property: 'status_mrp', values: ['COMPRAR'] }],
    },
  },
  {
    key: 'estoque', displayName: '1. Visão Estoque', accent: COLORS.teal,
    title: 'Visão Estoque', subtitle: 'Saúde do portfólio, disponibilidade e exceções | quantidades físicas somente por U.M. única',
    slicers: [{ ...col('dim_sku', 'unidade'), label: 'Unidade de medida' }, { ...col('dim_sku', 'grupo'), label: 'Grupo' }],
    measures: ['SKUs Ativos', 'SKUs com Saldo', 'SKUs Zerados', 'SKUs em Risco Estoque', 'SKUs Empenhados', 'Movimentações Ativas', 'Baixas Registradas'],
    charts: [
      { key: 'status_estoque', title: 'Distribuição por status de estoque', category: col('fato_estoque_atual', 'status_estoque'), measure: 'SKUs Ativos', color: COLORS.teal },
      { key: 'movimentos_tipo', title: 'Movimentações ativas por tipo', category: col('fato_movimentacoes_estoque', 'tipo'), measure: 'Movimentações Ativas', color: COLORS.blue },
    ],
    table: {
      title: 'Ações de estoque — somente exceções ativas',
      columns: [col('dim_sku', 'codigo'), col('dim_sku', 'descricao'), col('dim_sku', 'unidade'), col('dim_sku', 'grupo'), measure('Status Estoque Ação'), measure('Estoque Atual (U.M.)'), measure('Empenhado Total (U.M.)'), measure('Estoque Disponível (U.M.)')],
      sort: { entity: 'fato_mrp', property: 'Estoque Disponível (U.M.)', kind: 'Measure', direction: 'Ascending' },
      filters: [
        { entity: 'fato_estoque_atual', property: 'sku_ativo', values: [true] },
        { entity: 'fato_estoque_atual', property: 'status_estoque', values: ['ZERADO', 'BAIXO', 'SALDO_COMPROMETIDO'] },
      ],
    },
  },
  {
    key: 'pcp', displayName: '2. Visão PCP', accent: COLORS.amber,
    title: 'Visão PCP', subtitle: 'WIP, prazo, avanço e disponibilidade de materiais por O.S.',
    slicers: [{ ...col('dim_ordem_servico', 'cliente'), label: 'Cliente' }, { ...col('dim_ordem_servico', 'linha'), label: 'Linha' }],
    measures: ['O.S. no WIP', 'O.S. Atrasadas', '% O.S. Atrasadas', 'O.S. com Material Pendente', 'Avanço Médio %', 'Forecasts Ativos'],
    charts: [
      { key: 'wip_cliente', title: 'WIP por cliente', category: col('dim_ordem_servico', 'cliente'), measure: 'O.S. no WIP', color: COLORS.blue },
      { key: 'avanco_cliente', title: 'Avanço médio por cliente', category: col('dim_ordem_servico', 'cliente'), measure: 'Avanço Médio %', color: COLORS.green },
    ],
    table: {
      title: 'Fila de ação — O.S. em WIP',
      columns: [col('dim_ordem_servico', 'numero_os'), col('dim_ordem_servico', 'cliente'), col('dim_ordem_servico', 'modelo'), col('dim_ordem_servico', 'data_entrega_vigente'), col('dim_ordem_servico', 'percentual_avanco'), col('dim_ordem_servico', 'dias_no_wip'), measure('Risco O.S.'), measure('Linhas Material Pendente O.S.'), measure('Dias para Entrega O.S.')],
      sort: { entity: 'fato_mrp', property: 'Dias para Entrega O.S.', kind: 'Measure', direction: 'Ascending' },
      filters: [{ entity: 'dim_ordem_servico', property: 'em_wip', values: [true] }],
    },
  },
  {
    key: 'compras', displayName: '3. Compras e Trânsito', accent: COLORS.blue,
    title: 'Compras e Materiais em Trânsito', subtitle: 'Prazo, exposição financeira, recebimentos e inspeção',
    slicers: [{ ...col('fato_compras_transito', 'fornecedor'), label: 'Fornecedor' }, { ...col('fato_compras_transito', 'situacao_transito'), label: 'Situação' }],
    measures: ['Linhas em Trânsito', 'Valor em Trânsito', 'Linhas Atrasadas', 'Linhas sem Data', 'Linhas Data Inválida', 'Taxa Aprovação Média %'],
    charts: [
      { key: 'atrasos_fornecedor', title: 'Linhas atrasadas por fornecedor', category: col('fato_compras_transito', 'fornecedor'), measure: 'Linhas Atrasadas', color: COLORS.red },
      { key: 'valor_fornecedor', title: 'Valor em trânsito por fornecedor', category: col('fato_compras_transito', 'fornecedor'), measure: 'Valor em Trânsito', color: COLORS.blue },
    ],
    table: {
      title: 'Ações de compras — atraso ou ausência de data',
      columns: [col('fato_compras_transito', 'numero_oc'), col('fato_compras_transito', 'numero_linha'), col('fato_compras_transito', 'fornecedor'), col('fato_compras_transito', 'codigo'), col('fato_compras_transito', 'unidade'), col('fato_compras_transito', 'data_necessidade'), measure('Dias para Remessa Válido'), measure('Ação Compra'), measure('Valor Linha em Trânsito')],
      sort: { entity: 'fato_mrp', property: 'Dias para Remessa Válido', kind: 'Measure', direction: 'Ascending' },
      filters: [
        { entity: 'fato_compras_transito', property: 'em_transito', values: [true] },
        { entity: 'fato_compras_transito', property: 'situacao_transito', values: ['ATRASADA', 'SEM DATA'] },
      ],
    },
  },
  {
    key: 'mrp', displayName: '4. Visão MRP', accent: COLORS.blue,
    title: 'Visão MRP I', subtitle: 'Necessidade real versus estoque e trânsito | decisão por SKU e U.M.',
    slicers: [{ ...col('dim_sku', 'unidade'), label: 'Unidade de medida' }, { ...col('dim_sku', 'grupo'), label: 'Grupo' }],
    measures: ['SKUs com Demanda', 'SKUs Cobertos', '% SKUs MRP Cobertos', 'SKUs a Comprar', 'SKUs Críticos', 'O.S. Impactadas por Compra'],
    charts: [
      { key: 'cobertura_status', title: 'Cobertura dos SKUs com demanda', category: col('fato_mrp', 'status_mrp'), measure: 'SKUs MRP em Demanda', color: COLORS.blue },
      { key: 'comprar_grupo', title: 'SKUs a comprar por grupo', category: col('dim_sku', 'grupo'), measure: 'SKUs a Comprar', color: COLORS.amber },
    ],
    table: {
      title: 'Prioridade de materiais — linha operacional do MRP',
      columns: [col('dim_sku', 'codigo'), col('dim_sku', 'descricao'), col('dim_sku', 'unidade'), measure('Prioridade MRP'), measure('Próxima Necessidade'), measure('O.S. Impactadas pelo SKU'), measure('Necessidade Total (U.M.)'), measure('Estoque Disponível MRP (U.M.)'), measure('Em Trânsito (U.M.)'), measure('Necessidade Compra (U.M.)')],
      sort: { entity: 'fato_mrp', property: 'Ordem Prioridade MRP', kind: 'Measure', direction: 'Ascending' },
      filters: [{ entity: 'fato_mrp', property: 'status_mrp', values: ['COMPRAR'] }],
    },
  },
];

const drillPages = [
  {
    key: 'detalhe_sku', displayName: 'D. Detalhe SKU', title: 'Detalhe do SKU', subtitle: 'Drill-through consultivo: estoque, empenho, necessidade, trânsito e compra', accent: COLORS.teal,
    drill: col('dim_sku', 'codigo'),
    measures: ['Estoque Atual (U.M.)', 'Empenhado Total (U.M.)', 'Estoque Disponível (U.M.)', 'Necessidade Total (U.M.)', 'Em Trânsito (U.M.)', 'Necessidade Compra (U.M.)'],
    tables: [
      { key: 'sku_resumo', title: 'Resumo do material', y: 220, height: 190, columns: [col('dim_sku', 'codigo'), col('dim_sku', 'descricao'), col('dim_sku', 'unidade'), col('dim_sku', 'grupo'), col('fato_estoque_atual', 'status_estoque'), measure('Prioridade MRP'), measure('Próxima Necessidade')] },
      { key: 'sku_necessidades', title: 'O.S. que demandam o material', y: 422, height: 282, columns: [col('fato_necessidades_os', 'numero_os'), col('fato_necessidades_os', 'cliente'), col('fato_necessidades_os', 'setor'), col('fato_necessidades_os', 'data_entrega_vigente'), col('fato_necessidades_os', 'quantidade_necessaria'), col('fato_necessidades_os', 'quantidade_coberta'), col('fato_necessidades_os', 'quantidade_pendente'), col('fato_necessidades_os', 'status_necessidade')] },
    ],
  },
  {
    key: 'detalhe_os', displayName: 'D. Detalhe O.S.', title: 'Detalhe da O.S.', subtitle: 'Drill-through consultivo: prazo, avanço e necessidade real de materiais', accent: COLORS.amber,
    drill: col('dim_ordem_servico', 'numero_os'),
    measures: ['O.S. no WIP', 'Avanço Médio %', 'Linhas Material Pendente O.S.', 'Dias para Entrega O.S.'],
    tables: [
      { key: 'os_resumo', title: 'Resumo operacional', y: 220, height: 190, columns: [col('dim_ordem_servico', 'numero_os'), col('dim_ordem_servico', 'cliente'), col('dim_ordem_servico', 'modelo'), col('dim_ordem_servico', 'fase_wip'), col('dim_ordem_servico', 'data_entrega_vigente'), col('dim_ordem_servico', 'percentual_avanco'), measure('Risco O.S.')] },
      { key: 'os_necessidades', title: 'Necessidades de material da O.S.', y: 422, height: 282, columns: [col('fato_necessidades_os', 'codigo'), col('fato_necessidades_os', 'descricao'), col('fato_necessidades_os', 'unidade'), col('fato_necessidades_os', 'setor'), col('fato_necessidades_os', 'quantidade_necessaria'), col('fato_necessidades_os', 'quantidade_coberta'), col('fato_necessidades_os', 'quantidade_pendente'), col('fato_necessidades_os', 'status_necessidade')] },
    ],
  },
  {
    key: 'detalhe_fornecedor', displayName: 'D. Detalhe Fornecedor', title: 'Detalhe do Fornecedor', subtitle: 'Drill-through consultivo sobre linhas de compra e risco de prazo', accent: COLORS.blue,
    drill: col('fato_compras_transito', 'fornecedor'),
    measures: ['Linhas em Trânsito', 'Valor em Trânsito', 'Linhas Atrasadas', '% Linhas Atrasadas', 'Linhas sem Data', 'O.C. Atrasadas'],
    tables: [
      { key: 'fornecedor_compras', title: 'Linhas de compra do fornecedor', y: 220, height: 484, columns: [col('fato_compras_transito', 'numero_oc'), col('fato_compras_transito', 'codigo'), col('fato_compras_transito', 'descricao'), col('fato_compras_transito', 'unidade'), col('fato_compras_transito', 'quantidade_pendente'), col('fato_compras_transito', 'valor_pendente'), col('fato_compras_transito', 'data_necessidade'), col('fato_compras_transito', 'situacao_transito'), measure('Ação Compra')] },
    ],
  },
];

for (const entry of fs.readdirSync(reportRoot, { withFileTypes: true })) {
  if (entry.isDirectory()) fs.rmSync(path.join(reportRoot, entry.name), { recursive: true, force: true });
}

const pageOrder = [];
let visualCount = 0;

function writePage(pageSpec, isDrill = false) {
  const pageName = id(`page:${pageSpec.key}`);
  pageOrder.push(pageName);
  const pageDir = path.join(reportRoot, pageName);
  const visualsDir = path.join(pageDir, 'visuals');
  fs.mkdirSync(visualsDir, { recursive: true });

  const page = {
    $schema: `${schemaBase}/page/2.1.0/schema.json`,
    name: pageName,
    displayName: pageSpec.displayName,
    displayOption: 'FitToPage',
    height: 720,
    width: 1280,
    objects: {
      background: [{ properties: { color: colorLiteral(COLORS.canvas), transparency: literal('0D') } }],
      outspace: [{ properties: { color: colorLiteral('#E8ECF2'), transparency: literal('0D') } }],
    },
  };

  if (isDrill) {
    page.visibility = 'HiddenInViewMode';
    const drillName = filterId(`${pageSpec.key}:drill:${pageSpec.drill.entity}:${pageSpec.drill.property}`);
    page.filterConfig = { filters: [{ name: drillName, field: field(pageSpec.drill.entity, pageSpec.drill.property), type: 'Categorical', howCreated: 'Drillthrough' }] };
    page.pageBinding = {
      name: 'Pod',
      type: 'Drillthrough',
      parameters: [{ name: `Param_${drillName}`, boundFilter: drillName, fieldExpr: field(pageSpec.drill.entity, pageSpec.drill.property) }],
    };
  }

  fs.writeFileSync(path.join(pageDir, 'page.json'), `${JSON.stringify(page, null, 2)}\n`, 'utf8');

  const visuals = [
    textbox(pageSpec.key, 'header', pageSpec.title, pageSpec.subtitle, 20, 8, 820, 50, 0),
    divider(pageSpec.key, 20, 58, 1240, pageSpec.accent, 1),
  ];

  if (!isDrill) {
    pageSpec.slicers.forEach((spec, index) => visuals.push(slicer(pageSpec.key, spec, 848 + index * 212, 2 + index)));
    visuals.push(kpiStrip(pageSpec.key, pageSpec.measures, 156, 4));
    pageSpec.charts.forEach((chart, index) => visuals.push(barChart(
      pageSpec.key,
      chart.key,
      chart.title,
      chart.category,
      chart.measure,
      index === 0 ? 20 : 648,
      300,
      612,
      188,
      chart.color,
      5 + index
    )));
    visuals.push(table(pageSpec.key, 'actions', pageSpec.table.title, pageSpec.table.columns, 20, 500, 1240, 204, 7, pageSpec.table.sort, pageSpec.table.filters || []));
  } else {
    visuals.push(kpiStrip(pageSpec.key, pageSpec.measures, 76, 2));
    pageSpec.tables.forEach((spec, index) => visuals.push(table(pageSpec.key, spec.key, spec.title, spec.columns, 20, spec.y, 1240, spec.height, 3 + index, spec.sort)));
  }

  for (const visual of visuals) {
    const visualDir = path.join(visualsDir, visual.name);
    fs.mkdirSync(visualDir, { recursive: true });
    fs.writeFileSync(path.join(visualDir, 'visual.json'), `${JSON.stringify(visual.json, null, 2)}\n`, 'utf8');
  }
  visualCount += visuals.length;
}

pageSpecs.forEach((page) => writePage(page, false));
drillPages.forEach((page) => writePage(page, true));

const pagesMetadata = {
  $schema: `${schemaBase}/pagesMetadata/1.1.0/schema.json`,
  pageOrder,
  activePageName: pageOrder[0],
};
fs.writeFileSync(path.join(reportRoot, 'pages.json'), `${JSON.stringify(pagesMetadata, null, 2)}\n`, 'utf8');

// O Power BI Desktop pode remover o schema ao salvar o projeto. O gerador o
// restaura para que o PBIR continue publicável e validável pelo CLI oficial.
const definitionPbir = {
  $schema: 'https://developer.microsoft.com/json-schemas/fabric/item/report/definitionProperties/2.0.0/schema.json',
  version: '4.0',
  datasetReference: {
    byPath: {
      path: '../JI_Montadora_Operacional.SemanticModel',
    },
  },
};
fs.writeFileSync(
  path.join(path.dirname(path.dirname(reportRoot)), 'definition.pbir'),
  `${JSON.stringify(definitionPbir, null, 2)}\n`,
  'utf8'
);

console.log(`REPORT_OK pages=${pageOrder.length} visible=${pageSpecs.length} drillthrough=${drillPages.length} visuals=${visualCount}`);
