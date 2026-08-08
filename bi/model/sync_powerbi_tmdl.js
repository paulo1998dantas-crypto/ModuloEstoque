const fs = require('fs');
const path = require('path');

const measuresPath = path.join(__dirname, 'powerbi_measures.json');
const tmdlPath = path.join(
  __dirname,
  '..',
  'power-bi',
  'JI_Montadora_Operacional.SemanticModel',
  'definition',
  'tables',
  'fato_mrp.tmdl'
);

const measures = JSON.parse(fs.readFileSync(measuresPath, 'utf8'));
const source = fs.readFileSync(tmdlPath, 'utf8');
const firstMeasure = source.indexOf('\n\tmeasure ');
const firstColumn = source.indexOf('\n\tcolumn sku_id');

if (firstMeasure < 0 || firstColumn < 0 || firstColumn <= firstMeasure) {
  throw new Error('Nao foi possivel localizar o bloco de medidas em fato_mrp.tmdl.');
}

const quoteName = (name) => `'${name.replaceAll("'", "''")}'`;
const rendered = measures.map((measure) => {
  const lines = [`\tmeasure ${quoteName(measure.name)} = ${measure.expression}`];
  if (measure.format) lines.push(`\t\tformatString: ${measure.format}`);
  if (measure.folder) lines.push(`\t\tdisplayFolder: ${measure.folder}`);
  return lines.join('\n');
}).join('\n\n');

const updated = `${source.slice(0, firstMeasure)}\n${rendered}\n${source.slice(firstColumn)}`;
fs.writeFileSync(tmdlPath, updated, 'utf8');
console.log(`TMDL_MEASURES_OK count=${measures.length}`);
