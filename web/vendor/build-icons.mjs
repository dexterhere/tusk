import fs from 'fs';
const dir='node_modules/iconoir/icons/regular/';
const M={journey:'path-arrow',overview:'home',agents:'group',tests:'test-tube',findings:'search',fixes:'refresh-double',audit:'shield-check',report:'page',graph:'network',
route:'path-arrow',tag:'label',user:'user',bug:'bug',check:'check',clock:'clock',file:'page',list:'list',dl:'download',sun:'sun-light',moon:'half-moon',auto:'dashboard',
left:'nav-arrow-left',right:'nav-arrow-right',alert:'warning-triangle',cycle:'refresh-double',layers:'view-grid',cpu:'cpu',target:'check-circle',gauge:'dashboard',db:'database',
lock:'lock',copy:'copy',play:'play',info:'info-circle',tools:'tools',pin:'map-pin'};
const out={};
for(const [k,n] of Object.entries(M)){const s=fs.readFileSync(dir+n+'.svg','utf8');out[k]=s.replace(/^[\s\S]*?<svg[^>]*>/,'').replace(/<\/svg>\s*$/,'').replace(/\s+/g,' ').trim()}
fs.writeFileSync('out/iconoir.js','/* Iconoir 7.12.2, MIT license, https://iconoir.com. Inner SVG markup of the icons the dashboard uses (24x24, stroke currentColor). */\nwindow.UAT_ICONS='+JSON.stringify(out)+';\n');
