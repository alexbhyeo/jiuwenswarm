import assert from 'node:assert/strict';
import test from 'node:test';
import {
  findReferencedAssets,
  samePath,
  validateAssetName,
  withAssetReferenceNote,
} from '../node_modules/.cache/session-assets/assetReferences.mjs';

const asset = (name, path = `C:\\files\\${name}.png`, kind = 'image') => ({
  asset_id: `id-${name}`, name, kind, path, source: 'upload', created_at: 0,
});

test('finds referenced assets, longest name first, case-insensitively', () => {
  const assets = [asset('Oat'), asset('Oat Serum')];
  const found = findReferencedAssets('make a video of @oat serum next to @Oat', assets);
  assert.deepEqual(found.map((a) => a.name), ['Oat Serum', 'Oat']);
});

test('does not match a name that is only a prefix of a longer word', () => {
  assert.deepEqual(findReferencedAssets('see @foxes', [asset('fox')]), []);
  assert.deepEqual(findReferencedAssets('see @fox, then', [asset('fox')]).length, 1);
  assert.deepEqual(findReferencedAssets('看 @狐狸。', [asset('狐狸')]).length, 1);
});

test('no @ or no assets leaves the text untouched', () => {
  assert.equal(withAssetReferenceNote('plain text', [asset('fox')]), 'plain text');
  assert.equal(withAssetReferenceNote('hi @fox', []), 'hi @fox');
  assert.equal(withAssetReferenceNote('hi @nobody', [asset('fox')]), 'hi @nobody');
});

test('appends a path note for each referenced asset once', () => {
  const text = withAssetReferenceNote('use @fox and @fox again', [asset('fox', 'C:\\a\\fox.png')]);
  assert.equal(text, 'use @fox and @fox again\n\n[引用素材]\n@fox = C:\\a\\fox.png (image)');
});

test('validates asset names like the backend', () => {
  assert.deepEqual(validateAssetName('  Oat   Serum '), { name: 'Oat Serum' });
  for (const bad of ['', '   ', 'a@b', 'x'.repeat(61), 'two\nlines']) {
    assert.deepEqual(validateAssetName(bad), { error: 'invalid' });
  }
});

test('compares Windows paths ignoring case and slash direction', () => {
  assert.equal(samePath('C:\\A\\b.png', 'c:/a/B.PNG'), true);
  assert.equal(samePath('C:\\A\\b.png', 'C:\\A\\c.png'), false);
});
