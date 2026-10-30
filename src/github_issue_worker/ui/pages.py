"""The UI's pages as server-rendered HTML: no build step, no external assets, works offline."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html import escape
from typing import Any

from pydantic import BaseModel

from github_issue_worker.adapters.transcript import Entry, transcript
from github_issue_worker.application.history import (
    RunRecord,
    SplitBy,
    activity,
    cumulative_merged,
    daily_merged,
    kpis,
    model_ledger,
    resolve_times,
    weekly_mix,
)
from github_issue_worker.configuration import (
    AccountSettings,
    Configuration,
    DefaultsSettings,
    RepositorySettings,
    WorkerSettings,
)
from github_issue_worker.ui.charts import (
    Span,
    area_line,
    color_slots,
    donut,
    heatmap,
    horizontal_bars,
    ring_gauge,
    sparkline,
    stacked_columns,
    timeline,
)
from github_issue_worker.ui.forms import HELP, FormField, display, fields_of, is_set, value_at
from github_issue_worker.ui.model import STEPS, ClaimView, IssueView, Snapshot, WorkerView

STYLE = """
:root {
  --paper: #edf0f3; --sheet: #f7f9fb; --ink: #1b2430; --muted: #5a6775; --rule: #c8d0d9;
  --steel: #2e5c8a; --steel-soft: #d6e2ee; --amber: #b7791f; --amber-soft: #f3e6cf;
  --brick: #a63d2f; --brick-soft: #f2dcd8; --done: #4d6b52;
  --changed: #fbf0bd; --changed-rule: #a8862a;
  color-scheme: light;
}
@media (prefers-color-scheme: dark) {
  :root {
    --paper: #141a21; --sheet: #1b232c; --ink: #e3e8ee; --muted: #9aa6b3; --rule: #33404d;
    --steel: #7fa8d1; --steel-soft: #23384d; --amber: #e0a44a; --amber-soft: #3a2f1c;
    --brick: #e07a6b; --brick-soft: #3b2420; --done: #8fb596;
    --changed: #3d3714; --changed-rule: #c9a83f; color-scheme: dark;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--paper); color: var(--ink);
  font: 15px/1.5 system-ui, "Segoe UI", "Noto Sans", "Helvetica Neue", sans-serif; }
a { color: var(--steel); }
a:focus-visible, button:focus-visible, input:focus-visible, select:focus-visible,
textarea:focus-visible, summary:focus-visible { outline: 2px solid var(--steel); outline-offset: 2px; }
code, .mono { font-family: ui-monospace, "Cascadia Code", "Source Code Pro", monospace; font-size: 13px; }
.frame { display: grid; grid-template-columns: 13rem 1fr; min-height: 100vh; }
nav { border-right: 1px solid var(--rule); padding: 1.5rem 1rem; }
nav .who { font-weight: 650; margin-bottom: .25rem; }
nav .id { color: var(--muted); font-size: 13px; margin-bottom: 1.75rem; overflow-wrap: anywhere; }
nav a { display: block; padding: .4rem .6rem; border-radius: 4px; color: var(--ink);
  text-decoration: none; }
nav a[aria-current="page"] { background: var(--steel-soft); color: var(--steel); font-weight: 600; }
main { padding: 1.75rem 2.25rem 3rem; max-width: 70rem; }
h1 { font-size: 1.6rem; font-weight: 650; margin: 0 0 .25rem; letter-spacing: -.01em; }
.lede { color: var(--muted); margin: 0 0 1.75rem; max-width: 42rem; }
h2 { font-size: 1.1rem; margin: 2rem 0 .5rem; }
.lane { display: grid; grid-template-columns: 14rem 1fr auto; gap: 1.25rem; align-items: start;
  padding: 1.1rem 0; border-top: 1px solid var(--rule); }
.lane:last-child { border-bottom: 1px solid var(--rule); }
.lane .name { font-weight: 650; font-size: 1.05rem; }
.lane .runs { color: var(--muted); font-size: 13.5px; }
.lane .model { display: inline-block; margin-top: .3rem; font-weight: 600; }
.issue { font-weight: 600; }
.issue .repo { color: var(--muted); font-weight: 400; }
.track { display: grid; grid-template-columns: repeat(6, 1fr); gap: 3px; margin: .65rem 0 .35rem;
  max-width: 36rem; }
.track span { font-size: 12px; color: var(--muted); padding-top: .4rem;
  border-top: 5px solid var(--rule); }
.track span.past { border-top-color: var(--steel); color: var(--ink); }
.track span.now { border-top-color: var(--steel); color: var(--steel); font-weight: 650; }
.facts { color: var(--muted); font-size: 13.5px; }
.facts .mono { color: var(--ink); }
.quiet { color: var(--muted); }
.flag { display: inline-block; padding: .1rem .5rem; border-radius: 3px; font-size: 13px;
  font-weight: 600; }
.flag.limited, .flag.paused { background: var(--amber-soft); color: var(--amber); }
.flag.unavailable, .flag.failed { background: var(--brick-soft); color: var(--brick); }
.flag.working { background: var(--steel-soft); color: var(--steel); }
.actions { display: flex; gap: .4rem; flex-wrap: wrap; justify-content: flex-end; }
.nowrap { white-space: nowrap; }
.where { display: block; font-size: 13px; color: var(--muted); }
.where.local { color: var(--steel); }
td .actions { justify-content: flex-start; }
.log { list-style: none; margin: 0; padding: 0; max-width: 56rem; }
.log li { padding: .55rem 0; border-top: 1px solid var(--rule); }
.log .tool { font-family: ui-monospace, "Cascadia Code", "Source Code Pro", monospace; font-size: 13px; }
.log .tool strong { font-family: system-ui, sans-serif; font-size: 13px; color: var(--steel); margin-right: .5rem; }
.log pre { margin: .25rem 0 0; padding: .5rem .75rem; background: var(--sheet); border-radius: 4px;
  white-space: pre-wrap; overflow-wrap: anywhere; font-size: 12.5px; max-height: 18rem; overflow: auto; }
.log .thinking { color: var(--muted); }
.log .error { color: var(--brick); }
.log .done { font-weight: 600; }
.holder { white-space: nowrap; }
.holder .quiet { display: block; font-size: 13px; }
a.button { display: inline-block; text-decoration: none; }
button, .button { font: inherit; font-size: 13.5px; padding: .35rem .8rem; border-radius: 4px;
  border: 1px solid var(--rule); background: var(--sheet); color: var(--ink); cursor: pointer; }
button.primary { background: var(--steel); border-color: var(--steel); color: #fff; }
button.danger { color: var(--brick); }
table { border-collapse: collapse; width: 100%; font-size: 14px; }
th { text-align: left; font-weight: 600; color: var(--muted); padding: .45rem .75rem .45rem 0;
  border-bottom: 1px solid var(--rule); }
td { padding: .55rem .75rem .55rem 0; border-bottom: 1px solid var(--rule); vertical-align: top; }
form.inline { display: flex; gap: .5rem; align-items: center; flex-wrap: wrap; margin: 1rem 0; }
input, select, textarea { font: inherit; font-size: 14px; padding: .35rem .5rem; color: var(--ink);
  background: var(--sheet); border: 1px solid var(--rule); border-radius: 4px; }
fieldset { border: 0; border-top: 1px solid var(--rule); margin: 1.5rem 0 0; padding: 1rem 0 0; }
legend { font-weight: 650; font-size: 1.05rem; padding: 0 .5rem 0 0; }
.field { display: grid; grid-template-columns: 15rem minmax(0, 24rem) 1fr; gap: .25rem 1rem;
  align-items: start; padding: .45rem 0; }
.field label { font-weight: 550; }
.field .help { color: var(--muted); font-size: 13px; }
.field .source { color: var(--muted); font-size: 12px; }
.field.error input, .field.error select, .field.error textarea { border-color: var(--brick); }
input.changed, select.changed, textarea.changed { background: var(--changed);
  border-color: var(--changed-rule); }
input[type=checkbox].changed { outline: 2px solid var(--changed-rule); outline-offset: 1px; }
.problems { background: var(--brick-soft); color: var(--brick); padding: .75rem 1rem;
  border-radius: 4px; margin: 0 0 1rem; }
.notice { background: var(--steel-soft); color: var(--steel); padding: .6rem 1rem;
  border-radius: 4px; margin: 0 0 1rem; }
details { margin-top: .5rem; }
summary { cursor: pointer; color: var(--steel); white-space: nowrap; }
.empty { padding: 2rem 0; color: var(--muted); }
@media (max-width: 760px) {
  .frame { grid-template-columns: 1fr; }
  nav { border-right: 0; border-bottom: 1px solid var(--rule); display: flex; gap: .5rem;
    flex-wrap: wrap; align-items: center; padding: .75rem 1rem; }
  nav .who, nav .id { margin: 0 1rem 0 0; }
  main { padding: 1.25rem 1rem 2rem; }
  .lane { grid-template-columns: 1fr; }
  .actions { justify-content: flex-start; }
  .field { grid-template-columns: 1fr; }
}
body.deck {
  --paper: #060b14; --sheet: #101a2a; --ink: #e9f0f8; --muted: #8c9cb2; --rule: #20304a;
  --steel: #6fb3e8; --steel-soft: #16304d; --amber: #e0a44a; --amber-soft: #3a2f1c;
  --brick: #e07a6b; --brick-soft: #3b2420; --done: #8fb596; --glow: #4aa3ff;
  --changed: #3d3714; --changed-rule: #c9a83f;
  color-scheme: dark; background: var(--paper); overflow-x: hidden;
}
body.deck::before { content: ""; position: fixed; inset: -20%; z-index: -2; pointer-events: none;
  background:
    radial-gradient(40% 35% at 25% 20%, #1d4f8a55, transparent 70%),
    radial-gradient(35% 30% at 80% 10%, #6a3fa344, transparent 70%),
    radial-gradient(45% 40% at 60% 90%, #0f6b5a33, transparent 70%);
  filter: blur(30px); }
body.deck::after { content: ""; position: fixed; inset: 0; z-index: -1; pointer-events: none;
  background-image: linear-gradient(#ffffff07 1px, transparent 1px),
    linear-gradient(90deg, #ffffff07 1px, transparent 1px);
  background-size: 48px 48px;
  mask-image: radial-gradient(ellipse 90% 70% at 50% 0%, #000 30%, transparent 85%); }
body.deck main { max-width: 118rem; }
body.deck nav { background: #050a12d0; backdrop-filter: blur(12px); border-right-color: #1a2840; }
body.deck nav a[aria-current="page"] { background: linear-gradient(90deg, #1d4f8a, #16304d00);
  color: #fff; box-shadow: inset 2px 0 0 var(--glow); }
body.deck h1 { font-size: clamp(1.8rem, 2.6vw, 2.6rem); font-weight: 250; letter-spacing: .02em;
  background: linear-gradient(90deg, #ffffff, #8cc6ff 60%, #b39bff); -webkit-background-clip: text;
  background-clip: text; color: transparent; }
.readouts { display: grid; gap: .9rem;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 12rem), 1fr)); }
.readout, .panel { position: relative; min-width: 0; border-radius: 14px;
  background: linear-gradient(180deg, #13203599, #0c152499); backdrop-filter: blur(10px);
  border: 1px solid var(--rule);
  box-shadow: inset 0 1px 0 #ffffff0d, 0 24px 60px -30px #000c; }
.readout::before, .panel::before { content: ""; position: absolute; top: -1px; left: -1px;
  width: 18px; height: 18px; border-top: 2px solid var(--glow); border-left: 2px solid var(--glow);
  border-top-left-radius: 14px; opacity: .8; pointer-events: none; }
.readout::after, .panel::after { content: ""; position: absolute; bottom: -1px; right: -1px;
  width: 18px; height: 18px; border-bottom: 2px solid #b39bff; border-right: 2px solid #b39bff;
  border-bottom-right-radius: 14px; opacity: .6; pointer-events: none; }
.readout { padding: 1rem 1.2rem 1.1rem; overflow: hidden; }
.readout span { display: block; color: var(--muted); font-size: 13px; }
.readout b { display: block; font-weight: 200; font-size: clamp(2.2rem, 3.4vw, 3.3rem);
  line-height: 1.1; letter-spacing: -.03em; font-variant-numeric: tabular-nums;
  background: linear-gradient(180deg, #ffffff, #9fd0ff); -webkit-background-clip: text;
  background-clip: text; color: transparent; filter: drop-shadow(0 0 18px #4aa3ff66); }
.readout small { color: var(--muted); font-size: 12.5px; }
.readout .spark { display: block; width: 100%; max-width: 13rem; height: 2.1rem; margin-top: .2rem; }
.spark path { fill: none; stroke: var(--glow); stroke-width: 2; stroke-linejoin: round;
  filter: drop-shadow(0 0 4px #4aa3ff); }
.gauged { position: relative; display: flex; align-items: center; gap: .8rem; }
.gauge { width: 4.6rem; height: 4.6rem; flex: none; }
.gauge-track { fill: none; stroke: #1c2a40; stroke-width: 9; }
.gauge-fill { fill: none; stroke: var(--glow); stroke-width: 9;
  stroke-linecap: round; filter: drop-shadow(0 0 6px #4aa3ffaa); }
.deck-grid { display: grid; gap: .9rem; margin-top: .9rem;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 30rem), 1fr)); }
.panel { padding: 1.05rem 1.3rem 1.15rem; }
.panel.wide { grid-column: 1 / -1; }
.panel header { display: flex; flex-wrap: wrap; gap: .5rem 1rem; align-items: center;
  justify-content: space-between; }
.panel h2 { margin: 0; font-size: 1.05rem; font-weight: 550; letter-spacing: .01em;
  display: flex; align-items: center; gap: .55rem; }
.panel .sub { color: var(--muted); font-size: 13px; margin: .15rem 0 .7rem; }
fieldset.panel { margin: 0; padding: 1.05rem 1.3rem 1.15rem; min-inline-size: 0; }
fieldset.panel > legend { float: left; width: 100%; padding: 0; margin: 0 0 .5rem; font-size: 1.05rem;
  font-weight: 550; letter-spacing: .01em; display: flex; align-items: center; gap: .55rem; }
fieldset.panel > legend + * { clear: both; }
.panel h2::before, fieldset.panel > legend::before { content: ""; width: .5rem; height: .5rem;
  border-radius: 50%; flex: none; background: var(--glow); box-shadow: 0 0 10px var(--glow); }
.panel header form { margin: 0; }
.panel form.inline { margin: .2rem 0 0; }
.panel .empty { padding: .5rem 0; }
form.deck-grid > p { margin: 0; }
body.deck button, body.deck .button { background: #0c1626; border-color: #253956;
  transition: border-color .2s, box-shadow .2s; }
body.deck button:hover, body.deck .button:hover { border-color: var(--steel);
  box-shadow: 0 0 12px #4aa3ff33; }
body.deck button.primary { background: linear-gradient(180deg, #2a64a8, #1d4f8a);
  border-color: #3a78c0; color: #fff; box-shadow: 0 0 16px #4aa3ff44; }
body.deck button.danger { border-color: #4a2a2a; }
body.deck button.link { background: none; border: 0; box-shadow: none; }
input, select, textarea { max-width: 100%; }
.panel h3 { font-size: 13px; font-weight: 600; color: var(--muted); margin: 1rem 0 0; }
.panel .lane:last-child { border-bottom: 0; }
.panel .lane { border-top-color: #1a2840; }
.stack { display: grid; gap: .6rem; }
.run { display: grid; grid-template-columns: 1.1rem 1fr; gap: .7rem; align-items: start; }
.square { display: block; width: 1.1rem; height: 1.1rem; border-radius: 4px; margin-top: .2rem;
  box-shadow: 0 0 14px currentColor; color: transparent; }
.square.free { border: 1.5px dashed #2a3c58; box-shadow: none; }
.square.s1 { color: #3987e599; } .square.s2 { color: #d9592699; } .square.s3 { color: #199e7099; }
.square.s4 { color: #c9850099; } .square.s5 { color: #d5518199; } .square.s6 { color: #00830099; }
.square.s7 { color: #9085e999; } .square.s8 { color: #e6676799; }
form.open { display: inline; }
button.link { background: none; border: 0; padding: 0; color: var(--steel); cursor: pointer;
  font-family: ui-monospace, "Cascadia Code", "Source Code Pro", monospace; font-size: 13px;
  text-decoration: underline; }
.switches { display: flex; flex-wrap: wrap; gap: .5rem; }
.switch { display: inline-flex; border: 1px solid var(--rule); border-radius: 8px; overflow: hidden;
  background: #0a1220; }
.switch a { padding: .25rem .75rem; font-size: 13px; color: var(--muted); text-decoration: none;
  transition: background .2s, color .2s; }
.switch a + a { border-left: 1px solid var(--rule); }
.switch a:hover { color: var(--ink); }
.switch a[aria-current] { background: linear-gradient(180deg, #2a64a8, #1d4f8a); color: #fff;
  box-shadow: 0 0 16px #4aa3ff55; }
svg.chart { display: block; width: 100%; height: auto; overflow: visible; }
.chart .grid { stroke: #1b2a42; stroke-width: 1; }
.chart .tick { fill: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }
.chart .row-name { fill: var(--ink); font-size: 12.5px; }
.chart .row-value { fill: var(--muted); font-size: 12.5px; font-variant-numeric: tabular-nums; }
.chart .bar { fill: #3987e5; filter: drop-shadow(0 0 5px #3987e588); }
.chart .hit { fill: transparent; cursor: crosshair; }
.chart .hit:hover, .chart .hit:focus { fill: #ffffff0f; outline: none; }
.chart .seg, .chart .bar, .chart .arc, .chart .run-bar, .chart .cell { transition: filter .2s; }
.chart .arc:hover, .chart .arc:focus, .chart .run-bar:hover, .chart .run-bar:focus,
.chart .cell:hover { filter: brightness(1.35) drop-shadow(0 0 8px currentColor); outline: none; }
.s1 { fill: #3987e5; background: #3987e5; color: #3987e5; }
.s2 { fill: #d95926; background: #d95926; color: #d95926; }
.s3 { fill: #199e70; background: #199e70; color: #199e70; }
.s4 { fill: #c98500; background: #c98500; color: #c98500; }
.s5 { fill: #d55181; background: #d55181; color: #d55181; }
.s6 { fill: #008300; background: #008300; color: #008300; }
.s7 { fill: #9085e9; background: #9085e9; color: #9085e9; }
.s8 { fill: #e66767; background: #e66767; color: #e66767; }
.s0 { fill: #66758a; background: #66758a; color: #66758a; }
.square.s1, .square.s2, .square.s3, .square.s4, .square.s5, .square.s6, .square.s7,
.square.s8 { background: currentColor; }
.donut { max-width: 15rem; margin: 0 auto; }
.donut-track { fill: none; stroke: #121d2e; stroke-width: 32; }
.donut-value { fill: var(--ink); font-size: 40px; font-weight: 200; font-variant-numeric: tabular-nums; }
.donut-caption { fill: var(--muted); font-size: 12px; }
.share { display: grid; grid-template-columns: minmax(10rem, 15rem) 1fr; gap: 1.2rem;
  align-items: center; }
.legend.tall { flex-direction: column; flex-wrap: nowrap; gap: .45rem; }
.legend.tall li { display: flex; align-items: center; }
.legend.tall b { margin-left: auto; padding-left: 1rem; color: var(--ink); font-weight: 500;
  font-variant-numeric: tabular-nums; }
.area-fill { fill: url(#glow); }
.area-line { fill: none; stroke: #6fb8ff; stroke-width: 2.2; stroke-linejoin: round;
  filter: drop-shadow(0 0 6px #4aa3ff); }
.chart .dot { fill: transparent; }
.chart .dot:hover, .chart .dot:focus { fill: #6fb8ff; }
.head { fill: #fff; filter: drop-shadow(0 0 6px #6fb8ff); }
.head-pulse { fill: none; stroke: #6fb8ff; stroke-width: 2; transform-box: fill-box;
  transform-origin: center; opacity: 0; }
.timeline .lane-rule { stroke: #16233a; stroke-width: 16; stroke-linecap: round; }
.timeline .now { stroke: #b39bff; stroke-width: 1.5; stroke-dasharray: 3 4; }
.run-bar { filter: drop-shadow(0 0 5px currentColor); }
.heat .cell { fill: #1a2638; }
.heat .cell.on { fill: #4aa3ff; fill-opacity: var(--level); }
.empty-plot .empty-text { fill: var(--muted); font-size: 14px; letter-spacing: .02em; }
.legend { list-style: none; display: flex; flex-wrap: wrap; gap: .3rem 1.1rem; padding: 0;
  margin: 0 0 .6rem; font-size: 13px; color: var(--muted); }
.key { display: inline-block; width: .7rem; height: .7rem; border-radius: 3px; margin-right: .45rem;
  vertical-align: -1px; box-shadow: 0 0 8px currentColor; }
.scroll { overflow-x: auto; }
.panel table { font-size: 13px; font-variant-numeric: tabular-nums; }
.panel th { border-bottom-color: #22324a; }
.panel td { border-bottom-color: #172438; }
.panel td.n, .panel th.n { text-align: right; }
.panel tr:hover td { background: #ffffff05; }
#tip { position: fixed; z-index: 10; pointer-events: none; max-width: 24rem; padding: .4rem .7rem;
  background: #07101cf2; color: var(--ink); border: 1px solid #2a4a74; border-radius: 8px;
  font-size: 12.5px; box-shadow: 0 0 24px #4aa3ff33, 0 10px 30px #000a; backdrop-filter: blur(6px); }
@media (max-width: 760px) { .share { grid-template-columns: 1fr; } }
@media (prefers-reduced-motion: no-preference) {
  body.deck::before { animation: aurora 38s ease-in-out infinite alternate; }
  @keyframes aurora { 50% { transform: translate(4%, 3%) rotate(6deg) scale(1.08); }
    100% { transform: translate(-3%, 2%) rotate(-4deg) scale(1.04); } }
  body.boot .readout, body.boot .panel { animation: materialise .9s cubic-bezier(.2, .8, .2, 1) both;
    animation-delay: calc(var(--i, 0) * 90ms); }
  body.boot .deck-grid > :nth-child(1) { --i: 4; } body.boot .deck-grid > :nth-child(2) { --i: 5; }
  body.boot .deck-grid > :nth-child(3) { --i: 6; } body.boot .deck-grid > :nth-child(4) { --i: 7; }
  body.boot .deck-grid > :nth-child(5) { --i: 7.5; } body.boot .deck-grid > :nth-child(6) { --i: 8; }
  body.boot .deck-grid > :nth-child(7) { --i: 8.5; } body.boot .deck-grid > :nth-child(n+8) { --i: 9; }
  @keyframes materialise { from { opacity: 0; transform: translateY(18px) scale(.985);
    filter: blur(6px); } }
  body.boot .readouts::after { content: ""; position: fixed; inset: 0; pointer-events: none;
    background: linear-gradient(90deg, transparent, #6fb8ff22, transparent); width: 30%;
    animation: sweep 1.6s .2s ease-out both; }
  @keyframes sweep { from { transform: translateX(-100%); } to { transform: translateX(400%);
    opacity: 0; } }
  .boot .chart .seg { transform-box: fill-box; transform-origin: bottom;
    animation: rise .8s cubic-bezier(.2, .8, .2, 1) both;
    animation-delay: calc(.5s + var(--i, 0) * 16ms); }
  .boot .chart .bar, .boot .chart .run-bar { transform-box: fill-box; transform-origin: left;
    animation: reach .9s cubic-bezier(.2, .8, .2, 1) both;
    animation-delay: calc(.6s + var(--i, 0) * 60ms); }
  .boot .chart .arc { transform-box: view-box; transform-origin: center;
    animation: unfold 1s cubic-bezier(.2, .8, .2, 1) both;
    animation-delay: calc(.6s + var(--i, 0) * 110ms); }
  .boot .chart .cell { animation: appear .5s ease-out both;
    animation-delay: calc(.6s + var(--i, 0) * 4ms); }
  .boot .area-line { stroke-dasharray: 1; animation: draw 1.8s .6s ease-in-out both; }
  .boot .area-fill { animation: appear 1.2s 1.4s ease-out both; }
  .boot .gauge-fill { animation: fill 1.6s .5s cubic-bezier(.3, .7, .2, 1) both; }
  .boot .spark path { stroke-dasharray: 600; animation: spark 1.6s .4s ease-out both; }
  @keyframes rise { from { transform: scaleY(0); } }
  @keyframes reach { from { transform: scaleX(0); } }
  @keyframes unfold { from { transform: rotate(-40deg) scale(.6); opacity: 0; } }
  @keyframes appear { from { opacity: 0; } }
  @keyframes draw { from { stroke-dashoffset: 1; } to { stroke-dashoffset: 0; } }
  @keyframes fill { from { stroke-dasharray: 0 var(--length); } }
  @keyframes spark { from { stroke-dashoffset: 600; } to { stroke-dashoffset: 0; } }
  .head-pulse { animation: ping 2.4s ease-out infinite; }
  @keyframes ping { 0% { opacity: .9; transform: scale(1); } 100% { opacity: 0; transform: scale(4); } }
  .run-bar.live { animation: breathe 1.8s ease-in-out infinite; }
  @keyframes breathe { 50% { filter: brightness(1.5) drop-shadow(0 0 12px currentColor); } }
  .square:not(.free) { animation: glow 2.6s ease-in-out infinite; }
  @keyframes glow { 50% { box-shadow: 0 0 22px currentColor, 0 0 4px currentColor; } }
  .panel h2::before, fieldset.panel > legend::before { animation: blink 3s ease-in-out infinite; }
  @keyframes blink { 50% { opacity: .35; } }
}
@media (prefers-reduced-motion: no-preference) {
  .track span.now { animation: beat 2.4s ease-in-out infinite; }
  @keyframes beat { 50% { border-top-color: var(--steel-soft); } }
}
"""

UNSAVED = """
<script>
(() => {
  const form = document.querySelector("form[data-unsaved]");
  if (!form) return;
  const current = (control) =>
    control.type === "checkbox" ? String(control.checked) : control.value.replace(/\\r\\n/g, "\\n");
  const mark = () => {
    for (const control of form.querySelectorAll("[data-saved]"))
      control.classList.toggle("changed", current(control) !== control.dataset.saved);
  };
  form.addEventListener("input", mark);
  form.addEventListener("change", mark);
  mark();
})();
</script>
"""
"""Marks every control of a ``data-unsaved`` form whose value differs from its ``data-saved``."""

LIVE = """
<script>
(() => {
  const live = document.getElementById("live");
  if (!live || !window.EventSource) return;
  const refresh = async () => {
    if (live.contains(document.activeElement)) return;
    const query = new URLSearchParams(location.search);
    query.set("fragment", "1");
    const response = await fetch(location.pathname + "?" + query);
    if (response.ok) live.innerHTML = await response.text();
  };
  // Fresh nodes swapped in while the deck boots would boot again: refresh once it has booted.
  let waiting = false;
  document.addEventListener("booted", () => { if (waiting) { waiting = false; refresh(); } });
  const events = new EventSource("/events");
  events.onmessage = () => {
    if (document.body.classList.contains("boot")) waiting = true;
    else refresh();
  };
})();
</script>
"""

TIPS = """
<div id=tip hidden role=status></div>
<script>
(() => {
  const tip = document.getElementById("tip");
  const show = (target, x, y) => {
    tip.textContent = target.dataset.tip;
    tip.hidden = false;
    const width = tip.offsetWidth, height = tip.offsetHeight;
    tip.style.left = Math.max(8, Math.min(x + 14, innerWidth - width - 8)) + "px";
    tip.style.top = Math.max(8, y - height - 12) + "px";
  };
  document.addEventListener("pointermove", (event) => {
    const target = event.target.closest && event.target.closest("[data-tip]");
    if (target) show(target, event.clientX, event.clientY); else tip.hidden = true;
  });
  document.addEventListener("focusin", (event) => {
    const target = event.target.closest && event.target.closest("[data-tip]");
    if (!target) { tip.hidden = true; return; }
    const box = target.getBoundingClientRect();
    show(target, box.left + box.width / 2, box.top);
  });
  const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (!still) {
    for (const node of document.querySelectorAll("[data-count]")) {
      const target = Number(node.dataset.count), suffix = node.dataset.suffix || "";
      const began = performance.now() + 300;
      const tick = (now) => {
        const t = Math.min(Math.max((now - began) / 1400, 0), 1);
        node.textContent = Math.round(target * (1 - Math.pow(1 - t, 3))) + suffix;
        if (t < 1) requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    }
  }
  setTimeout(() => {
    document.body.classList.remove("boot");
    document.dispatchEvent(new Event("booted"));
  }, still ? 0 : 3200);
  // A switch redraws only its own panel: fetch the view, swap that panel, keep the URL in step.
  document.addEventListener("click", async (event) => {
    const link = event.target.closest && event.target.closest(".switch a");
    if (!link || event.ctrlKey || event.metaKey || event.shiftKey) return;
    const panel = link.closest("section[id]");
    if (!panel) return;
    event.preventDefault();
    const query = new URLSearchParams(link.search);
    history.replaceState(null, "", location.pathname + "?" + query);
    query.set("fragment", "1");
    const response = await fetch(location.pathname + "?" + query);
    if (!response.ok) { location.href = link.href; return; }
    const fresh = new DOMParser().parseFromString(await response.text(), "text/html")
      .getElementById(panel.id);
    if (!fresh) return;
    if (!still) fresh.classList.add("boot");
    panel.replaceWith(fresh);
    setTimeout(() => fresh.classList.remove("boot"), 2400);
  });
})();
</script>
"""
"""One tooltip for every chart mark that carries ``data-tip``, by pointer and by keyboard."""

PAGES = (
    ("/", "Issues"),
    ("/workers", "Cockpit"),
    ("/repositories", "Repositories"),
    ("/fleet", "Fleet"),
    ("/settings", "Settings"),
)


def e(value: object) -> str:
    return escape("" if value is None else str(value))


def span(delta: timedelta) -> str:
    minutes = int(abs(delta).total_seconds()) // 60
    days, minutes = divmod(minutes, 1440)
    hours, minutes = divmod(minutes, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def when(moment: datetime, now: datetime) -> str:
    relative = f"in {span(moment - now)}" if moment > now else f"{span(now - moment)} ago"
    return f"{moment:%Y-%m-%d %H:%M} UTC ({relative})"


def page(title: str, path: str, snapshot_id: str, body: str, live: bool = False) -> str:
    links = "".join(
        f'<a href="{href}"{" aria-current=page" if href == path else ""}>{name}</a>'
        for href, name in PAGES
    )
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{e(title)}: GitHubIssueWorker</title><style>{STYLE}</style></head>"
        + "<body class='deck boot'><div class=frame>"
        f"<nav><div class=who>GitHubIssueWorker</div>"
        f"<div class=id>manager {e(snapshot_id)}</div>{links}</nav>"
        f"<main>{body}</main></div>{LIVE if live else ''}{TIPS}</body></html>"
    )


# --- workers -------------------------------------------------------------------------------------


def _track(step: str | None) -> str:
    position = STEPS.index(step) if step in STEPS else -1
    cells = []
    for index, name in enumerate(STEPS):
        css = "now" if index == position else "past" if index < position else ""
        current = ' aria-current="step"' if index == position else ""
        cells.append(f'<span class="{css}"{current}>{name}</span>')
    return f'<div class=track role=list aria-label="Steps">{"".join(cells)}</div>'


def _issue_link(repository: str, number: int, title: str | None, body: str) -> str:
    tip = f' data-tip="{e(body[:400])}"' if body else ""
    return (
        f'<a href="https://github.com/{e(repository)}/issues/{number}"{tip}>'
        f"#{number} {e(title)}</a>"
    )


def _run(slot: int, issue: str, track: str, facts: Iterable[str]) -> str:
    """One issue a worker holds: its repository's coloured square, the issue, the track."""
    return (
        f'<div class=run><i class="square s{slot}" aria-hidden=true></i><div>'
        f"<div class=issue>{issue}</div>{track}<div class=facts>{', '.join(facts)}</div></div></div>"
    )


def _free(text: str) -> str:
    return f'<div class=run><i class="square free" aria-hidden=true></i><div>{text}</div></div>'


def _who(
    name: str, detail: str, model: str | None, unknown: str = "model shown after its first run"
) -> str:
    return f"<div class=name>{e(name)}</div><div class=runs>{detail}</div>" + (
        f"<div class=model>{e(model)}</div>"
        if model and model != "-"
        else f"<div class=quiet>{unknown}</div>"
    )


def _lane(
    worker: WorkerView,
    now: datetime,
    issues: Mapping[tuple[str, int], IssueView],
    slots: Mapping[str, int],
) -> str:
    who = _who(
        worker.name,
        f"{e(worker.agent)} on account <span class=nowrap>{e(worker.account)}</span>",
        worker.model,
    )
    if worker.state == "working" and worker.issue is not None and worker.repository:
        name = e(worker.name)
        facts = []
        if worker.branch:
            facts.append(
                f'<form method=post action="/workers/{name}/open" class=open>'
                f'<button class=link title="Open {e(worker.worktree)} in the file manager">'
                f"{e(worker.branch)}</button></form>"
            )
        if worker.since:
            facts.append(f"for {span(now - worker.since)}")
        known = issues.get((worker.repository, worker.issue))
        issue = f"<span class=repo>{e(worker.repository)}</span> " + _issue_link(
            worker.repository, worker.issue, worker.issue_title, known.body if known else ""
        )
        middle = _run(slots.get(worker.repository, 0), issue, _track(worker.step), facts)
    elif worker.state == "limited" and worker.limited_until:
        middle = _free(
            f'<span class="flag limited">Usage limit</span> '
            f"<span class=quiet>resets {when(worker.limited_until, now)}</span>"
        )
    elif worker.state == "unavailable":
        middle = _free(
            f'<span class="flag unavailable">Unavailable</span> <span class=quiet>{e(worker.reason)}</span>'
        )
    elif worker.state == "paused":
        middle = _free(
            '<span class="flag paused">Paused</span> <span class=quiet>takes no new issue</span>'
        )
    else:
        middle = _free("<span class=quiet>Idle, waiting for an issue.</span>")

    actions = _worker_actions(worker) + f'<a class=button href="/workers/{e(worker.name)}">Edit</a>'
    return (
        f'<div class=lane role=group aria-label="{e(worker.name)}"><div>{who}</div>'
        f"<div class=stack>{middle}</div><div class=actions>{actions}</div></div>"
    )


def _remote_lane(
    holder: str,
    claims: Sequence[ClaimView],
    now: datetime,
    issues: Mapping[tuple[str, int], IssueView],
    slots: Mapping[str, int],
) -> str:
    """Another manager's worker: the same squares, its branch on GitHub, and no controls."""
    manager, _, worker = holder.partition("/")
    model = next(
        (
            known.model
            for c in claims
            if (known := issues.get((c.repository, c.issue))) and known.model
        ),
        None,
    )
    runs = []
    for claim in claims:
        known = issues.get((claim.repository, claim.issue))
        issue = f"<span class=repo>{e(claim.repository)}</span> " + _issue_link(
            claim.repository, claim.issue, known.title if known else "", known.body if known else ""
        )
        facts = [
            f'<a href="https://github.com/{e(claim.repository)}/tree/{e(claim.branch)}">'
            f"{e(claim.branch)}</a>",
            f"renewed {span(now - claim.renewed)} ago",
        ]
        if claim.paused_until:
            facts.insert(
                0, f'<span class="flag paused">Paused</span> until {when(claim.paused_until, now)}'
            )
        runs.append(_run(slots.get(claim.repository, 0), issue, _track(claim.step), facts))
    who = _who(
        worker or holder, f"on <span class=nowrap>{e(manager)}</span>", model, "model not reported"
    )
    return (
        f'<div class="lane remote" role=group aria-label="{e(holder)}"><div>{who}</div>'
        f"<div class=stack>{''.join(runs)}</div><div></div></div>"
    )


def _worker_actions(worker: WorkerView, with_log: bool = True) -> str:
    """Log, pause or resume, and stop & release: what can be done to a local worker."""
    name = e(worker.name)
    toggle = "resume" if worker.state == "paused" else "pause"
    actions = []
    if worker.state == "working" and with_log:
        actions.append(f'<a class=button href="/workers/{name}/log">Log</a>')
    actions.append(
        f'<form method=post action="/workers/{name}/{toggle}"><button>{toggle.capitalize()}</button></form>'
    )
    if worker.state == "working":
        actions.append(
            f'<form method=post action="/workers/{name}/stop"><button class=danger '
            f'title="Kill the agent, give the issue back and pause {name}">Stop &amp; release</button></form>'
        )
    return "".join(actions)


@dataclass(frozen=True, slots=True)
class CockpitView:
    """What the cockpit's charts are split by and how far back they look."""

    by: SplitBy = "model"
    days: int = 30
    time: SplitBy = "model"

    def link(self, **change: object) -> str:
        values = {"by": self.by, "days": self.days, "time": self.time, **change}
        return "?" + "&amp;".join(f"{key}={value}" for key, value in values.items())


SPLITS: tuple[tuple[SplitBy, str], ...] = (
    ("model", "Model"),
    ("agent", "Agent"),
    ("repository", "Repository"),
)
WINDOWS = (30, 90, 180)
DEFAULT_VIEW = CockpitView()


def _key_of(record: RunRecord, by: SplitBy) -> str:
    return {"model": record.model, "agent": record.agent, "repository": record.repository}[by]


def _slots(history: Sequence[RunRecord], by: SplitBy) -> dict[str, int]:
    """Colour per entity by its first appearance in the whole history, so no view repaints it."""
    slots = color_slots(_key_of(record, by) for record in history)
    slots["Other"] = 0
    return slots


def _switch(
    label: str, view: CockpitView, field: str, choices: Iterable[tuple[object, str]]
) -> str:
    """Links that change one setting of the view and keep the others."""
    current = getattr(view, field)
    links = "".join(
        f'<a href="{view.link(**{field: value})}"'
        f"{' aria-current=true' if value == current else ''}>{e(name)}</a>"
        for value, name in choices
    )
    return f'<div class=switch role=group aria-label="{e(label)}">{links}</div>'


def _legend(series: Mapping[str, Sequence[int]], slots: Mapping[str, int]) -> str:
    keys = "".join(
        f'<li><i class="key s{slots.get(name, 0)}"></i>{e(name)}</li>' for name in series
    )
    return f'<ul class="legend">{keys}</ul>'


def _series_table(
    axis: Sequence[date], series: Mapping[str, Sequence[int]], period: str, fmt: str
) -> str:
    head = "".join(f"<th class=n>{e(name)}</th>" for name in series)
    rows = "".join(
        f"<tr><td>{day:{fmt}}</td>"
        + "".join(f"<td class=n>{values[index]}</td>" for values in series.values())
        + f"<td class=n>{sum(values[index] for values in series.values())}</td></tr>"
        for index, day in reversed(list(enumerate(axis)))
    )
    return (
        "<details><summary>Show as table</summary><div class=scroll><table>"
        f"<tr><th>{e(period)}</th>{head}<th class=n>Total</th></tr>{rows}</table></div></details>"
    )


def _count(value: int) -> str:
    """A readout's number, which counts up from zero while the deck boots."""
    return f"<b data-count={value}>{value}</b>"


def _readout_row(cells: Iterable[tuple[str, str, str]]) -> str:
    """The deck's top row: one tile per label, value and note."""
    return (
        '<section class=readouts aria-label="Readouts">'
        + "".join(
            f"<div class=readout style=--i:{index}><span>{e(label)}</span>{value}{note}</div>"
            for index, (label, value, note) in enumerate(cells)
        )
        + "</section>"
    )


def _panel(title: str, sub: str, content: str, label: str, tools: str = "") -> str:
    """A full-width deck panel; ``title``, ``sub`` and ``tools`` are markup, ``label`` is text."""
    return (
        f'<section class="panel wide" aria-label="{e(label)}"><header><h2>{title}</h2>{tools}'
        f"</header><p class=sub>{sub}</p>{content}</section>"
    )


def _readouts(history: list[RunRecord], now: datetime) -> str:
    numbers = kpis(history, now)
    average = sum(numbers.last_30_days) / 30
    median = span(numbers.median_resolve) if numbers.median_resolve is not None else "-"
    since = f"since {history[0].started:%b %d, %Y}" if history else "no run finished yet"
    share = (
        f"<b data-count={round(numbers.merge_share * 100)} data-suffix=%>{numbers.merge_share:.0%}</b>"
        if numbers.merge_share is not None
        else "<b>-</b>"
    )
    cells = (
        ("Merged today", _count(numbers.today), f"<small>{average:.1f} a day over 30 days</small>"),
        ("Last 7 days", _count(numbers.week), sparkline(numbers.last_30_days)),
        ("All time", _count(numbers.total), f"<small>{since}</small>"),
        ("Median time to merge", f"<b>{e(median)}</b>", "<small>first run to merge</small>"),
        (
            "Merge rate",
            f"<div class=gauged>{ring_gauge(numbers.merge_share)}{share}</div>",
            "<small>merged among merged and failed</small>",
        ),
    )
    return _readout_row(cells)


def _flight_log(snapshot: Snapshot, history: list[RunRecord]) -> str:
    """The last day as a timeline: every run per worker, the running ones still growing."""
    end = snapshot.now
    start = end - timedelta(hours=24)
    slots = _slots(history, "model")
    spans = [
        Span(
            record.worker,
            record.started,
            record.finished,
            slots.get(record.model, 0),
            f"{record.repository}#{record.issue}: {record.kind} run, {record.verdict}, "
            f"{record.model}, {span(record.finished - record.started)}",
        )
        for record in history
        if record.finished >= start
    ]
    for worker in snapshot.workers:
        if worker.state == "working" and worker.since and worker.issue is not None:
            spans.append(
                Span(
                    worker.name,
                    worker.since,
                    end,
                    slots.get(worker.model, 0),
                    f"{worker.repository}#{worker.issue}: running, {worker.step}, {worker.model}, "
                    f"{span(end - worker.since)} so far",
                    live=True,
                )
            )
    lanes = [w.name for w in snapshot.workers if any(s.lane == w.name for s in spans)]
    lanes += sorted({s.lane for s in spans} - set(lanes))
    return (
        '<section class="panel wide" id="flight" aria-label="Flight log">'
        "<header><h2>Flight log</h2></header>"
        f"<p class=sub>Every run of the last 24 hours, per worker, coloured by model; "
        f"{sum(1 for s in spans if s.live)} still running.</p>"
        f"{timeline(spans, lanes, start, end)}</section>"
    )


def _share(history: list[RunRecord]) -> str:
    rows = model_ledger(history)
    slots = _slots(history, "model")
    legend = "".join(
        f'<li><i class="key s{slots.get(row.model, 0)}"></i>{e(row.model)}<b>{row.runs}</b></li>'
        for row in rows
    )
    return (
        '<section class=panel id="share" aria-label="Model share">'
        "<header><h2>Model share</h2></header>"
        "<p class=sub>All runs ever, per model.</p>"
        f"<div class=share>{donut([(row.model, row.runs) for row in rows], slots, 'runs')}"
        f'<ul class="legend tall">{legend}</ul></div></section>'
    )


def _total(history: list[RunRecord], now: datetime) -> str:
    points = cumulative_merged(history)
    start = min((r.started for r in history), default=now - timedelta(days=1))
    return (
        '<section class=panel id="total" aria-label="Merged over time">'
        "<header><h2>Merged over time</h2></header>"
        f"<p class=sub>The running total since the first run: {len(points)} issues.</p>"
        f"{area_line(points, start, now, 'merged')}</section>"
    )


def _heat(history: list[RunRecord]) -> str:
    return (
        '<section class=panel id="heat" aria-label="When work gets done">'
        "<header><h2>When work gets done</h2></header>"
        "<p class=sub>Finished runs per weekday and hour (UTC); brighter is busier.</p>"
        f"{heatmap(activity(history), ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'))}</section>"
    )


def _crew(snapshot: Snapshot) -> str:
    working = sum(1 for worker in snapshot.workers if worker.state == "working")
    issues = {(issue.repository, issue.number): issue for issue in snapshot.issues}
    slots = color_slots(
        [repository.name for repository in snapshot.repositories]
        + [claim.repository for claim in snapshot.claims]
    )
    legend = "".join(
        f'<li><i class="key s{slot}"></i>{e(name)}</li>' for name, slot in slots.items()
    )
    lanes = (
        "".join(_lane(worker, snapshot.now, issues, slots) for worker in snapshot.workers)
        if snapshot.workers
        else '<p class=empty>No workers yet. <a href="/workers/new">Add one</a> from the agents '
        "installed on this machine.</p>"
    )
    remote: dict[str, list[ClaimView]] = {}
    for claim in snapshot.claims:
        if not claim.mine:
            remote.setdefault(claim.holder, []).append(claim)
    remote_lanes = "".join(
        _remote_lane(holder, claims, snapshot.now, issues, slots)
        for holder, claims in sorted(remote.items())
    )
    return (
        '<section class="panel wide" aria-label="Crew"><header><h2>Crew</h2>'
        f'<a href="/workers/new">Add a worker</a></header>'
        f"<p class=sub>{working} of {len(snapshot.workers)} on an issue here. A square is an issue "
        "a worker holds, coloured by its repository; the track shows how far it has come.</p>"
        f'<ul class="legend">{legend}</ul><h3>This machine</h3>{lanes}'
        + (f"<h3>Remote</h3>{remote_lanes}" if remote_lanes else "")
        + "</section>"
    )


def _throughput(history: list[RunRecord], now: datetime, view: CockpitView) -> str:
    axis, series = daily_merged(history, now, days=view.days, by=view.by)
    slots = _slots(history, view.by)
    switches = _switch("Split by", view, "by", SPLITS) + _switch(
        "Window", view, "days", ((days, f"{days} days") for days in WINDOWS)
    )
    total = sum(sum(values) for values in series.values())
    return (
        '<section class="panel wide" id="throughput" aria-label="Issues merged per day">'
        f"<header><h2>Issues merged per day</h2><div class=switches>{switches}</div></header>"
        f"<p class=sub>{total} merged in the last {view.days} days, "
        f"{total / view.days:.1f} a day, by {view.by}.</p>"
        f"{_legend(series, slots)}"
        f"{stacked_columns(axis, series, slots, unit='merged')}"
        f"{_series_table(axis, series, 'Day', '%Y-%m-%d')}</section>"
    )


def _mix(history: list[RunRecord], now: datetime) -> str:
    axis, series = weekly_mix(history, now, weeks=26)
    slots = _slots(history, "model")
    return (
        '<section class="panel wide" id="mix" aria-label="Model mix per week">'
        "<header><h2>Model mix per week</h2></header>"
        "<p class=sub>Runs per model per week over half a year, whatever their outcome.</p>"
        f"{_legend(series, slots)}"
        f"{stacked_columns(axis, series, slots, unit='runs', width=960, height=240, prefix='week of ')}"
        f"{_series_table(axis, series, 'Week of', '%Y-%m-%d')}</section>"
    )


def _time(history: list[RunRecord], view: CockpitView) -> str:
    rows = resolve_times(history, by=view.time)
    bars = horizontal_bars(
        [
            (row.name, row.average.total_seconds(), f"{span(row.average)} avg, {row.count} issues")
            for row in rows
        ],
        width=600,
    )
    table = "".join(
        f"<tr><td>{e(row.name)}</td><td class=n>{row.count}</td>"
        f"<td class=n>{span(row.average)}</td><td class=n>{span(row.median)}</td></tr>"
        for row in rows
    )
    return (
        '<section class=panel id="time" aria-label="Time to merge">'
        "<header><h2>Time to merge</h2>"
        f"{_switch('Split by', view, 'time', SPLITS)}</header>"
        f"<p class=sub>Average from an issue's first run to its merge, by {view.time}; "
        "slowest first.</p>"
        f"{bars}<details><summary>Show as table</summary><div class=scroll><table>"
        f"<tr><th>{e(view.time.capitalize())}</th><th class=n>Issues</th><th class=n>Average</th>"
        f"<th class=n>Median</th></tr>{table}</table></div></details></section>"
    )


def _ledger(history: list[RunRecord]) -> str:
    rows = model_ledger(history)
    slots = _slots(history, "model")
    if not rows:
        return ""
    body = "".join(
        f'<tr><td><i class="key s{slots.get(row.model, 0)}"></i>{e(row.model)}</td>'
        f"<td>{e(row.agent)}</td><td class=n>{row.runs}</td><td class=n>{row.merged}</td>"
        f"<td class=n>{row.merged / row.runs:.0%}</td>"
        f"<td class=n>{span(row.median_resolve) if row.median_resolve else '-'}</td>"
        f"<td class=n>{row.first_used:%Y-%m-%d}</td><td class=n>{row.last_used:%Y-%m-%d}</td></tr>"
        for row in rows
    )
    return (
        '<section class="panel wide" id="ledger" aria-label="Model ledger">'
        "<header><h2>Model ledger</h2></header>"
        "<p class=sub>Every model this manager has run, most runs first.</p>"
        "<div class=scroll><table><tr><th>Model</th><th>Agent</th><th class=n>Runs</th>"
        "<th class=n>Merged</th><th class=n>Merge share</th><th class=n>Median to merge</th>"
        f"<th class=n>First used</th><th class=n>Last used</th></tr>{body}</table></div></section>"
    )


def cockpit_fragment(
    snapshot: Snapshot, history: list[RunRecord], view: CockpitView = DEFAULT_VIEW
) -> str:
    return (
        _readouts(history, snapshot.now)
        + "<div class=deck-grid>"
        + _crew(snapshot)
        + _flight_log(snapshot, history)
        + _throughput(history, snapshot.now, view)
        + _share(history)
        + _total(history, snapshot.now)
        + _time(history, view)
        + _heat(history)
        + _mix(history, snapshot.now)
        + _ledger(history)
        + "</div>"
    )


def cockpit_page(
    snapshot: Snapshot,
    history: list[RunRecord],
    view: CockpitView = DEFAULT_VIEW,
    notice: str = "",
) -> str:
    body = (
        "<h1>Cockpit</h1>"
        "<p class=lede>The crew at work right now, and everything this manager has finished: "
        "kept across restarts in its state directory.</p>"
        f"{_notice(notice)}<div id=live>{cockpit_fragment(snapshot, history, view)}</div>"
    )
    return page("Cockpit", "/workers", snapshot.manager_id, body, live=True)


# --- repositories --------------------------------------------------------------------------------


def repositories_page(
    snapshot: Snapshot, config: Configuration, notice: str = "", problems: Iterable[str] = ()
) -> str:
    watched = snapshot.repositories
    readouts = _readout_row(
        (
            ("Watched", _count(len(watched)), "<small>repositories you can push to</small>"),
            (
                "Polled",
                _count(sum(1 for repository in watched if repository.watching_since)),
                "<small>at least once</small>",
            ),
            (
                "Issues in progress",
                _count(sum(sum(repository.counts.values()) for repository in watched)),
                "<small>across the watch list</small>",
            ),
            (
                "Claimed here",
                _count(sum(1 for claim in snapshot.claims if claim.mine)),
                "<small>by workers of this machine</small>",
            ),
        )
    )
    add = _panel(
        "Watch a repository",
        "New issues and comments in it are worked from then on.",
        '<form class=inline method=post action="/repositories/add">'
        "<label for=repository>Repository</label>"
        '<input id=repository name="repository" placeholder="owner/name or URL" size=36 required>'
        "<button class=primary>Watch repository</button></form>",
        "Watch a repository",
    )
    panels = []
    for repository in watched:
        counts = ", ".join(f"{count} {name}" for name, count in sorted(repository.counts.items()))
        since = repository.watching_since
        overrides = next(r for r in config.repositories if r.repository == repository.name)
        stop = (
            '<form method=post action="/repositories/remove">'
            f'<input type=hidden name="repository" value="{e(repository.name)}">'
            "<button class=danger>Stop watching</button></form>"
        )
        panels.append(
            _panel(
                f'<a href="https://github.com/{e(repository.name)}">{e(repository.name)}</a>',
                f"{'Watching since ' + e(when(since, snapshot.now)) if since else 'Not polled yet'}"
                f"; {e(counts) or 'no active issues'}.",
                _override_form(overrides, config.defaults),
                repository.name,
                stop,
            )
        )
    listed = "".join(panels) or _panel(
        "Watched",
        "Nothing yet.",
        "<p class=empty>No repository is watched yet. Add one you can push to.</p>",
        "Watched",
    )
    body = (
        "<h1>Repositories</h1>"
        "<p class=lede>New issues and comments in these repositories are worked. A repository "
        "is only added when your account can push to it.</p>"
        f"{_notice(notice)}{_problems(problems)}{readouts}<div class=deck-grid>{add}{listed}</div>"
    )
    return page("Repositories", "/repositories", snapshot.manager_id, body)


def _override_form(repository: RepositorySettings, defaults: DefaultsSettings) -> str:
    fields = [f for f in fields_of(RepositorySettings) if f.path != "repository"]
    inputs = "".join(
        _input(
            field,
            display(getattr(repository, _attr(RepositorySettings, field.path))),
            placeholder=display(value_at(defaults, field.path)),
            source="",
        )
        for field in fields
    )
    return (
        f'<details><summary>Overrides</summary><form method=post action="/repositories/save">'
        f'<input type=hidden name="repository" value="{e(repository.repository)}">'
        f"<p class=quiet>Empty fields use the defaults from Settings.</p>{inputs}"
        "<button class=primary>Save overrides</button></form></details>"
    )


# --- fleet ---------------------------------------------------------------------------------------


def _claim_row(claim: ClaimView, now: datetime) -> str:
    state = (
        f'<span title="{e(when(claim.paused_until, now))}">paused, resumes in {span(claim.paused_until - now)}</span>'
        if claim.paused_until
        else e(claim.state)
    )
    manager, _, worker = claim.holder.rpartition("/")
    holder = (
        f"<span class=holder>{e(worker)}<span class=quiet>"
        f"{'this manager' if claim.mine else e(manager)}</span></span>"
    )
    reference = f"{claim.repository}#{claim.issue}"
    release = (
        f'<details><summary>Release</summary><form method=post action="/claims/release">'
        f'<input type=hidden name="repository" value="{e(claim.repository)}">'
        f'<input type=hidden name="issue" value="{claim.issue}">'
        + (
            ""
            if claim.mine
            else f"<label>Type <code>{e(reference)}</code> to release another manager's claim "
            f'<input name="confirm" required></label>'
        )
        + "<button class=danger>Release</button></form></details>"
    )
    return (
        f'<tr><td><a href="https://github.com/{e(claim.repository)}/issues/{claim.issue}">'
        f"#{claim.issue}</a></td><td>{holder}</td><td>{state}</td>"
        f'<td>{e(claim.step)}</td><td class=nowrap title="{claim.renewed:%Y-%m-%d %H:%M} UTC">'
        f"{span(now - claim.renewed)} ago</td>"
        f'<td class=nowrap title="{claim.expires:%Y-%m-%d %H:%M} UTC">in {span(claim.expires - now)}</td>'
        f"<td class=mono>{e(claim.branch)}</td>"
        f"<td>{release}</td></tr>"
    )


def fleet_fragment(snapshot: Snapshot) -> str:
    claims = snapshot.claims
    repositories = sorted({claim.repository for claim in claims})
    others = {claim.holder.rpartition("/")[0] for claim in claims if not claim.mine}
    readouts = _readout_row(
        (
            ("Claims", _count(len(claims)), f"<small>in {len(repositories)} repositories</small>"),
            (
                "Held here",
                _count(sum(1 for claim in claims if claim.mine)),
                "<small>by workers of this machine</small>",
            ),
            ("Other managers", _count(len(others)), "<small>holding the rest</small>"),
            (
                "Paused",
                _count(sum(1 for claim in claims if claim.paused_until)),
                "<small>until a usage limit resets</small>",
            ),
        )
    )
    parts = []
    for repository in repositories:
        held = sorted((c for c in claims if c.repository == repository), key=lambda c: c.issue)
        rows = "".join(_claim_row(claim, snapshot.now) for claim in held)
        mine = sum(1 for claim in held if claim.mine)
        parts.append(
            _panel(
                e(repository),
                f"{len(held)} claimed, {mine} of them by this manager.",
                "<div class=scroll><table><thead><tr><th>Issue</th><th>Held by</th>"
                "<th>State</th><th>Step</th><th>Renewed</th><th>Expires</th><th>Branch</th>"
                f"<th></th></tr></thead><tbody>{rows}</tbody></table></div>",
                repository,
            )
        )
    listed = "".join(parts) or _panel(
        "Claims",
        "Nothing is held.",
        "<p class=empty>No issue is claimed right now, by this or any other manager.</p>",
        "Claims",
    )
    return f"{readouts}<div class=deck-grid>{listed}</div>"


def fleet_page(snapshot: Snapshot, notice: str = "", problems: Iterable[str] = ()) -> str:
    body = (
        "<h1>Fleet</h1>"
        "<p class=lede>Every issue claimed in the watched repositories, by this manager or any "
        "other. A claim nobody renews for its validity plus grace is free for others.</p>"
        f"{_notice(notice)}{_problems(problems)}<div id=live>{fleet_fragment(snapshot)}</div>"
    )
    return page("Fleet", "/fleet", snapshot.manager_id, body, live=True)


# --- settings and worker forms -------------------------------------------------------------------


def _attr(model: type[BaseModel], alias: str) -> str:
    return next(
        (name for name, info in model.model_fields.items() if (info.alias or name) == alias), alias
    )


def _saved(saved: str | None, value: str) -> str:
    """The attributes that let the page mark ``value`` while it differs from ``saved``."""
    if saved is None:
        return ""
    changed = value.replace("\r\n", "\n") != saved
    return f' data-saved="{e(saved)}"' + (' class="changed"' if changed else "")


def _input(
    field: FormField,
    value: str,
    placeholder: str = "",
    source: str = "",
    error: bool = False,
    saved: str | None = None,
) -> str:
    name = e(field.path)
    ident = "f-" + field.path.replace(".", "-").replace("[", "-").replace("]", "")
    # A secret is write-only: its control starts empty, so only what is typed into it counts.
    hint = _saved(saved, "" if field.kind == "secret" else value)
    if field.kind == "bool":
        control = (
            f'<input type=checkbox id="{ident}" name="{name}" value=true'
            f"{' checked' if value == 'true' else ''}{hint}>"
        )
    elif field.kind == "choice":
        options = [("", f"default ({placeholder})")] if field.optional or placeholder else []
        options += [(choice, choice) for choice in field.choices]
        control = (
            f'<select id="{ident}" name="{name}"{hint}>'
            + "".join(
                f'<option value="{e(v)}"{" selected" if v == value else ""}>{e(t)}</option>'
                for v, t in options
            )
            + "</select>"
        )
    elif field.kind == "env":
        control = f'<textarea id="{ident}" name="{name}" rows=3 cols=34 placeholder="NAME=value"{hint}>{e(value)}</textarea>'
    elif field.kind == "secret":
        state = "set; leave empty to keep" if value else "not set"
        control = f'<input type=password id="{ident}" name="{name}" placeholder="{state}" autocomplete=off{hint}>'
    else:
        kind = "number" if field.kind == "number" else "text"
        step = " step=any" if field.kind == "number" else ""
        control = (
            f'<input type={kind}{step} id="{ident}" name="{name}" value="{e(value)}" '
            f'placeholder="{e(placeholder)}" size=30{hint}>'
        )
    help_text = HELP.get(field.path, "")
    note = f"<div class=source>{e(source)}</div>" if source else ""
    return (
        f'<div class="field{" error" if error else ""}"><label for="{ident}">{e(field.label)}</label>'
        f"<div>{control}{note}</div><div class=help>{e(help_text)}</div></div>"
    )


SECTIONS = (
    ("manager", "This machine"),
    ("pollInterval", "Polling and trust"),
    ("ui", "This page"),
    ("fleet", "Fleet"),
    ("defaults", "Defaults for every repository"),
)


def settings_page(
    snapshot_id: str,
    config: Configuration,
    notice: str = "",
    problems: Iterable[str] = (),
    error_paths: frozenset[str] = frozenset(),
    submitted: Mapping[str, str] | None = None,
) -> str:
    fields = fields_of(Configuration, skip=frozenset({"workers", "repositories", "accounts"}))
    defaults = Configuration()
    groups: dict[str, list[str]] = {title: [] for _, title in SECTIONS}
    for field in fields:
        head = field.path.split(".")[0]
        title = next(
            (t for key, t in SECTIONS if key == head),
            "Polling and trust",
        )
        value = (
            submitted.get(field.path, "")
            if submitted is not None
            else display(value_at(config, field.path))
        )
        if field.kind == "bool" and submitted is not None:
            value = "true" if field.path in submitted else "false"
        if field.kind == "secret":
            value = "set" if config.ui.token else ""
        source = "set in the configuration" if is_set(config, field.path) else "default"
        saved = "" if field.kind == "secret" else display(value_at(config, field.path))
        groups[title].append(
            _input(
                field,
                value,
                display(value_at(defaults, field.path)),
                source,
                field.path in error_paths,
                saved,
            )
        )
    body = "".join(
        f'<fieldset class="panel wide"><legend>{e(title)}</legend>{"".join(inputs)}</fieldset>'
        for title, inputs in groups.items()
        if inputs
    )
    accounts = _accounts(config)
    page_body = (
        "<h1>Settings</h1>"
        "<p class=lede>Everything here is also in the configuration file; saving writes the file "
        "and applies the change without a restart. Workers and repositories have their own pages. "
        "A value you changed but did not save yet is highlighted."
        f"</p>{_notice(notice)}{_problems(problems)}"
        f'<form class=deck-grid method=post action="/settings" data-unsaved>{body}{accounts}'
        f"<p><button class=primary>Save settings</button></p></form>{UNSAVED}"
    )
    return page("Settings", "/settings", snapshot_id, page_body)


def _accounts(config: Configuration) -> str:
    rows = []
    for index, (name, account) in enumerate([*config.accounts.items(), ("", AccountSettings())]):
        runs = "" if account.max_concurrent_runs is None else str(account.max_concurrent_runs)
        rows.append(
            f'<tr><td><input name="accounts[{index}].name" value="{e(name)}" '
            f'placeholder="{"new account" if not name else ""}" aria-label="Account name"{_saved(name, name)}></td>'
            f'<td><input type=number min=1 name="accounts[{index}].maxConcurrentRuns" value="{e(runs)}" '
            f'placeholder="unlimited" aria-label="Runs at once"{_saved(runs, runs)}></td>'
            + (
                f'<td><label><input type=checkbox name="accounts[{index}].remove" value=true{_saved("false", "false")}> remove</label></td>'
                if name
                else "<td></td>"
            )
            + "</tr>"
        )
    return (
        '<fieldset class="panel wide"><legend>Accounts</legend>'
        "<p class=quiet>What workers bill against. Usage limits are tracked per account; a local "
        "model that serves one request at a time gets 1 run at once.</p>"
        "<div class=scroll><table><thead><tr><th>Name</th><th>Runs at once</th><th></th></tr>"
        f"</thead><tbody>{''.join(rows)}</tbody></table></div></fieldset>"
    )


WORKER_FIELDS = [f for f in fields_of(WorkerSettings) if not f.path.startswith("installation")]
INSTALLATION_FIELDS = ("type", "path", "env", "root", "profile")


def worker_page(
    snapshot_id: str,
    config: Configuration,
    worker: WorkerSettings | None,
    detected: list[dict[str, Any]] | None = None,
    problems: Iterable[str] = (),
) -> str:
    title = f"Worker {worker.name}" if worker else "Add a worker"
    current: dict[str, str] = {}
    if worker:
        for field in WORKER_FIELDS:
            current[field.path] = display(getattr(worker, _attr(WorkerSettings, field.path)))
        for key in INSTALLATION_FIELDS:
            current[f"installation.{key}"] = display(getattr(worker.installation, key, None))
    inputs = []
    for field in WORKER_FIELDS:
        if field.path == "account":
            options = "".join(
                f"<option{' selected' if name == current.get('account') else ''}>{e(name)}</option>"
                for name in config.accounts
            )
            inputs.append(
                f"<div class=field><label for=f-account>Account</label><div><select id=f-account "
                f'name="account">{options}</select></div><div class=help>Add accounts under Settings.</div></div>'
            )
            continue
        inputs.append(_input(field, current.get(field.path, "")))
    install_type = current.get("installation.type", "path")
    install = [
        _input(
            FormField(
                "installation.type",
                "Installation",
                "choice",
                ("path", "executable", "claudecodeportable", "claudelocal"),
            ),
            install_type,
        ),
        _input(
            FormField("installation.path", "Executable path", "text", optional=True),
            current.get("installation.path", ""),
            "for executable",
        ),
        _input(
            FormField("installation.env", "Environment", "env", optional=True),
            current.get("installation.env", ""),
        ),
        _input(
            FormField("installation.root", "Root folder", "text", optional=True),
            current.get("installation.root", ""),
            "~/ClaudeCodePortable or ~/ClaudeLocal",
        ),
        _input(
            FormField("installation.profile", "Profile", "text", optional=True),
            current.get("installation.profile", ""),
            "for claudecodeportable",
        ),
    ]
    action = f"/workers/{e(worker.name)}" if worker else "/workers/new"
    found = ""
    if detected:
        rows = "".join(
            f"<tr><td>{e(item['name'])}</td><td>{e(item['agent'])}</td>"
            f"<td>{e(item['installation']['type'])}</td>"
            f'<td><form method=post action="/workers/add-detected"><input type=hidden name="index" value={index}>'
            "<button>Add</button></form></td></tr>"
            for index, item in enumerate(detected)
        )
        found = _panel(
            "Found on this machine",
            "Add one as it is, or describe one below.",
            "<div class=scroll><table><thead><tr><th>Name</th><th>Agent</th>"
            f"<th>Installation</th><th></th></tr></thead><tbody>{rows}</tbody></table></div>",
            "Found on this machine",
        )
    remove = (
        f'<form method=post action="/workers/{e(worker.name)}/remove"><button class=danger>'
        "Remove worker</button> <span class=quiet>Its issue is released for other workers."
        "</span></form>"
        if worker
        else ""
    )
    body = (
        f"<h1>{e(title)}</h1>{_problems(problems)}"
        f'<div class=deck-grid>{found}<form class=deck-grid method=post action="{action}">'
        f'<fieldset class="panel wide"><legend>Agent</legend>{"".join(inputs)}</fieldset>'
        f'<fieldset class="panel wide"><legend>Installation</legend>{"".join(install)}</fieldset>'
        f"<p><button class=primary>Save worker</button></p></form>{remove}</div>"
    )
    return page(title, "/", snapshot_id, body)


def _notice(text: str) -> str:
    return f"<p class=notice role=status>{e(text)}</p>" if text else ""


def _problems(problems: Iterable[str]) -> str:
    items = list(problems)
    if not items:
        return ""
    lines = "".join(f"<li>{e(problem)}</li>" for problem in items)
    return f"<div class=problems role=alert><strong>Not saved.</strong><ul>{lines}</ul></div>"


# --- issues --------------------------------------------------------------------------------------


def _issue_row(issue: IssueView, workers: Mapping[str, WorkerView], now: datetime) -> str:
    link = (
        f"<span class=quiet>{e(issue.repository)}</span><br>"
        f'<a href="https://github.com/{e(issue.repository)}/issues/{issue.number}">'
        f"#{issue.number} {e(issue.title)}</a>"
    )
    if issue.worker:
        where = "this machine" if issue.local else f"{issue.manager}, remote"
        model = f" on {e(issue.model)}" if issue.model else ""
        who = (
            f"{e(issue.worker)}{model}"
            f'<span class="where{" local" if issue.local else ""}">{e(where)}</span>'
        )
    else:
        who = "<span class=quiet>nobody</span>"
    since = f" for {span(now - issue.since)}" if issue.since else ""
    step = f"{e(issue.step)}{since}" if issue.step else ""
    worker = workers.get(issue.worker or "") if issue.local else None
    if worker is not None:
        actions = f"<div class=actions>{_worker_actions(worker)}</div>"
    elif issue.worker:
        actions = '<a href="/fleet">Fleet</a>'
    else:
        actions = ""
    return (
        f"<tr><td>{link}</td><td>{e(issue.state)}</td><td>{who}</td><td class=nowrap>{step}</td>"
        f"<td>{actions}</td></tr>"
    )


def issues_fragment(snapshot: Snapshot) -> str:
    issues = snapshot.issues
    readouts = _readout_row(
        (
            ("Open", _count(len(issues)), "<small>in the watched repositories</small>"),
            (
                "Worked here",
                _count(sum(1 for issue in issues if issue.local)),
                "<small>by workers of this machine</small>",
            ),
            (
                "Worked elsewhere",
                _count(sum(1 for issue in issues if issue.worker and not issue.local)),
                "<small>by other managers</small>",
            ),
            (
                "Waiting",
                _count(sum(1 for issue in issues if not issue.worker)),
                "<small>for a worker, an answer or a review</small>",
            ),
        )
    )
    if not issues:
        listed = (
            "<p class=empty>No open issue in the watched repositories. "
            '<a href="/repositories">Watch another repository</a>.</p>'
        )
    else:
        workers = {worker.name: worker for worker in snapshot.workers}
        order = sorted(
            issues, key=lambda i: (not i.local, i.worker is None, i.repository, i.number)
        )
        rows = "".join(_issue_row(issue, workers, snapshot.now) for issue in order)
        listed = (
            "<div class=scroll><table><thead><tr><th>Issue</th><th>State</th><th>Worked by</th>"
            f"<th>Step</th><th></th></tr></thead><tbody>{rows}</tbody></table></div>"
        )
    board = _panel(
        "Open issues",
        "Worked on this machine first, then by other managers, then the ones nobody works yet.",
        listed,
        "Open issues",
    )
    return f"{readouts}<div class=deck-grid>{board}</div>"


def issues_page(snapshot: Snapshot, notice: str = "", problems: Iterable[str] = ()) -> str:
    body = (
        "<h1>Issues</h1>"
        "<p class=lede>Every open issue in the watched repositories, what is happening with it "
        "and who works it. Workers on this machine can be paused, stopped or watched.</p>"
        f"{_notice(notice)}{_problems(problems)}<div id=live>{issues_fragment(snapshot)}</div>"
    )
    return page("Issues", "/", snapshot.manager_id, body, live=True)


# --- the agent's log -----------------------------------------------------------------------------


def _entry(entry: Entry) -> str:
    if entry.kind == "tool":
        return f"<li class=tool><strong>{e(entry.title)}</strong>{e(entry.text)}</li>"
    if entry.kind == "output":
        return f"<li><pre>{e(entry.text)}</pre></li>"
    return f'<li class="{entry.kind}">{e(entry.text)}</li>'


def log_fragment(text: str | None) -> str:
    if text is None:
        content = "<p class=empty>This worker has not run yet, so there is no log.</p>"
    elif not (entries := transcript(text)):
        content = "<p class=empty>The agent has started; its first output appears here.</p>"
    else:
        content = f"<ol class=log>{''.join(_entry(entry) for entry in entries)}</ol>"
    return _panel(
        "Transcript", "Messages, commands and their output, oldest first.", content, "Transcript"
    )


def log_page(snapshot: Snapshot, worker: WorkerView, text: str | None) -> str:
    about = f" on #{worker.issue} {e(worker.issue_title)}" if worker.issue is not None else ""
    body = (
        f"<h1>{e(worker.name)}{about}</h1>"
        f"<p class=lede>What {e(worker.agent)} on {e(worker.model)} says and does, as it "
        "happens: messages, the commands and tools it runs, and their output.</p>"
        f'<div class=actions style="justify-content:flex-start;margin-bottom:1rem">{_worker_actions(worker, with_log=False)}</div>'
        f"<div id=live>{log_fragment(text)}</div>"
    )
    return page(f"Log of {worker.name}", "/workers", snapshot.manager_id, body, live=True)
