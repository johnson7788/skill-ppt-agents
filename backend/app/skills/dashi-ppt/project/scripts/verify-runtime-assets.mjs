import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { RUNTIME_ASSET_PATHS } from '../src/runtime-assets.mjs';

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const vendorRoot = path.join(projectRoot, 'assets', 'vendor');
const manifestPath = path.join(vendorRoot, 'manifest.json');
const errors = [];

function filesBelow(root) {
  if (!fs.existsSync(root)) return [];
  return fs.readdirSync(root, { withFileTypes: true }).flatMap(entry => {
    const absolute = path.join(root, entry.name);
    return entry.isDirectory() ? filesBelow(absolute) : [absolute];
  });
}

for (const assetPath of RUNTIME_ASSET_PATHS) {
  const absolute = path.join(projectRoot, assetPath);
  if (!fs.existsSync(absolute)) {
    errors.push(`missing runtime asset: ${assetPath}`);
  } else if (fs.statSync(absolute).isDirectory() && filesBelow(absolute).length === 0) {
    errors.push(`empty runtime asset directory: ${assetPath}`);
  }
}

if (!fs.existsSync(manifestPath)) {
  errors.push('missing vendor manifest: assets/vendor/manifest.json');
} else {
  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  for (const name of ['unicornstudio.umd.js', 'editable-pptx-browser.js']) {
    const spec = manifest[name];
    const absolute = path.join(vendorRoot, name);
    if (!spec || !fs.existsSync(absolute)) continue;
    const data = fs.readFileSync(absolute);
    const sha256 = crypto.createHash('sha256').update(data).digest('hex');
    if (data.length !== spec.bytes) {
      errors.push(`${name} size mismatch: expected ${spec.bytes}, got ${data.length}`);
    }
    if (sha256 !== spec.sha256) {
      errors.push(`${name} SHA-256 mismatch: expected ${spec.sha256}, got ${sha256}`);
    }
  }

  const fontFiles = filesBelow(path.join(vendorRoot, 'fonts'))
    .filter(file => file.toLowerCase().endsWith('.woff2'));
  const fontBytes = fontFiles.reduce((total, file) => total + fs.statSync(file).size, 0);
  if (fontFiles.length !== manifest.fonts?.woff2Files) {
    errors.push(`font file count mismatch: expected ${manifest.fonts?.woff2Files}, got ${fontFiles.length}`);
  }
  if (fontBytes !== manifest.fonts?.woff2Bytes) {
    errors.push(`font byte count mismatch: expected ${manifest.fonts?.woff2Bytes}, got ${fontBytes}`);
  }
}

if (errors.length) {
  console.error(errors.join('\n'));
  process.exit(1);
}

console.log(`Runtime assets verified: ${RUNTIME_ASSET_PATHS.length} paths.`);
