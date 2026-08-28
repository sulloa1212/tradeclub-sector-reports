/* Regression guard for the breakeven calculator's generated JavaScript.
 *
 * This repo has no test suite, and a syntax error or a changed number in the
 * JS that gap_engine._be_calc emits would only surface once a report is live.
 * So before publishing a change to _be_calc:
 *
 *   1. render the script without touching the API:
 *        python tools/render_be.py            -> /tmp/be.js
 *   2. node --check /tmp/be.js                -> syntax
 *   3. node tools/check_be_regression.mjs     -> numbers
 *
 * It asserts that overnight and 1-week come out bit-identical to the values the
 * published report already carries, so an additive change cannot quietly move
 * a horizon that was working.
 */
/* Run the NEW generated calculator logic against the OLD published parameters.
   Overnight and 1-week must come out bit-identical: the exit branch is additive,
   and the tenor lines are pinned to pass through those two anchors. */
import fs from 'fs';
const js = fs.readFileSync('/tmp/be.js','utf8');

// expose the internals the IIFE keeps private
// replace the LAST '})();' — there is an inner IIFE earlier in the file
const i = js.lastIndexOf('})();');
const patched = js.slice(0,i) + 'globalThis.__T={params,skewTenorN,driftTenorN,sessionsUntil,CFG,BE};})();' + js.slice(i+5);
// the index <select> must resolve to a real key, or the init code dereferences BE['']
const stub = () => ({addEventListener(){},style:{},classList:{toggle(){}},value:'spx',focus(){},
                     textContent:'',innerHTML:'',placeholder:'',min:''});
const dom = {getElementById:stub,
             querySelector:()=>({style:{},addEventListener(){},getAttribute:()=>''}),
             querySelectorAll:()=>[], documentElement:{style:{}}};
globalThis.document=dom; globalThis.window=globalThis;
globalThis.setInterval=()=>0; globalThis.Intl=Intl;
eval(patched);
const {params, skewTenorN, driftTenorN, CFG, BE} = globalThis.__T;

const d = BE.spx;
let bad = 0;
console.log('=== overnight y 1-week: nuevo vs parámetros publicados ===');
for (const h of ['on','wk']) {
  const p = params(d, h, 0, {f:1});
  const ref = d[h];
  for (const k of ['sd','su','mu']) {
    const same = Math.abs(p[k]-ref[k]) < 1e-12;
    if (!same) bad++;
    console.log(`  ${h}.${k.padEnd(2)}  nuevo ${p[k].toFixed(6)}  publicado ${ref[k].toFixed(6)}  ${same?'idéntico ✓':'CAMBIÓ ✗'}`);
  }
}
console.log('\n=== las rectas de tenor pasan por los anclajes ===');
const checks = [['skew N=1',skewTenorN(1),1],['skew N=5',skewTenorN(5),CFG.wkskew],
                ['drift N=1',driftTenorN(1),1],['drift N=5',driftTenorN(5),CFG.wkdrift]];
for (const [n,got,want] of checks){
  const ok = Math.abs(got-want)<1e-12; if(!ok) bad++;
  console.log(`  ${n.padEnd(10)} ${got.toFixed(4)}  esperado ${want.toFixed(4)}  ${ok?'✓':'✗'}`);
}
console.log('\n=== el horizonte nuevo produce números sanos ===');
for (const N of [1,5,10,20]) {
  const p = params(d,'exit',0,null,N);
  console.log(`  N=${String(N).padStart(2)}  1SD ±${p.sd1.toFixed(2)}%  sd/su ${(p.sd/p.su).toFixed(3)}  mu ${p.mu.toFixed(4)}`);
}
const p1 = params(d,'exit',0,null,1), pw = params(d,'exit',0,null,5);
console.log(`\n  N=5 contra el 1-week publicado: 1SD ${pw.sd1.toFixed(4)} vs ${d.wk.sd1.toFixed(4)}  ${Math.abs(pw.sd1-d.wk.sd1)<1e-9?'idéntico ✓':'difiere'}`);
console.log(`  fecha vacía -> ${params(d,'exit',0,null,0)===null?'null, la UI muestra el aviso ✓':'✗'}`);
process.exit(bad?1:0);
