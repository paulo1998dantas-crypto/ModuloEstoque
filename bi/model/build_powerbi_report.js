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
const LOGO_RESOURCE = 'JIMontadoraLogoGradient-202608081845.png';

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

function slicer(pageKey, spec, x, tabOrder, width = 200) {
  const name = id(`${pageKey}:slicer:${spec.entity}:${spec.property}`);
  return {
    name,
    json: {
      $schema: `${schemaBase}/visualContainer/2.11.0/schema.json`,
      name,
      position: position(x, 68, width, 80, tabOrder),
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

function imageVisual(pageKey, key, resourceName, x, y, width, height, tabOrder) {
  const name = id(`${pageKey}:image:${key}`);
  return {
    name,
    json: {
      $schema: `${schemaBase}/visualContainer/2.11.0/schema.json`,
      name,
      position: position(x, y, width, height, tabOrder),
      visual: {
        visualType: 'image',
        objects: {
          general: [{
            properties: {
              imageUrl: {
                expr: {
                  ResourcePackageItem: {
                    PackageName: 'RegisteredResources',
                    PackageType: 1,
                    ItemName: resourceName,
                  },
                },
              },
            },
          }],
        },
        visualContainerObjects: {
          background: [{ properties: { show: literal('false') } }],
          border: [{ properties: { show: literal('false') } }],
          visualHeader: [{ properties: { show: literal('false') } }],
        },
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

function lineChart(pageKey, key, title, category, measures, x, y, width, height, tabOrder) {
  const name = id(`${pageKey}:chart:${key}`);
  return {
    name,
    json: {
      $schema: `${schemaBase}/visualContainer/2.11.0/schema.json`,
      name,
      position: position(x, y, width, height, tabOrder),
      visual: {
        visualType: 'lineChart',
        query: {
          queryState: {
            Category: { projections: [{ ...projection(category), active: true }] },
            Y: { projections: measures.map((property) => projection({ entity: 'fato_mrp', property, kind: 'Measure' })) },
          },
          sortDefinition: {
            sort: [{ field: field(category.entity, category.property, category.kind || 'Column'), direction: 'Ascending' }],
            isDefaultSort: true,
          },
        },
        objects: {
          labels: [{ properties: { show: literal('true'), optimizeLabelDisplay: literal('true') } }],
          legend: [{ properties: { show: literal('true'), position: stringLiteral('Top') } }],
          categoryAxis: [{ properties: { labelColor: colorLiteral(COLORS.muted), titleText: stringLiteral('') } }],
          valueAxis: [{ properties: { start: literal('0D'), labelColor: colorLiteral(COLORS.muted), gridlineColor: colorLiteral('#E7EBF1'), gridlineStyle: stringLiteral('solid'), gridlineThickness: literal('1D') } }],
        },
        visualContainerObjects: containerObjects(title, { padding: 8 }),
        drillFilterOtherVisuals: true,
      },
    },
  };
}

function stackedColumnChart(pageKey, key, title, category, legend, measureName, x, y, width, height, tabOrder) {
  const name = id(`${pageKey}:stacked:${key}`);
  return {
    name,
    json: {
      $schema: `${schemaBase}/visualContainer/2.11.0/schema.json`,
      name,
      position: position(x, y, width, height, tabOrder),
      visual: {
        visualType: 'clusteredColumnChart',
        query: {
          queryState: {
            Category: { projections: [{ ...projection(category), active: true }] },
            Series: { projections: [{ ...projection(legend), active: true }] },
            Y: { projections: [projection({ entity: 'fato_mrp', property: measureName, kind: 'Measure' })] },
          },
          sortDefinition: { sort: [{ field: field(category.entity, category.property, category.kind || 'Column'), direction: 'Ascending' }], isDefaultSort: true },
        },
        objects: {
          labels: [{ properties: { show: literal('true'), optimizeLabelDisplay: literal('true') } }],
          legend: [{ properties: { show: literal('true'), position: stringLiteral('Top') } }],
          categoryAxis: [{ properties: { labelColor: colorLiteral(COLORS.muted), titleText: stringLiteral('') } }],
          valueAxis: [{ properties: { start: literal('0D'), labelColor: colorLiteral(COLORS.muted), gridlineColor: colorLiteral('#E7EBF1'), gridlineStyle: stringLiteral('solid'), gridlineThickness: literal('1D') } }],
        },
        visualContainerObjects: containerObjects(title, { padding: 8 }),
        drillFilterOtherVisuals: true,
      },
    },
  };
}

function donutChart(pageKey, key, title, category, measureName, x, y, width, height, tabOrder) {
  const name = id(`${pageKey}:donut:${key}`);
  return {
    name,
    json: {
      $schema: `${schemaBase}/visualContainer/2.11.0/schema.json`,
      name,
      position: position(x, y, width, height, tabOrder),
      visual: {
        visualType: 'donutChart',
        query: {
          queryState: {
            Category: { projections: [{ ...projection(category), active: true }] },
            Y: { projections: [projection({ entity: 'fato_mrp', property: measureName, kind: 'Measure' })] },
          },
          sortDefinition: { sort: [{ field: field('fato_mrp', measureName, 'Measure'), direction: 'Descending' }], isDefaultSort: true },
        },
        objects: {
          legend: [{ properties: { show: literal('true'), position: stringLiteral('RightCenter') } }],
          labels: [{ properties: { show: literal('true'), labelStyle: stringLiteral('Category, percent of total') } }],
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
    slicers: [{ ...col('dim_ordem_servico', 'categoria_servico'), label: 'Tipo de serviço' }, { ...col('dim_ordem_servico', 'cliente'), label: 'Cliente' }, { ...col('dim_sku', 'categoria'), label: 'Categoria de material' }],
    measures: ['O.S. no WIP', 'O.S. Atrasadas', 'O.S. com Material Pendente', 'SKUs Críticos', 'SKUs a Comprar', 'Linhas Atrasadas'],
    charts: [
      { key: 'os_atrasadas_cliente', title: 'O.S. atrasadas por cliente', category: col('dim_ordem_servico', 'cliente'), measure: 'O.S. Atrasadas', color: COLORS.red },
      { key: 'skus_criticos_categoria', title: 'SKUs críticos por categoria', category: col('dim_sku', 'categoria'), measure: 'SKUs Críticos', color: COLORS.blue },
    ],
    table: {
      title: 'Ações prioritárias — materiais',
      columns: [col('dim_sku', 'codigo'), col('dim_sku', 'descricao'), col('dim_sku', 'unidade'), measure('Prioridade MRP'), measure('Próxima Necessidade'), measure('O.S. Impactadas pelo SKU'), measure('Necessidade Compra (U.M.)')],
      sort: { entity: 'fato_mrp', property: 'Ordem Prioridade MRP', kind: 'Measure', direction: 'Ascending' },
      filters: [{ entity: 'fato_necessidades_os', property: 'sem_estoque_disponivel', values: [true] }],
    },
  },
  {
    key: 'estoque', displayName: '1. Visão Estoque', accent: COLORS.teal,
    title: 'Visão Estoque', subtitle: 'Saúde do portfólio, disponibilidade e exceções | quantidades físicas somente por U.M. única',
    slicers: [{ ...col('dim_ordem_servico', 'categoria_servico'), label: 'Tipo de serviço' }, { ...col('dim_sku', 'unidade'), label: 'Unidade de medida' }, { ...col('dim_sku', 'categoria'), label: 'Categoria de material' }],
    measures: ['SKUs Ativos', 'SKUs com Saldo', 'SKUs Zerados', 'SKUs em Risco Estoque', 'SKUs Empenhados', 'Movimentações Ativas', 'Baixas Registradas'],
    charts: [
      { key: 'status_estoque', title: 'Distribuição por status de estoque', category: col('fato_estoque_atual', 'status_estoque'), measure: 'SKUs Ativos', color: COLORS.teal },
      { key: 'movimentos_tipo', title: 'Movimentações ativas por tipo', category: col('fato_movimentacoes_estoque', 'tipo'), measure: 'Movimentações Ativas', color: COLORS.blue },
    ],
    table: {
      title: 'Ações de estoque — somente exceções ativas',
      columns: [col('dim_sku', 'codigo'), col('dim_sku', 'descricao'), col('dim_sku', 'unidade'), col('dim_sku', 'categoria'), measure('Status Estoque Ação'), measure('Estoque Atual (U.M.)'), measure('Empenhado Total (U.M.)'), measure('Estoque Disponível (U.M.)')],
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
    slicers: [{ ...col('dim_ordem_servico', 'categoria_servico'), label: 'Tipo de serviço' }, { ...col('dim_ordem_servico', 'cliente'), label: 'Cliente' }, { ...col('dim_ordem_servico', 'linha_produto'), label: 'Linha' }],
    measures: ['O.S. no WIP', 'O.S. Atrasadas', '% O.S. Atrasadas', 'O.S. com Material Pendente', 'Avanço Médio %', 'Forecasts Ativos'],
    charts: [
      { key: 'wip_cliente', title: 'WIP por cliente', category: col('dim_ordem_servico', 'cliente'), measure: 'O.S. no WIP', color: COLORS.blue },
      { key: 'avanco_cliente', title: 'Avanço médio por cliente', category: col('dim_ordem_servico', 'cliente'), measure: 'Avanço Médio %', color: COLORS.green },
    ],
    table: {
      title: 'Fila de ação — O.S. em WIP',
      columns: [col('dim_ordem_servico', 'numero_os'), col('dim_ordem_servico', 'cliente'), col('dim_ordem_servico', 'linha_produto'), col('dim_ordem_servico', 'data_inicio_producao'), col('dim_ordem_servico', 'prazo_producao_dias'), col('dim_ordem_servico', 'data_limite_producao'), col('dim_ordem_servico', 'percentual_avanco'), col('dim_ordem_servico', 'dias_atraso_producao'), measure('Risco O.S.'), measure('Linhas Material Pendente O.S.')],
      sort: { entity: 'fato_mrp', property: 'Dias para Entrega O.S.', kind: 'Measure', direction: 'Ascending' },
      filters: [{ entity: 'dim_ordem_servico', property: 'em_wip', values: [true] }],
    },
  },
  {
    key: 'compras', displayName: '3. Compras e Trânsito', accent: COLORS.blue,
    title: 'Compras e Materiais em Trânsito', subtitle: 'Prazo, exposição financeira, recebimentos e inspeção',
    slicers: [{ ...col('dim_ordem_servico', 'categoria_servico'), label: 'Tipo de serviço' }, { ...col('fato_compras_transito', 'fornecedor'), label: 'Fornecedor' }, { ...col('fato_compras_transito', 'situacao_transito'), label: 'Situação' }],
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
    slicers: [{ ...col('dim_ordem_servico', 'categoria_servico'), label: 'Tipo de serviço' }, { ...col('dim_sku', 'unidade'), label: 'Unidade de medida' }, { ...col('dim_sku', 'categoria'), label: 'Categoria de material' }],
    measures: ['SKUs com Demanda', 'SKUs em Risco Estoque', 'SKUs Críticos', 'SKUs a Comprar', 'O.S. com Material Pendente', 'O.S. Impactadas por Compra'],
    charts: [
      { key: 'criticos_categoria', title: 'SKUs críticos por categoria', category: col('dim_sku', 'categoria'), measure: 'SKUs Críticos', color: COLORS.red },
      { key: 'os_compras_linha', title: 'O.S. impactadas por compras por linha', category: col('dim_ordem_servico', 'linha_produto'), measure: 'O.S. Impactadas por Compra', color: COLORS.amber },
    ],
    table: {
      title: 'Prioridade de materiais — linha operacional do MRP',
      columns: [col('dim_sku', 'codigo'), col('dim_sku', 'descricao'), col('dim_sku', 'unidade'), measure('Prioridade MRP'), measure('Próxima Necessidade'), measure('O.S. Impactadas pelo SKU'), measure('Necessidade Total (U.M.)'), measure('Estoque Disponível MRP (U.M.)'), measure('Em Trânsito (U.M.)'), measure('Necessidade Compra (U.M.)')],
      sort: { entity: 'fato_mrp', property: 'Ordem Prioridade MRP', kind: 'Measure', direction: 'Ascending' },
      filters: [{ entity: 'fato_necessidades_os', property: 'sem_estoque_disponivel', values: [true] }],
    },
  },
  {
    key: 'historico_producao', displayName: '5. Histórico de Produção', accent: COLORS.green,
    title: 'Histórico de Conclusão e Entrega', subtitle: 'Produção concluída, entrega real, prazo e duração | fonte MES',
    layout: 'history',
    slicers: [
      { ...col('dim_mes_historico', 'ano_mes'), label: 'Mês com dados' },
      { ...col('dim_ordem_servico', 'categoria_servico'), label: 'Tipo de serviço' },
      { ...col('fato_historico_conclusao', 'mercado'), label: 'Mercado' },
      { ...col('fato_historico_conclusao', 'cliente'), label: 'Cliente' },
      { ...col('fato_historico_conclusao', 'linha_produto'), label: 'Linha de produto' },
    ],
    measures: ['Carros Finalizados', 'Carros Entregues', 'Carros Retirados', 'Tempo Médio Produção (dias)', 'Mediana Produção (dias)', 'Finalizados em Atraso', '% Finalizados em Atraso'],
    table: {
      title: 'Histórico por veículo — conclusão, entrega e prazo',
      columns: [col('fato_historico_conclusao', 'numero_os'), col('fato_historico_conclusao', 'chassi_exibicao'), col('fato_historico_conclusao', 'cliente'), col('fato_historico_conclusao', 'categoria_servico'), col('fato_historico_conclusao', 'mercado'), col('fato_historico_conclusao', 'linha_produto'), col('fato_historico_conclusao', 'data_inicio_producao'), col('fato_historico_conclusao', 'data_limite_producao'), col('fato_historico_conclusao', 'data_finalizacao'), col('fato_historico_conclusao', 'data_entrega'), col('fato_historico_conclusao', 'data_retirada'), col('fato_historico_conclusao', 'dias_producao'), col('fato_historico_conclusao', 'situacao_finalizacao')],
      sort: { entity: 'fato_historico_conclusao', property: 'data_finalizacao', kind: 'Column', direction: 'Descending' },
      filters: [{ entity: 'fato_historico_conclusao', property: 'status', values: ['FINALIZADA', 'ENTREGUE', 'RETIRADA'] }],
    },
  },
  {
    key: 'fechamento_producao', displayName: '6. Fechamento de Produção', accent: COLORS.blue,
    title: 'Fechamento de Produção', subtitle: 'Etapas concluídas por setor, finalizações e entregas | semanas, meses e anos',
    layout: 'production',
    slicers: [
      { ...col('dim_ordem_servico', 'categoria_servico'), label: 'Tipo de serviço' },
      { ...col('dim_ordem_servico', 'linha_produto'), label: 'Linha de produto' },
      { ...col('fato_progresso_producao', 'setor'), label: 'Setor produtivo' },
      { ...col('fato_progresso_producao', 'ano'), label: 'Ano' },
    ],
    measures: ['Etapas Concluídas', 'Veículos Finalizados (Produção)', 'Veículos Entregues (Produção)', 'Setores com Produção'],
  },
];

const drillPages = [
  {
    key: 'detalhe_sku', displayName: 'D. Detalhe SKU', title: 'Detalhe do SKU', subtitle: 'Drill-through consultivo: estoque, empenho, necessidade, trânsito e compra', accent: COLORS.teal,
    drill: col('dim_sku', 'codigo'),
    measures: ['Estoque Atual (U.M.)', 'Empenhado Total (U.M.)', 'Estoque Disponível (U.M.)', 'Necessidade Total (U.M.)', 'Em Trânsito (U.M.)', 'Necessidade Compra (U.M.)'],
    tables: [
      { key: 'sku_resumo', title: 'Resumo do material', y: 220, height: 190, columns: [col('dim_sku', 'codigo'), col('dim_sku', 'descricao'), col('dim_sku', 'unidade'), col('dim_sku', 'categoria'), col('fato_estoque_atual', 'status_estoque'), measure('Prioridade MRP'), measure('Próxima Necessidade')] },
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
    textbox(pageSpec.key, 'header', pageSpec.title, pageSpec.subtitle, 20, 8, 1120, 50, 0),
    divider(pageSpec.key, 20, 58, 1240, pageSpec.accent, 1),
    imageVisual(pageSpec.key, 'logo', LOGO_RESOURCE, 1128, 3, 132, 54, 99),
  ];

  if (!isDrill) {
    if (pageSpec.layout === 'history') {
      pageSpec.slicers.forEach((spec, index) => visuals.push(slicer(pageSpec.key, spec, 300 + index * 192, 2 + index, 188)));
      visuals.push(kpiStrip(pageSpec.key, pageSpec.measures, 156, 7));
      visuals.push(lineChart(pageSpec.key, 'mensal', 'Finalizados, entregues e retirados por mês', col('dCalendario', 'AnoMes'), ['Carros Finalizados', 'Carros Entregues', 'Carros Retirados'], 20, 300, 600, 188, 8));
      visuals.push(barChart(pageSpec.key, 'mercado', 'Carros finalizados por mercado', col('fato_historico_conclusao', 'mercado'), 'Carros Finalizados', 632, 300, 300, 188, COLORS.green, 9));
      visuals.push(barChart(pageSpec.key, 'linha_tempo', 'Tempo médio por linha (dias)', col('fato_historico_conclusao', 'linha_produto'), 'Tempo Médio Produção (dias)', 944, 300, 316, 188, COLORS.blue, 10));
      visuals.push(table(pageSpec.key, 'history', pageSpec.table.title, pageSpec.table.columns, 20, 500, 1240, 204, 11, pageSpec.table.sort, pageSpec.table.filters || []));
    } else if (pageSpec.layout === 'production') {
      pageSpec.slicers.forEach((spec, index) => visuals.push(slicer(pageSpec.key, spec, 420 + index * 176, 2 + index, 164)));
      visuals.push(kpiStrip(pageSpec.key, pageSpec.measures, 92, 7));
      visuals.push(stackedColumnChart(pageSpec.key, 'semanal_setor', 'Etapas concluídas por setor — semanal', col('fato_progresso_producao', 'semana_inicio'), col('fato_progresso_producao', 'setor'), 'Etapas Concluídas', 20, 248, 820, 218, 8));
      visuals.push(donutChart(pageSpec.key, 'mix_setor', 'Participação da produção por setor', col('fato_progresso_producao', 'setor'), 'Etapas Concluídas', 860, 248, 400, 218, 9));
      visuals.push(lineChart(pageSpec.key, 'finalizacao_entrega', 'Finalizações e entregas — semanal', col('fato_progresso_producao', 'semana_inicio'), ['Veículos Finalizados (Produção)', 'Veículos Entregues (Produção)'], 20, 486, 610, 218, 10));
      visuals.push(barChart(pageSpec.key, 'mensal_setor', 'Etapas concluídas por mês', col('fato_progresso_producao', 'ano_mes'), 'Etapas Concluídas', 648, 486, 300, 218, COLORS.teal, 11));
      visuals.push(barChart(pageSpec.key, 'anual_setor', 'Etapas concluídas por ano', col('fato_progresso_producao', 'ano'), 'Etapas Concluídas', 960, 486, 300, 218, COLORS.blue, 12));
    } else {
      pageSpec.slicers.forEach((spec, index) => visuals.push(slicer(pageSpec.key, spec, 636 + index * 212, 2 + index)));
      visuals.push(kpiStrip(pageSpec.key, pageSpec.measures, 156, 6));
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
        7 + index
      )));
      visuals.push(table(pageSpec.key, 'actions', pageSpec.table.title, pageSpec.table.columns, 20, 500, 1240, 204, 9, pageSpec.table.sort, pageSpec.table.filters || []));
    }
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
