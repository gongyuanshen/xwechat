const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const { EventEmitter } = require('node:events');

// Exercise the real readiness probe without starting Nuxt or Electron.
const source = fs.readFileSync(path.join(__dirname, 'dev.cjs'), 'utf8');
const httpReady = vm.runInNewContext(
  source.slice(0, source.lastIndexOf('\nmain().catch(')) + '\nhttpReady;',
  { require, process, __dirname, setTimeout }
);

async function probe(t, handler) {
  const server = http.createServer(handler);
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  return httpReady(`http://127.0.0.1:${server.address().port}/`);
}

test('a complete HTTP 200 response is ready', async t => {
  assert.equal(await probe(t, (_req, res) => res.end('ready')), true);
});

test('Nuxt HTTP 503 loading page is not ready', async t => {
  assert.equal(await probe(t, (_req, res) => {
    res.writeHead(503);
    res.end('Dev server is loading...');
  }), false);
});

test('HTTP 500 surfaces the frontend error instead of reporting readiness', async t => {
  await assert.rejects(probe(t, (_req, res) => {
    res.writeHead(500);
    res.end('Build failed');
  }), /HTTP 500/);
});

test('an interrupted HTTP 200 body is not ready', async t => {
  await assert.rejects(probe(t, (_req, res) => {
    res.writeHead(200, { 'Content-Length': 20 });
    res.write('partial');
    setImmediate(() => res.destroy());
  }), /aborted|reset/i);
});

test('a frontend readiness failure stops its process before propagating the error', async t => {
  const server = http.createServer((_req, res) => {
    res.writeHead(500);
    res.end('Build failed');
  });
  t.after(() => new Promise(resolve => server.close(resolve)));
  const frontend = Object.assign(new EventEmitter(), {
    pid: 12345, exitCode: null, killed: false, stdout: null, stderr: null,
  });
  const stopped = [];
  const launcherProcess = {
    env: {}, execPath: process.execPath, platform: 'win32',
    stdout: { write() {} }, on() {},
  };
  const main = vm.runInNewContext(
    source.slice(0, source.lastIndexOf('\nmain().catch(')) + '\nmain;',
    {
      require(name) {
        if (name === 'electron') return 'electron-not-launched';
        if (name === 'child_process') return {
          spawn(command, args, options) {
            assert.equal(command, process.execPath);
            assert.match(args[0], /nuxi\.mjs$/);
            server.listen(Number(options.env.NUXT_PORT), '127.0.0.1');
            return frontend;
          },
          spawnSync(command, args) { stopped.push([command, ...args]); },
        };
        return require(name);
      },
      process: launcherProcess, __dirname, setTimeout,
    }
  );

  await assert.rejects(main(), /HTTP 500/);
  assert.deepEqual(stopped, [['taskkill', '/pid', '12345', '/t', '/f']]);
  assert.equal(launcherProcess.exitCode, 1);
});
