#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';

const NODE_TYPES = new Set(['text', 'file', 'link', 'group']);
const SIDES = new Set(['top', 'right', 'bottom', 'left']);
const ENDS = new Set(['none', 'arrow']);
const COLOR_RE = /^(?:[1-6]|#[0-9a-fA-F]{6})$/;
const HEX_ID_RE = /^[0-9a-f]{16}$/;

function fail(errors, msg) {
  errors.push(msg);
}

function isObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function validateColor(errors, where, color) {
  if (color === undefined) return;
  if (typeof color !== 'string' || !COLOR_RE.test(color)) {
    fail(errors, `${where}: color must be "1"-"6" or #RRGGBB`);
  }
}

function validateCanvas(file) {
  const errors = [];
  let canvas;
  try {
    canvas = JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch (err) {
    return [`${file}: invalid JSON: ${err.message}`];
  }

  if (!isObject(canvas)) {
    return [`${file}: top level must be an object`];
  }

  const nodes = canvas.nodes ?? [];
  const edges = canvas.edges ?? [];
  if (!Array.isArray(nodes)) fail(errors, 'top level nodes must be an array');
  if (!Array.isArray(edges)) fail(errors, 'top level edges must be an array');
  if (errors.length) return errors;

  const ids = new Set();
  const nodeIds = new Set();

  nodes.forEach((node, index) => {
    const where = `nodes[${index}]`;
    if (!isObject(node)) {
      fail(errors, `${where}: node must be an object`);
      return;
    }
    if (typeof node.id !== 'string' || !node.id) {
      fail(errors, `${where}: id is required`);
    } else {
      if (!HEX_ID_RE.test(node.id)) fail(errors, `${where}: id should be 16 lowercase hex characters`);
      if (ids.has(node.id)) fail(errors, `${where}: duplicate id ${node.id}`);
      ids.add(node.id);
      nodeIds.add(node.id);
    }
    if (!NODE_TYPES.has(node.type)) fail(errors, `${where}: invalid type ${JSON.stringify(node.type)}`);
    for (const key of ['x', 'y', 'width', 'height']) {
      if (!Number.isInteger(node[key])) fail(errors, `${where}: ${key} must be an integer`);
    }
    if (Number.isInteger(node.width) && node.width <= 0) fail(errors, `${where}: width must be positive`);
    if (Number.isInteger(node.height) && node.height <= 0) fail(errors, `${where}: height must be positive`);
    validateColor(errors, where, node.color);

    if (node.type === 'text' && typeof node.text !== 'string') fail(errors, `${where}: text node requires string text`);
    if (node.type === 'file' && typeof node.file !== 'string') fail(errors, `${where}: file node requires string file`);
    if (node.type === 'link' && typeof node.url !== 'string') fail(errors, `${where}: link node requires string url`);
    if (node.type === 'file' && node.subpath !== undefined && (typeof node.subpath !== 'string' || !node.subpath.startsWith('#'))) {
      fail(errors, `${where}: subpath must start with #`);
    }
    if (node.type === 'group') {
      if (node.label !== undefined && typeof node.label !== 'string') fail(errors, `${where}: label must be a string`);
      if (node.background !== undefined && typeof node.background !== 'string') fail(errors, `${where}: background must be a string`);
      if (node.backgroundStyle !== undefined && !['cover', 'ratio', 'repeat'].includes(node.backgroundStyle)) {
        fail(errors, `${where}: backgroundStyle must be cover, ratio, or repeat`);
      }
    }
  });

  edges.forEach((edge, index) => {
    const where = `edges[${index}]`;
    if (!isObject(edge)) {
      fail(errors, `${where}: edge must be an object`);
      return;
    }
    if (typeof edge.id !== 'string' || !edge.id) {
      fail(errors, `${where}: id is required`);
    } else {
      if (!HEX_ID_RE.test(edge.id)) fail(errors, `${where}: id should be 16 lowercase hex characters`);
      if (ids.has(edge.id)) fail(errors, `${where}: duplicate id ${edge.id}`);
      ids.add(edge.id);
    }
    for (const key of ['fromNode', 'toNode']) {
      if (typeof edge[key] !== 'string' || !edge[key]) fail(errors, `${where}: ${key} is required`);
      else if (!nodeIds.has(edge[key])) fail(errors, `${where}: ${key} references missing node ${edge[key]}`);
    }
    for (const key of ['fromSide', 'toSide']) {
      if (edge[key] !== undefined && !SIDES.has(edge[key])) fail(errors, `${where}: ${key} must be top/right/bottom/left`);
    }
    for (const key of ['fromEnd', 'toEnd']) {
      if (edge[key] !== undefined && !ENDS.has(edge[key])) fail(errors, `${where}: ${key} must be none/arrow`);
    }
    if (edge.label !== undefined && typeof edge.label !== 'string') fail(errors, `${where}: label must be a string`);
    validateColor(errors, where, edge.color);
  });

  return errors;
}

const files = process.argv.slice(2);
if (!files.length) {
  console.error(`Usage: ${path.basename(process.argv[1])} <file.canvas> [...]`);
  process.exit(2);
}

let failed = false;
for (const file of files) {
  const errors = validateCanvas(file);
  if (errors.length) {
    failed = true;
    console.error(`✗ ${file}`);
    for (const error of errors) console.error(`  - ${error}`);
  } else {
    console.log(`✓ ${file}`);
  }
}
process.exit(failed ? 1 : 0);
