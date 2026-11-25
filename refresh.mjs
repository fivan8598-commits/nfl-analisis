import {pathToFileURL} from 'node:url';
import {setTimeout as sleep} from 'node:timers/promises';
import {createFeedReader} from './feed-http.mjs';

const hour = 3600000;
const endpoints = ['disponibilidad','reportes','resultados'];
const retryDelays = [5_000,7_000,13_000];
const dateValue = text => Number.isFinite(Date.parse(text));
const valid = {
  cartelera: data => Array.isArray(data?.games) && dateValue(data.updatedAt),
  disponibilidad: data => Array.isArray(data?.reports) && dateValue(data.updatedAt),
  reportes: data => Array.isArray(data?.reports) && dateValue(data.checkedAt),
  resultados: data => Array.isArray(data?.games) &&
    ['finals','teamGames','individualGames'].every(key=>Number.isInteger(data.coverage?.[key]))
};

const activity = (games,now) => games.some(game=>{
  const kickoff = Date.parse(game.kickoff);
  return game.status==='live' ||
    game.status==='scheduled' && kickoff>=now-4*hour && kickoff<=now+2*hour ||
    game.status==='final' && kickoff<=now && now-kickoff<=6*hour;
});

const recentFinals = (games,now) => games.filter(game =>
  game.status==='final' && game.seasonType!==1 &&
  Date.parse(game.kickoff)<=now && now-Date.parse(game.kickoff)<=8*hour
).map(game=>String(game.id));

function missingRecent(response,expectedFinals) {
  const ids = new Set((response?.body?.games || []).map(game=>String(game.id)));
  return expectedFinals.filter(id=>!ids.has(id));
}

const refreshing = response => response.body?.refreshing===true;

function recheck(name,response,expectedFinals) {
  if (!response.ok || response.pending || refreshing(response) || response.body?.stale===true) return true;
  if (name!=='resultados') return false;
  return !dateValue(response.body.checkedAt) || missingRecent(response,expectedFinals).length>0;
}

function line(name,response,expectedFinals) {
  if (!response.ok) return name+': '+response.error;
  if (response.pending) return name+': primer corte en proceso';
  const {body} = response;
  const details = [body.checkedAt || 'revisión aún sin registrar'];
  if (body.updatedAt && body.updatedAt!==body.checkedAt) details.push('corte '+body.updatedAt);
  if (body.stale) details.push('último corte guardado');
  if (refreshing(response)) details.push('consulta en curso');
  if (name==='resultados') {
    const {teamGames,individualGames,finals} = body.coverage;
    details.push(teamGames+'/'+finals+' actas de equipo y '+individualGames+' con estadísticas individuales');
    const missing = missingRecent(response,expectedFinals);
    if (missing.length) details.push(missing.length+' final(es) reciente(s) en revisión');
  } else if (name==='reportes') {
    details.push((body.covered?.length || 0)+' juegos con revisión de club');
  }
  if (body.warning) details.push(body.warning);
  return name+': '+details.join(' · ');
}

export async function refreshNiftl({mode='all',now=Date.now(),read=createFeedReader(),pause=sleep}={}) {
  async function load(name) {
    try {
      const {status,body} = await read(name);
      if (status===204 && name==='reportes') return {ok:true,pending:true};
      if (status!==200) return {ok:false,error:'HTTP '+status};
      if (!valid[name](body)) return {ok:false,error:'Formato inesperado'};
      return {ok:true,body};
    } catch (error) {
      return {ok:false,error:error instanceof SyntaxError?'JSON inválido':'No se pudo consultar el sitio'};
    }
  }

  async function loadWithRetry(names,expectedFinals) {
    let snapshots=await Promise.all(names.map(load));
    let needsRead=snapshots.map((response,index)=>recheck(names[index],response,expectedFinals));
    // Only a background consultation gets the full window of 25 seconds.
    for (const delay of retryDelays) {
      if (!needsRead.some(Boolean)) break;
      await pause(delay);
      snapshots=await Promise.all(snapshots.map(async (previous,index)=>{
        if (!needsRead[index]) return previous;
        const updated=await load(names[index]);
        return updated.ok||!previous.ok?updated:previous;
      }));
      needsRead=snapshots.map((response,index)=>needsRead[index]&&refreshing(response));
    }
    return snapshots;
  }

  const [calendar]=await loadWithRetry(['cartelera'],[]);
  const games = calendar.body?.games || [];
  const previousRound = line('cartelera',calendar,[]);
  if (mode==='game-window' && calendar.ok && !refreshing(calendar) && !calendar.body.stale && !activity(games,now)) {
    return {ok:true,skipped:true,lines:[previousRound,'Sin partidos en la ventana de actualización intensiva.']};
  }

  const expectedFinals = recentFinals(games,now);
  const feeds=await loadWithRetry(endpoints,expectedFinals);
  return {ok:calendar.ok && feeds.every(result=>result.ok),skipped:false,
    lines:[previousRound,...feeds.map((response,index)=>line(endpoints[index],response,expectedFinals))]};
}

if (process.argv[1] && import.meta.url===pathToFileURL(process.argv[1]).href) {
  const mode = process.env.NIFTL_MODE || 'all';
  const report = await refreshNiftl({mode});
  const output = report.lines.join('\n')+'\n';
  process.stdout.write(output);
  if (!report.ok) process.exitCode=1;
}
