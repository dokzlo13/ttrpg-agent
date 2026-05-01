---
name: ttrpg-rules-5etools-native
description: |
  Native 5etools JS workflow for canonical records not exposed by query_5etools
  or for schema/renderer spelunking. Use for class/subclass progression, feats,
  backgrounds, 2014/2024 representation details, or one-off transformations
  that require direct reads from imports/5etools/.
---

# ttrpg-rules-5etools-native

## When to use this skill

Use this skill when the built-in `query_5etools` tool is **close, but not enough**.

Typical cases:

- You need an entity type the simple tool does not expose yet.
- You need a very specific 5etools renderer/helper.
- You want to inspect how 2014/2024 variants are represented in raw data.
- You need a one-off extraction or transformation from `imports/5etools/`.
- You are debugging schema weirdness instead of answering a normal game-prep query.
- You need canonical class progression facts, e.g. "what does Paladin get at level 5?"

For normal creature/spell/item lookups, use **`query_5etools` first**.

## Ground rules

- `imports/5etools/` is a **read-only clone**.
- Prefer **native JS helpers/renderers** over reimplementing parsing logic.
- Keep ad-hoc scripts **small and local**: `node --input-type=module <<'EOF' ... EOF`.
- If the task becomes common, promote it into the `query_5etools` extension instead of repeating shell snippets forever.

## Minimal loader pattern

Run from the project root. This imports the browser-ish globals and enables Node-side JSON loading:

```bash
node --input-type=module <<'EOF'
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const root = path.resolve(process.env.TTRPG_5ETOOLS_DIR || 'imports/5etools');
const load = (rel) => import(pathToFileURL(path.join(root, rel)).href);

await load('js/parser.js');
await load('js/utils.js');
await load('js/utils-ui.js');
await load('js/render.js');
await load('js/render-dice.js');
await load('js/hist.js');
await load('js/utils-dataloader.js');
await load('js/utils-config.js');
await load('js/filter.js');
await load('js/utils-brew.js');
await load('js/omnidexer.js');
await load('js/render-markdown.js');
const ut = await load('node/util.js');

ut.patchLoadJson();
// ...your code here...
EOF
```

## Native APIs worth reaching for

### Load data

```js
const spells = await globalThis.DataUtil.loadJSON(path.join(root, 'data/spells/spells-phb.json'));
const allMonsters = await globalThis.DataUtil.monster.pLoadAll();
const allSpells = await globalThis.DataUtil.spell.pLoadAll();
const classes = await globalThis.DataUtil.loadJSON(path.join(root, 'data/class/class-paladin.json'));
```

### Helpful parsers

- `Parser.sourceJsonToAbv(source)`
- `Parser.sourceJsonToFull(source)`
- `Parser.sizeAbvToFull(size)`
- `Parser.alignmentListToFull(alignment)`
- `Parser.monTypeToFullObj(type).asText`
- `Parser.spLevelSchoolMetaToFull(level, school, meta, subschools)`
- `Parser.itemValueToFull(item)`
- `Parser.itemWeightToFull(item)`

### Markdown renderers

- Spells:
  ```js
  RendererMarkdown.spell.getCompactRenderedString(spell)
  ```
- Items:
  ```js
  RendererMarkdown.item.getCompactRenderedString(item)
  ```
  If that throws on a weird base-item case, fall back to a custom summary.
- Monsters:
  ```js
  await RendererMarkdown.exporting.pGetMarkdownDoc({ents:[monster], prop:'monster'})
  ```
  Prefer this over `RendererMarkdown.monster.getCompactRenderedString(...)`.

## Ready-made snippets

### Render one spell

```bash
node --input-type=module <<'EOF'
import path from 'node:path';
import { pathToFileURL } from 'node:url';
const root = path.resolve(process.env.TTRPG_5ETOOLS_DIR || 'imports/5etools');
const load = (rel) => import(pathToFileURL(path.join(root, rel)).href);
await load('js/parser.js'); await load('js/utils.js'); await load('js/utils-ui.js');
await load('js/render.js'); await load('js/render-dice.js'); await load('js/hist.js');
await load('js/utils-dataloader.js'); await load('js/utils-config.js');
await load('js/filter.js'); await load('js/utils-brew.js'); await load('js/omnidexer.js');
await load('js/render-markdown.js');
const ut = await load('node/util.js');
ut.patchLoadJson();
const data = await globalThis.DataUtil.loadJSON(path.join(root, 'data/spells/spells-phb.json'));
const spell = data.spell.find(it => it.name === 'Fireball');
console.log(globalThis.RendererMarkdown.spell.getCompactRenderedString(spell));
EOF
```

### Render one monster statblock

```bash
node --input-type=module <<'EOF'
import path from 'node:path';
import { pathToFileURL } from 'node:url';
const root = path.resolve(process.env.TTRPG_5ETOOLS_DIR || 'imports/5etools');
const load = (rel) => import(pathToFileURL(path.join(root, rel)).href);
await load('js/parser.js'); await load('js/utils.js'); await load('js/utils-ui.js');
await load('js/render.js'); await load('js/render-dice.js'); await load('js/hist.js');
await load('js/utils-dataloader.js'); await load('js/utils-config.js');
await load('js/filter.js'); await load('js/utils-brew.js'); await load('js/omnidexer.js');
await load('js/render-markdown.js');
const ut = await load('node/util.js');
ut.patchLoadJson();
const data = await globalThis.DataUtil.loadJSON(path.join(root, 'data/bestiary/bestiary-mm.json'));
const monster = data.monster.find(it => it.name === 'Goblin');
console.log(await globalThis.RendererMarkdown.exporting.pGetMarkdownDoc({ents:[monster], prop:'monster'}));
EOF
```

### Inspect class features at a level

```bash
node --input-type=module <<'EOF'
import path from 'node:path';
import { pathToFileURL } from 'node:url';
const root = path.resolve(process.env.TTRPG_5ETOOLS_DIR || 'imports/5etools');
const load = (rel) => import(pathToFileURL(path.join(root, rel)).href);
await load('js/parser.js'); await load('js/utils.js'); await load('js/utils-ui.js');
await load('js/render.js'); await load('js/render-dice.js'); await load('js/hist.js');
await load('js/utils-dataloader.js'); await load('js/utils-config.js');
await load('js/filter.js'); await load('js/utils-brew.js'); await load('js/omnidexer.js');
await load('js/render-markdown.js');
const ut = await load('node/util.js');
ut.patchLoadJson();
const data = await globalThis.DataUtil.loadJSON(path.join(root, 'data/class/class-paladin.json'));
const paladin = data.class.find(it => it.name === 'Paladin');
console.log(JSON.stringify(paladin.classFeatures[4], null, 2)); // level 5 features
EOF
```

## Decision rule

- **Common query** → `query_5etools`
- **Weird one-off native 5etools spelunking** → this skill + `bash`
- **Repeated weirdness** → improve `query_5etools` so the next session does not need spelunking
