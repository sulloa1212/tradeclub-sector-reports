/* Cross-implementation check for the Expiration horizon.
 *
 * Runs the report's generated exit branch against Gap Scout's, which has been
 * live since 4 August with its own 1,903-comparison suite. Two independent
 * transcriptions of the same formulas: agreement to 0 means neither has a typo.
 *
 * Needs /tmp/be.js — run `python tools/render_be.py` first.
 */
/* Cross-check: the NEW exit branch in the report's generated JS against Gap
   Scout's exit horizon, which has been running since 4 August with 1,903
   numeric comparisons green. Two independent implementations of the same
   formulas — if they agree, neither has a transcription error. */
import fs from 'fs';
import * as S from '/Users/admin/Desktop/Trae-Projects/BABY-RHINO-GAP-SCOUT/app/scout.core.js';

const js = fs.readFileSync('/tmp/be.js','utf8');
const i = js.lastIndexOf('})();');
const patched = js.slice(0,i) + 'globalThis.__T={params,skewTenorN,driftTenorN,CFG,BE};})();' + js.slice(i+5);
const stub = () => ({addEventListener(){},style:{},classList:{toggle(){}},value:'spx',focus(){},
                     textContent:'',innerHTML:'',placeholder:'',min:''});
globalThis.document={getElementById:stub,querySelector:()=>({style:{},addEventListener(){},getAttribute:()=>''}),
                     querySelectorAll:()=>[],documentElement:{style:{}}};
globalThis.window=globalThis; globalThis.setInterval=()=>0;
eval(patched);
const {params, skewTenorN, driftTenorN} = globalThis.__T;
const d = globalThis.__T.BE.spx;

console.log('N   | report sd   scout sd   | report su   scout su   | report mu   scout mu   | max diff');
console.log('-'.repeat(92));
let worst = 0;
for (const N of [1,2,3,5,8,10,15,20,30,45]) {
  const r = params(d,'exit',0,null,N);
  const g = S.horizonParams({r:d.r, signal:d.sg, horizon:'exit', vol:d.vol, nDays:N});
  const diffs = [Math.abs(r.sd-g.sd), Math.abs(r.su-g.su), Math.abs(r.mu-g.mu)];
  const mx = Math.max(...diffs); worst = Math.max(worst, mx);
  console.log(`${String(N).padStart(3)} | ${r.sd.toFixed(6)}  ${g.sd.toFixed(6)}  |`
    + ` ${r.su.toFixed(6)}  ${g.su.toFixed(6)}  |`
    + ` ${r.mu.toFixed(6)}  ${g.mu.toFixed(6)}  | ${mx.toExponential(1)}`);
}
console.log(`\nmayor discrepancia sobre 10 tenores: ${worst.toExponential(2)}`);
// also confirm the tenor helpers agree
let tw = 0;
for (const N of [1,2,5,10,20,40]) tw = Math.max(tw,
  Math.abs(skewTenorN(N)-S.skewTenorDays(N)), Math.abs(driftTenorN(N)-S.driftTenorDays(N)));
console.log(`rectas de tenor, mayor discrepancia: ${tw.toExponential(2)}`);
process.exit(worst < 1e-12 && tw < 1e-12 ? 0 : 1);
