import assert from 'node:assert/strict';
import test from 'node:test';
import {refreshNews} from './refresh-news.mjs';

const time = Date.parse('2026-11-25T16:48:00Z');
const checkedAt = new Date(time).toISOString();
const published = {news: [{title: 'Titular'}], checkedAt, updatedAt: checkedAt, refreshing: false};
const checks = (read, pauses = []) => refreshNews({now: () => time, read, pause: async delay => pauses.push(delay)});
const reply = body => ({status: 200, body});

test('relee un refresco de noticias en curso y avisa si la cobertura es parcial', async () => {
  const pauses = [];
  let attempts = 0;
  const result = await checks(async (name, options) => {
    assert.equal(name, 'noticias');
    assert.deepEqual(options, {timeout: 8_000, noCache: true});
    if (++attempts === 1) return reply({...published, refreshing: true});
    return reply({...published, reason: 'partial', stale: true});
  }, pauses);
  assert.deepEqual(pauses, [3_000]);
  assert.equal(result.ok, true);
  assert.match(result.warning, /Cobertura parcial/);
  assert.match(result.message, /1 titulares/);
});

test('reintenta fallas transitorias y conserva los errores de vigencia', async () => {
  const pauses = [];
  let attempts = 0;
  const result = await checks(async () => ++attempts === 1 ? {status: 503}
    : reply({...published, updatedAt: new Date(time - 121_000).toISOString()}), pauses);
  assert.deepEqual(pauses, [1_000]);
  assert.equal(result.ok, false);
  assert.match(result.error, /caché vieja/);
});

test('los cortes incompletos y persistentes no pasan la verificación', async () => {
  const malformed = await checks(async () => reply({news: []}));
  assert.match(malformed.error, /inválido o incompleto/);
  const pauses = [];
  const pending = await checks(async () => reply({...published, refreshing: true}), pauses);
  assert.deepEqual(pauses, [3_000, 5_000, 7_000, 7_000, 8_000]);
  assert.match(pending.error, /aún no tiene un corte/);
});
