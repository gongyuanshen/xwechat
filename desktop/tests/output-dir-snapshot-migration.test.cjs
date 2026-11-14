const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const { migrateOutputDirectory, rollbackOutputDirectoryChange } = require('../src/output-dir.cjs');

const generationName = 'generation-' + 'a'.repeat(32);
const account = 'wxid_test';
const manifestRelative = path.join('verified_snapshots', generationName, 'manifest.json');

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'xwechat-output-move-test-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const source = path.join(root, 'source');
  const target = path.join(root, 'relocated-output');
  const generation = path.join(source, 'verified_snapshots', generationName);
  const databases = path.join(generation, 'databases', account);
  const database = path.join(databases, 'contact.db');
  fs.mkdirSync(databases, { recursive: true });
  fs.mkdirSync(path.join(source, 'databases', account), { recursive: true });
  fs.writeFileSync(database, 'database fixture bytes');
  const manifest = {
    status: 'success', account, generation_path: generation, account_path: databases,
    guarantee: 'stable_observation', source_unchanged: true, captured_prefix_unchanged: true,
    capture_boundaries: {}, observation_started_at: '2026-11-14T00:00:00Z',
    observation_completed_at: '2026-11-14T00:00:01Z', total_databases: 1,
    manifest: { 'contact.db': { size: 22, sha256: 'captured-source-fingerprint' } },
    db_diagnostics: { 'contact.db': {
      db_path: path.join(source, '.snapshot-capture', 'contact.db'),
      source_db_path: path.join(root, 'external-wechat', 'db_storage', 'contact.db'),
      output_path: database, success: true, key_authenticated: true, output_size: 22,
      diagnostics: { path: database, integrity_check_ok: true },
      output_header_debug: { path: database, header: 'preserve-debug-evidence' },
    } },
  };
  const original = Buffer.from(JSON.stringify(manifest, null, 2));
  fs.writeFileSync(path.join(source, manifestRelative), original);
  fs.writeFileSync(path.join(source, 'databases', account, '_snapshot_current.json'), JSON.stringify({
    version: 1, account, generation_account: account,
    generation_path: `verified_snapshots/${generationName}`, revision: 1,
    guarantee: 'stable_observation', published_at: '2026-11-14T00:00:01Z',
  }));
  return { root, source, target, manifest, original };
}

function assertRelocated(f) {
  const actual = JSON.parse(fs.readFileSync(path.join(f.target, manifestRelative), 'utf8'));
  const expected = structuredClone(f.manifest);
  expected.generation_path = path.join(f.target, 'verified_snapshots', generationName);
  expected.account_path = path.join(expected.generation_path, 'databases', account);
  const database = path.join(expected.account_path, 'contact.db');
  expected.db_diagnostics['contact.db'].output_path = database;
  expected.db_diagnostics['contact.db'].diagnostics.path = database;
  expected.db_diagnostics['contact.db'].output_header_debug.path = database;
  assert.deepEqual(actual, expected);
  assert.equal(fs.readFileSync(database, 'utf8'), 'database fixture bytes');
}

test('migration rebases snapshot output paths but preserves source provenance and original backup', async t => {
  const f = fixture(t);
  const result = await migrateOutputDirectory({ currentDir: f.source, nextDir: f.target });
  assertRelocated(f);
  assert.deepEqual(fs.readFileSync(path.join(result.backupDir, manifestRelative)), f.original);
  assert.equal(fs.existsSync(f.source), false);
});

test('migration preserves integer and decimal provenance tokens without JavaScript number rounding', async t => {
  const f = fixture(t);
  const sourcePath = path.join(f.source, manifestRelative);
  const numericEvidence = '"mtime_ns": 1793786155448223500, "inode": 9007199254740993, "read_ms": 0.12345678901234567890';
  const original = fs.readFileSync(sourcePath, 'utf8').replace('"size": 22,', `${numericEvidence}, "size": 22,`);
  fs.writeFileSync(sourcePath, original);
  const result = await migrateOutputDirectory({ currentDir: f.source, nextDir: f.target });
  const relocated = fs.readFileSync(path.join(f.target, manifestRelative), 'utf8');
  assert.match(relocated, /"mtime_ns": 1793786155448223500/);
  assert.match(relocated, /"inode": 9007199254740993/);
  assert.match(relocated, /"read_ms": 0\.12345678901234567890/);
  assert.equal(fs.readFileSync(path.join(result.backupDir, manifestRelative), 'utf8'), original);
});

test('rollback verifies relocated manifests and restores original metadata', async t => {
  const f = fixture(t);
  const result = await migrateOutputDirectory({ currentDir: f.source, nextDir: f.target });
  assertRelocated(f);
  await rollbackOutputDirectoryChange({
    previousDir: f.source, currentDir: f.target, backupDir: result.backupDir, sourceWasEmpty: false,
  });
  assert.deepEqual(fs.readFileSync(path.join(f.source, manifestRelative)), f.original);
  assert.equal(fs.existsSync(f.target), false);
});

test('restart resumes a promoted target after source was renamed to its backup', async t => {
  const f = fixture(t);
  await migrateOutputDirectory({ currentDir: f.source, nextDir: f.target });
  assertRelocated(f);
  const resumed = await migrateOutputDirectory({ currentDir: f.source, nextDir: f.target });
  assertRelocated(f);
  assert.deepEqual(fs.readFileSync(path.join(resumed.backupDir, manifestRelative)), f.original);
});

test('restart resumes a verified staging directory with paths rooted at the final destination', async t => {
  const f = fixture(t);
  const result = await migrateOutputDirectory({ currentDir: f.source, nextDir: f.target });
  fs.renameSync(f.target, `${f.target}.migrating-interrupted`);
  fs.renameSync(result.backupDir, f.source);
  const resumed = await migrateOutputDirectory({ currentDir: f.source, nextDir: f.target });
  assertRelocated(f);
  assert.deepEqual(fs.readFileSync(path.join(resumed.backupDir, manifestRelative)), f.original);
});

test('malformed snapshot metadata fails before moving or deleting source files', async t => {
  const f = fixture(t);
  fs.writeFileSync(path.join(f.source, manifestRelative), '{invalid-json');
  await assert.rejects(migrateOutputDirectory({ currentDir: f.source, nextDir: f.target }), /snapshot|快照/i);
  assert.equal(fs.readFileSync(path.join(f.source, manifestRelative), 'utf8'), '{invalid-json');
  assert.equal(fs.existsSync(f.target), false);
});

test('a database output path outside its generation is rejected rather than repaired', async t => {
  const f = fixture(t);
  f.manifest.db_diagnostics['contact.db'].output_path = path.join(f.root, 'other-account', 'contact.db');
  const invalid = JSON.stringify(f.manifest);
  fs.writeFileSync(path.join(f.source, manifestRelative), invalid);
  await assert.rejects(migrateOutputDirectory({ currentDir: f.source, nextDir: f.target }), /snapshot|快照/i);
  assert.equal(fs.readFileSync(path.join(f.source, manifestRelative), 'utf8'), invalid);
  assert.equal(fs.existsSync(f.target), false);
});

test('rollback refuses altered database bytes and keeps target and backup', async t => {
  const f = fixture(t);
  const result = await migrateOutputDirectory({ currentDir: f.source, nextDir: f.target });
  const database = path.join(f.target, 'verified_snapshots', generationName, 'databases', account, 'contact.db');
  fs.writeFileSync(database, 'Database fixture bytes');
  await assert.rejects(rollbackOutputDirectoryChange({
    previousDir: f.source, currentDir: f.target, backupDir: result.backupDir, sourceWasEmpty: false,
  }), /内容不一致/);
  assert.equal(fs.existsSync(f.target), true);
  assert.equal(fs.existsSync(result.backupDir), true);
});

test('rollback refuses changed snapshot verification evidence', async t => {
  const f = fixture(t);
  const result = await migrateOutputDirectory({ currentDir: f.source, nextDir: f.target });
  const manifestPath = path.join(f.target, manifestRelative);
  const altered = fs.readFileSync(manifestPath, 'utf8').replace('2026-11-14T00:00:01Z', '2026-11-14T00:00:02Z');
  fs.writeFileSync(manifestPath, altered);
  await assert.rejects(rollbackOutputDirectoryChange({
    previousDir: f.source, currentDir: f.target, backupDir: result.backupDir, sourceWasEmpty: false,
  }), /内容不一致/);
  assert.equal(fs.readFileSync(manifestPath, 'utf8'), altered);
  assert.equal(fs.existsSync(result.backupDir), true);
});

test('migration without verified snapshots still copies all bytes and supports rollback', async t => {
  const f = fixture(t);
  fs.rmSync(path.join(f.source, 'verified_snapshots'), { recursive: true });
  fs.rmSync(path.join(f.source, 'databases'), { recursive: true });
  const settings = Buffer.from('{"preferred":"unchanged"}');
  fs.writeFileSync(path.join(f.source, 'runtime_settings.json'), settings);
  const result = await migrateOutputDirectory({ currentDir: f.source, nextDir: f.target });
  assert.deepEqual(fs.readFileSync(path.join(f.target, 'runtime_settings.json')), settings);
  await rollbackOutputDirectoryChange({
    previousDir: f.source, currentDir: f.target, backupDir: result.backupDir, sourceWasEmpty: false,
  });
  assert.deepEqual(fs.readFileSync(path.join(f.source, 'runtime_settings.json')), settings);
});
