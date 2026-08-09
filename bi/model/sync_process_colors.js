const fs = require('fs');
const path = require('path');

const reportPages = path.resolve(
  __dirname,
  '..',
  'power-bi',
  'JI_Montadora_Operacional.Report',
  'definition',
  'pages'
);

const readJson = (file) => JSON.parse(fs.readFileSync(file, 'utf8'));
const writeJson = (file, value) => fs.writeFileSync(file, `${JSON.stringify(value, null, 2)}\n`, 'utf8');

function findVisualFiles(directory) {
  const files = [];
  for (const page of fs.readdirSync(directory, { withFileTypes: true })) {
    if (!page.isDirectory()) continue;
    const visuals = path.join(directory, page.name, 'visuals');
    if (!fs.existsSync(visuals)) continue;
    for (const visual of fs.readdirSync(visuals, { withFileTypes: true })) {
      const file = path.join(visuals, visual.name, 'visual.json');
      if (visual.isDirectory() && fs.existsSync(file)) files.push(file);
    }
  }
  return files;
}

function isProcessChart(document) {
  const visual = document.visual;
  const series = visual?.query?.queryState?.Series?.projections ?? [];
  return visual?.visualType === 'clusteredColumnChart'
    && series.some(({ field }) => field?.Column?.Expression?.SourceRef?.Entity === 'fato_progresso_producao'
      && field.Column.Property === 'setor');
}

function isDailyProcessChart(document) {
  const categories = document.visual?.query?.queryState?.Category?.projections ?? [];
  return isProcessChart(document)
    && categories.some(({ field }) => field?.Column?.Property === 'data_evento');
}

const visualFiles = findVisualFiles(reportPages);
const documents = visualFiles.map((file) => ({ file, document: readJson(file) }));
const source = documents.find(({ document }) => isDailyProcessChart(document) && document.visual.objects?.dataPoint?.length);

if (!source) throw new Error('Não foi encontrado o gráfico diário com a paleta de processos.');

const palette = source.document.visual.objects.dataPoint;
let updated = 0;

for (const target of documents) {
  if (target.file === source.file || !isProcessChart(target.document)) continue;
  target.document.visual.objects.dataPoint = structuredClone(palette);
  writeJson(target.file, target.document);
  updated += 1;
}

console.log(`PROCESS_COLORS_OK source=${path.basename(path.dirname(source.file))} entries=${palette.length} updated=${updated}`);
