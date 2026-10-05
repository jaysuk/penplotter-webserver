// Run with: node --test tests/theme.test.js
const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');

const theme = require(path.join(__dirname, '..', 'static', 'theme.js'));

test('the theme to show follows the choice, and the device only for "auto"', () => {
  assert.strictEqual(theme.resolve('light', true), 'light');
  assert.strictEqual(theme.resolve('dark', false), 'dark');
  assert.strictEqual(theme.resolve('auto', true), 'dark');
  assert.strictEqual(theme.resolve('auto', false), 'light');
  assert.strictEqual(theme.resolve('nonsense', true), 'light');
});

test('without storage the page is light and saving does not throw', () => {
  assert.strictEqual(theme.stored(), 'light');
  assert.doesNotThrow(() => theme.save('dark'));
  assert.doesNotThrow(() => theme.save('nonsense'));
});
