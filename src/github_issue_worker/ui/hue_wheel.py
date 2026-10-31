"""The model colour wheel on the Settings page (F14.20): every maker's hue segment as an arc.

The page renders the data, the script draws the wheel: an OKLCH hue ring in the colours the charts
use, one labelled arc per maker on a track of its own (segments may overlap), a handle in the middle
of each arc for its hue and one at each end for its width. The number fields stay the form's only
controls; the wheel writes into them and reads them back, so saving, validation and the unsaved
marking work exactly as for typed values, and without a script the fields remain on their own.
The preview recomputes the chart colour of sample models with the same formula as
``model_colors``, from the places the server works out for each name.
"""

from __future__ import annotations

import json
from typing import Any

from github_issue_worker.configuration import HueSegment, ModelColorsSettings
from github_issue_worker.ui.model_colors import WHEEL, maker_place, model_series, placements

MAKERS = tuple(ModelColorsSettings.model_fields)
"""The segments, in the order their arcs sit on the wheel from the outside in."""

LABELS = {
    "anthropic": "Anthropic",
    "openai": "OpenAI",
    "google": "Google",
    "meta": "Meta",
    "xai": "xAI",
    "deepseek": "DeepSeek",
    "nvidia": "NVIDIA",
    "alibaba": "Alibaba",
    "mistral": "Mistral",
    "moonshot": "Moonshot",
    "zai": "Z.ai",
    "other": "Other",
}

SAMPLES = {
    "anthropic": ("Claude Opus 5.5", "Claude Sonnet 5", "Claude Haiku 4.5", "Claude Fable 5"),
    "openai": ("GPT 6.1 Sol", "GPT-5 Codex", "o4 mini"),
    "google": ("Gemini 4 Argon (Medium)", "Gemini 2.5 Flash", "Gemini 2.5 Pro"),
    "meta": ("Llama 4 Maverick", "Llama 4 Scout", "Llama 3.3 70B"),
    "xai": ("Grok 4", "Grok 4 Fast", "Grok Code Fast 1"),
    "deepseek": ("DeepSeek V3.2", "DeepSeek R1", "DeepSeek Coder V2"),
    "nvidia": ("Nemotron Ultra 253B", "Nemotron Super 49B", "Nemotron Nano 9B"),
    "alibaba": ("Qwen3.8 Flash Next IQ3_S", "Qwen3 Max", "Qwen3 Coder 480B"),
    "mistral": ("Mistral Large 3", "Codestral 25.08", "Devstral Medium"),
    "moonshot": ("Kimi K2", "Kimi K2 Thinking", "Kimi Dev 72B"),
    "zai": ("GLM-4.6", "GLM-4.5 Air", "GLM-4.5V"),
    "other": ("Phi-4", "Granite 3.3", "Nova Pro", "OLMo 2"),
}
"""Models the preview shows for each maker; ``other`` names makers that share its segment."""

TRACKS = tuple(154 - 8 * index for index in range(len(MAKERS)))
"""The radius of each maker's track, from the outside in: every arc keeps a track of its own, so
two segments that overlap stay apart."""


def wheel_data(colors: ModelColorsSettings) -> dict[str, Any]:
    """What the script draws: the segments, the sample models and the colour constants."""
    makers = []
    for key, track in zip(MAKERS, TRACKS, strict=True):
        segment: HueSegment = getattr(colors, key)
        names = SAMPLES[key]
        series = model_series(names, colors)
        samples = [
            {
                "name": placed.name,
                "place": placed.place,
                "shade": placed.shade,
                "makerPlace": maker_place(placed.creator),
                "color": series[placed.name],
            }
            for placed in placements(names)
        ]
        makers.append(
            {
                "key": key,
                "label": LABELS[key],
                "hue": segment.hue,
                "width": segment.width,
                "track": track,
                "samples": samples,
            }
        )
    return {"makers": makers, "wheel": WHEEL}


def hue_wheel(colors: ModelColorsSettings) -> str:
    """The wheel's markup, hidden until its script has drawn it."""
    data = json.dumps(wheel_data(colors), separators=(",", ":")).replace("<", "\\u003c")
    return (
        f"{WHEEL_STYLE}"
        '<div class="hue-wheel" hidden>'
        '<svg class="wheel" viewBox="-90 -22 580 444" role="group" '
        'aria-label="Model colour wheel"></svg>'
        '<div class="wheel-side">'
        '<div class="wheel-makers" role="group" aria-label="Maker"></div>'
        '<p class="wheel-readout" aria-live="polite"></p>'
        '<ul class="wheel-swatches" aria-label="Preview"></ul>'
        '<p class="wheel-hint">Drag the middle of an arc to move its hue, an end to change its '
        "width; or focus a handle and use the arrow keys, Shift for 10 degrees.</p>"
        "</div>"
        f'<script type="application/json" id="hue-wheel-data">{data}</script>'
        "</div><!-- hue-wheel -->"
        f"{WHEEL_SCRIPT}"
    )


WHEEL_STYLE = """<style>
.hue-wheel { clear: both; display: grid; grid-template-columns: minmax(0, 30rem) minmax(0, 1fr);
  gap: 1rem 2rem;
  align-items: center; padding: .25rem 0 1rem; margin-bottom: .5rem;
  border-bottom: 1px solid var(--rule); }
.hue-wheel[hidden] { display: none; }
.wheel { display: block; width: 100%; height: auto; overflow: visible; touch-action: none;
  user-select: none; -webkit-user-select: none; -webkit-tap-highlight-color: transparent; }
.wheel .ring path { fill: none; stroke-width: 16; }
.wheel .tick { stroke: var(--paper); stroke-width: 2; }
.wheel .track { fill: none; stroke: #ffffff0a; stroke-width: 7; }
.wheel .band path { fill: none; stroke-width: 6; stroke-linecap: butt; cursor: grab; }
.wheel .band circle { cursor: grab; }
.wheel .maker { transition: opacity .2s; }
.wheel.editing .maker:not(.active) { opacity: .28; }
.wheel .maker.active .band { filter: drop-shadow(0 0 7px var(--c)); }
.wheel .maker.active .band path { stroke-width: 9; }
.wheel .maker.active .track { stroke: #ffffff26; stroke-width: 10; }
.wheel .label { font-size: 18px; font-weight: 550; fill: var(--lc); paint-order: stroke;
  stroke: var(--paper); stroke-width: 4px; }
.wheel .maker.active .label { font-weight: 800; font-size: 20px; text-decoration: underline; }
.wheel .handle { cursor: grab; outline: none; }
.wheel .handle .hit { fill: transparent; }
.wheel .handle .knob { fill: var(--c); stroke: #f4f8fc; stroke-width: 2.5; }
.wheel .handle.end .knob { fill: #0b1424; stroke: var(--c); stroke-width: 3; }
.wheel .handle:focus-visible .knob, .wheel .handle.grabbed .knob { stroke: #ffffff;
  stroke-width: 4; filter: drop-shadow(0 0 6px var(--glow, #4aa3ff)); }
.wheel .handle:focus-visible .hit { stroke: var(--glow, #4aa3ff); stroke-width: 2;
  stroke-dasharray: 3 3; }
.wheel.dragging, .wheel.dragging * { cursor: grabbing; }
.wheel .core { fill: #0a1220; stroke: var(--rule); }
.wheel .core-name { fill: var(--ink); font-size: 16px; font-weight: 650; text-anchor: middle; }
.wheel .core-value { fill: var(--muted); font-size: 11px; text-anchor: middle;
  font-variant-numeric: tabular-nums; }
.wheel-makers { display: flex; flex-wrap: wrap; gap: .4rem; }
.wheel-makers button { display: inline-flex; align-items: center; gap: .45rem;
  min-height: 2.25rem; }
.wheel-makers button[aria-pressed=true] { border-color: var(--c); box-shadow: 0 0 12px var(--c);
  color: var(--ink); }
.wheel-makers .key { margin: 0; }
.wheel-readout { margin: .8rem 0 .5rem; font-variant-numeric: tabular-nums; color: var(--ink); }
.wheel-readout b { color: var(--lc); }
.wheel-swatches { list-style: none; margin: 0; padding: 0; display: grid; gap: .45rem;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 13rem), 1fr)); }
.wheel-swatches li { display: grid; grid-template-columns: 2.4rem minmax(0, 1fr); gap: .1rem .7rem;
  align-items: center; padding: .4rem .55rem; border-radius: 10px; background: #0a12204d;
  border: 1px solid #16233a; }
.wheel-swatches .chip { grid-row: span 2; width: 2.4rem; height: 2.4rem; border-radius: 8px;
  background: var(--c); box-shadow: 0 0 14px var(--c); }
.wheel-swatches .name { font-weight: 600; overflow-wrap: anywhere; }
.wheel-swatches code { color: var(--muted); font-size: 12px; }
.wheel-hint { color: var(--muted); font-size: 13px; margin: .7rem 0 0; }
@media (max-width: 760px) { .hue-wheel { grid-template-columns: minmax(0, 1fr); } }
@media (prefers-reduced-motion: reduce) { .wheel .maker { transition: none; } }
</style>"""

WHEEL_SCRIPT = """<script>
// The number fields follow the wheel in the page, so it starts once the page is read.
document.addEventListener("DOMContentLoaded", () => {
  const root = document.querySelector(".hue-wheel");
  const source = document.getElementById("hue-wheel-data");
  if (!root || !source) return;
  const data = JSON.parse(source.textContent);
  const NS = "http://www.w3.org/2000/svg";
  const svg = root.querySelector("svg.wheel");
  const side = root.querySelector(".wheel-makers");
  const readout = root.querySelector(".wheel-readout");
  const swatches = root.querySelector(".wheel-swatches");
  const { hues: H, lightness: L, chroma: CHROMA, subsegment: SUB } = data.wheel;
  const C = 200, RING = 172, LABEL = 197, HIT = 24;
  const LIMITS = { hue: [0, 359], width: [0, 360] };

  // The colour formula of model_colors.py, operation for operation, so the preview is what a
  // chart draws: Python's float % and its round-half-to-even "{:.0f}" included.
  const mod = (x, n) => { const r = x % n; return r < 0 ? r + n : r; };
  const whole = (x) => {
    const low = Math.floor(x);
    return x - low === 0.5 ? (low % 2 === 0 ? low : low + 1) : Math.round(x);
  };
  const segmentOf = (maker, sample) => {
    if (maker.key !== "other") return maker;
    const width = Math.min(maker.width, SUB);
    const reach = Math.max(maker.width - width, 0);
    const centre = maker.hue + reach * (sample.makerPlace - 0.5);
    return { hue: mod(Math.trunc(centre), 360), width };
  };
  const colorOf = (maker, sample) => {
    const segment = segmentOf(maker, sample);
    const hue = mod(segment.hue + segment.width * (((sample.place % H) + 0.5) / H - 0.5), 360);
    return `oklch(${L[sample.shade % L.length]} ${CHROMA} ${whole(hue)})`;
  };
  const tint = (hue, lightness = L[0], chroma = CHROMA) =>
    `oklch(${lightness} ${chroma} ${Math.round(mod(hue, 360))})`;

  const el = (name, attrs = {}, parent = null) => {
    const node = document.createElementNS(NS, name);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    if (parent) parent.appendChild(node);
    return node;
  };
  const at = (angle, radius) => {
    const a = (angle * Math.PI) / 180;
    return [C + radius * Math.sin(a), C - radius * Math.cos(a)];
  };
  const arc = (radius, from, to) => {
    const [x0, y0] = at(from, radius), [x1, y1] = at(to, radius);
    const large = to - from > 180 ? 1 : 0;
    return `M${x0.toFixed(2)} ${y0.toFixed(2)}A${radius} ${radius} 0 ${large} 1 ` +
      `${x1.toFixed(2)} ${y1.toFixed(2)}`;
  };
  const apart = (a, b) => { const d = mod(a - b, 360); return Math.min(d, 360 - d); };

  const ring = el("g", { class: "ring", "aria-hidden": "true" }, svg);
  for (let hue = 0; hue < 360; hue += 3)
    el("path", { d: arc(RING, hue, hue + 3.4), stroke: tint(hue + 1.5) }, ring);
  for (let hue = 0; hue < 360; hue += 30) {
    const [x0, y0] = at(hue, RING - 8), [x1, y1] = at(hue, RING + 8);
    el("line", { class: "tick", x1: x0, y1: y0, x2: x1, y2: y1 }, ring);
  }
  el("circle", { class: "core", cx: C, cy: C, r: 58 }, svg);
  const coreName = el("text", { class: "core-name", x: C, y: C - 4 }, svg);
  const coreValue = el("text", { class: "core-value", x: C, y: C + 16 }, svg);

  const field = (key, part) => document.getElementById(`f-modelColors-${key}-${part}`);
  const valid = (part, raw) => {
    if (!/^\\s*\\d+\\s*$/.test(raw)) return null;
    const value = Number(raw);
    return value >= LIMITS[part][0] && value <= LIMITS[part][1] ? value : null;
  };
  let current = null, drag = null;
  const makers = data.makers.map((maker) => {
    const m = { ...maker };
    for (const part of ["hue", "width"]) {
      const input = field(m.key, part);
      const typed = input ? valid(part, input.value) : null;
      if (typed !== null) m[part] = typed;
    }
    m.group = el("g", { class: "maker", "data-maker": m.key }, svg);
    el("circle", { class: "track", cx: C, cy: C, r: m.track, "aria-hidden": "true" }, m.group);
    m.band = el("g", { class: "band", "aria-hidden": "true" }, m.group);
    m.text = el("text", { class: "label", "dominant-baseline": "middle" }, m.group);
    m.text.textContent = m.label;
    m.handles = {};
    for (const [part, name] of [["hue", "hue"], ["start", "width, start"], ["end", "width, end"]]) {
      const handle = el("g", {
        class: part === "hue" ? "handle mid" : "handle end", role: "slider", tabindex: "0",
        "aria-label": `${maker.label} ${name}`, "data-part": part,
        "aria-valuemin": "0", "aria-valuemax": part === "hue" ? "359" : "360",
      }, m.group);
      el("circle", { class: "hit", r: HIT }, handle);
      el("circle", { class: "knob", r: part === "hue" ? 6.5 : 4.5 }, handle);
      handle.addEventListener("pointerdown", (event) => press(event, m, part));
      handle.addEventListener("keydown", (event) => key(event, m, part));
      handle.addEventListener("focus", () => { m.focused = true; select(m); });
      handle.addEventListener("blur", () => { m.focused = false; draw(); });
      m.handles[part] = handle;
    }
    m.band.addEventListener("pointerdown", (event) => press(event, m, "hue"));
    m.button = document.createElement("button");
    m.button.type = "button";
    m.button.innerHTML = '<span class="key"></span>';
    m.button.append(maker.label);
    m.button.addEventListener("click", () => m.handles.hue.focus());
    side.appendChild(m.button);
    return m;
  });

  const drawMaker = (m) => {
    const color = tint(m.hue);
    const light = tint(m.hue, 0.8, 0.13);
    m.group.style.setProperty("--c", color);
    m.group.style.setProperty("--lc", light);
    m.button.style.setProperty("--c", color);
    m.button.setAttribute("aria-pressed", String(m === current));
    m.button.querySelector(".key").style.setProperty("--c", color);
    m.band.replaceChildren();
    const from = m.hue - m.width / 2;
    if (m.width === 0) {
      const [x, y] = at(m.hue, m.track);
      el("circle", { cx: x, cy: y, r: 3.5, fill: color }, m.band);
    }
    const pieces = Math.ceil(m.width / 4);
    for (let i = 0; i < pieces; i++) {
      const a = from + (m.width * i) / pieces, b = from + (m.width * (i + 1)) / pieces;
      const overlap = i + 1 < pieces ? 0.4 : 0;
      el("path", { d: arc(m.track, a, b + overlap), stroke: tint((a + b) / 2) }, m.band);
    }
    // An end handle never sits on top of the middle one, even on a very narrow arc.
    const least = (Math.asin(Math.min(1, (HIT + 2) / m.track)) * 180) / Math.PI;
    const half = Math.max(m.width / 2, least);
    m.spots = {};
    const place = (handle, angle) => {
      const [x, y] = at(angle, m.track);
      m.spots[handle.dataset.part] = [x, y];
      handle.setAttribute("transform", `translate(${x.toFixed(2)} ${y.toFixed(2)})`);
    };
    place(m.handles.hue, m.hue);
    place(m.handles.start, m.hue - half);
    place(m.handles.end, m.hue + half);
    m.handles.hue.setAttribute("aria-valuenow", m.hue);
    m.handles.hue.setAttribute("aria-valuetext", `${m.hue} degrees`);
    for (const part of ["start", "end"]) {
      m.handles[part].setAttribute("aria-valuenow", m.width);
      m.handles[part].setAttribute("aria-valuetext", `${m.width} degrees wide`);
    }
    const [lx, ly] = at(m.hue, LABEL);
    const sin = Math.sin((m.hue * Math.PI) / 180);
    m.text.setAttribute("x", lx.toFixed(2));
    m.text.setAttribute("y", ly.toFixed(2));
    m.text.setAttribute("text-anchor", sin > 0.3 ? "start" : sin < -0.3 ? "end" : "middle");
    const editing = m === current && (drag !== null || Boolean(m.focused));
    m.group.classList.toggle("active", editing);
  };

  const draw = () => {
    for (const m of makers) drawMaker(m);
    const editing = makers.some((m) => m.group.classList.contains("active"));
    svg.classList.toggle("editing", editing);
    svg.classList.toggle("dragging", drag !== null);
    const m = current;
    const low = mod(m.hue - m.width / 2, 360), high = mod(m.hue + m.width / 2, 360);
    coreName.textContent = m.label;
    coreValue.textContent = `${m.hue}\\u00b0 \\u00b7 ${m.width}\\u00b0 wide`;
    readout.style.setProperty("--lc", tint(m.hue, 0.8, 0.13));
    readout.innerHTML = "";
    const name = document.createElement("b");
    name.textContent = m.label;
    readout.append(name, `: hue ${m.hue}\\u00b0, width ${m.width}\\u00b0 ` +
      `(from ${Math.round(low)}\\u00b0 to ${Math.round(high)}\\u00b0)`);
    swatches.replaceChildren();
    for (const sample of m.samples) {
      const color = colorOf(m, sample);
      const item = document.createElement("li");
      item.dataset.model = sample.name;
      item.dataset.color = color;
      item.style.setProperty("--c", color);
      item.innerHTML = '<span class="chip"></span><span class="name"></span><code></code>';
      item.querySelector(".name").textContent = sample.name;
      item.querySelector("code").textContent = color;
      swatches.appendChild(item);
    }
  };

  const select = (m) => { current = m; draw(); };
  const store = (m, part, value) => {
    if (m[part] === value) { draw(); return; }
    m[part] = value;
    const input = field(m.key, part);
    if (input) {
      input.value = String(value);
      input.dispatchEvent(new Event("input", { bubbles: true }));
    }
    draw();
  };
  const localOf = (event) => {
    const point = svg.createSVGPoint();
    point.x = event.clientX;
    point.y = event.clientY;
    return point.matrixTransform(svg.getScreenCTM().inverse());
  };
  const angleOf = (event) => {
    const local = localOf(event);
    return mod((Math.atan2(local.x - C, C - local.y) * 180) / Math.PI, 360);
  };
  // Neighbouring tracks lie closer than a finger is wide, so the touch areas of their handles
  // overlap: a press takes the handle nearest to it, not the one drawn on top.
  const nearest = (event, m, part) => {
    const local = localOf(event);
    let best = { m, part }, least = Infinity;
    for (const other of makers)
      for (const [name, [x, y]] of Object.entries(other.spots)) {
        const distance = Math.hypot(local.x - x, local.y - y);
        if (distance > HIT || distance >= least) continue;
        least = distance;
        best = { m: other, part: name };
      }
    return best;
  };
  function press(event, m, part) {
    const found = nearest(event, m, part);
    grab(event, found.m, found.part);
  }
  const signed = (a) => { const d = mod(a, 360); return d > 180 ? d - 360 : d; };

  function grab(event, m, part) {
    if (event.button !== undefined && event.button > 0) return;
    event.preventDefault();
    event.stopPropagation();
    const target = m.handles[part];
    target.setPointerCapture(event.pointerId);
    const angle = angleOf(event);
    drag = { m, part, id: event.pointerId, target,
      offset: part === "hue" ? signed(m.hue - angle) : m.width / 2 - apart(angle, m.hue) };
    target.classList.add("grabbed");
    select(m);
  }
  svg.addEventListener("pointermove", (event) => {
    if (!drag || event.pointerId !== drag.id) return;
    event.preventDefault();
    const angle = angleOf(event);
    if (drag.part === "hue") store(drag.m, "hue", mod(Math.round(angle + drag.offset), 360));
    else {
      const width = Math.round(2 * (apart(angle, drag.m.hue) + drag.offset));
      store(drag.m, "width", Math.min(360, Math.max(0, width)));
    }
  });
  const release = (event) => {
    if (!drag || event.pointerId !== drag.id) return;
    drag.target.classList.remove("grabbed");
    drag = null;
    draw();
  };
  svg.addEventListener("pointerup", release);
  svg.addEventListener("pointercancel", release);
  svg.addEventListener("lostpointercapture", release);

  function key(event, m, part) {
    const steps = { ArrowRight: 1, ArrowUp: 1, ArrowLeft: -1, ArrowDown: -1,
      PageUp: 10, PageDown: -10 };
    if (!(event.key in steps)) return;
    event.preventDefault();
    const step = steps[event.key] * (event.shiftKey ? 10 : 1);
    if (part === "hue") store(m, "hue", mod(m.hue + step, 360));
    else store(m, "width", Math.min(360, Math.max(0, m.width + step)));
  }

  for (const m of makers)
    for (const part of ["hue", "width"]) {
      const input = field(m.key, part);
      if (!input) continue;
      input.addEventListener("input", () => {
        const typed = valid(part, input.value);
        if (typed === null || typed === m[part]) return;
        m[part] = typed;
        select(m);
      });
    }

  current = makers[0];
  root.hidden = false;
  draw();
});
</script>"""
