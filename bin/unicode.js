#!/usr/bin/env node
'use strict';

const { spawnSync } = require('child_process');
const path = require('path');
const os = require('os');

const pkgRoot = path.join(__dirname, '..');

// Prefer 'python' on Windows, 'python3' elsewhere; fall back to the other.
const pythonCandidates = os.platform() === 'win32'
  ? ['python', 'python3']
  : ['python3', 'python'];

function findPython() {
  for (const cmd of pythonCandidates) {
    const r = spawnSync(cmd, ['--version'], { encoding: 'utf8' });
    if (r.status === 0) return cmd;
  }
  return null;
}

const python = findPython();
if (!python) {
  console.error('unicode: Python not found. Install Python 3.10+ and ensure it is on your PATH.');
  process.exit(1);
}

const sep = os.platform() === 'win32' ? ';' : ':';
const pythonPath = process.env.PYTHONPATH
  ? pkgRoot + sep + process.env.PYTHONPATH
  : pkgRoot;

const result = spawnSync(
  python,
  [path.join(pkgRoot, 'orchestrator.py'), ...process.argv.slice(2)],
  {
    stdio: 'inherit',
    env: { ...process.env, PYTHONPATH: pythonPath },
  }
);

process.exit(result.status ?? 1);
