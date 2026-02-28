'use strict';

const { spawnSync } = require('child_process');
const path = require('path');
const os = require('os');

const pkgRoot = path.join(__dirname, '..');
const req = path.join(pkgRoot, 'requirements.txt');

const pipCandidates = os.platform() === 'win32'
  ? ['pip', 'pip3']
  : ['pip3', 'pip'];

function runPip(cmd) {
  const r = spawnSync(cmd, ['install', '-r', req, '--quiet'], { stdio: 'inherit' });
  return r.status === 0;
}

console.log('unicode: installing Python dependencies…');
const succeeded = pipCandidates.some(runPip);

if (!succeeded) {
  console.warn(
    '\nunicode: could not install Python dependencies automatically.\n' +
    'Ensure Python 3.10+ and pip are on your PATH, then run:\n' +
    `  pip install -r "${req}"\n`
  );
}
