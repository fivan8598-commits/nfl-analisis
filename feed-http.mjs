const SITE = 'https://campo-nfl-tribuna.lissomegardenia2.chatgpt.site';
const AUDIENCE = 'niftl-feed-refresh';
const FEEDS = new Set(['cartelera', 'disponibilidad', 'reportes', 'resultados', 'noticias']);
const REFRESH_MARGIN = 30_000;

function expiry(value) {
  if (typeof value !== 'string' || value.length > 16_000 || !/^[\w-]+\.[\w-]+\.[\w-]+$/.test(value)) return 0;
  try {
    const claims = JSON.parse(Buffer.from(value.split('.')[1], 'base64url').toString('utf8'));
    return Number.isSafeInteger(claims.exp) ? claims.exp * 1000 : 0;
  } catch {
    return 0;
  }
}

async function json(response, limit) {
  if (!response.body) throw new SyntaxError('La respuesta no contiene JSON.');
  const chunks = [];
  let size = 0;
  for await (const chunk of response.body) {
    size += chunk.length;
    if (size > limit) throw new Error('La respuesta supera el tamaño admitido.');
    chunks.push(chunk);
  }
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}

async function get(transport, url, bearer, timeout, noCache = false) {
  const headers = {Accept: 'application/json', Authorization: 'Bearer ' + bearer};
  if (noCache) headers['Cache-Control'] = 'no-cache';
  return transport(url, {
    method: 'GET', headers, redirect: 'error', signal: AbortSignal.timeout(timeout),
  });
}

export function createFeedReader({env = process.env, transport = fetch, clock = Date.now} = {}) {
  let cached, pending;

  async function issue() {
    const {ACTIONS_ID_TOKEN_REQUEST_URL, ACTIONS_ID_TOKEN_REQUEST_TOKEN} = env;
    let url;
    try {
      url = new URL(ACTIONS_ID_TOKEN_REQUEST_URL);
    } catch {
      throw new Error('Falta la configuración OIDC de GitHub Actions.');
    }
    if (url.protocol !== 'https:' || url.username || url.password || url.hash
        || !/^[!-~]{1,16000}$/.test(ACTIONS_ID_TOKEN_REQUEST_TOKEN || '')) {
      throw new Error('Configuración OIDC de GitHub Actions inválida.');
    }
    url.searchParams.set('audience', AUDIENCE);
    try {
      const response = await get(transport, url, ACTIONS_ID_TOKEN_REQUEST_TOKEN, 8_000);
      if (!response.ok) {
        await response.body?.cancel();
        throw Error();
      }
      const {value} = await json(response, 32_000);
      const expires = expiry(value);
      if (expires <= clock()) throw Error();
      cached = {value, expires};
      return value;
    } catch {
      throw new Error('GitHub no entregó una credencial OIDC válida.');
    }
  }

  function bearer(rejected) {
    if (cached && cached.value !== rejected && cached.expires > clock() + REFRESH_MARGIN) return cached.value;
    pending ??= issue().finally(() => { pending = null; });
    return pending;
  }

  return async function readFeed(name, {timeout = 6_000, noCache = false} = {}) {
    if (!FEEDS.has(name)) throw new Error('Fuente de Niftl desconocida.');
    const url = new URL('/api/' + name, SITE);
    const credential = await bearer();
    let response = await get(transport, url, credential, timeout, noCache);
    if (response.status === 401 || response.status === 403) {
      await response.body?.cancel();
      response = await get(transport, url, await bearer(credential), timeout, noCache);
    }
    if (response.status !== 200) {
      await response.body?.cancel();
      return {status: response.status, body: null};
    }
    return {status: response.status, body: await json(response, 6_000_000)};
  };
}
