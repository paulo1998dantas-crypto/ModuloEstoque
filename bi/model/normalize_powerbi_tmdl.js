const fs = require('fs');
const path = require('path');

const tablesDir = path.join(
  __dirname,
  '..',
  'power-bi',
  'JI_Montadora_Operacional.SemanticModel',
  'definition',
  'tables'
);

let filesChanged = 0;
let columnsChanged = 0;

for (const entry of fs.readdirSync(tablesDir, { withFileTypes: true })) {
  if (!entry.isFile() || !entry.name.endsWith('.tmdl')) continue;
  const filePath = path.join(tablesDir, entry.name);
  const source = fs.readFileSync(filePath, 'utf8');
  const matches = source.match(/summarizeBy: (sum|count|average|min|max)/g) || [];
  if (!matches.length) continue;
  const updated = source.replace(/summarizeBy: (sum|count|average|min|max)/g, 'summarizeBy: none');
  fs.writeFileSync(filePath, updated, 'utf8');
  filesChanged += 1;
  columnsChanged += matches.length;
}

console.log(`TMDL_SUMMARIZATION_OK files=${filesChanged} columns=${columnsChanged}`);
