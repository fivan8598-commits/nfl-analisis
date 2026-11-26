import assert from 'node:assert/strict';
import test from 'node:test';
import {createFeedReader} from './feed-http.mjs';

const oidcUrl = 'https://mock.actions.githubusercontent.com/token?api-version=2.0&other=kept';
const site = 'https://campo-nfl-tribuna.lissomegardenia2.chatgpt.site';
const requestToken = 'mock-runner-credential';
const env = {
  ACTIONS_ID_TOKEN_REQUEST_URL: oidcUrl,
  ACTIONS_ID_TOKEN_REQUEST_TOKEN: requestToken,
};
const now = 1_797_000_000_000;
const jwt = (id, expiry = now + 300_000) => 'e30.' + Buffer.from(JSON.stringify({exp: expiry / 1000})).toString('base64url') + '.' + id;

test('solicita la audiencia fija y usa la credencial de cada origen sin seguir redirecciones', async () => {
  let issuances = 0;
  const calls = [];
  const read = createFeedReader({env, clock: () => now, transport: async (url, options) => {
    assert.equal(options.method, 'GET');
    assert.equal(options.redirect, 'error');
    assert.ok(options.signal instanceof AbortSignal);
    if (url.hostname === 'mock.actions.githubusercontent.com') {
      issuances++;
      assert.equal(url.pathname, '/token');
      assert.equal(url.searchParams.get('api-version'), '2.0');
      assert.equal(url.searchParams.get('other'), 'kept');
      assert.deepEqual(url.searchParams.getAll('audience'), ['niftl-feed-refresh']);
      assert.equal(options.headers.Authorization, 'Bearer ' + requestToken);
      return Response.json({value: jwt('approved')});
    }
    calls.push(url.pathname+url.search);
    assert.equal(url.origin, site);
    assert.equal(options.headers.Authorization, 'Bearer ' + jwt('approved'));
    assert.equal(options.headers['Cache-Control'], url.pathname === '/api/noticias' ? 'no-cache' : undefined);
    return url.pathname === '/api/reportes' ? new Response(null, {status: 204}) : Response.json({ok: true});
  }});
  const [news, reports, depth] = await Promise.all([
    read('noticias', {noCache: true}),read('reportes'),read('profundidad',{team:'LAR'})
  ]);
  assert.deepEqual(news, {status: 200, body: {ok: true}});
  assert.deepEqual(reports, {status: 204, body: null});
  assert.deepEqual(depth, {status: 200, body: {ok: true}});
  assert.deepEqual(calls, ['/api/noticias', '/api/reportes', '/api/profundidad?team=LAR']);
  assert.equal(issuances, 1);
  await assert.rejects(read('encuestas'), /desconocida/);
  await assert.rejects(read('profundidad',{team:'LAR&team=GB'}), /desconocido/);
  assert.equal(calls.length, 3);
});

test('renueva una sola vez si el sitio rechaza el token o este está próximo a caducar', async () => {
  let issuances = 0;
  let time = now;
  const tokens = [];
  const read = createFeedReader({env, clock: () => time, transport: async (url, options) => {
    if (url.hostname === 'mock.actions.githubusercontent.com') {
      return Response.json({value: jwt('issue' + ++issuances, time + 300_000)});
    }
    tokens.push(options.headers.Authorization);
    return tokens.at(-1) === 'Bearer ' + jwt('issue1') ? new Response(null, {status: 403}) : Response.json([]);
  }});
  assert.equal((await read('cartelera')).status, 200);
  assert.deepEqual(tokens, ['Bearer ' + jwt('issue1'), 'Bearer ' + jwt('issue2')]);
  time += 275_000;
  assert.equal((await read('resultados')).status, 200);
  assert.equal(tokens.at(-1), 'Bearer ' + jwt('issue3', time + 300_000));
  assert.equal(issuances, 3);
});

test('exige HTTPS y un token usable sin devolver secretos en el error', async () => {
  let requests = 0;
  const noHttps = createFeedReader({env: {...env, ACTIONS_ID_TOKEN_REQUEST_URL: 'http://insecure.invalid/token'},
    transport: async () => { requests++; }});
  await assert.rejects(noHttps('noticias'), /Configuración OIDC.*inválida/);
  const issue = createFeedReader({env, clock: () => now, transport: async () => {
    requests++;
    return Response.json({value: jwt('expired', now - 1_000)});
  }});
  await assert.rejects(issue('noticias'), {message: 'GitHub no entregó una credencial OIDC válida.'});
  assert.equal(requests, 1);
});
