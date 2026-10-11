const test = require('node:test');
const assert = require('node:assert/strict');
const { aiPackagingArgs } = require('../scripts/ai-packaging.cjs');
const manifest = require('../../src/wechat_decrypt_tool/resources/local_acceleration_dml.json');

test('external DirectML workers retain metadata for each application dependency', () => {
  const args = aiPackagingArgs();
  const copied = args.filter((_, index) => args[index - 1] === '--copy-metadata');
  for (const requirement of manifest.dependencies_from_application) {
    assert.ok(copied.includes(requirement.split(/[>=<!~]/)[0]), requirement);
  }
  assert.ok(!args.includes('onnxruntime-directml'), 'DirectML belongs to its isolated component directory');
});
