const test = require('node:test');
const assert = require('node:assert/strict');
const { aiPackagingArgs } = require('../scripts/ai-packaging.cjs');

test('collects RapidOCR configuration, models and package metadata for offline OCR', () => {
  const args = aiPackagingArgs();
  const collected = args.filter((_, index) => args[index - 1] === '--collect-all');
  assert.ok(collected.includes('rapidocr'), 'RapidOCR models and licenses must accompany the frozen backend');
  assert.ok(collected.includes('onnxruntime'), 'OCR uses the existing bundled ONNX runtime');
  const metadata = args.filter((_, index) => args[index - 1] === '--recursive-copy-metadata');
  assert.ok(metadata.includes('rapidocr'), 'OCR dependencies must retain their license and attribution files');
});
