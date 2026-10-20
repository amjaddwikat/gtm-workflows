#!/usr/bin/env node
// Run an n8n Code node's JavaScript outside n8n, with mocked inputs.
//
//   node tests/run_code_node.js <workflow.json> "<node name>" <fixture.json>
//
// fixture.json: {
//   "nodes":  { "<node name>": <json of its first item> },   // backs $('Name').first()
//   "input":  [ <json of each input item> ],                 // backs $input
//   "query":  { ... },                                       // merged into input[0].query
//   "env":    { ... },                                       // backs $env
//   "rawBody": "<string>"                                    // backs helpers.getBinaryDataBuffer
// }
// Prints the node's returned items as JSON, or {"error": "..."} if it throws.
const fs = require('fs');

const [, , workflowPath, nodeName, fixturePath] = process.argv;
const workflow = JSON.parse(fs.readFileSync(workflowPath, 'utf8'));
const node = workflow.nodes.find((n) => n.name === nodeName);
if (!node) throw new Error(`node not found: ${nodeName}`);
const fx = JSON.parse(fs.readFileSync(fixturePath, 'utf8'));

const items = (fx.input || [{}]).map((json) => ({ json }));
if (fx.query) items[0].json.query = fx.query;

const $ = (name) => {
  if (!(name in (fx.nodes || {}))) throw new Error(`fixture has no node ${name}`);
  return { first: () => ({ json: fx.nodes[name] }), item: { json: fx.nodes[name] } };
};
const $input = { first: () => items[0], all: () => items };
const ctx = { helpers: { getBinaryDataBuffer: async () => Buffer.from(fx.rawBody || '', 'utf8') } };

const AsyncFunction = Object.getPrototypeOf(async () => {}).constructor;
const run = new AsyncFunction('$', '$input', '$env', 'require', node.parameters.jsCode);

run.call(ctx, $, $input, fx.env || {}, require)
  .then((out) => console.log(JSON.stringify(out)))
  .catch((err) => console.log(JSON.stringify({ error: err.message })));
