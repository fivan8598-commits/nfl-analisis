import {pathToFileURL} from 'node:url';
import {setTimeout as sleep} from 'node:timers/promises';
import {createFeedReader,nflTeams} from './feed-http.mjs';

const hour = 3600000;
const endpoints = ['disponibilidad','reportes','resultados'];
const retryDelays = [5_000,7_000,13_000];
const depthBatchSize=8;
const depthWindow=15_000;
const dateValue = text => Number.isFinite(Date.parse(text));
const valid = {
  cartelera: data => Array.isArray(data?.games) && dateValue(data.updatedAt),
  disponibilidad: data => Array.isArray(data?.reports) && dateValue(data.updatedAt),
  reportes: data => Array.isArray(data?.reports) && dateValue(data.checkedAt),
  resultados: data => Array.isArray(data?.games) &&
    ['finals','teamGames','individualGames'].every(key=>Number.isInteger(data.coverage?.[key]))
};

function liveWindow(game,now) {
  const kickoff = Date.parse(game.kickoff);
  return game.status==='live' ||
    game.status==='scheduled' && kickoff>=now-4*hour && kickoff<=now+2*hour ||
    game.status==='final' && kickoff<=now && now-kickoff<=6*hour;
}

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

async function depthInBatches(teams,check) {
  const results=[];
  for(let i=0;i<teams.length;i+=depthBatchSize) results.push(...await Promise.all(teams.slice(i,i+depthBatchSize).map(check)));
  return results;
}

async function refreshDepth(teams,read,pause) {
  if(!teams.length)return null;
  async function check(team) {
    try {
      const {status,body}=await read('profundidad',{team});
      if(status!==200)return {team,status};
      if(body?.team===team&&Array.isArray(body.slots)&&Array.isArray(body.sources)) return {team,status,body};
    } catch { /* Preserve other teams when one request fails. */ }
    return {team,status:null};
  }
  const first=await check(teams[0]);
  // Existing deployments keep their data refreshes while the depth route rolls out.
  if([401,403,404].includes(first.status)) return `profundidad: consulta no disponible (HTTP ${first.status})`;
  const rows=[first,...await depthInBatches(teams.slice(1),check)];
  const pending=rows.filter(row=>row.body?.refreshing===true).map(row=>row.team);
  if(pending.length) {
    await pause(depthWindow);
    const updated=new Map((await depthInBatches(pending,check)).filter(row=>row.body).map(row=>[row.team,row]));
    for(let i=0;i<rows.length;i++) if(updated.has(rows[i].team)) rows[i]=updated.get(rows[i].team);
  }
  const hasChart=body=>Boolean(body?.slots.length)&&dateValue(body.updatedAt);
  const charts=rows.filter(row=>hasChart(row.body));
  const checked=rows.map(row=>row.body?.checkedAt).filter(dateValue).sort();
  const verified=charts.filter(row=>!row.body.stale&&row.body.refreshing!==true).length;
  const stillPending=rows.filter(row=>row.body?.refreshing===true).length;
  const issues=rows.filter(row=>!hasChart(row.body)).map(row=>row.team);
  return [`profundidad: ${charts.length}/${teams.length} equipos con alineación`,`${verified} vigentes`,
    checked.length&&'última consulta '+checked.at(-1),stillPending&&`${stillPending} en consulta`,
    issues.length&&'sin corte: '+issues.join(', ')].filter(Boolean).join(' · ');
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
  const uncertain=!calendar.ok||refreshing(calendar)||calendar.body.stale;
  const active=games.filter(game=>liveWindow(game,now));
  if (mode==='game-window' && !uncertain && !active.length) {
    return {ok:true,skipped:true,lines:[previousRound,'Sin partidos en la ventana de actualización intensiva.']};
  }

  const expectedFinals = recentFinals(games,now);
  const nearClubs=new Set(active.flatMap(game=>[game.away,game.home]));
  const teams=mode==='all'||uncertain?nflTeams:nflTeams.filter(team=>nearClubs.has(team));
  const [feeds,depth]=await Promise.all([loadWithRetry(endpoints,expectedFinals),refreshDepth(teams,read,pause)]);
  const lines=[previousRound,...feeds.map((response,index)=>line(endpoints[index],response,expectedFinals))];
  if(depth)lines.push(depth);
  return {ok:calendar.ok && feeds.every(result=>result.ok),skipped:false,lines};
}

if (process.argv[1] && import.meta.url===pathToFileURL(process.argv[1]).href) {
  const mode = process.env.NIFTL_MODE || 'all';
  const report = await refreshNiftl({mode});
  const output = report.lines.join('\n')+'\n';
  process.stdout.write(output);
  if (!report.ok) process.exitCode=1;
}
