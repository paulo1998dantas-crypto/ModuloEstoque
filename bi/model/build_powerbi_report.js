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

const field = (entity, property, kind = 'Column') => ({
  [kind]: {
    Expression: { SourceRef: { Entity: entity } },
    Property: property,
  },
});

const projection = (entity, property, kind = 'Column') => ({
  field: field(entity, property, kind),
  queryRef: `${entity}.${property}`,
  nativeQueryRef: property,
});

const filter = (scope, entity, property, kind = 'Column', type = 'Categorical') => ({
  name: id(`${scope}:filter:${entity}:${property}`),
  field: field(entity, property, kind),
  type,
});

const position = (x, y, width, height, tabOrder) => ({
  x,
  y,
  z: tabOrder,
  height,
  width,
  tabOrder,
});

function card(pageKey, measure, x, tabOrder) {
  const name = id(`${pageKey}:card:${measure}`);
  return {
    name,
    json: {
      $schema: `${schemaBase}/visualContainer/2.11.0/schema.json`,
      name,
      position: position(x, 92, 236, 132, tabOrder),
      visual: {
        visualType: 'cardVisual',
        query: {
          queryState: {
            Data: { projections: [projection('fato_mrp', measure, 'Measure')] },
          },
          sortDefinition: {
            sort: [{ field: field('fato_mrp', measure, 'Measure'), direction: 'Descending' }],
            isDefaultSort: true,
          },
        },
        drillFilterOtherVisuals: true,
      },
      filterConfig: {
        filters: [filter(name, 'fato_mrp', measure, 'Measure', 'Advanced')],
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
      position: position(x, 10, 236, 70, tabOrder),
      visual: {
        visualType: 'slicer',
        query: {
          queryState: {
            Values: { projections: [projection(spec.entity, spec.property)] },
          },
        },
        drillFilterOtherVisuals: true,
      },
      filterConfig: {
        filters: [filter(name, spec.entity, spec.property, 'Column', spec.type || 'Categorical')],
      },
    },
  };
}

function table(pageKey, columns, tabOrder) {
  const name = id(`${pageKey}:table`);
  return {
    name,
    json: {
      $schema: `${schemaBase}/visualContainer/2.11.0/schema.json`,
      name,
      position: position(20, 238, 1240, 462, tabOrder),
      visual: {
        visualType: 'tableEx',
        query: {
          queryState: {
            Values: {
              projections: columns.map((c) => projection(c.entity, c.property)),
            },
          },
        },
        drillFilterOtherVisuals: true,
      },
      filterConfig: {
        filters: columns.map((c) =>
          filter(name, c.entity, c.property, 'Column', c.type || 'Categorical')
        ),
      },
    },
  };
}

const pageSpecs = [
  {
    key: 'estoque',
    displayName: '1. Visão Estoque',
    slicers: [
      ['dim_sku', 'codigo'],
      ['dim_sku', 'grupo'],
      ['dim_sku', 'categoria'],
      ['fato_movimentacoes_estoque', 'setor'],
      ['dCalendario', 'Data', 'Advanced'],
    ],
    measures: ['Estoque Atual', 'Empenhado Total', 'Estoque Disponivel', 'SKUs Zerados', 'Consumo'],
    columns: [
      ['fato_estoque_atual', 'codigo'],
      ['fato_estoque_atual', 'descricao'],
      ['fato_estoque_atual', 'estoque_atual', 'Advanced'],
      ['fato_estoque_atual', 'empenhado_total', 'Advanced'],
      ['fato_estoque_atual', 'estoque_disponivel', 'Advanced'],
      ['fato_estoque_atual', 'estoque_minimo', 'Advanced'],
      ['fato_estoque_atual', 'status_estoque'],
    ],
  },
  {
    key: 'pcp',
    displayName: '2. Visão PCP',
    slicers: [
      ['dim_ordem_servico', 'cliente'],
      ['dim_ordem_servico', 'numero_os'],
      ['fato_necessidades_os', 'setor'],
      ['dim_sku', 'codigo'],
      ['dCalendario', 'Data', 'Advanced'],
    ],
    measures: ['O.S. no WIP', 'O.S. em Producao', 'O.S. Atrasadas', 'Avanco Medio %', 'O.S. com Material Pendente'],
    columns: [
      ['dim_ordem_servico', 'numero_os'],
      ['dim_ordem_servico', 'cliente'],
      ['dim_ordem_servico', 'modelo'],
      ['dim_ordem_servico', 'status'],
      ['dim_ordem_servico', 'fase_wip'],
      ['dim_ordem_servico', 'percentual_avanco', 'Advanced'],
      ['dim_ordem_servico', 'dias_no_wip', 'Advanced'],
      ['dim_ordem_servico', 'data_entrega_vigente', 'Advanced'],
    ],
  },
  {
    key: 'compras',
    displayName: '3. Compras e Trânsito',
    slicers: [
      ['fato_compras_transito', 'fornecedor'],
      ['dim_sku', 'codigo'],
      ['fato_compras_transito', 'situacao_transito'],
      ['fato_compras_transito', 'numero_oc'],
      ['dCalendario', 'Data', 'Advanced'],
    ],
    measures: ['Linhas em Transito', 'Valor em Transito', 'Linhas Atrasadas', 'O.C. Abertas', 'Taxa Aprovacao %'],
    columns: [
      ['fato_compras_transito', 'numero_oc'],
      ['fato_compras_transito', 'fornecedor'],
      ['fato_compras_transito', 'codigo'],
      ['fato_compras_transito', 'descricao'],
      ['fato_compras_transito', 'quantidade_pendente', 'Advanced'],
      ['fato_compras_transito', 'valor_pendente', 'Advanced'],
      ['fato_compras_transito', 'data_necessidade', 'Advanced'],
      ['fato_compras_transito', 'situacao_transito'],
    ],
  },
  {
    key: 'mrp',
    displayName: '4. Visão MRP',
    slicers: [
      ['dim_sku', 'codigo'],
      ['dim_sku', 'grupo'],
      ['dim_sku', 'categoria'],
      ['fato_mrp', 'status_mrp'],
      ['fato_mrp', 'proxima_remessa', 'Advanced'],
    ],
    measures: ['Necessidade Total', 'Em Transito', 'MRP Estoque Disponivel', 'Necessidade de Compra', 'SKUs a Comprar'],
    columns: [
      ['fato_mrp', 'codigo'],
      ['fato_mrp', 'descricao'],
      ['fato_mrp', 'necessidade_total', 'Advanced'],
      ['fato_mrp', 'estoque_disponivel', 'Advanced'],
      ['fato_mrp', 'quantidade_transito', 'Advanced'],
      ['fato_mrp', 'saldo_projetado', 'Advanced'],
      ['fato_mrp', 'necessidade_compra', 'Advanced'],
      ['fato_mrp', 'status_mrp'],
    ],
  },
].map((p) => ({
  ...p,
  slicers: p.slicers.map(([entity, property, type]) => ({ entity, property, type })),
  columns: p.columns.map(([entity, property, type]) => ({ entity, property, type })),
}));

for (const entry of fs.readdirSync(reportRoot, { withFileTypes: true })) {
  if (entry.isDirectory()) {
    fs.rmSync(path.join(reportRoot, entry.name), { recursive: true, force: true });
  }
}

const pageOrder = [];
for (const pageSpec of pageSpecs) {
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
  };
  fs.writeFileSync(path.join(pageDir, 'page.json'), `${JSON.stringify(page, null, 2)}\n`, 'utf8');

  const visuals = [];
  pageSpec.slicers.forEach((spec, index) => {
    visuals.push(slicer(pageSpec.key, spec, 20 + index * 248, index));
  });
  pageSpec.measures.forEach((measure, index) => {
    visuals.push(card(pageSpec.key, measure, 20 + index * 248, 5 + index));
  });
  visuals.push(table(pageSpec.key, pageSpec.columns, 10));

  for (const visual of visuals) {
    const visualDir = path.join(visualsDir, visual.name);
    fs.mkdirSync(visualDir, { recursive: true });
    fs.writeFileSync(
      path.join(visualDir, 'visual.json'),
      `${JSON.stringify(visual.json, null, 2)}\n`,
      'utf8'
    );
  }
}

const pagesMetadata = {
  $schema: `${schemaBase}/pagesMetadata/1.1.0/schema.json`,
  pageOrder,
  activePageName: pageOrder[0],
};
fs.writeFileSync(
  path.join(reportRoot, 'pages.json'),
  `${JSON.stringify(pagesMetadata, null, 2)}\n`,
  'utf8'
);

console.log(`REPORT_OK pages=${pageSpecs.length} visuals=${pageSpecs.length * 11}`);
