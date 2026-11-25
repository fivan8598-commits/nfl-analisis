import {pathToFileURL} from 'node:url';
import {setTimeout as sleep} from 'node:timers/promises';
import {createFeedReader} from './feed-http.mjs';

const delays = [0, 3, 5, 7, 7, 8];
const retryable = new Set([408, 429, 500, 502, 503, 504, 522, 524]);
const isoUtc = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/;
const failed = error => ({ok: false, error});
const invalid = () => failed('La ruta de noticias devolvió JSON inválido o incompleto.');

function recent(value, time) {
  const age = typeof value === 'string' && isoUtc.test(value) ? time - Date.parse(value) : NaN;
  return age >= -30_000 && age <= 120_000;
}

export async function refreshNews({read = createFeedReader(), pause = sleep, now = Date.now} = {}) {
  async function readNews() {
    let reply;
    try {
      reply = await read('noticias', {timeout: 8_000, noCache: true});
    } catch (error) {
      if (error instanceof SyntaxError) throw error;
    }
    if (reply && !retryable.has(reply.status)) return reply;
    await pause(1_000);
    return read('noticias', {timeout: 8_000, noCache: true});
  }

  let data;
  for (const delay of delays) {
    if (delay) await pause(delay * 1_000);
    let reply;
    try {
      reply = await readNews();
    } catch (error) {
      return error instanceof SyntaxError ? invalid() : failed('No se pudo consultar el corte de noticias.');
    }
    if (reply.status !== 200) return failed('La consulta de noticias devolvió HTTP ' + reply.status + '.');
    data = reply.body;
    if (!Array.isArray(data?.news) || typeof data.refreshing !== 'boolean') return invalid();
    if (!data.refreshing) break;
  }

  const state = data.refreshing ? 'refreshing' : data.reason === false ? 'ok' : data.reason ?? 'ok';
  if (state === 'refreshing' || state === 'unverified') return failed('La revisión aún no tiene un corte verificado.');
  if (state === 'sources-unavailable') return failed('Falló la consulta a las fuentes. Se conserva el último corte disponible.');
  if (state === 'storage-unavailable') return failed('No se pudo consultar el almacenamiento de noticias.');
  if (!['ok', 'partial'].includes(state)) return failed('Estado de noticias inesperado.');
  const time = now();
  if (!recent(data.checkedAt, time) || !recent(data.updatedAt, time) || !data.news.length) {
    return failed('La API devolvió caché vieja, sin titulares o sin un corte verificable.');
  }
  if (data.stale === true && state !== 'partial') return failed('El servidor marcó el corte como atrasado.');
  return {ok: true,
    warning: state === 'partial' ? 'Cobertura parcial. El corte conserva las noticias previas de las fuentes que fallaron.' : null,
    message: data.news.length + ' titulares · revisión ' + data.checkedAt + ' · corte ' + data.updatedAt,
  };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const result = await refreshNews();
  if (result.ok) {
    if (result.warning) console.log('::warning::' + result.warning);
    console.log(result.message);
  } else {
    console.error('::error::' + result.error);
    process.exitCode = 1;
  }
}
