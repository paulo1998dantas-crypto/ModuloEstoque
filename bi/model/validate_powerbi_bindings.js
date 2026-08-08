const fs = require('fs');
const path = require('path');

const biRoot = path.resolve(__dirname, '..');
const tablesRoot = path.join(
  biRoot,
  'power-bi',
  'JI_Montadora_Operacional.SemanticModel',
  'definition',
  'tables'
);
const reportRoot = path.join(
  biRoot,
  'power-bi',
  'JI_Montadora_Operacional.Report'
);

function unquoteTmdlName(value) {
  const trimmed = value.trim();
  if (trimmed.startsWith("'") && trimmed.endsWith("'")) {
    return trimmed.slice(1, -1).replaceAll("''", "'");
  }
  return trimmed;
}

const model = new Map();
for (const file of fs.readdirSync(tablesRoot).filter((name) => name.endsWith('.tmdl'))) {
  const table = path.basename(file, '.tmdl');
  const fields = { columns: new Set(), measures: new Set() };
  const lines = fs.readFileSync(path.join(tablesRoot, file), 'utf8').split(/\r?\n/);
  for (const line of lines) {
    const trimmed = line.trim();
    if (trimmed.startsWith('column ')) {
      fields.columns.add(unquoteTmdlName(trimmed.slice('column '.length)));
    } else if (trimmed.startsWith('measure ')) {
      const match = trimmed.slice('measure '.length).match(/^(.*?)\s*=/);
      if (match) fields.measures.add(unquoteTmdlName(match[1]));
    }
  }
  model.set(table, fields);
}

function listJsonFiles(dir) {
  const files = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) files.push(...listJsonFiles(fullPath));
    else if (entry.isFile() && entry.name.endsWith('.json')) files.push(fullPath);
  }
  return files;
}

const errors = [];
let bindingCount = 0;

function inspect(node, file) {
  if (!node || typeof node !== 'object') return;
  for (const kind of ['Column', 'Measure']) {
    const binding = node[kind];
    const entity = binding?.Expression?.SourceRef?.Entity;
    const property = binding?.Property;
    if (!entity || !property) continue;
    bindingCount += 1;
    const fields = model.get(entity);
    if (!fields) {
      errors.push(`${path.relative(biRoot, file)}: tabela inexistente ${entity}`);
      continue;
    }
    const collection = kind === 'Column' ? fields.columns : fields.measures;
    if (!collection.has(property)) {
      errors.push(`${path.relative(biRoot, file)}: ${kind.toLowerCase()} inexistente ${entity}[${property}]`);
    }
  }
  for (const value of Object.values(node)) inspect(value, file);
}

const jsonFiles = listJsonFiles(reportRoot);
for (const file of jsonFiles) {
  const content = JSON.parse(fs.readFileSync(file, 'utf8'));
  inspect(content, file);
}

if (errors.length) {
  console.error(errors.join('\n'));
  process.exit(1);
}

const measureCount = [...model.values()].reduce((sum, fields) => sum + fields.measures.size, 0);
console.log(`BINDINGS_OK files=${jsonFiles.length} tables=${model.size} measures=${measureCount} bindings=${bindingCount}`);
