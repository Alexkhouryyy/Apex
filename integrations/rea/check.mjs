// Exercise the released server and its actual analysis schema without a model API.
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Client } from '@modelcontextprotocol/client';
import { StdioClientTransport } from '@modelcontextprotocol/client/stdio';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '../..');
const cli = join(here, 'node_modules/rea-agents/scripts/rea.mjs');
const dependency = JSON.parse(await readFile(join(here, 'package.json'), 'utf8'));
const expectedVersion = dependency.dependencies['rea-agents'];
const fixture = await mkdtemp(join(tmpdir(), 'apex-rea-check-'));
const client = new Client({ name: 'apex-rea-integration-check', version: '1.0.0' });
const transport = new StdioClientTransport({
  command: process.execPath,
  args: [cli, 'mcp'],
  cwd: root,
  env: process.env,
  stderr: 'pipe',
});
let serverErrors = '';
transport.stderr?.on('data', chunk => { serverErrors = (serverErrors + chunk).slice(-2000); });

try {
  // The input throws if executed. REA must recover the import graph statically.
  const sources = {
    'package.json': JSON.stringify({ name: 'rea-probe', type: 'module', main: 'index.js' }),
    'index.js': 'import { sum } from "./math.js";\nthrow new Error("STATIC_INPUT_MUST_NOT_RUN");\nexport const answer = sum(2, 3);\n',
    'math.js': 'export function sum(a, b) { return a + b; }\n',
  };
  for (const [name, content] of Object.entries(sources)) {
    await writeFile(join(fixture, name), content, 'utf8');
  }

  await client.connect(transport);
  assert.equal(client.getServerVersion()?.version, expectedVersion);
  const tools = [];
  let cursor;
  do {
    const page = await client.listTools(cursor ? { cursor } : {});
    tools.push(...page.tools);
    cursor = page.nextCursor;
  } while (cursor);
  assert(tools.some(tool => tool.name === 'analyze_javascript_application'));
  const result = await client.callTool({
    name: 'analyze_javascript_application',
    arguments: { input_path: fixture, format: 'directory', detail: 'complete' },
  }, undefined, { timeout: 60000 });
  assert(!result.isError, JSON.stringify(result).slice(0, 1000));
  const text = JSON.stringify(result);
  assert(text.includes('index.js') && text.includes('math.js'), 'Analysis must include both input modules');
  assert(result.structuredContent?.evidence_id, 'Analysis must return structured Evidence');
  for (const [name, content] of Object.entries(sources)) {
    assert.equal(await readFile(join(fixture, name), 'utf8'), content, 'Static analysis changed an input file');
  }

  // Preview only the requested client. Do not apply changes to the CI host.
  const preview = spawnSync(process.execPath, [cli, 'setup', '--client', 'codex', '--dry-run', '--json'], {
    cwd: root, encoding: 'utf8', timeout: 60000,
  });
  assert.equal(preview.status, 0, preview.stderr || preview.error?.message);
  const plan = JSON.parse(preview.stdout);
  assert.equal(plan.status, 'planned');
  assert(plan.plannedActions.some(action => action.id === 'configure_client:codex'));
  assert(plan.plannedActions.every(action => !action.external), 'Codex setup must not install an analysis engine');

  // Keep the project skill aligned with the package used by cloud tasks.
  const shipped = join(here, 'node_modules/rea-agents/skills/reverse-engineer-anything');
  const installed = join(root, '.agents/skills/reverse-engineer-anything');
  async function compareSkill(relative = '') {
    for (const entry of await readdir(join(shipped, relative), { withFileTypes: true })) {
      const name = join(relative, entry.name);
      if (entry.isDirectory()) await compareSkill(name);
      else assert.deepEqual(await readFile(join(installed, name)), await readFile(join(shipped, name)), `Skill differs: ${name}`);
    }
  }
  await compareSkill();
  console.log(`PASS REA ${expectedVersion}: ${tools.length} MCP tools, static import graph, unchanged input files, scoped Codex setup plan and matching skill.`);
} catch (error) {
  console.error(error.message);
  if (serverErrors) console.error(serverErrors);
  process.exitCode = 1;
} finally {
  await client.close();
  await rm(fixture, { recursive: true, force: true });
}
