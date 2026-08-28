/* Does the Expiration horizon produce TRUE numbers, not just consistent ones?
 *
 * Simulates paths straight from the distribution parameters and compares. The
 * simulation never calls the touch formulas it is judging.
 *
 * Read the sign, not just the size: the estimator is an upper bound, so it must
 * come out AT OR ABOVE the simulation. A case landing below means either the
 * code or the harness is wrong — that is how a broken RNG got caught here once.
 *
 * Needs /tmp/be.js — run `python tools/render_be.py` first.
 */
/* Do the exit-horizon touch odds match simulated paths? The Monte Carlo is
   written from the distribution parameters alone — it never calls the touch
   formulas it is judging. */
import fs from 'fs';
const js = fs.readFileSync('/tmp/be.js','utf8');
const i = js.lastIndexOf('})();');
const patched = js.slice(0,i) + 'globalThis.__T={params,touchDn,touchUp,F,BE};})();' + js.slice(i+5);
const stub = () => ({addEventListener(){},style:{},classList:{toggle(){}},value:'spx',focus(){},
                     textContent:'',innerHTML:'',placeholder:'',min:''});
globalThis.document={getElementById:stub,querySelector:()=>({style:{},addEventListener(){},getAttribute:()=>''}),
                     querySelectorAll:()=>[],documentElement:{style:{}}};
globalThis.window=globalThis; globalThis.setInterval=()=>0;
eval(patched);
const {params, touchDn, touchUp, BE} = globalThis.__T;
const d = BE.spx;

/* mulberry32. The LCG I reached for first overflows 2^53 in JS on the multiply
   and gets truncated by the & — it produced correlated garbage that made one
   case simulate ABOVE the union bound, which is impossible by construction.
   That impossibility is what flagged the harness rather than the code. */
let seed = 20260828 >>> 0;
const rnd = () => {
  seed = (seed + 0x6D2B79F5) >>> 0;
  let t = seed;
  t = Math.imul(t ^ (t >>> 15), t | 1);
  t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
};
const gauss = () => { let u=0,v=0; while(!u)u=rnd(); while(!v)v=rnd();
  return Math.sqrt(-2*Math.log(u))*Math.cos(2*Math.PI*v); };

console.log('caso                       | calculador | simulación | sesgo');
console.log('-'.repeat(66));
const N_PATHS = 60000, STEPS = 300;
for (const [N, w] of [[5,0.010],[5,0.020],[14,0.020],[14,0.035],[30,0.040]]) {
  const p = params(d,'exit',0,null,N);
  const a = -w*100, b = w*100;
  const tool = Math.min(1, touchDn(a,p.mu,p.sd) + touchUp(b,p.mu,p.su));
  let hit = 0;
  for (let k=0;k<N_PATHS;k++){
    let x = 0;
    for (let s=0;s<STEPS;s++){
      const sd = x < p.mu ? p.sd : p.su;
      x += p.mu/STEPS + sd*Math.sqrt(1/STEPS)*gauss();
      if (x<=a || x>=b) { hit++; break; }
    }
  }
  const mc = hit/N_PATHS;
  const tag = tool>=mc ? `conservador +${((tool-mc)*100).toFixed(1)}` : `SUBESTIMA ${((tool-mc)*100).toFixed(1)}`;
  console.log(`N=${String(N).padStart(2)} tent ±${(w*100).toFixed(1)}%`.padEnd(27)
    + `| ${(tool*100).toFixed(1).padStart(9)}% | ${(mc*100).toFixed(1).padStart(9)}% | ${tag}`);
}
