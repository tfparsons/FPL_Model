"""Build the dashboard site (site/index.html + site/history.html).

FPL-styled static pages: plan, pitch view, charts and the season track record.
Self-contained — inline CSS/SVG/JS, no external assets — so it works on Vercel,
from a file, or pulled into a phone via the repo.
"""

from __future__ import annotations

import html
import json
import shutil
from datetime import datetime, timezone

import pandas as pd

from .changelog import CHANGELOG, VERSION_TITLES
from .config import PROCESSED, ROOT
from .data import load_dataset
from .history import load_history, load_outcomes
from .schedule import UK, fmt_uk, gw_breaks
from .strengths import build_strengths, fixture_lambdas

ESC = html.escape
SITE = ROOT / "site"

TEAM_COLOURS = {
    "ARS": "#EF0107", "AVL": "#670E36", "BOU": "#DA291C", "BRE": "#E30613",
    "BHA": "#0057B8", "BUR": "#6C1D45", "CHE": "#034694", "COV": "#3A9BD9",
    "CRY": "#1B458F", "EVE": "#003399", "FUL": "#2B2B2B", "HUL": "#F18A00",
    "IPS": "#0044A9", "LEE": "#1D428A", "LEI": "#003090", "LIV": "#C8102E",
    "MCI": "#6CABDD", "MUN": "#DA291C", "NEW": "#241F20", "NFO": "#DD0000",
    "SHU": "#EE2737", "SUN": "#EB172B", "TOT": "#132257", "WHU": "#7A263A",
    "WOL": "#FDB913",
}
# FPL-style difficulty scale: green (easy) through grey to red (hard).
DIFF_RAMP = ["#257d5a", "#00ff86", "#ebebe4", "#ff005a", "#861d46"]
DIFF_INK = ["#ffffff", "#12281c", "#3a3a32", "#ffffff", "#ffffff"]

CSS = """
:root {
  color-scheme: light;
  --purple: #37003c; --purple-2: #2a0030; --green: #00ff87; --cyan: #04f5ff;
  --pink: #e90052;
  --bg: #f2f2f5; --card: #ffffff; --ink: #1b1b1f; --muted: #5f5f6b;
  --line: #e3e3ea; --chipbg: #f4f1f7; --pitch-a: #2f9c4b; --pitch-b: #35a852;
  --series-in: #00a65a; --series-out: #6a2c91; --series-alt1: #e90052;
  --series-alt2: #0098b3; --grid: #e9e9ee; --good: #008a4a; --warn: #b35c00;
  --bad: #c0003f;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --bg: #120716; --card: #1b1020; --ink: #f3eef6; --muted: #b9a9c3;
    --line: #2d2136; --chipbg: #261a2c; --grid: #2d2136;
    --series-in: #00a85c; --series-out: #9d6fff; --series-alt1: #ef2d73;
    --series-alt2: #1aa3c4; --good: #3fd48a; --warn: #f0b35b; --bad: #ff5c8a;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
header.top { background: linear-gradient(100deg, var(--purple) 0%, var(--purple-2) 100%);
  color: #fff; padding: 14px 16px 0; }
header.top .wrap { max-width: 900px; margin: 0 auto; }
header.top h1 { margin: 0; font-size: 1.45rem; font-weight: 800; letter-spacing: -.01em; }
header.top .gwpill { display: inline-block; background: var(--green); color: var(--purple);
  font-weight: 800; font-size: .78rem; padding: 2px 10px; border-radius: 999px;
  margin-left: 8px; vertical-align: middle; }
header.top .sub { color: #d9c7e0; font-size: .86rem; margin: 4px 0 10px; }
header.top .sub b { color: #fff; }
nav.tabs { display: flex; gap: 4px; }
nav.tabs a { color: #fff; text-decoration: none; padding: 8px 14px; font-weight: 600;
  font-size: .88rem; border-radius: 8px 8px 0 0; opacity: .75; }
nav.tabs a.active { background: var(--bg); color: var(--purple); opacity: 1; }
.rbtn { background: transparent; border: 1px solid #ffffff55; color: #fff; border-radius: 6px;
  padding: 2px 9px; font-size: .78rem; cursor: pointer; font-weight: 600; }
.rbtn:hover { background: #ffffff22; } .rbtn:disabled { opacity: .5; cursor: default; }
.rmsg { color: var(--green); font-size: .78rem; }
.ibtn { width: 17px; height: 17px; border-radius: 50%; border: 1px solid var(--muted);
  color: var(--muted); background: transparent; font: 700 .68rem/1 inherit; cursor: pointer;
  padding: 0; vertical-align: 1px; }
.ibtn[aria-expanded="true"], .ibtn:hover { border-color: var(--purple); color: var(--purple); }
.inote { display: none; position: fixed; z-index: 30; background: var(--card); color: var(--muted);
  border: 1px solid var(--line); border-radius: 8px; padding: 9px 13px; font-size: .78rem;
  line-height: 1.5; box-shadow: 0 8px 24px rgba(0,0,0,.25); text-transform: none;
  letter-spacing: normal; font-weight: 400; text-align: left; }
.inote.show { display: block; }
.devi { color: var(--warn); font-weight: 600; }
.talert { display: flex; gap: 12px; border: 1px solid var(--warn);
  border-left: 4px solid var(--warn); border-radius: 8px; padding: 12px 14px;
  background: var(--chipbg); }
.talert-ic { width: 26px; height: 26px; border-radius: 50%; background: #f2b24f;
  color: #2b1c04; font-weight: 800; text-align: center; line-height: 26px; flex: none; }
.talert-h { font-weight: 800; margin-bottom: 5px; }
.talert p { margin: 0 0 7px; font-size: .88rem; max-width: none; }
.talert-win { display: inline-block; background: #f2b24f; color: #2b1c04;
  border-radius: 6px; padding: 4px 11px; font-size: .82rem; font-weight: 600; }
.stripe { height: 4px; background: linear-gradient(90deg, var(--cyan), var(--green)); }
main { max-width: 900px; margin: 0 auto; padding: 14px 12px 40px; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 10px;
  padding: 16px; margin-bottom: 14px; }
.card h2 { margin: 0 0 10px; font-size: 1rem; font-weight: 800; color: var(--purple);
  text-transform: uppercase; letter-spacing: .04em; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) .card h2 { color: var(--green); }
  :root:not([data-theme="light"]) nav.tabs a.active { color: var(--green); } }
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin-bottom: 14px; }
.kpi { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px;
  font-size: .76rem; color: var(--muted); }
.kpi b { display: block; color: var(--ink); font-size: 1.25rem; font-weight: 800; }
.kpi.good b { color: var(--good); } .kpi.warn b { color: var(--warn); } .kpi.bad b { color: var(--bad); }
.banner { border-radius: 10px; padding: 10px 14px; font-size: .86rem; margin-bottom: 12px;
  background: #fff3d6; color: #6b3d00; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) .banner { background: #3a2a10; color: #f0c070; } }
.vhead { display: flex; justify-content: space-between; gap: 14px; flex-wrap: wrap;
  align-items: flex-start; margin-bottom: 14px; }
.verdict { font-size: 1.3rem; font-weight: 800; letter-spacing: -.01em; }
.rationale { color: var(--muted); font-size: .9rem; max-width: 56ch; margin-top: 4px; }
.rationale b { color: var(--ink); }
.vchips { display: flex; flex-direction: column; gap: 6px; align-items: flex-end; }
.vchip { background: var(--chipbg); border-radius: 8px; padding: 3px 10px; font-size: .78rem;
  color: var(--muted); white-space: nowrap; }
.vchip b { color: var(--ink); }
.goodt { color: var(--good) !important; } .warnt { color: var(--warn) !important; }
.badt { color: var(--bad) !important; }
.swapgrid { display: grid; grid-template-columns: repeat(auto-fit, minmax(310px, 1fr));
  gap: 12px; margin: 10px 0; }
.swap { position: relative; border: 1px solid var(--line); border-radius: 10px;
  padding: 30px 14px 12px; background: var(--bg); }
.swap.heldswap { opacity: .78; }
.ribbon { position: absolute; top: 0; right: 0; font-size: .64rem; font-weight: 800;
  padding: 3px 10px; border-radius: 0 9px 0 9px; letter-spacing: .04em; }
.rb-pay { background: var(--green); color: var(--purple); }
.rb-fin { background: var(--chipbg); color: var(--muted); }
.rb-done { background: var(--good); color: #fff; }
.doneh { font-size: .78rem; font-weight: 800; letter-spacing: .06em;
  text-transform: uppercase; color: var(--good); margin: 14px 0 2px; }
.heldswap .ribbon { background: var(--warn); color: #fff; }
.shead { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.sp { display: flex; align-items: center; gap: 8px; flex: 1; min-width: 0; }
.sp svg { width: 30px; height: 30px; flex: none; }
.sp b { font-size: .92rem; }
.spout b { color: var(--bad); text-decoration: line-through;
  text-decoration-thickness: 1px; text-decoration-color: var(--line); }
.spin b { color: var(--good); }
.sarrow { color: var(--muted); font-size: 1.1rem; flex: none; }
.cmpgrid { display: grid; grid-template-columns: 1fr auto auto; gap: 3px 16px;
  font-size: .82rem; color: var(--muted); border-top: 1px solid var(--line);
  padding-top: 8px; margin-bottom: 8px; }
.cmpgrid .cl { font-size: .68rem; text-transform: uppercase; letter-spacing: .05em; }
.cmpgrid .num { text-align: right; color: var(--ink); font-variant-numeric: tabular-nums; }
.delta { font-style: normal; color: var(--good); font-weight: 700; font-size: .76rem; }
.delta.neg { color: var(--bad); }
.swap .spark { width: 100%; height: auto; display: block; margin: 4px 0 8px; }
.fdot { display: inline-block; width: 10px; height: 10px; border-radius: 2px;
  margin-right: 2px; vertical-align: -1px; }
.cmpgrid .fx { white-space: nowrap; }
.altbox { border-top: 1px solid var(--line); margin-top: 8px; padding-top: 7px; }
.althead { font-size: .88rem; color: var(--ink); font-weight: 700; margin-bottom: 4px; }
.alttab { font-size: .8rem; } .alttab th { font-size: .66rem; padding: 2px 8px; border-bottom: 1px solid var(--line); }
.alttab td { padding: 3px 8px; border-bottom: none; }
.fdrchips { display: flex; gap: 3px; flex-wrap: wrap; }
.fdrchips span { border-radius: 4px; padding: 2px 5px; font-size: .68rem; font-weight: 700; }
.drvchip { background: var(--chipbg); border-radius: 6px; padding: 2px 8px;
  font-size: .74rem; color: var(--muted); }
.drvchip b { color: var(--ink); }
.sfoot { color: var(--muted); font-size: .76rem; border-top: 1px solid var(--line);
  padding-top: 7px; }
.sfoot:empty { display: none; border: none; padding: 0; }
.sfoot b { color: var(--ink); }
.ladder svg { width: 100%; max-width: 680px; height: auto; display: block; }
.themegrid { display: flex; gap: 10px; flex-wrap: wrap; margin: 8px 0 6px; }
.theme { border: 1px solid var(--line); border-radius: 8px; padding: 8px 12px;
  min-width: 132px; background: var(--bg); }
.theme .thead { display: flex; justify-content: space-between; gap: 10px;
  align-items: center; margin-bottom: 6px; }
.tdir { font-size: .62rem; font-weight: 800; padding: 1px 7px; border-radius: 5px;
  letter-spacing: .05em; }
.tdir.in { background: var(--green); color: var(--purple); }
.tdir.out { background: var(--pink); color: #fff; }
.tmeter { height: 6px; border-radius: 3px; background: var(--chipbg); overflow: hidden;
  margin-bottom: 5px; }
.tmeter div { height: 100%; border-radius: 3px; }
.tmeter.in div { background: var(--series-in); }
.tmeter.out div { background: var(--series-out); }
.tpct { font-size: .72rem; color: var(--muted); }
table.scen { max-width: 680px; }
table.scen td.meter { width: 30%; min-width: 90px; }
table.scen td.meter div { height: 10px; border-radius: 4px; background: var(--series-alt2); }
.move { font-size: 1.1rem; margin-bottom: 10px; }
.move .why { color: var(--muted); font-size: .84rem; margin-top: 3px; }
.in { color: var(--good); font-weight: 700; } .out { color: var(--bad); }
.hits { color: var(--warn); font-size: .9rem; }
.big { font-size: 1.15rem; font-weight: 700; }
.timing { border-top: 1px solid var(--line); margin-top: 10px; padding-top: 10px; font-size: .92rem; }
.note { color: var(--muted); font-size: .8rem; }
.tablewrap { overflow-x: auto; -webkit-overflow-scrolling: touch;
  /* scroll shadows: fade at the edges whenever there is more table to scroll */
  background:
    linear-gradient(90deg, var(--card) 40%, rgba(0,0,0,0)) left / 28px 100%,
    linear-gradient(270deg, var(--card) 40%, rgba(0,0,0,0)) right / 28px 100%,
    radial-gradient(farthest-side at 0 50%, rgba(0,0,0,.28), rgba(0,0,0,0)) left / 12px 100%,
    radial-gradient(farthest-side at 100% 50%, rgba(0,0,0,.28), rgba(0,0,0,0)) right / 12px 100%;
  background-repeat: no-repeat;
  background-attachment: local, local, scroll, scroll; }
/* nothing may ever poke past the viewport: wide leaf content scrolls in place */
main { overflow-x: clip; }
@media (max-width: 520px) {
  table { font-size: .74rem; }
  th { font-size: .64rem; padding: 5px 5px; }
  td { padding: 5px 5px; }
  main { padding: 10px 8px 32px; }
  .vchips { flex-direction: row; flex-wrap: wrap; align-items: flex-start; }
}
table { border-collapse: collapse; width: 100%; font-size: .86rem; }
th { text-align: left; color: var(--muted); font-weight: 600; font-size: .74rem; text-transform: uppercase;
  letter-spacing: .03em; border-bottom: 2px solid var(--line); padding: 6px 8px; white-space: nowrap; }
td { border-bottom: 1px solid var(--line); padding: 7px 8px; vertical-align: top; }
tr:last-child td { border-bottom: none; }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.fix { color: var(--muted); font-size: .78rem; white-space: nowrap; }
.secrow td { text-align: center; color: var(--purple); font-size: .74rem; font-weight: 800;
  text-transform: uppercase; letter-spacing: .05em; background: var(--chipbg); padding: 5px 8px; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) .secrow td { color: var(--green); } }
.brkrow td { text-align: center; color: var(--muted); font-size: .76rem;
  background: var(--chipbg); padding: 4px 8px; letter-spacing: .02em; }
.flag { background: #fee4e2; color: #b42318; font-size: .7rem; padding: 1px 6px; border-radius: 6px; font-weight: 700; }
:root { --sq: #6a2c91; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --sq: #9d6fff; } }
:root[data-theme="dark"] { --sq: #9d6fff; }
.sqxp { color: var(--sq) !important; }
.sqtag { font-size: .6rem; background: var(--sq); color: #fff; border-radius: 4px; padding: 1px 5px;
  margin-left: 6px; font-weight: 800; vertical-align: middle; text-transform: uppercase; letter-spacing: .04em; }
.adjxp { color: #c46a00 !important; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) .adjxp { color: #f2a33a !important; } }
:root[data-theme="dark"] .adjxp { color: #f2a33a !important; }
.adjtag { font-size: .6rem; background: #f2a33a; color: #2b1c04; border-radius: 4px; padding: 1px 5px;
  margin-left: 6px; font-weight: 800; vertical-align: middle; text-transform: uppercase; letter-spacing: .04em; }
.outbadge { background: var(--pink); color: #fff; font-size: .68rem; padding: 1px 6px; border-radius: 6px; font-weight: 800; }
.news { color: var(--muted); font-size: .75rem; }
.badge { background: var(--pink); color: #fff; font-size: .68rem; padding: 2px 7px; border-radius: 6px; font-weight: 700; vertical-align: middle; }
/* pitch */
.pitch { background: repeating-linear-gradient(180deg, var(--pitch-a) 0 48px, var(--pitch-b) 48px 96px);
  border-radius: 10px; padding: 14px 6px 10px; position: relative; }
.pitch .row { display: flex; justify-content: center; gap: 6px; margin-bottom: 10px; flex-wrap: wrap; }
.chiprow { display: flex; justify-content: center; gap: 10px; margin: 2px 0 12px; flex-wrap: wrap; }
@media (max-width: 480px) { .chiprow { display: grid; grid-template-columns: repeat(2, 92px);
  justify-content: center; gap: 12px 24px; } }
.chipbadge { text-align: center; width: 92px; }
.chipbadge .cb, .chiptile .cb { display: block; width: 38px; height: 38px; margin: 0 auto 3px; border-radius: 50%;
  border: 2px solid var(--green); color: var(--purple); background: var(--green);
  font-weight: 800; font-size: .8rem; line-height: 34px; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) .chipbadge .cb, :root:not([data-theme="light"]) .chiptile .cb {
  background: transparent; color: var(--green); } }
.chipbadge small { color: var(--muted); font-size: .66rem; display: block; }
.chipbadge.off .cb, .chiptile.off .cb { border-color: var(--line); background: var(--chipbg); color: var(--muted); }
.chipbadge.off small { text-decoration: line-through; }
.chipgrid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
@media (max-width: 700px) { .chipgrid { grid-template-columns: repeat(2, 1fr); } }
.chiptile { border: 1px solid var(--line); border-radius: 10px; padding: 12px 12px 6px;
  background: var(--bg); text-align: center; }
.chiptile .cname { font-weight: 700; font-size: .85rem; margin: 4px 0 10px; }
.chiptile .crow { display: block; text-align: left; font-size: .78rem;
  color: var(--muted); padding: 7px 0 8px; border-top: 1px solid var(--line); }
.chiptile .crow > span:first-child { display: block; font-size: .62rem;
  text-transform: uppercase; letter-spacing: .06em; margin-bottom: 3px; }
.chiptile .crow > span:last-child { display: block; }
.chiptile .cv { color: var(--ink); font-weight: 600; }
.chiptile.off { opacity: .55; }
.chipplan { border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px; margin: 0 0 12px; font-size: .85rem; }
.chipplan.endgame { border-color: var(--warn); }
.chipplan .note { margin-top: 4px; }
.pcard { width: 84px; text-align: center; position: relative; }
.pcard svg { width: 40px; height: 40px; display: block; margin: 0 auto 2px; filter: drop-shadow(0 1px 1px rgba(0,0,0,.3)); }
.pcard .nm { background: var(--purple); color: #fff; font-size: .7rem; font-weight: 700; padding: 2px 4px;
  border-radius: 4px 4px 0 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.pcard .xp { background: #fff; color: var(--purple); font-size: .72rem; font-weight: 800; padding: 1px 4px; }
.pcard .pmeta { background: #fff; color: #6b6b76; font-size: .58rem; padding: 0 2px 2px;
  border-radius: 0 0 4px 4px; white-space: nowrap; }
.pcard .cap, .pcard .vc { position: absolute; top: -5px; right: 6px; width: 18px; height: 18px;
  border-radius: 50%; font-size: .66rem; font-weight: 800; line-height: 18px;
  box-shadow: 0 1px 2px rgba(0,0,0,.35); z-index: 1; }
.pcard .cap { background: var(--green); color: var(--purple); } .pcard .vc { background: #fff; color: var(--purple); }
.pcard .newp { position: absolute; top: -5px; left: 2px; background: var(--green);
  color: var(--purple); border-radius: 999px; font-size: .5rem; font-weight: 800;
  letter-spacing: .04em; padding: 3px 5px; box-shadow: 0 1px 2px rgba(0,0,0,.25); }
.pcard.newin .nm { box-shadow: inset 0 0 0 1.5px var(--green); }
.bench { background: var(--chipbg); border-radius: 8px; padding: 8px; margin-top: 8px; display: flex;
  justify-content: center; gap: 6px; flex-wrap: wrap; }
.bench .pcard .nm { background: var(--muted); }
/* charts */
.chart { position: relative; }
.chart svg { width: 100%; height: auto; display: block; }
.legend { display: flex; gap: 14px; flex-wrap: wrap; font-size: .8rem; color: var(--muted); margin: 4px 0 6px; }
.bandck { display: inline-flex; align-items: center; gap: 5px; cursor: pointer; }
.bandck input { accent-color: var(--purple); margin: 0; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) .bandck input { accent-color: var(--green); } }
.legend i { display: inline-block; width: 14px; height: 3px; border-radius: 2px; vertical-align: middle; margin-right: 5px; }
.tip { position: absolute; pointer-events: none; background: var(--card); color: var(--ink); border: 1px solid var(--line);
  border-radius: 8px; padding: 6px 9px; font-size: .78rem; box-shadow: 0 4px 14px rgba(0,0,0,.12); display: none;
  white-space: nowrap; z-index: 5; }
.tip b { display: block; font-size: .8rem; margin-bottom: 2px; }
.axis text { fill: var(--muted); font-size: 11px; }
.grid line { stroke: var(--grid); stroke-width: 1; }
.dl text { fill: var(--ink); font-size: 11px; font-weight: 600; }
.ticker td.cell { padding: 4px 3px; text-align: center; font-size: .72rem; font-weight: 700; border-radius: 4px; }
.ticker td.cell span { display: block; border-radius: 4px; padding: 3px 2px; }
.controls { display: flex; flex-wrap: wrap; gap: 10px 16px; align-items: center; margin-bottom: 12px; font-size: .86rem; }
.controls label { color: var(--muted); }
.controls input[type=range] { width: 220px; accent-color: var(--purple); }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) .controls input[type=range] { accent-color: var(--green); } }
.controls input[type=search] { padding: 5px 9px; border: 1px solid var(--line); border-radius: 6px; background: var(--card); color: var(--ink); font-size: .86rem; }
.seg { display: inline-flex; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
.seg button { background: var(--card); color: var(--ink); border: 0; padding: 5px 11px; font-size: .82rem; cursor: pointer; font-weight: 600; }
.seg button.on { background: var(--purple); color: #fff; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) .seg button.on { background: var(--green); color: var(--purple); } }
.hrz { font-weight: 800; color: var(--purple); font-size: 1rem; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) .hrz { color: var(--green); } }
th.num { text-align: right; }
.ab { border-left: 2px solid var(--warn) !important; }
.mvsel { padding: 5px 9px; border: 1px solid var(--line); border-radius: 6px;
  background: var(--card); color: var(--ink); font-size: .84rem; max-width: 280px; }
tr.own td:first-child { box-shadow: inset 3px 0 var(--series-in); }
tr.tgt.t1 td:first-child { box-shadow: inset 3px 0 #e90052; }
tr.tgt.t2 td:first-child { box-shadow: inset 3px 0 rgba(233,0,82,.55); }
tr.tgt.t3 td:first-child { box-shadow: inset 3px 0 rgba(233,0,82,.28); }
th.sort { cursor: pointer; user-select: none; } th.sort.on { color: var(--ink); }
th.sort.on::after { content: " ↓"; }
th.sort.on.asc::after { content: " ↑"; }
th.csort { cursor: pointer; user-select: none; }
th.csort.on::after { content: " ↓"; }
th.csort.on.asc::after { content: " ↑"; }
tr.own td { background: var(--chipbg); }
.owntag { font-size: .66rem; background: var(--purple); color: #fff; border-radius: 4px; padding: 1px 5px; margin-left: 5px; vertical-align: middle; }
td.cell.dim span { opacity: .28; }
.more { text-align: center; padding: 8px; } .more button { background: var(--chipbg); border: 1px solid var(--line); color: var(--ink); border-radius: 8px; padding: 6px 14px; cursor: pointer; }
details.runs { border-bottom: 1px solid var(--line); padding: 8px 0; }
details.runs:last-of-type { border-bottom: none; }
details.runs summary { cursor: pointer; font-size: .9rem; line-height: 1.6; }
details.runs summary:hover { color: var(--accent, inherit); }
details.runs .tablewrap { margin-top: 8px; }
tr.frun td { background: var(--chipbg); }
.pill { display: inline-block; padding: 1px 9px; border-radius: 999px; font-size: .68rem; font-weight: 800; }
.pill.hold { background: var(--chipbg); color: var(--muted); }
.pill.consider { background: var(--green); color: var(--purple); }
.pill.good { background: var(--good); color: #fff; }
.pill.warn { background: var(--warn); color: #fff; }
.pill.bad { background: var(--bad); color: #fff; }
.noopw { color: var(--muted); font-weight: 700; font-style: italic; }
.fdr { display: inline-block; min-width: 34px; text-align: center; border-radius: 6px; padding: 1px 6px; font-weight: 800; font-size: .8rem; }
.tl { display: flex; gap: 3px; align-items: center; font-size: .74rem; color: var(--muted); margin-top: 6px; }
.tl i { width: 18px; height: 10px; display: inline-block; border-radius: 2px; }
footer { color: var(--muted); font-size: .74rem; max-width: 900px; margin: 0 auto; padding: 0 12px 30px; }
code { background: var(--chipbg); padding: 1px 5px; border-radius: 4px; font-size: .82em; }
"""

JS = """
document.querySelectorAll('.ibtn').forEach(function (b) {
  function note() {
    var x = b.nextElementSibling;
    if (!x || !x.classList.contains('inote')) x = b.parentElement.nextElementSibling;
    return (x && x.classList.contains('inote')) ? x : null;
  }
  function show(on) { var x = note(); if (!x) return;
    if (on) {
      var r = b.getBoundingClientRect();
      var vw = window.innerWidth || document.documentElement.clientWidth || 800;
      var vh = window.innerHeight || document.documentElement.clientHeight || 600;
      var w = Math.min(340, vw - 24);
      x.style.width = w + 'px';
      x.classList.add('show');
      var left = Math.min(r.left, vw - x.offsetWidth - 12);
      x.style.left = Math.max(8, left) + 'px';
      var top = r.bottom + 8;
      if (top + x.offsetHeight > vh - 8) top = r.top - x.offsetHeight - 8;
      x.style.top = Math.max(8, top) + 'px';
    } else { x.classList.remove('show'); }
    b.setAttribute('aria-expanded', on ? 'true' : 'false'); }
  b.addEventListener('mouseenter', function () { show(true); });
  b.addEventListener('mouseleave', function () { show(false); });
  b.addEventListener('click', function () { var x = note(); if (x) show(!x.classList.contains('show')); });
});
(function () {
  var btn = document.getElementById('rbtn'), msg = document.getElementById('rmsg');
  if (!btn) return;
  function poll() {
    setTimeout(function () {
      fetch('/api/refresh').then(function (r) { return r.json(); }).then(function (j) {
        if (j.status === 'running') { poll(); }
        else { msg.innerHTML = 'run finished — redeploying, <a href="#" onclick="location.reload(true);return false">reload</a> in ~1 min'; }
      }).catch(poll);
    }, 20000);
  }
  btn.addEventListener('click', function () {
    btn.disabled = true; msg.textContent = 'starting…';
    fetch('/api/refresh', { method: 'POST' }).then(function (r) { return r.json(); })
      .then(function (j) {
        if (j.status === 'started' || j.status === 'already_running') {
          msg.textContent = 'model running — takes ~6 min'; poll();
        } else { msg.textContent = 'could not start (' + j.status + ')'; btn.disabled = false; }
      }).catch(function () { msg.textContent = 'refresh only works on the live site'; btn.disabled = false; });
  });
})();
document.querySelectorAll('.chart[data-chart]').forEach(function (box) {
  var d = JSON.parse(box.getAttribute('data-chart'));
  d.y = new Function('v', 'return ' + box.getAttribute('data-y'));
  var svg = box.querySelector('svg'), tip = box.querySelector('.tip');
  var cross = svg.querySelector('.cross'), dots = svg.querySelectorAll('.hdot');
  function idxAt(evt) {
    var r = svg.getBoundingClientRect();
    var vbW = svg.viewBox.baseVal.width;
    var x = (evt.clientX - r.left) * vbW / r.width;
    var n = d.x.length, best = 0, bd = 1e9;
    for (var i = 0; i < n; i++) { var px = d.x0 + (d.x1 - d.x0) * i / Math.max(n - 1, 1);
      if (Math.abs(px - x) < bd) { bd = Math.abs(px - x); best = i; } }
    return best;
  }
  svg.addEventListener('mousemove', function (evt) {
    var i = idxAt(evt), px = d.x0 + (d.x1 - d.x0) * i / Math.max(d.x.length - 1, 1);
    if (cross) { cross.setAttribute('x1', px); cross.setAttribute('x2', px); cross.style.display = 'block'; }
    var s = '<b>' + d.x[i] + '</b>';
    d.series.forEach(function (se, k) { var v = se.v[i];
      s += '<span style="display:inline-block;width:10px;height:3px;background:' + se.c + ';margin-right:5px;vertical-align:middle"></span>' + se.n + ': ' + (v == null ? '—' : v.toFixed(1)) + '<br>';
      var dot = dots[k]; if (dot) { if (v == null) { dot.style.display = 'none'; } else {
        dot.style.display = 'block'; dot.setAttribute('cx', px); dot.setAttribute('cy', d.y(v)); } } });
    tip.innerHTML = s; tip.style.display = 'block';
    var r = box.getBoundingClientRect(); var lx = evt.clientX - r.left + 12, ly = evt.clientY - r.top - 10;
    if (lx + tip.offsetWidth > r.width) lx = evt.clientX - r.left - tip.offsetWidth - 12;
    tip.style.left = lx + 'px'; tip.style.top = ly + 'px';
  });
  svg.addEventListener('mouseleave', function () { tip.style.display = 'none';
    if (cross) cross.style.display = 'none'; dots.forEach(function (dd) { dd.style.display = 'none'; }); });
});
(function () {
  var tip = document.createElement('div'); tip.className = 'tip'; tip.style.position = 'fixed';
  document.body.appendChild(tip);
  document.addEventListener('mouseover', function (evt) {
    var el = evt.target.closest('[data-tip]'); if (!el) { tip.style.display = 'none'; return; }
    tip.innerHTML = el.getAttribute('data-tip'); tip.style.display = 'block';
  });
  document.addEventListener('mousemove', function (evt) {
    if (tip.style.display !== 'block') return;
    var x = evt.clientX + 12, y = evt.clientY - 10;
    if (x + tip.offsetWidth > window.innerWidth) x = evt.clientX - tip.offsetWidth - 12;
    tip.style.left = x + 'px'; tip.style.top = y + 'px';
  });
})();
"""


def _nice_ticks(vmax: float, n: int = 4) -> list[float]:
    if vmax <= 0:
        return [0, 1]
    raw = vmax / n
    mag = 10 ** int(f"{raw:e}".split("e")[1])
    step = min((s for s in (1, 2, 2.5, 5, 10) if s * mag >= raw), default=10) * mag
    ticks = []
    v = 0.0
    while v <= vmax + 1e-9:
        ticks.append(v)
        v += step
    return ticks


def line_chart(series: list[dict], x_labels: list[str], y_label: str = "",
               height: int = 240) -> str:
    """Responsive inline SVG line chart with legend, end labels and hover tooltip.

    series: [{"name", "color" (CSS var name), "values": [float|None]}]
    """
    W, H = 680, height
    L, R, T, B = 36, 100, 14, 28
    vmax = max((v for s in series for v in s["values"] if v is not None), default=1)
    ticks = _nice_ticks(vmax)
    ymax = ticks[-1] if ticks[-1] >= vmax else ticks[-1] + (ticks[1] - ticks[0])
    n = len(x_labels)
    x0, x1 = L, W - R
    px = lambda i: x0 + (x1 - x0) * i / max(n - 1, 1)
    py = lambda v: T + (H - T - B) * (1 - v / ymax)

    parts = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{ESC(y_label)}">']
    parts.append('<g class="grid">')
    for t in ticks:
        parts.append(f'<line x1="{x0}" y1="{py(t):.1f}" x2="{x1}" y2="{py(t):.1f}"/>')
    parts.append('</g><g class="axis">')
    for t in ticks:
        parts.append(f'<text x="{x0 - 6}" y="{py(t) + 4:.1f}" text-anchor="end">{t:g}</text>')
    for i, lab in enumerate(x_labels):
        parts.append(f'<text x="{px(i):.1f}" y="{H - 8}" text-anchor="middle">{ESC(lab)}</text>')
    parts.append('</g>')
    parts.append(f'<line class="cross" x1="0" y1="{T}" x2="0" y2="{H - B}" stroke="var(--muted)" '
                 f'stroke-width="1" style="display:none"/>')
    # End labels only where they don't collide — converging series fall back
    # to the legend + tooltip rather than stacked, detached labels.
    ends = {}
    for k, s in enumerate(series):
        vals = [v for v in s["values"] if v is not None]
        if vals:
            ends[k] = py(vals[-1])
    collide = {k for k in ends for j in ends if j != k and abs(ends[k] - ends[j]) < 13}
    for k, s in enumerate(series):
        pts = [(px(i), py(v)) for i, v in enumerate(s["values"]) if v is not None]
        if not pts:
            continue
        d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        parts.append(f'<path d="{d}" fill="none" stroke="var(--{s["color"]})" stroke-width="2" '
                     f'stroke-linejoin="round" stroke-linecap="round"/>')
        ex, ey = pts[-1]
        parts.append(f'<circle cx="{ex:.1f}" cy="{ey:.1f}" r="4" fill="var(--{s["color"]})" '
                     f'stroke="var(--card)" stroke-width="2"/>')
        if k not in collide:
            parts.append(f'<g class="dl"><text x="{ex + 8:.1f}" y="{ey + 4:.1f}">{ESC(s["name"])}</text></g>')
    for s in series:
        parts.append(f'<circle class="hdot" r="5" fill="var(--{s["color"]})" stroke="var(--card)" '
                     f'stroke-width="2" style="display:none"/>')
    parts.append('</svg>')
    data = {"x": x_labels, "x0": x0, "x1": x1,
            "series": [{"n": s["name"], "c": f"var(--{s['color']})", "v": s["values"]} for s in series]}
    # y() needs ymax/T/H/B at runtime — embed as numbers and rebuild in JS via Function.
    data_js = json.dumps(data).replace("'", "&#39;")
    yfn = f"{T} + ({H - T - B}) * (1 - v / {ymax})"
    legend = "".join(f'<span><i style="background:var(--{s["color"]})"></i>{ESC(s["name"])}</span>'
                     for s in series)
    return (f'<div class="legend">{legend}</div>'
            f'<div class="chart" data-chart=\'{data_js}\' data-y="{yfn}">'
            + "".join(parts) + '<div class="tip"></div></div>')


def bar_chart(rows: list[tuple[str, float]], fmt: str = "{:.0%}", color: str = "series-alt2",
              height_per: int = 26) -> str:
    """Horizontal bar chart, single series, value labels at the tip."""
    W = 680
    L = 230
    H = height_per * len(rows) + 8
    vmax = max((v for _, v in rows), default=1) or 1
    parts = [f'<svg viewBox="0 0 {W} {H}">']
    for i, (label, v) in enumerate(rows):
        y = 4 + i * height_per
        w = (W - L - 60) * v / vmax
        parts.append(f'<text x="{L - 8}" y="{y + 15}" text-anchor="end" fill="var(--ink)" '
                     f'font-size="12">{ESC(label)}</text>')
        parts.append(f'<rect x="{L}" y="{y + 3}" width="{max(w, 2):.1f}" height="16" rx="4" '
                     f'fill="var(--{color})" data-tip="{ESC(label)}: {fmt.format(v)}"/>')
        parts.append(f'<text x="{L + w + 6:.1f}" y="{y + 15}" fill="var(--muted)" font-size="11">'
                     f'{fmt.format(v)}</text>')
    parts.append('</svg>')
    return '<div class="chart">' + "".join(parts) + '</div>'


def info(text: str) -> str:
    """A small i-button that toggles a plain-English explainer."""
    return (f"<button class='ibtn' type='button' aria-expanded='false' "
            f"aria-label='What does this mean?'>i</button>"
            f"<span class='inote note'>{text}</span>")


def _spark(vals_in: list[float], vals_out: list[float], label: str) -> str:
    """Full-width two-line sparkline: IN (green) vs OUT (pink), no axes."""
    W, H, pad = 320, 62, 6
    allv = [v for v in vals_in + vals_out]
    vmax, vmin = max(allv + [0.1]), min(allv + [0])
    span = (vmax - vmin) or 1
    n = max(len(vals_in), 2)
    px = lambda i: pad + (W - 2 * pad) * i / (n - 1)
    py = lambda v: pad + (H - 2 * pad) * (1 - (v - vmin) / span)
    def line(vals, colour):
        pts = " ".join(f"{px(i):.1f},{py(v):.1f}" for i, v in enumerate(vals))
        e = f'<circle cx="{px(len(vals)-1):.1f}" cy="{py(vals[-1]):.1f}" r="3.5" fill="{colour}"/>'
        return (f'<polyline points="{pts}" fill="none" stroke="{colour}" '
                f'stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>{e}')
    return (f'<svg viewBox="0 0 {W} {H}" class="spark" role="img" aria-label="{ESC(label)}">'
            f'<title>{ESC(label)}</title>'
            + line(vals_out, "var(--bad)") + line(vals_in, "var(--good)")
            + '</svg>')


def shirt_svg(colour: str) -> str:
    return (f'<svg viewBox="0 0 40 40"><path d="M8 6 L15 3 Q20 8 25 3 L32 6 L38 13 L32 17 L31 36 '
            f'L9 36 L8 17 L2 13 Z" fill="{colour}" stroke="#fff" stroke-width="1.5" '
            f'stroke-linejoin="round"/></svg>')


def page(title: str, active: str, header_sub: str, gw: int, body: str,
         team: str = "FPL Model") -> str:
    nav = "".join(
        f'<a href="{href}" class="{"active" if key == active else ""}">{label}</a>'
        for key, href, label in [("plan", "index.html", "Weekly"),
                                 ("general", "general.html", "General"),
                                 ("history", "history.html", "Track record")])
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{ESC(title)}</title>
<style>{CSS}</style></head><body>
<header class="top"><div class="wrap">
<h1>{ESC(team)} <span class="gwpill">GW{gw}</span></h1>
<div class="sub">{header_sub} · <button id="rbtn" class="rbtn" type="button">↻ Refresh</button>
<span id="rmsg" class="rmsg"></span></div>
<nav class="tabs">{nav}</nav>
</div></header><div class="stripe"></div>
<main>{body}</main>
<script>{JS}</script></body></html>"""


GENERAL_JS = r"""
(function () {
  var D = window.MUD; var N = D.gws.length; var n = Math.min(5, N); var tn = 5;
  var pos = 'ALL', q = '', sortKey = 'xp', sortAsc = false, showAll = false, teamSort = 'fdr';
  var RAMP = D.ramp, INK = D.ink;
  var $ = function (id) { return document.getElementById(id); };
  function step(d) { var k = 0; D.q.forEach(function (t) { if (d > t) k++; }); return k; }
  function esc(x) { return String(x).replace(/&/g,'&amp;').replace(/</g,'&lt;'); }
  function sum(arr, k) { var t = 0; for (var i = 0; i < k; i++) t += arr[i]; return t; }
  function adjAttr(p) {
    if (!p.adj) return '';
    if (p.adj === 'cover') return ' class="sqxp" data-tip="Squad competition: ' + esc(p.cv || 'a regular') + ' out at this club, so the starting slot passes to the fit candidates in proportion to their own chances. Own scoring rates, more minutes."';
    if (p.adj === 'squeeze') return ' class="sqxp" data-tip="Squad competition: more fit starters than slots in this position at this club, so start probabilities are scaled to fit."';
    if (p.adj === 'return') return ' class="adjxp" data-tip="xP eased in: a regular back from a 3+ game absence; start probability capped and minutes trimmed until 2 games of evidence (' + p.ag + ' so far)."';
    if (p.adj === 'thin') return ' class="adjxp" data-tip="xP on a thin prior: last season was under 750 minutes, so it says little about this year\'s role; the prior leans on price and this season\'s games weigh more (' + p.ag + ' so far; settles at 4)."';
    var why = p.adj === 'move' ? 'moved club mid-season, so old-club starts do not count'
                               : 'new to this club or league, so last season\'s record is from elsewhere';
    return ' class="adjxp" data-tip="xP rebuilt on thin evidence: ' + why + '. ' + p.ag +
      ' game' + (p.ag === 1 ? '' : 's') + ' at the new club so far; attack uplift capped until 4."';
  }

  function renderPlayers() {
    var rows = D.players.map(function (p) {
      var xp = sum(p.x, n);
      return { p: p, xp: xp, val: xp / p.p, gw1: p.x[0], x8: sum(p.x, p.x.length) * D.sf };
    }).filter(function (r) {
      if (pos !== 'ALL' && r.p.pos !== pos) return false;
      if (q && r.p.n.toLowerCase().indexOf(q) < 0 && r.p.t.toLowerCase().indexOf(q) < 0) return false;
      return true;
    });
    rows.sort(function (a, b) {
      var cmp;
      if (sortKey === 'name') cmp = b.p.n.localeCompare(a.p.n);
      else if (sortKey === 'price') cmp = b.p.p - a.p.p;
      else if (sortKey === 'own') cmp = b.p.o - a.p.o;
      else if (sortKey === 'val') cmp = b.val - a.val;
      else if (sortKey === 'gw1') cmp = b.gw1 - a.gw1;
      else if (sortKey === 'x8') cmp = b.x8 - a.x8;
      else if (sortKey === 'form') cmp = b.p.f - a.p.f;
      else if (sortKey === 'total') cmp = b.p.tp - a.p.tp;
      else cmp = b.xp - a.xp;
      return sortAsc ? -cmp : cmp;   // first click high-to-low, second flips
    });
    var shown = showAll ? rows : rows.slice(0, 60);
    var h = '';
    shown.forEach(function (r, i) {
      var p = r.p, own = D.owned.indexOf(p.id) >= 0;
      var flag = p.s && p.s !== 'a' ? ' <span class="flag">' + esc(p.s.toUpperCase()) + '</span>' : '';
      h += '<tr' + (own ? ' class="own"' : '') + '><td class="num">' + (i + 1) + '</td><td>' + esc(p.n) + flag +
        (own ? '<span class="owntag">MINE</span>' : '') + '</td><td>' + p.pos + '</td><td>' + esc(p.t) +
        '</td><td class="num">£' + p.p.toFixed(1) + '</td><td class="num"><b' + adjAttr(p) + '>' + r.xp.toFixed(1) + '</b></td>' +
        '<td class="num"><span' + adjAttr(p) + '>' + r.gw1.toFixed(1) + '</span></td><td class="num"><span' + adjAttr(p) + '>' + r.x8.toFixed(1) + '</span></td>' +
        '<td class="num"><span' + adjAttr(p) + '>' + r.val.toFixed(2) + '</span></td>' +
        '<td class="num">' + p.f.toFixed(1) + '</td><td class="num">' + p.tp + '</td>' +
        '<td class="num"><span' + adjAttr(p) + '>' + Math.round(p.m) + '</span></td><td class="num">' + p.o.toFixed(0) + '%</td></tr>';
    });
    $('prows').innerHTML = h || '<tr><td colspan="10">No players match.</td></tr>';
    $('pmore').style.display = (!showAll && rows.length > 60) ? 'block' : 'none';
    $('pcount').textContent = rows.length;
    document.querySelectorAll('#ptable th.sort').forEach(function (th) {
      th.classList.toggle('on', th.dataset.k === sortKey);
      th.classList.toggle('asc', th.dataset.k === sortKey && sortAsc);
    });

  }

  function renderTop15() {
    var rows = D.players.map(function (p) {
      return { p: p, xp: sum(p.x, tn) };
    }).sort(function (a, b) { return b.xp - a.xp; });
    var top = rows.slice(0, 15), W = 680, L = 150, per = 24, H = per * top.length + 8;
    var vmax = top.length ? top[0].xp : 1, svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="Top 15 players by expected points over the projected gameweeks">';
    top.forEach(function (r, i) {
      var y = 4 + i * per, w = (W - L - 70) * r.xp / vmax;
      svg += '<text x="' + (L - 8) + '" y="' + (y + 15) + '" text-anchor="end" fill="var(--ink)" font-size="12">' + esc(r.p.n) + '</text>' +
        '<rect x="' + L + '" y="' + (y + 3) + '" width="' + w.toFixed(1) + '" height="16" rx="4" fill="var(--series-alt2)" data-tip="' + esc(r.p.n) + ' (' + esc(r.p.t) + '): ' + r.xp.toFixed(1) + ' xP over next ' + tn + ' GW"/>' +
        '<text x="' + (L + w + 6).toFixed(1) + '" y="' + (y + 15) + '" fill="' + (r.p.adj === 'cover' || r.p.adj === 'squeeze' ? 'var(--sq)' : r.p.adj ? '#f2a33a' : 'var(--muted)') + '" font-size="11">' + r.xp.toFixed(1) + '</text>';
    });
    $('pchart').innerHTML = svg + '</svg>';
    $('tlabel').textContent = 'Next ' + tn + ' gameweeks (GW' + D.gws[0] + '\u2013' + D.gws[tn - 1] + ')';
  }

  function renderTeams() {
    var rows = D.teams.map(function (t) {
      var fx = [], la = 0, lf = 0, st = 0, k = 0;
      for (var i = 0; i < n; i++) t.f[i].forEach(function (f) { fx.push(f); la += f.la; lf += f.lf; st += step(f.d); k++; });
      return { t: t, k: k, la: k ? la / k : 0, lf: k ? lf / k : 0, fdr: k ? 1 + st / k : 0 };
    });
    rows.sort(function (a, b) {
      if (!a.k && b.k) return 1; if (a.k && !b.k) return -1;
      if (teamSort === 'att') return b.lf - a.lf;
      if (teamSort === 'def') return a.la - b.la;
      if (teamSort === 'name') return a.t.s.localeCompare(b.t.s);
      return a.fdr - b.fdr;
    });
    var h = '';
    rows.forEach(function (r) {
      h += '<tr><td><b>' + esc(r.t.s) + '</b></td>';
      r.t.f.forEach(function (fl, i) {
        var dim = (i >= n ? ' dim' : '') + (D.bafter.indexOf(D.gws[i]) >= 0 ? ' ab' : '');
        if (!fl.length) { h += '<td class="cell' + dim + '"><span style="background:var(--chipbg);color:var(--muted)">—</span></td>'; return; }
        h += '<td class="cell' + dim + '">';
        fl.forEach(function (f) {
          var k = step(f.d), ink = INK[k];
          h += '<span style="background:' + RAMP[k] + ';color:' + ink + '" data-tip="GW' + D.gws[i] + ': ' + esc(f.o) + (f.h ? ' (h)' : ' (a)') +
            ' · opponent strength ' + f.d.toFixed(2) + ' · xG for ' + f.lf.toFixed(2) + ' · xG against ' + f.la.toFixed(2) + '">' + esc(f.o) + (f.h ? ' (h)' : ' (a)') + '</span>';
        });
        h += '</td>';
      });
      var k = r.k ? Math.min(4, Math.max(0, Math.round(r.fdr - 1))) : 0, ink = INK[k];
      h += '<td class="num">' + r.k + '</td><td class="num">' + r.lf.toFixed(2) + '</td><td class="num">' + r.la.toFixed(2) + '</td>' +
        '<td class="num"><span class="fdr" style="background:' + (r.k ? RAMP[k] : 'var(--chipbg)') + ';color:' + ink + '">' + (r.k ? r.fdr.toFixed(1) : '—') + '</span></td></tr>';
    });
    $('trows').innerHTML = h;
    document.querySelectorAll('#ttable th.sort').forEach(function (th) { th.classList.toggle('on', th.dataset.k === teamSort); });
  }

  function render() {
    $('hlabel').textContent = 'Next ' + n + ' gameweek' + (n > 1 ? 's' : '') + ' (GW' + D.gws[0] + (n > 1 ? '–' + D.gws[n - 1] : '') + ')';
    document.querySelectorAll('.hn').forEach(function (e) { e.textContent = n; });
    renderPlayers(); renderTeams();
  }
  $('hrange').max = N; $('hrange').value = n;
  $('hrange').addEventListener('input', function () { n = +this.value; render(); });
  $('trange').addEventListener('input', function () { tn = +this.value; renderTop15(); });
  renderTop15();
  document.querySelectorAll('#posseg button').forEach(function (b) {
    b.addEventListener('click', function () { pos = this.dataset.pos;
      document.querySelectorAll('#posseg button').forEach(function (x) { x.classList.toggle('on', x === b); }); renderPlayers(); });
  });
  $('psearch').addEventListener('input', function () { q = this.value.trim().toLowerCase(); renderPlayers(); });
  document.querySelectorAll('#ptable th.sort').forEach(function (th) { th.addEventListener('click', function () {
    if (sortKey === th.dataset.k) { sortAsc = !sortAsc; } else { sortKey = th.dataset.k; sortAsc = false; }
    renderPlayers(); }); });
  document.querySelectorAll('#ttable th.sort').forEach(function (th) { th.addEventListener('click', function () { teamSort = th.dataset.k; renderTeams(); }); });
  $('pmorebtn').addEventListener('click', function () { showAll = true; renderPlayers(); });
  render();
})();
"""


WEEKLY_JS = r"""
(function () {
  function renderLine(box, xl, series) {
    var W = 680, H = 240, L = 36, R = 100, T = 14, B = 28;
    var vmax = 0;
    series.forEach(function (s) { s.v.forEach(function (v) { if (v > vmax) vmax = v; }); });
    if (vmax <= 0) vmax = 1;
    var step = Math.pow(10, Math.floor(Math.log10(vmax / 4)));
    var mult = [1, 2, 2.5, 5, 10].filter(function (m) { return m * step >= vmax / 4; })[0] || 10;
    step = mult * step;
    var ymax = Math.ceil(vmax / step) * step;
    var px = function (i) { return L + (W - L - R) * i / Math.max(xl.length - 1, 1); };
    var py = function (v) { return T + (H - T - B) * (1 - v / ymax); };
    var svg = '<svg viewBox="0 0 ' + W + ' ' + H + '">';
    for (var t = 0; t <= ymax + 1e-9; t += step) {
      svg += '<line x1="' + L + '" y1="' + py(t).toFixed(1) + '" x2="' + (W - R) + '" y2="' + py(t).toFixed(1) + '" stroke="var(--grid)"/>'
        + '<text x="' + (L - 6) + '" y="' + (py(t) + 4).toFixed(1) + '" text-anchor="end" fill="var(--muted)" font-size="11">' + (+t.toFixed(2)) + '</text>';
    }
    xl.forEach(function (x, i) {
      svg += '<text x="' + px(i).toFixed(1) + '" y="' + (H - 8) + '" text-anchor="middle" fill="var(--muted)" font-size="11">' + x + '</text>';
    });
    var ends = [];
    series.forEach(function (s) {
      var pts = s.v.map(function (v, i) { return [px(i), py(v)]; });
      if (pts.length > 1) {
        svg += '<path d="M' + pts.map(function (pt) { return pt[0].toFixed(1) + ',' + pt[1].toFixed(1); }).join(' L')
          + '" fill="none" stroke="' + s.c + '" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>';
      }
      var e = pts[pts.length - 1];
      svg += '<circle cx="' + e[0].toFixed(1) + '" cy="' + e[1].toFixed(1) + '" r="4" fill="' + s.c + '" stroke="var(--card)" stroke-width="2"/>';
      ends.push({ x: e[0], y: e[1], name: s.n });
    });
    ends.forEach(function (e, i) {
      var clash = ends.some(function (o, j) { return j < i && Math.abs(o.y - e.y) < 13; });
      if (!clash) svg += '<text x="' + (e.x + 8).toFixed(1) + '" y="' + (e.y + 4).toFixed(1) + '" fill="var(--ink)" font-size="11" font-weight="600">' + e.name + '</text>';
    });
    svg += '</svg>';
    var legend = '<div class="legend">' + series.map(function (s) {
      return '<span><i style="background:' + s.c + '"></i>' + s.n + '</span>'; }).join('') + '</div>';
    box.innerHTML = legend + svg + '<div class="tip"></div>';
    var svgel = box.querySelector('svg'), tip = box.querySelector('.tip');
    svgel.addEventListener('mousemove', function (evt) {
      var r = svgel.getBoundingClientRect(); var x = (evt.clientX - r.left) * W / r.width;
      var best = 0, bd = 1e9;
      for (var i = 0; i < xl.length; i++) { var d = Math.abs(px(i) - x); if (d < bd) { bd = d; best = i; } }
      var html = '<b>' + xl[best] + '</b>';
      series.forEach(function (s) {
        html += '<span style="display:inline-block;width:10px;height:3px;background:' + s.c + ';margin-right:5px;vertical-align:middle"></span>' + s.n + ': ' + s.v[best].toFixed(1) + '<br>'; });
      tip.innerHTML = html; tip.style.display = 'block';
      var br = box.getBoundingClientRect(); var lx = evt.clientX - br.left + 12;
      if (lx + tip.offsetWidth > br.width) lx = evt.clientX - br.left - tip.offsetWidth - 12;
      tip.style.left = lx + 'px'; tip.style.top = (evt.clientY - br.top - 10) + 'px';
    });
    svgel.addEventListener('mouseleave', function () { tip.style.display = 'none'; });
  }

  var D = window.WK;
  var IN = ['var(--series-in)', 'var(--series-alt2)'], OUT = ['var(--series-out)', 'var(--series-alt1)'];
  if (D && D.moves.length) {
    var n = Math.min(5, D.gws.length), sel = 0;
    function draw() {
      document.querySelectorAll('.hn2').forEach(function (e) { e.textContent = n; });
      document.getElementById('wklabel').textContent = 'GW' + D.gws[0] + (n > 1 ? '\u2013' + D.gws[n - 1] : '');
      var mv = D.moves[sel], series = [];
      var sumByWeek = function (ps) {
        var out = [];
        for (var i = 0; i < n; i++) {
          out.push(ps.reduce(function (a, pp) { return a + pp.x[i]; }, 0));
        }
        return out;
      };
      if (mv.ins.length > 1 || mv.outs.length > 1) {
        // A multi-player move is judged as a package: combined in vs combined out.
        series.push({ n: 'In: ' + mv.ins.map(function (pp) { return pp.n; }).join(' + '),
                      c: IN[0], v: sumByWeek(mv.ins) });
        series.push({ n: 'Out: ' + mv.outs.map(function (pp) { return pp.n; }).join(' + '),
                      c: OUT[0], v: sumByWeek(mv.outs) });
      } else {
        mv.ins.forEach(function (pp, i) { series.push({ n: pp.n, c: IN[i % 2], v: pp.x.slice(0, n) }); });
        mv.outs.forEach(function (pp, i) { series.push({ n: pp.n, c: OUT[i % 2], v: pp.x.slice(0, n) }); });
      }
      var sum = function (ps) { return ps.reduce(function (a, pp) {
        return a + pp.x.slice(0, n).reduce(function (x, y) { return x + y; }, 0); }, 0); };
      var d = sum(mv.ins) - sum(mv.outs);
      document.getElementById('wkdelta').textContent = 'net ' + (d >= 0 ? '+' : '') + d.toFixed(1) + ' xP';
      renderLine(document.getElementById('wkchart'), D.gws.slice(0, n).map(function (g) { return 'GW' + g; }), series);
    }
    document.getElementById('wkrange').addEventListener('input', function () { n = +this.value; draw(); });
    document.getElementById('wksel').addEventListener('change', function () { sel = +this.value; draw(); });
    draw();
  }

  var C = window.WKCMP;
  if (C) {
    var pos = 'ALL', view = 'table', metric = 'x5';
    var bands = { own: true, t1: true, t2: true, t3: true };
    function bandOf(tr) {
      if (tr.classList.contains('own')) return 'own';
      if (tr.classList.contains('t1')) return 't1';
      if (tr.classList.contains('t2')) return 't2';
      return 't3';
    }
    var FMT = {
      x5: function (v) { return v.toFixed(1); }, x1: function (v) { return v.toFixed(1); },
      x8: function (v) { return v.toFixed(0); }, p: function (v) { return '\u00a3' + v.toFixed(1); },
      o: function (v) { return v.toFixed(0) + '%'; }, tp: function (v) { return v.toFixed(0); },
      t1: function (v) { return (v >= 0 ? '+' : '') + v.toFixed(0) + '%'; }
    };
    function apply() {
      document.querySelectorAll('#ctable tr[data-pos]').forEach(function (tr) {
        var okPos = (pos === 'ALL' || tr.getAttribute('data-pos') === pos);
        tr.style.display = (okPos && bands[bandOf(tr)]) ? '' : 'none';
      });
      var g = document.getElementById('cgraph'), t = document.getElementById('ctable');
      var msel = document.getElementById('cmetric');
      t.style.display = view === 'table' ? '' : 'none';
      g.style.display = view === 'graph' ? '' : 'none';
      msel.style.display = view === 'graph' ? '' : 'none';
      if (view !== 'graph') return;
      var BANDKEY = ['own', 't1', 't2', 't3'];
      var rows = C.players.filter(function (pp) {
        return (pos === 'ALL' || pp.pos === pos) && bands[BANDKEY[pp.tier]];
      }).sort(function (a, b) { return b[metric] - a[metric]; });   // always high to low
      var W = 680, per = 22, L = 150, H = per * rows.length + 8;
      var vals = rows.map(function (r) { return r[metric]; });
      var vmax = Math.max.apply(null, vals.concat([0.001]));
      var vmin = Math.min.apply(null, vals.concat([0]));
      var span = (vmax - vmin) || 1;
      var svg = '<svg viewBox="0 0 ' + W + ' ' + H + '">';
      rows.forEach(function (r, i) {
        var y = 4 + i * per, v = r[metric];
        var w = (W - L - 60) * (v - vmin) / span;
        var TIERC = ['var(--series-in)', '#e90052', 'rgba(233,0,82,.55)', 'rgba(233,0,82,.28)'];
        var c = TIERC[r.tier];
        svg += '<text x="' + (L - 8) + '" y="' + (y + 14) + '" text-anchor="end" fill="var(--ink)" font-size="11">' + r.n + '</text>'
          + '<rect x="' + L + '" y="' + (y + 2) + '" width="' + Math.max(w, 2).toFixed(1) + '" height="15" rx="4" fill="' + c
          + '" data-tip="' + r.n + ' (' + r.pos + ', \u00a3' + r.p.toFixed(1) + ', owned by ' + r.o + '%): ' + FMT[metric](v) + '"/>'
          + '<text x="' + (L + w + 6).toFixed(1) + '" y="' + (y + 14) + '" fill="var(--muted)" font-size="11">' + FMT[metric](v) + '</text>';
      });
      g.innerHTML = svg + '</svg>';
    }
    document.getElementById('cmetric').addEventListener('change', function () {
      metric = this.value; apply();
    });
    document.querySelectorAll('#cbands input').forEach(function (ck) {
      ck.addEventListener('change', function () { bands[ck.dataset.band] = ck.checked; apply(); });
    });
    var sortK = 'x5', sortAsc = false;
    document.querySelectorAll('#ctable th.csort').forEach(function (th) {
      th.addEventListener('click', function () {
        if (sortK === th.dataset.k) { sortAsc = !sortAsc; }
        else { sortK = th.dataset.k; sortAsc = false; }   // first click: high to low
        document.querySelectorAll('#ctable th.csort').forEach(function (x) {
          x.classList.toggle('on', x === th);
          x.classList.toggle('asc', x === th && sortAsc);
        });
        var tbl = document.querySelector('#ctable table');
        var rows = Array.prototype.slice.call(tbl.querySelectorAll('tr[data-pos]'));
        rows.sort(function (a, b) {
          var ka = a.getAttribute('data-' + sortK), kb = b.getAttribute('data-' + sortK);
          var na = parseFloat(ka), nb = parseFloat(kb);
          var cmp = (!isNaN(na) && !isNaN(nb)) ? na - nb : String(ka).localeCompare(String(kb));
          return sortAsc ? cmp : -cmp;
        });
        rows.forEach(function (r) { r.parentNode.appendChild(r); });
      });
    });
    document.querySelectorAll('#cposseg button').forEach(function (b) {
      b.addEventListener('click', function () { pos = b.dataset.pos;
        document.querySelectorAll('#cposseg button').forEach(function (x) { x.classList.toggle('on', x === b); }); apply(); });
    });
    document.querySelectorAll('#cview button').forEach(function (b) {
      b.addEventListener('click', function () { view = b.dataset.v;
        document.querySelectorAll('#cview button').forEach(function (x) { x.classList.toggle('on', x === b); }); apply(); });
    });
  }
})();
"""


def _general_body(matrix: pd.DataFrame, ds, gws: list[int], owned: set[int],
                  fx_by_team_gw: dict, short: dict, q: list[float]) -> str:
    """The market view: every player and every team over a chosen horizon."""
    boot = ds.players.set_index("id")
    players = []
    for _, r in matrix.iterrows():
        pid_ = int(r["id"])
        players.append({
            "id": pid_, "n": str(r["web_name"]), "pos": str(r["pos"]),
            "t": str(r["team_short"]), "p": round(float(r["now_cost"]) / 10, 1),
            "o": float(r["selected_by_percent"]), "s": str(r["status"]),
            "m": round(float(r["xmins"]), 1),
            "adj": str(r.get("adjusted") or "") if isinstance(r.get("adjusted"), str) else "",
            "cv": str(r.get("cover_for") or "") if isinstance(r.get("cover_for"), str) else "",
            "ag": int(r.get("adj_games") or 0),
            "tp": int(boot.at[pid_, "total_points"]),
            "f": float(boot.at[pid_, "form"]),
            "x": [round(float(r[f"xp_gw{g}"]), 2) for g in gws],
        })
    teams = []
    for _, t in ds.teams.iterrows():
        tid = int(t["id"])
        per_gw = []
        for g in gws:
            per_gw.append([
                {"o": short[int(f["opponent"])], "h": bool(f["is_home"]),
                 "la": round(float(f["lam_against"]), 3), "lf": round(float(f["lam_for"]), 3),
                 "d": round(float(f["difficulty"]), 3)}
                for f in fx_by_team_gw.get((tid, g), [])])
        teams.append({"id": tid, "s": str(t["short_name"]), "f": per_gw})
    gb = {bb["after_gw"] + 1 for bb in gw_breaks(ds, gws)}
    data = {"gws": gws, "sf": round((38 - ds.next_gw + 1) / len(gws), 4),
            "owned": sorted(owned), "q": [round(x, 4) for x in q],
            "ramp": DIFF_RAMP, "ink": DIFF_INK, "bafter": sorted(gb),
            "players": players, "teams": teams}
    data_js = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    ticker_head = "".join(
        f"<th style='text-align:center'{' class=ab' if g in gb else ''}>GW{g}</th>"
        for g in gws)
    ticker_legend = "".join(f"<i style='background:{c}'></i>" for c in DIFF_RAMP)
    return f"""
<div class="card"><h2>Horizon {info("Expected points already account for minutes, "
    "fixtures, doubles and blanks; the fixture scores summarise the same model at team level. "
    "Orange = evidence adjustment (club move, new arrival, return from injury, injury-hit last season). Purple = squad competition adjustment (covering for an absent teammate, or squeezed by a returner). Applies to xP and expected minutes — hover for details.")}</h2>
  <div class="controls"><label for="hrange">Look ahead</label>
    <input type="range" id="hrange" min="1" max="{len(gws)}" value="5" aria-label="Gameweeks to look ahead">
    <span class="hrz" id="hlabel"></span></div></div>

<div class="card"><h2>Expected points — all players</h2>
  <div class="controls">
    <span class="seg" id="posseg"><button class="on" data-pos="ALL">All</button><button data-pos="GKP">GKP</button>
      <button data-pos="DEF">DEF</button><button data-pos="MID">MID</button><button data-pos="FWD">FWD</button></span>
    <input type="search" id="psearch" placeholder="Player or team…" aria-label="Search players">
    <span class="note"><span id="pcount"></span> players · click a column to sort</span></div>
  <div class="tablewrap"><table id="ptable">
    <tr><th class="num">#</th><th class="sort" data-k="name">Player</th><th>Pos</th><th>Team</th><th class="sort num" data-k="price">£</th>
    <th class="sort on num" data-k="xp">xP next <span class="hn"></span></th><th class="sort num" data-k="gw1">GW{gws[0]}</th>
    <th class="sort num" data-k="x8" data-tip="Model xP over the next {len(gws)} gameweeks, extrapolated across the rest of the season (fixture-neutral beyond the horizon)">Season xP</th><th class="sort num" data-k="val">xP / £m</th>
    <th class="sort num" data-k="form">Form</th><th class="sort num" data-k="total">Total</th>
    <th class="num" data-tip="Expected minutes per match. A flag means the status is not &quot;available&quot; and the xP is already discounted for it.">xMins</th><th class="sort num" data-k="own">Owned</th></tr>
    <tbody id="prows"></tbody></table></div>
  <div class="more" id="pmore"><button id="pmorebtn">Show all</button></div>
</div>

<div class="card"><h2>Top 15 over the horizon</h2>
  <div class="controls"><label for="trange">Project ahead</label>
    <input type="range" id="trange" min="3" max="{len(gws)}" value="5" aria-label="Gameweeks projected ahead">
    <span class="hrz" id="tlabel"></span></div>
  <div class="chart" id="pchart"></div></div>

<div class="card"><h2>Fixture ticker — all teams</h2>
  <div class="tablewrap"><table class="ticker" id="ttable">
    <tr><th class="sort" data-k="name">Team</th>{ticker_head}<th class="sort num" data-k="fix" title="fixtures in range">Fix</th>
    <th class="sort num" data-k="att" data-tip="Expected goals scored per match — what matters for the team&#x27;s attackers. Includes the team&#x27;s own strength.">Att</th><th class="sort num" data-k="def" data-tip="Expected goals conceded per match — what matters for clean sheets. Includes the team&#x27;s own strength.">Def</th><th class="sort on num" data-k="fdr" data-tip="Average opponent strength over the range, 1–5: the opponent&#x27;s attack and defence on the relevant side (home/away), independent of the team&#x27;s own quality.">FDR</th></tr>
    <tbody id="trows"></tbody></table></div>
  <div class="tl">easier {ticker_legend} harder &nbsp;·&nbsp; <span style='color:var(--warn)'>│</span> = international break</div>
</div>
<script>window.MUD = {data_js};</script>
<script>{GENERAL_JS}</script>
"""


def build_site() -> None:
    plan = json.loads((PROCESSED / "plan.json").read_text())
    matrix = pd.read_csv(PROCESSED / "xp_matrix.csv")
    comps = pd.read_csv(PROCESSED / "xp_components.csv")
    ds = load_dataset()
    m = matrix.set_index("id")
    meta = plan["meta"]
    next_gw = meta["next_gw"]
    sched = meta.get("schedule", {})
    gw_cols = sorted([c for c in matrix.columns if c.startswith("xp_gw")],
                     key=lambda c: int(c.replace("xp_gw", "")))
    gws = [int(c.replace("xp_gw", "")) for c in gw_cols]
    near = gws[:5]
    now = datetime.now(timezone.utc)
    deadline = datetime.fromisoformat(meta["deadline"].replace("Z", "+00:00"))
    hours_left = (deadline - now).total_seconds() / 3600
    countdown = f"{hours_left/24:.0f} days" if hours_left > 48 else f"{max(hours_left,0):.0f} hours"

    def name(p): return ESC(str(m.at[p, "web_name"]))
    def team(p): return str(m.at[p, "team_short"])
    def price(p): return f"{m.at[p, 'now_cost']/10:.1f}"
    def xp(p, g): return float(m.at[p, f"xp_gw{g}"])
    def xp_near(p): return sum(xp(p, g) for g in near)
    adj_of = (m["adjusted"].fillna("").to_dict() if "adjusted" in m.columns else {})
    cover_of = (m["cover_for"].fillna("").to_dict() if "cover_for" in m.columns else {})
    adj_games = (m["adj_games"].fillna(0).to_dict() if "adj_games" in m.columns else {})

    def adj_tip(pid):
        a = adj_of.get(pid, "")
        if not a:
            return ""
        g = int(adj_games.get(pid, 0))
        if a == "cover":
            return (f"Squad competition: {cover_of.get(pid, 'a regular')} out at "
                    f"this club, so his starting slot passes to the fit "
                    f"candidates in proportion to their own chances. Own scoring "
                    f"rates, more minutes.")
        if a == "squeeze":
            return ("Squad competition: more fit starters than slots in "
                    "this position at this club, so everyone's start probability "
                    "is scaled to fit.")
        if a == "return":
            return (f"xP eased in: a regular back from a 3+ game absence — start "
                    f"probability capped and minutes trimmed until 2 games of "
                    f"evidence ({g} so far).")
        if a == "thin":
            return (f"xP on a thin prior: last season was under 750 minutes "
                    f"(injury or bit-part), so it says little about this year's "
                    f"role — the prior leans on price and this season's games "
                    f"weigh more ({g} so far; settles at 4).")
        why = ("moved club mid-season, so old-club starts don't count"
               if a == "move" else
               "new to this club or league, so last season's record is from elsewhere")
        return (f"xP rebuilt on thin evidence: {why}. {g} game{'s' if g != 1 else ''} "
                f"at the new club so far; attack uplift capped until 4.")

    def adj_cls(pid):
        return "sqxp" if adj_of.get(pid, "") in ("cover", "squeeze") else "adjxp"

    def xpv(pid, val, fmt="{:.1f}"):
        t = adj_tip(pid)
        txt = fmt.format(val)
        return f"<span class='{adj_cls(pid)}' data-tip='{ESC(t)}'>{txt}</span>" if t else txt

    plans = plan["baseline"]["plans"]
    first = plans[0]
    rob = plan.get("robustness") or {}
    owned = set(int(i) for i in ds.squad["id"])
    sell = ds.squad.set_index("id")["sell_price"]

    # ---- fixtures + difficulty per team per GW ----
    strengths = build_strengths(ds)
    lams = fixture_lambdas(ds, strengths)
    lams = lams[lams["gw"].isin(gws)]
    q = lams["difficulty"].quantile([0.2, 0.4, 0.6, 0.8]).tolist()
    short = ds.teams.set_index("id")["short_name"].to_dict()

    def diff_step(diff: float) -> int:
        return sum(diff > t for t in q)

    bafter = {bb["after_gw"] + 1 for bb in gw_breaks(ds, gws)}

    fx_by_team_gw: dict[tuple[int, int], list] = {}
    for _, r in lams.iterrows():
        fx_by_team_gw.setdefault((int(r["team"]), int(r["gw"])), []).append(r)

    def fixture_cells(team_id: int) -> str:
        cells = []
        for g in gws:
            ab = " ab" if g in bafter else ""
            fl = fx_by_team_gw.get((team_id, g), [])
            if not fl:
                cells.append(f'<td class="cell{ab}"><span style="background:var(--chipbg);color:var(--muted)">—</span></td>')
                continue
            inner = []
            for r in fl:
                step = diff_step(r["difficulty"])
                bg = DIFF_RAMP[step]
                ink = DIFF_INK[step]
                opp = short[int(r["opponent"])] + (" (h)" if r["is_home"] else " (a)")
                inner.append(f'<span style="background:{bg};color:{ink}" '
                             f'data-tip="GW{g}: {opp} · opponent strength {r["difficulty"]:.2f} · xG for {r["lam_for"]:.2f} · xG against {r["lam_against"]:.2f}">{opp}</span>')
            cells.append(f'<td class="cell{ab}">' + "".join(inner) + "</td>")
        return "".join(cells)

    def fixture_string(team_id: int, n: int = 5) -> str:
        parts = []
        for g in gws[:n]:
            fl = fx_by_team_gw.get((team_id, g), [])
            if not fl:
                parts.append("—")
            else:
                parts.append(" + ".join(
                    short[int(r["opponent"])] + (" (h)" if r["is_home"] else " (a)") for r in fl))
        return ", ".join(parts)

    # ---- this week's call: verdict header + swap modules + package ladder ----
    survival = rob.get("survival")
    surv_class = "good" if (survival or 0) >= 0.6 else "warn" if (survival or 0) >= 0.3 else "bad"
    surv_text = f"{survival:.0%}" if survival is not None else "n/a"

    md = plan.get("move_decision") or {}
    dc = plan.get("decomposition")
    hd = plan.get("hit_decision") or {}
    held = bool(md.get("held"))
    comp = plan.get("completed") or {}
    done_tx = comp.get("transfers") or []
    done_in = [t["in"] for t in done_tx if t["in"] in m.index]
    done_out = [t["out"] for t in done_tx if t["out"] in m.index]
    ft_left = plan["meta"].get("free_transfers_assumed")
    disp_in = first["transfers_in"] or md.get("rec_in", [])
    disp_out = first["transfers_out"] or md.get("rec_out", [])

    def _pair_up(ins, outs):
        """FPL transfers are like-for-like: pair within position, best to best."""
        ins_by, outs_by = {}, {}
        for x in ins:
            ins_by.setdefault(str(m.at[x, "pos"]), []).append(x)
        for x in outs:
            outs_by.setdefault(str(m.at[x, "pos"]), []).append(x)
        prs = []
        for k in ["GKP", "DEF", "MID", "FWD"]:
            aa = sorted(ins_by.get(k, []), key=lambda x: -xp_near(x))
            bb = sorted(outs_by.get(k, []), key=lambda x: -xp_near(x))
            prs += list(zip(aa, bb))
        pi_, po_ = {x for x, _ in prs}, {x for _, x in prs}
        prs += list(zip((x for x in ins if x not in pi_),
                        (x for x in outs if x not in po_)))
        return prs

    pairs = _pair_up(disp_in, disp_out)

    DRV_LABEL = {"appearance": "mins", "goals": "goals", "assists": "assists",
                 "cs": "CS", "defcon": "defcon", "saves": "saves", "bonus": "bonus"}
    DRV_PHRASE = {"cs": "the best clean-sheet upgrade available",
                  "goals": "an attacking upgrade", "assists": "an attacking upgrade",
                  "defcon": "a defensive-contribution engine",
                  "appearance": "a minutes upgrade", "saves": "a shot-stopping upgrade",
                  "bonus": "a bonus magnet"}

    def _drivers_of(pid):
        rows_ = comps[(comps["id"] == pid) & (comps["gw"].isin(near))]
        if rows_.empty:
            return []
        sums = rows_[["appearance", "goals", "assists", "cs", "defcon",
                      "saves", "bonus"]].sum()
        return [(k, v) for k, v in sums.sort_values(ascending=False).head(3).items()
                if v > 0.3]

    themes_in_rate = {int(pid_): r for pid_, r in (rob.get("themes", {}) or {}).get("in", [])}

    def _role_of(pin, pout):
        gain = xp_near(pin) - xp_near(pout)
        freed = (int(sell.get(pout, m.at[pout, "now_cost"]))
                 - int(m.at[pin, "now_cost"]))
        if freed > 0 and gain < 2:
            return "financing", freed, gain
        return "payload", freed, gain

    DRV_FULL = {"appearance": "minutes", "goals": "goals", "assists": "assists",
                "cs": "clean sheets", "defcon": "def. contribution",
                "saves": "saves", "bonus": "bonus"}

    def _driver_sums(pid, keys):
        rows_ = comps[(comps["id"] == pid) & (comps["gw"].isin(near))]
        if rows_.empty:
            return {k: 0.0 for k in keys}
        sums = rows_[["appearance", "goals", "assists", "cs", "defcon",
                      "saves", "bonus"]].sum()
        return {k: float(sums.get(k, 0)) for k in keys}

    def _fdr_dots(team_id: int) -> tuple[str, float]:
        dots, steps = [], []
        for g in near:
            for r in fx_by_team_gw.get((team_id, g), []):
                k = diff_step(r["difficulty"])
                steps.append(k)
                opp = short[int(r["opponent"])] + (" (h)" if r["is_home"] else " (a)")
                dots.append(f"<i class='fdot' style='background:{DIFF_RAMP[k]}' "
                            f"data-tip='GW{g}: {ESC(opp)} · opponent strength "
                            f"{r['difficulty']:.2f}'></i>")
        avg = 1 + sum(steps) / len(steps) if steps else 0.0
        return "".join(dots), avg

    def _swap_module(pin, pout, done=False):
        role, freed, gain = _role_of(pin, pout)
        ribbon = ("PAYLOAD" if role == "payload"
                  else f"FINANCING · frees £{freed/10:.1f}")
        vin = [xp(pin, g) for g in near]
        vout = [xp(pout, g) for g in near]
        spark = _spark(vin, vout,
                       f"{m.at[pin,'web_name']} vs {m.at[pout,'web_name']}, xP GW{near[0]}-{near[-1]}")
        # comparison rows: xP, ownership, then the in-player's top drivers,
        # every one shown out vs in with the delta on the right
        keys = [k for k, _ in _drivers_of(pin)]
        in_d, out_d = _driver_sums(pin, keys), _driver_sums(pout, keys)
        def cmp_row(label_, vo, vi, fo="{:.1f}", strong=False, xp_row=True):
            d = vi - vo
            so = xpv(pout, vo, fo) if xp_row else fo.format(vo)
            si = xpv(pin, vi, fo) if xp_row else fo.format(vi)
            return (f"<span>{ESC(label_)}</span>"
                    f"<span class='num'>{so}</span>"
                    f"<span class='num'>{'<b>' if strong else ''}{si}"
                    f"{'</b>' if strong else ''} <em class='delta{'' if d >= 0 else ' neg'}'>"
                    f"{d:+.1f}</em></span>")
        rows_html = cmp_row(f"xP next {len(near)}", xp_near(pout), xp_near(pin),
                            strong=True)
        rows_html += cmp_row("owned by %", float(m.at[pout, "selected_by_percent"]),
                             float(m.at[pin, "selected_by_percent"]), "{:.0f}", xp_row=False)
        for k in keys:
            rows_html += cmp_row(DRV_FULL[k], out_d[k], in_d[k])
        out_dots, out_fdr = _fdr_dots(int(m.at[pout, "team"]))
        in_dots, in_fdr = _fdr_dots(int(m.at[pin, "team"]))
        fdr_d = in_fdr - out_fdr   # lower FDR = easier, so negative is good
        rows_html += (f"<span>fixtures (FDR)</span>"
                      f"<span class='num fx'>{out_dots} {out_fdr:.1f}</span>"
                      f"<span class='num fx'>{in_dots} <b>{in_fdr:.1f}</b> "
                      f"<em class='delta{'' if fdr_d <= 0 else ' neg'}'>"
                      f"{fdr_d:+.1f}</em></span>")
        alts = matrix[(matrix["pos"] == m.at[pin, "pos"])
                      & (~matrix["id"].isin(owned))
                      & (matrix["id"] != pin)
                      & ((matrix["now_cost"] - m.at[pin, "now_cost"]).abs() <= 10)].copy()
        alts["near_"] = alts[[f"xp_gw{g}" for g in near]].sum(axis=1)

        def _vs(v, fmt="{:+.1f}"):
            if abs(v) < 0.05:
                return "<span class='news'>(±0.0)</span>"
            return (f"<em class='delta{'' if v > 0 else ' neg'}'>"
                    f"({fmt.format(v)})</em>")

        pin_price = float(m.at[pin, "now_cost"]) / 10
        pin_near = xp_near(pin)
        alt_rows = "".join(
            f"<tr><td>{ESC(str(r_['web_name']))} <span class='news'>{ESC(str(r_['team_short']))}</span></td>"
            f"<td class='num'>£{r_['now_cost']/10:.1f} {_vs(r_['now_cost']/10 - pin_price)}</td>"
            f"<td class='num'>{r_['near_']:.1f} {_vs(r_['near_'] - pin_near)}</td></tr>"
            for _, r_ in alts.nlargest(3, "near_").iterrows())
        rate = rob.get("player_in_rates", {}).get(str(pin), themes_in_rate.get(int(pin)))
        rate_txt = (f"this swap appears in <b>{rate:.0%}</b> of shaken scenarios"
                    if rate is not None else "")
        def block(pid_, price_, cls):
            tag_ = ""
            if adj_tip(pid_):
                a_ = adj_of.get(pid_, "")
                word = {"cover": "cover", "squeeze": "squeezed", "return": "returning",
                        "thin": "thin prior", "move": "new club", "arrival": "arrival"}.get(a_, "adjusted")
                tag_ = (f"<span class='{'sqtag' if a_ in ('cover', 'squeeze') else 'adjtag'}' "
                        f"data-tip='{ESC(adj_tip(pid_))}'>{word}</span>")
            return (f"<div class='sp {cls}'>{shirt_svg(TEAM_COLOURS.get(team(pid_), '#888'))}"
                    f"<div><b>{name(pid_)}</b>{tag_}<div class='news'>{team(pid_)} · "
                    f"{ESC(str(m.at[pid_, 'pos']))} · £{price_:.1f}</div></div></div>")
        return f"""<div class="swap{' heldswap' if held and not done else ''}">
  <span class="ribbon {'rb-done' if done else 'rb-pay' if role == 'payload' else 'rb-fin'}">{'✓ MADE' if done else 'CONSIDERED — below the bar' if held else ribbon}</span>
  <div class="shead">{block(pout, sell.get(pout, m.at[pout, 'now_cost'])/10, 'spout')}
    <span class="sarrow">→</span>
    {block(pin, m.at[pin, 'now_cost']/10, 'spin')}</div>
  <div class="cmpgrid">
    <span class="cl"></span><span class="cl">out</span><span class="cl">in</span>
    {rows_html}
  </div>
  {spark}
  <div class="sfoot">{rate_txt}</div>
  <div class="altbox"><div class="althead">Alternative Solutions</div>
    <table class="alttab"><tr><th></th><th class='num'>£</th><th class='num'>xP next {len(near)}</th></tr>
    {alt_rows}</table></div>
</div>"""

    swap_html = ("<div class='swapgrid'>"
                 + "".join(_swap_module(a_, b_) for a_, b_ in pairs)
                 + "</div>") if pairs else ""

    def _scen_rows(mvs):
        rows = ""
        mx = max((mv["share"] for mv in mvs), default=0) or 1
        for mv in mvs:
            if not all(x in m.index for x in list(mv["in"]) + list(mv["out"])):
                continue
            ins = ", ".join(str(m.at[x, "web_name"]) for x in mv["in"])
            ins_cell = (f"<td class='in'>{ESC(ins)}</td>" if ins
                        else "<td class='noopw'>hold</td>")
            outs = ", ".join(str(m.at[x, "web_name"]) for x in mv["out"]) or "—"
            w = 100 * mv["share"] / mx
            rows += (f"<tr>" + ins_cell + f"<td class='out'>{ESC(outs)}</td>"
                     f"<td class='num'>{mv['share']:.0%}</td>"
                     f"<td class='meter'><div style='width:{w:.0f}%'></div></td></tr>")
        return rows

    done_pairs = _pair_up(done_in, done_out)
    done_html = ""
    if done_pairs:
        done_html = ("<div class='doneh'>Made this week</div><div class='swapgrid'>"
                     + "".join(_swap_module(a_, b_, done=True)
                               for a_, b_ in done_pairs) + "</div>")
        mt_ = comp.get("matched") or {}
        past_rows = _scen_rows(mt_.get("top_moves") or [])
        if past_rows:
            conv_ = mt_.get("conviction")
            made_names = " + ".join(name(a_) for a_, _ in done_pairs)
            conv_bit = (f" — chosen on payload conviction (<b>{conv_:.0%}</b>): "
                        f"with a double, the exact pair rarely repeats under "
                        f"noise, but the key player recurs"
                        if conv_ else "")
            done_html += (
                f"<details class='runs'><summary class='note'>For the record — "
                f"the shake-test field at the time of the call</summary>"
                f"<div class='note' style='margin-top:6px'>Your package was "
                f"<b>{ESC(made_names)}</b>{conv_bit}. This table is frozen at "
                f"the moment of the call, for judging the decision later:</div>"
                f"<div class='tablewrap'><table class='scen'>"
                f"<tr><th>In</th><th>Out</th><th class='num'>Share</th><th></th></tr>"
                f"{past_rows}</table></div></details>")

    # package ladder: the decision rule as a picture
    ladder = ""
    if dc:
        bar = md.get("threshold")
        pkg = dc["package_gain"]
        sing = dc.get("single")
        pts = [(0.0, "hold")]
        if sing and sing.get("in"):
            pts.append((sing["gain"], f"single: {name(sing['in'][0])}"))
        pts.append((pkg, "this package" if not held else "considered package"))
        xmax = max([v for v, _ in pts] + [bar or 0, 0.5]) * 1.2
        W_, H_ = 680, 84
        x_ = lambda v: 60 + (W_ - 120) * max(v, 0) / xmax
        elems = [f'<line x1="60" y1="46" x2="{W_-60}" y2="46" stroke="var(--grid)"/>']
        if bar:
            elems.append(f'<line x1="{x_(bar):.0f}" y1="12" x2="{x_(bar):.0f}" y2="66" '
                         f'stroke="var(--warn)" stroke-dasharray="4 3"/>'
                         f'<text x="{x_(bar):.0f}" y="80" text-anchor="middle" '
                         f'fill="var(--warn)" font-size="11">bar +{bar:.1f}</text>')
        for i, (v, lab) in enumerate(pts):
            last_pt = i == len(pts) - 1
            col = ("var(--good)" if (bar is None or v >= bar) else "var(--warn)")                 if last_pt else "var(--muted)"
            elems.append(
                f'<circle cx="{x_(v):.0f}" cy="46" r="6" fill="{col}" '
                f'stroke="var(--card)" stroke-width="2"/>'
                f'<text x="{x_(v):.0f}" y="{28 if i % 2 == 0 else 12}" text-anchor="middle" '
                f'fill="var(--ink)" font-size="11" font-weight="600">+{v:.1f}</text>'
                f'<text x="{x_(v):.0f}" y="66" text-anchor="middle" '
                f'fill="var(--muted)" font-size="10.5">{ESC(lab)}</text>')
        ladder = (f"<div class='ladder'><svg viewBox='0 0 {W_} {H_}' role='img' "
                  f"aria-label='Plan value of each option versus the move bar'>"
                  + "".join(elems) + "</svg></div>")

    # verdict header
    t_d = plan.get("timing", {})
    timing_chip = ("consider moving early" if t_d.get("deviates")
                   else "move late" if pairs and not held else "—")
    chips_html = ""
    if done_tx:
        chips_html += (f"<span class='vchip'>this week <b>{len(done_tx)} "
                       f"transfer{'s' if len(done_tx) != 1 else ''} ✓</b></span>")
    if md:
        chips_html += (f"<span class='vchip'>vs holding <b>{md['gain']:+.1f}</b> "
                       f"(bar +{md['threshold']:.1f})</span>")
    chips_html += (f"<span class='vchip'>confidence "
                   f"<b class='{surv_class}t'>{('high' if (survival or 0) >= .6 else 'medium' if (survival or 0) >= .3 else 'low').upper()}</b></span>")
    if pairs and not held:
        chips_html += f"<span class='vchip'>timing <b>{ESC(timing_chip)}</b></span>"

    payload_pairs = [(a_, b_) for a_, b_ in pairs if _role_of(a_, b_)[0] == "payload"]
    fin_pairs = [(a_, b_) for a_, b_ in pairs if _role_of(a_, b_)[0] == "financing"]

    def _done_rationale():
        """The story of transfers already made: what, why, and what's left."""
        mt = comp.get("matched")
        if comp.get("in_line") and mt:
            when_ = fmt_uk(datetime.fromisoformat(mt["run_at"]))
            txt = f"Made in line with the model's {when_} recommendation"
            facts = []
            if mt.get("move_gain") is not None:
                facts.append(f"the package beat holding by <b>+{mt['move_gain']:.1f}</b> "
                             f"against the +{mt['move_bar']:.1f} bar")
            if mt.get("risk"):
                r_ = mt["risk"]
                facts.append(f"made early under price pressure — a {r_['p_deadline']:.0%} "
                             f"chance of the package breaking by the deadline"
                             + (f", next-best option {r_['gap']:.1f} xP behind"
                                if r_.get("gap") else ""))
            if facts:
                txt += ": " + "; ".join(facts)
            txt += "."
            if mt.get("conviction"):
                txt += f" Payload conviction at the time: <b>{mt['conviction']:.0%}</b>."
        else:
            txt = "Your own call — not what the model recommended at the time."
        return txt

    if done_pairs and not pairs:
        n_ = len(done_pairs)
        verdict = f"{n_} transfer{'s' if n_ != 1 else ''} made"
        rationale = _done_rationale()
        if ft_left == 0:
            rationale += (" <b>No further moves</b> — anything else now costs a −4 hit, "
                          "and a fresh FT arrives next week.")
        else:
            rationale += (" <b>No further moves recommended</b> — the remaining FT "
                          "rolls over.")
    elif held:
        verdict = "Hold — avoid a hit" if ft_left == 0 else "Hold — bank the FT"
        names_ = " + ".join(name(x) for x in disp_in)
        rationale = (f"The best package ({names_}) is only "
                     f"<b>{md.get('gain', 0):+.1f}</b> against the "
                     f"+{md.get('threshold', 0):.1f} bar "
                     f"{len(disp_in)} transfer{'s' if len(disp_in) != 1 else ''} must "
                     f"clear — the spare FTs' option value wins this week.")
    elif pairs:
        verdict = f"Make {len(pairs)} transfer{'s' if len(pairs) != 1 else ''}"
        bits = []
        for a_, b_ in payload_pairs:
            # Appearance points are table stakes — phrase the differentiator.
            drv = [d for d in _drivers_of(a_) if d[0] != "appearance"] or _drivers_of(a_)
            phrase = DRV_PHRASE.get(drv[0][0], "the strongest upgrade available") if drv else "the strongest upgrade available"
            bits.append(f"<b>{name(a_)}</b> is the target — {phrase}")
        for a_, b_ in fin_pairs:
            _, freed, _ = _role_of(a_, b_)
            bits.append(f"{name(b_)} → {name(a_)} frees the £{freed/10:.1f} that funds it")
        rationale = "; ".join(bits) + "."
        if md:
            rationale += (f" The package beats holding by <b>{md['gain']:+.1f}</b> "
                          f"against the +{md['threshold']:.1f} bar.")
        if ft_left == 0 and not first["hits"]:
            rationale += " (Costs a −4 with no FT left — the gain above is net of it.)"
        if first["hits"] and hd.get("taken"):
            rationale += (f" Includes a −{4*first['hits']} hit, which buys "
                          f"+{hd['gain']:.1f} over the best free plan — worth it.")
        elif hd and not hd.get("taken"):
            rationale += (f" A −{4*hd.get('hits', 1)} hit was considered and "
                          f"rejected (+{hd['gain']:.1f} only).")
    else:
        if ft_left == 0:
            verdict = "Hold — avoid a hit"
            rationale = ("Nothing here is worth a −4 — a fresh FT arrives "
                         "next week.")
        else:
            verdict = "Hold — bank the FT"
            rationale = "No move worth its free transfer this week."
    if done_pairs and pairs:
        rationale = (f"You've already made {len(done_pairs)} this week (below) — "
                     + rationale)

    vhead = (f"<div class='vhead'><div><div class='verdict'>{verdict}</div>"
             f"<div class='rationale'>{rationale}</div></div>"
             f"<div class='vchips'>{chips_html}</div></div>")

    headline = vhead + swap_html + done_html + ladder

    timing_d = plan.get("timing", {})
    advice = ESC(timing_d.get("advice", ""))
    if timing_d.get("deviates"):
        pr_ = timing_d.get("package_risk")
        if pr_:
            moving = " · ".join(
                f"<span class='{'out' if d_ == 'fall' else 'in'}'>{ESC(n_)} "
                f"{'▼' if d_ == 'fall' else '▲'} {p1_:.0f}%</span>"
                for n_, d_, _p0, p1_ in pr_["at_risk"][:4])
            window = ""
            iso_ = pr_.get("act_before_iso")
            past_ = bool(iso_) and datetime.fromisoformat(iso_) < datetime.now(timezone.utc)
            if pr_.get("act_before") and not past_:
                window = (f"<div class='talert-win'>Act between "
                          f"<b>{ESC(pr_['act_from'])}</b> → "
                          f"<b>{ESC(pr_['act_before'])}</b></div>")
            elif past_:
                window = ("<p class='news'>The computed window has passed — "
                          "awaiting the next run.</p>")
            body_ = (
                f"<p>The package is an exact fit (£{pr_['slack']:.1f} slack) and "
                f"{moving} are moving against it.</p>"
                f"<p>≈<b>{pr_['p_deadline']:.0%}</b> chance it breaks before the "
                f"deadline ({pr_['p_tonight']:.0%} tonight alone); the fallback "
                f"costs ~{pr_['gap']:.1f} xP.</p>{window}")
        else:
            body_ = f"<p>{advice}</p>"
        timing_html = (
            f"<div class='timing'><div class='talert'><div class='talert-ic'>!</div>"
            f"<div><div class='talert-h'>Timing — consider moving early</div>"
            f"{body_}</div></div></div>")
    else:
        timing_html = (
            f"<div class='timing'><b>Timing:</b> {advice} "
            f"<button class='ibtn' type='button' aria-label='Why transfer late?' "
            f"aria-expanded='false'>i</button>"
            f"<span class='inote note'>Default is always late — team news lands "
            f"the day before each club's match, and a price rise only banks you "
            f"£0.05 on resale.</span></div>")

    gw_xp = (sum(xp(p_, next_gw) for p_ in first["lineup"])
             + xp(first["captain"], next_gw) - 4 * first["hits"])

    # ---- pitch ----
    def pcard(p, bench=False):
        badge = ""
        if p == first["captain"]:
            badge = "<span class='cap'>C</span>"
        elif p == first["vice"]:
            badge = "<span class='vc'>V</span>"
        new_ = p in done_in
        if new_:
            badge += ("<span class='newp' data-tip='Joined this week — one of "
                      "the transfers you made'>NEW</span>")
        col = TEAM_COLOURS.get(team(p), "#888")
        price_m = m.at[p, "now_cost"] / 10
        value = xp(p, next_gw) / price_m
        return (f"<div class='pcard{' newin' if new_ else ''}'>{badge}{shirt_svg(col)}<div class='nm'>{name(p)}</div>"
                f"<div class='xp'>{xpv(p, xp(p, next_gw))}</div>"
                f"<div class='pmeta'>£{price_m:.1f} · {xpv(p, value, '{:.2f}')}/£</div></div>")
    by_pos = {"GKP": [], "DEF": [], "MID": [], "FWD": []}
    for p in first["lineup"]:
        by_pos[m.at[p, "pos"]].append(pcard(p))
    pitch = "".join(f"<div class='row'>{''.join(by_pos[k])}</div>" for k in ["GKP", "DEF", "MID", "FWD"])
    bench = "".join(pcard(p, True) for p in first["bench"])

    # ---- comparison chart data (rendered client-side) ----
    cands = []
    if first["transfers_in"]:
        cands.append((tuple(first["transfers_in"]), tuple(first["transfers_out"])))
    if done_in:
        cands.append((tuple(done_in), tuple(done_out)))
        for mvv in ((comp.get("matched") or {}).get("top_moves") or []):
            ids_ = list(mvv["in"]) + list(mvv["out"])
            if mvv["in"] and all(x in m.index for x in ids_):
                cands.append((tuple(mvv["in"]), tuple(mvv["out"])))
    for mvv in rob.get("top_moves", []):
        if mvv["in"]:
            cands.append((tuple(mvv["in"]), tuple(mvv["out"])))
    pos_rank = {"GKP": 0, "DEF": 1, "MID": 2, "FWD": 3}
    by_pos_xp = lambda x: (pos_rank.get(str(m.at[x, "pos"]), 9), -xp_near(x))
    cands = [(tuple(sorted(ins, key=by_pos_xp)), tuple(sorted(outs, key=by_pos_xp)))
             for ins, outs in cands]
    seen_mv, wk_moves = set(), []
    for ins, outs in cands:
        if (ins, outs) in seen_mv:
            continue
        seen_mv.add((ins, outs))
        wk_moves.append({
            "label": (", ".join(str(m.at[x, "web_name"]) for x in ins) + " ⇄ "
                      + (", ".join(str(m.at[x, "web_name"]) for x in outs) or "—")
                      + (" (made)" if done_in and set(ins) == set(done_in) else "")),
            "ins": [{"n": str(m.at[x, "web_name"]),
                     "x": [round(xp(x, g), 2) for g in gws]} for x in ins],
            "outs": [{"n": str(m.at[x, "web_name"]),
                      "x": [round(xp(x, g), 2) for g in gws]} for x in outs],
        })
    wk_moves = wk_moves[:6]
    wk_data = json.dumps({"gws": gws, "moves": wk_moves},
                         separators=(",", ":")).replace("</", "<\\/")
    wk_options = "".join(f"<option value='{i}'>{ESC(mv['label'])}</option>"
                         for i, mv in enumerate(wk_moves))

    # ---- plan table ----
    gw_notes = sched.get("gw_notes", {})
    breaks_after = {b["after_gw"]: b for b in gw_breaks(ds, gws)}
    plan_rows, plan_rows_far = "", ""
    for pi, p in enumerate(plans):
        ins = ", ".join(f"{name(x)} ({team(x)})" for x in p["transfers_in"]) or "—"
        outs = ", ".join(f"{name(x)} ({team(x)})" for x in p["transfers_out"]) or "—"
        hit = f"−{4*p['hits']}" if p["hits"] else ""
        note = ESC(gw_notes.get(str(p["gw"]), "") or "")
        row = (f"<tr><td>GW{p['gw']}</td><td class='in'>{ins}</td><td class='out'>{outs}</td>"
               f"<td>{hit}</td><td class='num'>{p['ft_before']}</td><td>{name(p['captain'])}</td>"
               f"<td class='num'>£{p['bank_after']:.1f}</td><td class='fix'>{note}</td></tr>")
        if p["gw"] in breaks_after:
            b = breaks_after[p["gw"]]
            row += (f"<tr class='brkrow'><td colspan='8'>⏸ {ESC(b['label'])}"
                    f" — {b['days']} days to the GW{p['gw'] + 1} deadline</td></tr>")
        if pi < 3:
            plan_rows += row
        else:
            plan_rows_far += row

    # ---- fixture ticker (FDR aggregate + break markers) ----
    def team_fdr(team_id: int) -> str:
        steps = [diff_step(r["difficulty"]) for g in gws
                 for r in fx_by_team_gw.get((team_id, g), [])]
        if not steps:
            return "—"
        fdr = 1 + sum(steps) / len(steps)
        k = min(4, max(0, round(fdr - 1)))
        return (f"<span class='fdr' style='background:{DIFF_RAMP[k]};"
                f"color:{DIFF_INK[k]}'>{fdr:.1f}</span>")

    ticker_ids = [int(p) for p in ds.squad["id"]] + [p for p in first["transfers_in"]]
    ticker_ids = sorted(set(ticker_ids), key=lambda p: -xp_near(p))
    ticker_rows = ""
    for p in ticker_ids:
        tag = " <span class='outbadge'>IN</span>" if p not in owned else (
            " <span class='outbadge'>OUT</span>" if p in first["transfers_out"] else "")
        ticker_rows += (f"<tr><td>{name(p)}{tag}<div class='news'>{team(p)} · {ESC(str(m.at[p,'pos']))}</div></td>"
                        + fixture_cells(int(m.at[p, "team"]))
                        + f"<td class='num'>{team_fdr(int(m.at[p, 'team']))}</td></tr>")
    ticker_head = "".join(
        f"<th style='text-align:center'{' class=ab' if g in bafter else ''}>GW{g}</th>"
        for g in gws) + "<th class='num'>FDR</th>"
    ticker_legend = "".join(f"<i style='background:{c}'></i>" for c in DIFF_RAMP)

    # ---- squad vs targets (one table, aligned columns) ----
    from .api import load_snapshot
    elements = {e["id"]: e for e in load_snapshot()["bootstrap"]["elements"]}

    def price_proj(pid: int):
        proj = {x["offset"]: float(x["projected_percent"])
                for x in (elements.get(pid, {}).get("price_change_projections") or [])
                if x.get("projected_percent") is not None}
        return (proj.get(0), proj.get(1)) if proj else None

    def trend_cell(pid: int) -> str:
        pr = price_proj(pid)
        return "—" if pr is None else f"{pr[0]:+.0f} · {pr[1]:+.0f}%"


    def price_arrow(pid: int) -> str:
        pr = price_proj(pid)
        t1 = (pr[1] if pr and pr[1] is not None else 0)
        if abs(t1) < 10:
            return "<span class='news'>—</span>"
        t0 = pr[0] if pr and pr[0] is not None else 0
        arrow, cls = ("▲", "in") if t1 > 0 else ("▼", "out")
        return (f"<span class='{cls}' data-tip='Progress toward a price "
                f"{'rise' if t1 > 0 else 'fall'}: tonight {t0:+.0f}%, "
                f"tomorrow {t1:+.0f}% (100% = change due)'>{arrow} {abs(t1):.0f}%</span>")

    def compare_row(pid: int) -> str:
        pid = int(pid)
        is_owned = pid in owned
        flag = ""
        status = m.at[pid, "status"]
        if isinstance(status, str) and status != "a":
            ch = m.at[pid, "chance_of_playing_next_round"]
            flag = f"<span class='flag'>{ESC(status.upper())}{f' {ch:.0f}%' if pd.notna(ch) else ''}</span> "
        news = m.at[pid, "news"]
        news_s = (f"<div class='news'>{ESC(str(news))}</div>"
                  if is_owned and isinstance(news, str) and news.strip() else "")
        price_m = (sell[pid] if is_owned else m.at[pid, "now_cost"]) / 10
        ob = "<span class='outbadge'>OUT</span> " if pid in set(first["transfers_out"]) else ""
        pr_ = price_proj(pid)
        t1_ = pr_[1] if pr_ and pr_[1] is not None else 0
        row_cls = "own" if is_owned else f"tgt t{target_tier.get(pid, 3)}"
        return (f"<tr class='{row_cls}' data-pos='{ESC(str(m.at[pid, 'pos']))}' "
                f"data-name='{ESC(str(m.at[pid, 'web_name']).lower())}' "
                f"data-team='{team(pid)}' data-p='{price_m:.1f}' "
                f"data-x1='{xp(pid, next_gw):.2f}' data-x5='{xp_near(pid):.2f}' "
                f"data-x8='{float(m.at[pid, 'xp_total']) * ((38 - next_gw + 1) / len(gws)):.0f}' "
                f"data-o='{float(m.at[pid, 'selected_by_percent']):.0f}' "
                f"data-tp='{int(elements.get(pid, {}).get('total_points', 0))}' "
                f"data-t1='{t1_:.0f}'><td>{ob}{flag}{name(pid)}{news_s}</td>"
                f"<td>{ESC(str(m.at[pid, 'pos']))}</td><td>{team(pid)}</td>"
                f"<td class='num'>£{price_m:.1f}</td>"
                f"<td class='num'>{price_arrow(pid)}</td>"
                f"<td class='num'>{float(m.at[pid, 'selected_by_percent']):.0f}%</td>"
                f"<td class='num'>{int(elements.get(pid, {}).get('total_points', 0))}</td>"
                f"<td class='num'>{xpv(pid, xp(pid, next_gw))}</td>"
                f"<td class='num'>{xpv(pid, xp_near(pid))}</td>"
                f"<td class='num'>{xpv(pid, float(m.at[pid, 'xp_total']) * ((38 - next_gw + 1) / len(gws)), '{:.0f}')}</td></tr>")

    mkt = matrix[~matrix["id"].isin(owned)].copy()
    mkt["xp_near_"] = mkt[[f"xp_gw{g}" for g in near]].sum(axis=1)
    watch = mkt.nlargest(30, "xp_near_")
    target_tier = {int(w): (1 if i < 10 else 2 if i < 20 else 3)
                   for i, w in enumerate(watch["id"])}
    all_ids = set(int(i) for i in owned) | {int(w) for w in watch["id"]}
    compare_rows = "".join(compare_row(p) for p in
                           sorted(all_ids, key=lambda p: -xp_near(p)))

    movers = []
    for pid in owned:
        pr = price_proj(pid)
        if pr and (abs(pr[0] or 0) >= 15 or abs(pr[1] or 0) >= 15):
            movers.append((pid, pr))
    movers.sort(key=lambda mv: -max(abs(mv[1][0] or 0), abs(mv[1][1] or 0)))
    season_factor = (38 - next_gw + 1) / len(gws)

    def _t1(pid):
        pr_ = price_proj(int(pid))
        return round(pr_[1]) if pr_ and pr_[1] is not None else 0
    cmp_players = [{"n": str(m.at[int(pid), "web_name"]),
                    "pos": str(m.at[int(pid), "pos"]),
                    "own": int(pid) in owned,
                    "p": round(float((sell[int(pid)] if int(pid) in owned
                                      else m.at[int(pid), "now_cost"])) / 10, 1),
                    "x1": round(xp(int(pid), next_gw), 1),
                    "x5": round(xp_near(int(pid)), 1),
                    "x8": round(float(m.at[int(pid), "xp_total"]) * season_factor),
                    "o": round(float(m.at[int(pid), "selected_by_percent"])),
                    "tp": int(elements.get(int(pid), {}).get("total_points", 0)),
                    "t1": _t1(pid),
                    "tier": 0 if int(pid) in owned else target_tier.get(int(pid), 3)}
                   for pid in sorted(all_ids, key=lambda x: -xp_near(x))]
    cmp_data = json.dumps({"players": cmp_players},
                          separators=(",", ":")).replace("</", "<\\/")
    movers_line = ""
    if movers:
        mrows = ""
        for pid, pr in movers[:6]:
            t1 = pr[1] or 0
            arrow, cls, word = ("▲", "in", "rise") if t1 > 0 else ("▼", "out", "fall")
            when = ("due tomorrow" if abs(t1) >= 100
                    else f"{abs(t1):.0f}% by tomorrow")
            mrows += (f"<tr><td>{name(pid)}</td>"
                      f"<td><span class='{cls}'>{arrow} {word}</span></td>"
                      f"<td class='num'>{when}</td></tr>")
        movers_line = (
            f"<div class='altbox'><div class='althead'>Price watch — your squad</div>"
            f"<table class='alttab'><tr><th></th><th>Move</th>"
            f"<th class='num'>Progress</th></tr>{mrows}</table>"
            f"<p class='news' style='margin:4px 0 0'>A rise banks £0.05 on resale; "
            f"a fall costs the same — timing context only.</p></div>")

    # ---- captaincy / watchlist ----
    cap_rows = ""
    for i, p in enumerate(sorted(first["lineup"], key=lambda p: -xp(p, next_gw))[:3], 1):
        mark = " ← recommended" if p == first["captain"] else ""
        cap_rows += f"<tr><td>{i}</td><td>{name(p)}</td><td>{team(p)}</td><td class='num'>{xp(p, next_gw):.1f}</td><td>{mark}</td></tr>"
    movers = []
    for pid in owned:
        pr = price_proj(pid)
        if pr and (abs(pr[0] or 0) >= 15 or abs(pr[1] or 0) >= 15):
            movers.append((pid, pr))
    movers.sort(key=lambda m: -max(abs(m[1][0] or 0), abs(m[1][1] or 0)))
    movers_line = ""
    if movers:
        mrows = ""
        for pid, pr in movers[:6]:
            t1 = pr[1] or 0
            arrow, cls, word = ("▲", "in", "rise") if t1 > 0 else ("▼", "out", "fall")
            when = ("due tomorrow" if abs(t1) >= 100
                    else f"{abs(t1):.0f}% by tomorrow")
            mrows += (f"<tr><td>{name(pid)}</td>"
                      f"<td><span class='{cls}'>{arrow} {word}</span></td>"
                      f"<td class='num'>{when}</td></tr>")
        movers_line = (
            f"<div class='altbox'><div class='althead'>Price watch — your squad</div>"
            f"<table class='alttab'><tr><th></th><th>Move</th>"
            f"<th class='num'>Progress</th></tr>{mrows}</table>"
            f"<p class='news' style='margin:4px 0 0'>A rise banks £0.05 on resale; "
            f"a fall costs the same — timing context only.</p></div>")

    # ---- robustness bars ----
    scen_rows = _scen_rows(rob.get("top_moves", []))
    rob_chart = (f"<div class='tablewrap'><table class='scen'>"
                 f"<tr><th>In</th><th>Out</th><th class='num'>Share</th><th></th></tr>"
                 f"{scen_rows}</table></div>") if scen_rows else         "<p class='note'>Robustness check not run.</p>"

    # ---- chips: calendar, runway, verdict and the one-chip-per-week plan ----
    chip_labels = {"wildcard": "Wildcard", "freehit": "Free Hit", "bboost": "Bench Boost", "3xc": "Triple Captain"}
    chip_plan = plan.get("chip_plan") or {}
    chip_defs = [("wildcard", "WC", "Wildcard"), ("freehit", "FH", "Free Hit"),
                 ("bboost", "BB", "Bench Boost"), ("3xc", "TC", "Triple Captain")]

    def _chip_condition(c: str, i: dict) -> str:
        """One-line trigger read-out at the chip's assigned week, else its best week in view."""
        trig = i.get("triggers") or {}
        g = i.get("assigned_gw")
        if g is None:
            g = i.get("profile_best_gw")
        t = (trig.get(str(g)) or trig.get(g)) if g is not None else None
        if not t:
            return ""
        ok = lambda flag: "✓" if flag else "✗"
        if c == "bboost":
            txt = (f"{ok(t['fit_ok'])} {t['n_fit']}/{t['n_squad']} fit · "
                   f"{ok(t['bench_ok'])} bench {t['bench_ratio']:.2f}× norm"
                   + (" · ✓ week after wildcard" if t.get("post_wildcard") else ""))
        elif c == "3xc":
            txt = (f"{ok(t['standout'])} {t.get('captain_name', 'captain')} "
                   f"{t['ratio']:.2f}× his norm"
                   + (" · ✓ plays twice" if t.get("captain_doubles") else ""))
        elif c == "freehit":
            txt = (f"{ok(not t['crisis'])} {t['n_fit']} fit · "
                   + (f"✓ {t['n_blank']} starters blank" if t.get("blank") else "✗ no blank"))
        else:
            return ""
        return f"GW{int(g)}: {ESC(txt)}"

    chip_tiles = ""
    for c, abbr, label in chip_defs:
        i = (plan.get("chips") or {}).get(c) or {}
        avail = c in meta["chips_available"]
        why = (info(ESC(i["why"]) + ".") if i.get("why") else "")
        verdict = str(i.get("verdict") or ("hold" if avail else "used"))
        pill_cls = {"play": "good", "expiring": "warn", "consider": "consider"}.get(verdict, "hold")
        verdict_ = f"<span class='pill {pill_cls}'>{ESC(verdict).upper()}</span>"
        if avail:
            g = i.get("assigned_gw")
            if g is not None:
                plan_ = f"GW{int(g)}" + (" — this deadline" if int(g) == ds.next_gw else "")
            elif i.get("dgw_in_runway"):
                plan_ = f"hold for the GW{int(i['dgw_in_runway'][0])} double"
            else:
                plan_ = "hold"
            rw = i.get("runway")
            run_ = (f"{int(rw)} GW{'s' if int(rw) != 1 else ''} to GW{int(i['expiry_gw'])}"
                    if rw is not None else "—")
            bar_ = f"bar {i['bar']:.1f}" if i.get("bar") is not None else ""
            add_ = (f"+{i['gain']:.1f}{' (approx.)' if i.get('approx') else ''}"
                    if i.get("gain") is not None else "—")
            cond_ = _chip_condition(c, i)
        else:
            used_gw = i.get("used_gw")
            plan_ = f"played GW{int(used_gw)}" if used_gw else "used"
            nw = i.get("next_window")
            run_ = f"next unlocks GW{int(nw[0])}" if nw else "—"
            bar_, add_, cond_ = "", "—", ""
        chip_tiles += (
            f"<div class='chiptile{'' if avail else ' off'}'>"
            f"<span class='cb'>{abbr}</span>"
            f"<div class='cname'>{label} {why}</div>"
            f"<div class='crow'><span>Verdict</span><span>{verdict_}</span></div>"
            f"<div class='crow'><span>Plan</span><span class='cv'>{ESC(plan_)}</span></div>"
            f"<div class='crow'><span>Runway</span><span class='cv'>{ESC(run_)}</span></div>"
            f"<div class='crow'><span>Would add</span><span class='cv'>{ESC(add_)}"
            f"{(' · ' + ESC(bar_)) if bar_ else ''}</span></div>"
            + (f"<div class='crow'><span>Conditions</span><span>{cond_}</span></div>" if cond_ else "")
            + "</div>")
    chip_strip = ""
    if chip_plan.get("order"):
        seq = " · ".join(f"GW{int(r['gw'])} {chip_labels.get(r['chip'], r['chip'])}"
                         for r in chip_plan["order"])
        lost = chip_plan.get("unassigned") or []
        head = "Endgame — one chip per week" if chip_plan.get("endgame") else "Chip plan"
        chip_strip = (
            f"<div class='chipplan{' endgame' if chip_plan.get('endgame') else ''}'>"
            f"<b>{head}:</b> {ESC(seq)}"
            + (f" · <span style='color:var(--bad)'>would lapse: "
               f"{ESC(', '.join(chip_labels.get(x, x) for x in lost))}</span>" if lost else "")
            + f"<div class='note'>{ESC(chip_plan.get('note', ''))}</div></div>")
    elif chip_plan.get("note"):
        chip_strip = f"<div class='chipplan'><div class='note'>{ESC(chip_plan['note'])}</div></div>"

    # ---- banners ----
    banners = ""
    live = ds.events[ds.events["is_current"] & ~ds.events["finished"]]
    if len(live):
        banners += (f"<div class='banner'>⚠ GW{int(live['id'].iloc[0])} was still in progress when data was "
                    f"fetched — stats are partial. The next scheduled run after the final whistle has the final word.</div>")
    changes = sched.get("changes_since_last_pull") or []
    unsched = sched.get("unscheduled") or []
    if changes or unsched:
        bits = []
        if changes:
            bits.append("<b>Schedule changed since last pull:</b> " + "; ".join(ESC(c) for c in changes))
        if unsched:
            bits.append("<b>Awaiting rescheduling (blank until assigned):</b> " + ", ".join(ESC(u) for u in unsched))
        banners += "<div class='banner'>📅 " + "<br>".join(bits) + "</div>"

    midweek = " <span class='badge'>midweek</span>" if sched.get("midweek") else ""
    shape = []
    if sched.get("doubles"):
        shape.append("double for " + ", ".join(sched["doubles"]))
    if sched.get("blanks"):
        shape.append("blank for " + ", ".join(sched["blanks"]))
    header_sub = (f"Deadline <b>{ESC(sched.get('deadline_uk', ''))}</b> ({countdown} away){midweek}"
                  + (" · " + "; ".join(shape) if shape else "")
                  + f" · updated {fmt_uk(now)}")
    rerun = "; ".join(sched.get("rerun", []))
    chips_avail = ", ".join(chip_labels.get(c, c) for c in meta["chips_available"])

    plan_far_html = ""
    if plan_rows_far:
        plan_far_html = (
            f"<details class='runs'><summary class='note'>Beyond GW{plans[2]['gw']} "
            f"— directional only, re-optimised every run</summary>"
            f"<div class='tablewrap'><table>"
            f"<tr><th>GW</th><th>In</th><th>Out</th><th>Hit</th><th class='num'>FT</th>"
            f"<th>Captain</th><th class='num'>Bank</th><th>Schedule</th></tr>"
            f"{plan_rows_far}</table></div></details>")

    band = ("high" if (survival or 0) >= .6 else
            "medium" if (survival or 0) >= .3 else "low")
    band_msg = {
        "low": "several moves are effectively tied, so let team news break the tie before acting",
        "medium": "a real favourite, but check the final pre-deadline run before acting",
        "high": "solid whatever the pressers bring",
    }[band]
    themes = rob.get("themes") or {}
    theme_items = []
    for pid_, rate in (themes.get("out") or [])[:3]:
        if rate >= 0.25:
            theme_items.append((int(pid_), rate, "out"))
    for pid_, rate in (themes.get("in") or [])[:3]:
        if rate >= 0.25:
            theme_items.append((int(pid_), rate, "in"))
    theme_items.sort(key=lambda t: -t[1])
    themes_html = ""
    if theme_items:
        cards = "".join(
            f"<div class='theme'><div class='thead'><b>{name(pid_)}</b>"
            f"<span class='tdir {d}'>{'IN' if d == 'in' else 'OUT'}</span></div>"
            f"<div class='tmeter {d}'><div style='width:{r*100:.0f}%'></div></div>"
            f"<div class='tpct'>{r:.0%} of scenarios</div></div>"
            for pid_, r, d in theme_items)
        themes_html = ("<div class='note' style='margin-top:8px'>Robust themes — "
                       "the stable parts of the call:</div>"
                       f"<div class='themegrid'>{cards}</div>")
    if done_tx:
        # Transfers are in: the verdict header already says "no further moves"
        # with the confidence chip, and the decision-time shake field lives in
        # the Made-this-week fold — a standalone module would just repeat both.
        solid_html = ""
    else:
        solid_html = (
            f"<div class='timing'><b>How solid?</b> "
            f"<span class='pill {surv_class}'>{band.upper()} CONFIDENCE</span> "
            + info(f"The solver re-ran {rob.get('runs', 0)} times with every forecast "
                   f"nudged by realistic errors. This exact call wins {surv_text} of "
                   f"those scenarios — {band_msg}. The themes show which parts of the "
                   f"call stay stable across scenarios; the table lists the most "
                   f"common first moves.")
            + f"{themes_html}{rob_chart}</div>")

    if wk_moves:
        wk_block = f"""<div class="controls">
    <label for="wkrange">Look ahead</label>
    <input type="range" id="wkrange" min="1" max="{len(gws)}" value="{min(5, len(gws))}" aria-label="Gameweeks ahead">
    <span class="hrz" id="wklabel"></span>
    <select id="wksel" class="mvsel" aria-label="Comparison">{wk_options}</select>
    <span class="hrz" id="wkdelta"></span>
    {info("xP = expected points: the average score across thousands of imagined "
          "replays of the gameweek, built from likely minutes, goal and assist "
          "rates, clean-sheet odds and fixture difficulty. A 5.0 doesn't promise "
          "5 points — hauls and blanks average out to 5. 'net' is the extra xP "
          "the swap buys over the chosen horizon; the dropdown lists the "
          "recommended move first, then the shake-test runners-up.")}</div>
  <div class="chart" id="wkchart"></div>"""
    else:
        wk_block = ("<p class='note'>n/a — no move candidates this week. "
                    "The General tab charts any player over the horizon.</p>")

    body = f"""
{banners}
<div class="kpis">
  <div class="kpi">GW{next_gw} xP <b>{gw_xp:.1f}</b></div>
  <div class="kpi">Season points <b>{ds.total_points}</b></div>
  <div class="kpi">Bank <b>£{meta['bank']:.1f}</b></div>
  <div class="kpi">Free transfers <b>{meta['free_transfers_assumed']}</b></div>
</div>
<div class="card"><h2>GW{next_gw} line-up {info(
    "Each card: expected points this gameweek, then price and expected points per £1m — value for "
    "money. C = captain (his score counts double), V = vice, who steps in if the captain doesn't play. "
    "Orange = evidence adjustment (club move, new arrival, return from injury, injury-hit last season). Purple = squad competition adjustment (covering for an absent teammate, or squeezed by a returner). Applies to xP and expected minutes — hover for details.")}</h2>
  <div class="pitch">{pitch}<div class="bench">{bench}</div></div></div>
<div class="card"><h2>Chips {info(
    "Two of every chip, one usable in each half of the season; an unused first-half chip is lost at GW19. "
    "Runway = weeks left before it lapses. 'Would add' is the expected points a chip adds if used inside this "
    "8-gameweek window; the bar it must clear slides down to zero as its runway closes, counting the other "
    "chips that still need a week of their own (one chip per gameweek). A double gameweek anywhere in the "
    "half is held for. PLAY = this deadline; CONSIDER = a later week in view; EXPIRING = its slot in the "
    "endgame plan; the tooltip on each chip gives the reasoning.")}</h2>
  {chip_strip}<div class="chipgrid">{chip_tiles}</div></div>
<div class="card"><h2>This week's call</h2>{headline}
  <div class="timing">{wk_block}</div>
  {timing_html}
  {movers_line}
  {solid_html}
  <p class="note">Next run: {ESC(rerun)}.</p></div>
<div class="card"><h2>Captaincy</h2><div class="tablewrap"><table>
  <tr><th>#</th><th>Player</th><th>Team</th><th class='num'>GW{next_gw} xP</th><th></th></tr>{cap_rows}</table></div></div>
<div class="card"><h2>The plan, GW by GW</h2><div class="tablewrap"><table>
  <tr><th>GW</th><th>In</th><th>Out</th><th>Hit</th><th class='num'>FT</th><th>Captain</th><th class='num'>Bank</th><th>Schedule</th></tr>
  {plan_rows}</table></div>
  {plan_far_html}
</div>
<div class="card"><h2>Your squad vs top targets</h2>
  <div class="controls">
    <span class="seg" id="cposseg"><button class="on" data-pos="ALL">All</button><button data-pos="GKP">GKP</button>
      <button data-pos="DEF">DEF</button><button data-pos="MID">MID</button><button data-pos="FWD">FWD</button></span>
    <span class="seg" id="cview"><button class="on" data-v="table">Table</button><button data-v="graph">Graph</button></span>
    <select id="cmetric" class="mvsel" style="display:none" aria-label="Graph metric">
      <option value="x5" selected>xP next {len(near)}</option>
      <option value="x1">xP GW{next_gw}</option>
      <option value="x8">Season xP</option>
      <option value="p">Price £m</option>
      <option value="o">Owned %</option>
      <option value="tp">Total points</option>
      <option value="t1">Price trend (tmrw %)</option>
    </select>
    <span class="legend" id="cbands">
    <label class="bandck"><input type="checkbox" checked data-band="own"><i style="background:var(--series-in)"></i>Yours</label>
    <label class="bandck"><input type="checkbox" checked data-band="t1"><i style="background:#e90052"></i>Top 1–10</label>
    <label class="bandck"><input type="checkbox" checked data-band="t2"><i style="background:rgba(233,0,82,.55)"></i>11–20</label>
    <label class="bandck"><input type="checkbox" checked data-band="t3"><i style="background:rgba(233,0,82,.28)"></i>21–30</label></span>
  </div>
  <div class="tablewrap" id="ctable"><table>
  <tr><th class="csort" data-k="name">Player</th><th class="csort" data-k="pos">Pos</th>
  <th class="csort" data-k="team">Team</th><th class='num csort' data-k="p">£</th>
  <th class='num csort' data-k="t1">Price move</th>
  <th class='num csort' data-k="o">Owned by</th>
  <th class='num csort' data-k="tp">Total</th>
  <th class='num csort' data-k="x1">GW{next_gw} xP</th>
  <th class='num csort on' data-k="x5">Next {len(near)}</th>
  <th class='num csort' data-k="x8" data-tip="Model xP over the next {len(gws)} gameweeks, extrapolated across the rest of the season (fixture-neutral beyond the horizon)">Season xP</th></tr>
  {compare_rows}</table></div>
  <div class="chart" id="cgraph" style="display:none"></div></div>
<div class="card"><h2>Fixture ticker — your squad</h2><div class="tablewrap"><table class="ticker">
  <tr><th>Player</th>{ticker_head}</tr>{ticker_rows}</table></div>
  <div class="tl">easier {ticker_legend} harder &nbsp;·&nbsp; <span style='color:var(--warn)'>│</span> = international break
  &nbsp;·&nbsp; FDR = average opponent strength over the horizon</div></div>
<script>window.WK = {wk_data}; window.WKCMP = {cmp_data};</script>
<script>{WEEKLY_JS}</script>
"""
    SITE.mkdir(exist_ok=True)
    team_name = ds.entry_name
    index = page(f"{team_name} — GW{next_gw} plan", "plan", header_sub,
                 next_gw, body, team=team_name)
    (SITE / "index.html").write_text(index)
    (SITE / "general.html").write_text(page(
        f"{team_name} — the market", "general", header_sub, next_gw,
        _general_body(matrix, ds, gws, owned, fx_by_team_gw, short, q),
        team=team_name))
    (SITE / "history.html").write_text(
        page(f"{team_name} — track record", "history", header_sub, next_gw,
             _history_body(ds, ((plan.get("completed") or {}).get("matched")
                                or {}).get("run_at"))))
    shutil.copy(SITE / "index.html", ROOT / "report.html")
    arch = ROOT / "docs" / "architecture.html"
    if arch.exists():  # the IA reference rides along at /architecture
        shutil.copy(arch, SITE / "architecture.html")


def _history_body(ds, acted_run_at: str | None = None) -> str:
    runs = load_history()
    outcomes = load_outcomes()
    settled = sorted(outcomes.values(), key=lambda o: o["gw"])

    if settled:
        labels = [f"GW{o['gw']}" for o in settled]
        chart = line_chart([
            {"name": "Model predicted", "color": "series-alt2", "values": [o["pred_points"] for o in settled]},
            {"name": "Model actual", "color": "series-in", "values": [o["model_points"] for o in settled]},
            {"name": "Your actual", "color": "series-alt1", "values": [o["tim_points"] for o in settled]},
        ], labels, "Points per gameweek")
        rows = ""
        for o in settled:
            ver_ = o.get("model")
            rows += (f"<tr><td>GW{o['gw']}"
                     + (f" <span class='news'>v{ESC(ver_)}</span>" if ver_ else "")
                     + f"</td><td class='in'>{ESC(', '.join(o['model_call_in']) or '—')}</td>"
                     f"<td>{ESC(', '.join(o['tim_in']) or '—')}</td>"
                     f"<td>{'in line' if o['followed'] else 'your own call'}</td>"
                     f"<td class='num'>{o['pred_points']:.1f}</td><td class='num'>{o['model_points']}</td>"
                     f"<td class='num'>{o['tim_points']}</td></tr>")
        avg_pts = sum(o["tim_points"] for o in settled) / len(settled)
        bias = sum(o["model_points"] - o["pred_points"] for o in settled) / len(settled)
        record = f"""
<div class="kpis">
  <div class="kpi">Gameweeks settled <b>{len(settled)}</b></div>
  <div class="kpi">Your points, avg <b>{avg_pts:.0f}</b></div>
  <div class="kpi">Prediction error, avg {info("Actual points of the model's "
      "final line-up minus its prediction, averaged over settled gameweeks. "
      "Positive means reality beat the forecast.")}<b>{bias:+.1f}</b></div>
</div>
<div class="card"><h2>Points per gameweek {info('"Model actual" is what the '
    "model's last pre-deadline line-up and captain would have scored (no "
    'auto-subs, hits deducted). "Model predicted" is its forecast for that '
    "line-up.")}</h2>{chart}</div>
<div class="card"><h2>Decision record {info("Each settled gameweek: the "
    "model's final pre-deadline call, the transfers actually made, and how "
    "each line-up scored. The v-tag is the model version that made the "
    "call.")}</h2><div class="tablewrap"><table>
  <tr><th>GW</th><th>Model's call</th><th>Decision</th><th></th><th class='num'>Predicted</th><th class='num'>Model line-up scored</th><th class='num'>Your team scored</th></tr>
  {rows}</table></div></div>"""
    else:
        record = ("<div class='card'><h2>Track record</h2><p>Starts once the first gameweek with a recorded "
                  "call is complete — outcomes settle automatically on the next data update after the GW is "
                  "finalised.</p></div>")

    now_iso = datetime.now(timezone.utc).isoformat()
    by_gw: dict[int, list[dict]] = {}
    for r in runs:
        by_gw.setdefault(r["gw"], []).append(r)

    blocks = ""
    for gw in sorted(by_gw, reverse=True):
        rs = sorted(by_gw[gw], key=lambda r: r["run_at"], reverse=True)
        deadline = rs[0]["deadline"]
        closed = now_iso > deadline
        # The run that counts: last before the deadline once it has passed,
        # otherwise the latest so far (still updating).
        final = next((r for r in rs if r["run_at"] <= deadline), rs[-1])             if closed else rs[0]
        label = "final call" if closed else "latest call"
        _noop = lambda r_: "stick" if r_.get("fts") == 0 else "hold"
        f_ins = ", ".join(t["name"] for t in final["transfers_in"]) or _noop(final)
        f_outs = ", ".join(t["name"] for t in final["transfers_out"])
        fc = final.get("conviction", final.get("survival"))
        f_surv = f"{fc:.0%}" if fc is not None else "—"
        rows = ""
        for r in rs:
            when = fmt_uk(datetime.fromisoformat(r["run_at"]))
            ins_ = ", ".join(t["name"] for t in r["transfers_in"])
            ins_cell = (f"<td class='in'>{ESC(ins_)}</td>" if ins_
                        else f"<td class='noopw'>{_noop(r)}</td>")
            outs = ", ".join(t["name"] for t in r["transfers_out"])
            conv = r.get("conviction", r.get("survival"))
            exact = r.get("survival")
            tip = (f" data-tip='Share of shaken scenarios buying the strongest "
                   f"recommended player. Exact full-set match: "
                   f"{exact:.0%} — near zero is normal for multi-transfer moves.'"
                   if exact is not None and r.get("conviction") is not None else "")
            surv = (f"<span{tip}>{conv:.0%}</span>" if conv is not None else "—")
            mark = " class='frun'" if r is final else ""
            tag = (" <span class='pill consider'>counts</span>"
                   if r is final and closed else "")
            if r.get("model"):
                vt = VERSION_TITLES.get(r["model"], "")
                ver_cell = (f"<td class='news'><span data-tip='"
                            f"{ESC(vt) if vt else 'Model version'}'>"
                            f"v{ESC(r['model'])}</span></td>")
            else:
                ver_cell = "<td class='news'>—</td>"
            if r["run_at"] == acted_run_at:
                tag += (" <span class='pill good' data-tip='The run whose "
                        "recommendation these transfers followed — its numbers "
                        "are the decision-time record.'>acted on</span>")
            rows += (f"<tr{mark}><td class='fix'>{when}{tag}</td>" + ver_cell
                     + ins_cell + f"<td class='out'>{ESC(outs)}</td>"
                     f"<td>{('−' + str(4*r['hits'])) if r['hits'] else ''}</td>"
                     f"<td class='num'>{r['pred_points']:.1f}</td>"
                     f"<td class='num'>{surv}</td></tr>")
        blocks += f"""<details class="runs"{'' if closed else ' open'}>
<summary><b>GW{gw}</b> <span class="note">{label}:</span> <span class="{'in' if final['transfers_in'] else 'noopw'}">{ESC(f_ins)}</span>
{f"<span class='out'>← {ESC(f_outs)}</span>" if f_outs else ""}
<span class="note">· {final['pred_points']:.1f} pred · conviction {f_surv} · {len(rs)} run{'s' if len(rs) > 1 else ''}</span></summary>
<div class="tablewrap"><table>
<tr><th>Run</th><th>Model</th><th>In</th><th>Out</th><th>Hit</th><th class='num'>Pred. pts</th><th class='num'>Conviction</th></tr>
{rows}</table></div></details>"""

    log = f"""<div class="card"><h2>Run log {info("The run that counts is the "
    "last one before each deadline; until then the latest run leads and keeps "
    "updating. The v-tag is the model version that produced the run — a "
    "changed recommendation between runs can be a model change, not just new "
    "data; the Model changes card below has the history.")}</h2>
{blocks or "<p class='note'>No runs recorded yet.</p>"}</div>"""

    ch_rows = ""
    for c in CHANGELOG:
        os_ = [o for o in settled if o.get("model") == c["version"]]
        measured = (f"{len(os_)} GW settled · prediction error "
                    f"{sum(o['model_points'] - o['pred_points'] for o in os_) / len(os_):+.1f} avg"
                    if os_ else "no settled gameweeks yet")
        ch_rows += (f"<tr><td class='fix'><b>v{ESC(c['version'])}</b>"
                    f"<div class='news'>{ESC(c['date'])}</div></td>"
                    f"<td><b>{ESC(c['title'])}</b>"
                    f"<div class='news'>{ESC(c['detail'])}</div></td>"
                    f"<td>{ESC(c['impact'])}</td>"
                    f"<td>{ESC(measured)}</td></tr>")
    changes = f"""<div class="card"><h2>Model changes {info("Every change to "
    "how the model scores or recommends, newest first. 'Measured' fills in as "
    "gameweeks settle under each version — over a season this shows whether "
    "each tweak actually helped.")}</h2>
<div class="tablewrap"><table>
<tr><th>Version</th><th>Change</th><th>Expected impact</th><th>Measured</th></tr>
{ch_rows}</table></div></div>"""
    return record + log + changes
