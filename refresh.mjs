import {execFile} from 'node:child_process';
import {pathToFileURL} from 'node:url';
import {promisify} from 'node:util';
import {setTimeout as sleep} from 'node:timers/promises';

const exec = promisify(execFile);
const site = 'https://campo-nfl-tribuna.lissomegardenia2.chatgpt.site';
const hour = 3600000;
const endpoints = ['disponibilidad','reportes','resultados'];
const dateValue = text => Number.isFinite(Date.parse(text));
const valid = {
  cartelera: data => Array.isArray(data?.games) && dateValue(data.updatedAt),
  disponibilidad: data => Array.isArray(data?.reports) && dateValue(data.updatedAt),
  reportes: data => Array.isArray(data?.reports) && dateValue(data.checkedAt),
  resultados: data => Array.isArray(data?.games) &&
    ['finals','teamGames','individualGames'].every(key=>Number.isInteger(data.coverage?.[key]))
};

async function readNiftl(name) {
  const {stdout} = await exec('curl',[
    '--silent','--show-error','--proto','=https','--connect-timeout','3','--max-time','6',
    '--header','Accept: application/json','--write-out','\n%{http_code}',site+'/api/'+name
  ],{encoding:'utf8',maxBuffer:6_000_000});
  const split = stdout.lastIndexOf('\n');
  const status = Number(stdout.slice(split+1));
  const body = status===200 ? JSON.parse(stdout.slice(0,split)) : null;
  return {status,body};
}

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

function recheck(name,response,expectedFinals) {
  if (!response.ok || response.pending || response.body?.stale===true) return true;
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

export async function refreshNiftl({mode='all',now=Date.now(),read=readNiftl,pause=sleep}={}) {
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

  let calendar = await load('cartelera');
  if (recheck('cartelera',calendar,[])) {
    await pause(5000);
    const updated = await load('cartelera');
    if (updated.ok || !calendar.ok) calendar=updated;
  }
  const games = calendar.body?.games || [];
  const previousRound = line('cartelera',calendar,[]);
  if (mode==='game-window' && calendar.ok && !calendar.body.stale && !activity(games,now)) {
    return {ok:true,skipped:true,lines:[previousRound,'Sin partidos en la ventana de actualización intensiva.']};
  }

  const expectedFinals = recentFinals(games,now);
  let feeds = await Promise.all(endpoints.map(load));
  const retry = feeds.map((response,index)=>recheck(endpoints[index],response,expectedFinals));
  if (retry.some(Boolean)) {
    await pause(5000);
    feeds = await Promise.all(feeds.map(async (response,index)=>{
      if (!retry[index]) return response;
      const updated = await load(endpoints[index]);
      return updated.ok || !response.ok ? updated : response;
    }));
  }
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
