import assert from 'node:assert/strict';
import test from 'node:test';
import {refreshNiftl} from './refresh.mjs';

const now = Date.parse('2026-11-27T04:00:00Z');
const checkedAt = new Date(now).toISOString();
const kickoff = new Date(now-4*3600000).toISOString();
const final = {id:'401123456',status:'final',seasonType:2,kickoff};
const valid = {
  cartelera:{updatedAt:checkedAt,games:[]},
  disponibilidad:{updatedAt:checkedAt,reports:[]},
  reportes:{checkedAt,reports:[],covered:[]},
  resultados:{checkedAt,games:[],coverage:{teamGames:0,individualGames:0,finals:0}}
};
const response = body => ({status:200,body});

test('la ventana intensiva sin juegos consulta solo la cartelera',async()=>{
  const calls=[];
  const out=await refreshNiftl({mode:'game-window',now,read:async name=>{
    calls.push(name);return response(valid[name]);
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
    return response(valid[name]);
  }});
  assert.deepEqual(calls,{cartelera:1,disponibilidad:1,reportes:2,resultados:2});
  assert.deepEqual(pauses,[5000]);
  assert.match(out.lines.at(-1),/1\/1 actas/);
  assert.equal(out.ok,true);
});

test('relee una cartelera guardada y reporta un fallo HTTP persistente',async()=>{
  let calendarCalls=0;
  const out=await refreshNiftl({now,pause:async()=>{},read:async name=>{
    if(name==='cartelera')return response({...valid[name],stale:++calendarCalls===1});
    if(name==='disponibilidad')return {status:503};
    return response(valid[name]);
  }});
  assert.equal(calendarCalls,2);
  assert.equal(out.ok,false);
  assert.ok(out.lines.includes('disponibilidad: HTTP 503'));
});
