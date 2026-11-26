import assert from 'node:assert/strict';
import test from 'node:test';
import {refreshNiftl} from './refresh.mjs';
import {nflTeams} from './feed-http.mjs';

const now = Date.parse('2026-11-27T04:00:00Z');
const checkedAt = new Date(now).toISOString();
const kickoff = new Date(now-4*3600000).toISOString();
const final = {id:'401123456',status:'final',seasonType:2,kickoff,away:'GB',home:'LAR'};
const valid = {
  cartelera:{updatedAt:checkedAt,games:[]},
  disponibilidad:{updatedAt:checkedAt,reports:[]},
  reportes:{checkedAt,reports:[],covered:[]},
  resultados:{checkedAt,games:[],coverage:{teamGames:0,individualGames:0,finals:0}}
};
const response = body => ({status:200,body});
const fixture = name => name==='profundidad'?{status:404}:response(valid[name]);

test('la ventana intensiva sin juegos consulta solo la cartelera',async()=>{
  const calls=[];
  const out=await refreshNiftl({mode:'game-window',now,read:async name=>{
    calls.push(name);return fixture(name);
  }});
  assert.deepEqual(calls,['cartelera']);
  assert.equal(out.skipped,true);
  assert.equal(out.ok,true);
});

test('relee un final nuevo y el primer corte asíncrono de reportes',async()=>{
  const calls={},pauses=[];
  const out=await refreshNiftl({mode:'game-window',now,pause:async ms=>pauses.push(ms),read:async name=>{
    calls[name]=(calls[name]||0)+1;
    if(name==='cartelera')return response({...valid[name],games:[final]});
    if(name==='reportes'&&calls[name]===1)return {status:204};
    if(name==='resultados'&&calls[name]>1)return response({...valid[name],games:[final],coverage:{teamGames:1,individualGames:1,finals:1}});
    return fixture(name);
  }});
  assert.deepEqual(calls,{cartelera:1,disponibilidad:1,reportes:2,resultados:2,profundidad:1});
  assert.deepEqual(pauses,[5000]);
  assert.match(out.lines.find(line=>line.startsWith('resultados:')),/1\/1 actas/);
  assert.equal(out.ok,true);
});

test('relee una cartelera guardada y reporta un fallo HTTP persistente',async()=>{
  let calendarCalls=0;
  const out=await refreshNiftl({now,pause:async()=>{},read:async name=>{
    if(name==='cartelera')return response({...valid[name],stale:++calendarCalls===1});
    if(name==='disponibilidad')return {status:503};
    return fixture(name);
  }});
  assert.equal(calendarCalls,2);
  assert.equal(out.ok,false);
  assert.ok(out.lines.includes('disponibilidad: HTTP 503'));
});

test('espera la cartelera preliminar y usa el final descubierto para revisar las actas',async()=>{
  const calls={},pauses=[];
  const out=await refreshNiftl({mode:'game-window',now,pause:async ms=>pauses.push(ms),read:async name=>{
    calls[name]=(calls[name]||0)+1;
    if(name==='cartelera')return response(calls[name]<4?{...valid[name],refreshing:true}:{...valid[name],games:[final]});
    if(name==='resultados'&&calls[name]>1)return response({...valid[name],games:[final],coverage:{teamGames:1,individualGames:1,finals:1}});
    return fixture(name);
  }});
  assert.deepEqual(calls,{cartelera:4,disponibilidad:1,reportes:1,resultados:2,profundidad:1});
  assert.deepEqual(pauses,[5000,7000,13000,5000]);
  assert.equal(out.skipped,false);
  assert.equal(out.ok,true);
  assert.match(out.lines.find(line=>line.startsWith('resultados:')),/1\/1 actas/);
});

test('una cartelera que sigue actualizándose no puede cerrar la ventana de partidos',async()=>{
  const calls={},pauses=[];
  const out=await refreshNiftl({mode:'game-window',now,pause:async ms=>pauses.push(ms),read:async name=>{
    calls[name]=(calls[name]||0)+1;
    return name==='cartelera'?response({...valid[name],refreshing:true}):fixture(name);
  }});
  assert.deepEqual(calls,{cartelera:4,disponibilidad:1,reportes:1,resultados:1,profundidad:1});
  assert.deepEqual(pauses,[5000,7000,13000]);
  assert.equal(out.skipped,false);
  assert.match(out.lines[0],/consulta en curso/);
});

test('solo vuelve a pedir disponibilidad tras un fallo de transporte durante el sondeo',async()=>{
  const calls={},pauses=[];
  const out=await refreshNiftl({now,pause:async ms=>pauses.push(ms),read:async name=>{
    calls[name]=(calls[name]||0)+1;
    if(name==='disponibilidad'&&calls[name]===2)return {status:503};
    if(name==='disponibilidad'&&calls[name]<4)return response({...valid[name],refreshing:true});
    return fixture(name);
  }});
  assert.deepEqual(calls,{cartelera:1,disponibilidad:4,reportes:1,resultados:1,profundidad:1});
  assert.deepEqual(pauses,[5000,7000,13000]);
  assert.equal(out.ok,true);
  assert.doesNotMatch(out.lines[1],/consulta en curso|HTTP 503/);
});

const depth=(team,extra={})=>response({team,slots:[{position:'QB'}],sources:[],checkedAt,updatedAt:checkedAt,stale:false,...extra});

test('el ciclo general consulta 32 clubes y espera la actualización de los pendientes en lotes',async()=>{
  const calls=new Map(),pauses=[];
  let inFlight=0,peak=0;
  const out=await refreshNiftl({now,pause:async ms=>pauses.push(ms),read:async(name,{team}={})=>{
    if(name!=='profundidad')return fixture(name);
    calls.set(team,(calls.get(team)||0)+1);
    peak=Math.max(peak,++inFlight);
    await new Promise(resolve=>setImmediate(resolve));
    inFlight--;
    if(team==='ARI'&&calls.get(team)===1)return depth(team,{slots:[],checkedAt:null,updatedAt:null,refreshing:true});
    if(team==='BUF')return depth(team,{stale:true});
    if(team==='WAS')return {status:503};
    return depth(team);
  }});
  assert.deepEqual([...calls.keys()].sort(),[...nflTeams].sort());
  assert.equal(calls.get('ARI'),2);
  assert.ok([...calls.entries()].every(([team,count])=>count===(team==='ARI'?2:1)));
  assert.equal(peak,8);
  assert.deepEqual(pauses,[15000]);
  assert.match(out.lines.at(-1),/31\/32 equipos.*30 vigentes.*última consulta 2026.*sin corte: WAS/);
});

test('la ventana intensiva consulta profundidad de equipos que juegan cerca y conserva el resto del ciclo',async()=>{
  const teams=[];
  const game={...final,status:'scheduled',kickoff:new Date(now+3600000).toISOString()};
  const distant={...game,away:'KC',home:'PHI',kickoff:new Date(now+4*3600000).toISOString()};
  const out=await refreshNiftl({mode:'game-window',now,read:async(name,{team}={})=>{
    if(name==='cartelera')return response({...valid[name],games:[game,distant]});
    if(name==='profundidad'){teams.push(team);return depth(team);}
    return fixture(name);
  }});
  assert.deepEqual(teams,['GB','LAR']);
  assert.equal(out.skipped,false);
  assert.match(out.lines.at(-1),/2\/2 equipos.*2 vigentes/);
});
