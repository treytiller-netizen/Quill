"""The Quill Hub — a native window modeled on Wispr Flow's Hub.

Home (transcript feed + stat rail), Insights (usage, streak heatmap, voice
profile), Dictionary (view/add/remove terms). Rendered as branded HTML inside
a WKWebView; actions round-trip to Python over a script-message bridge.
"""

import json
import logging
import subprocess
import threading

import objc
from AppKit import (
    NSApp,
    NSBackingStoreBuffered,
    NSMakeRect,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
)
from Foundation import NSObject, NSURL
from WebKit import WKUserContentController, WKWebView, WKWebViewConfiguration

from . import config, history, stats
from .flowbar import _on_main

log = logging.getLogger("quill.window")

_hub = None
_brain = None


def init(brain) -> None:
    global _brain
    _brain = brain


def show_hub() -> None:
    """Open (or focus) the Hub. Safe to call from any thread."""

    def _show():
        global _hub
        if _hub is None:
            _hub = HubWindow()
        _hub.show()

    _on_main(_show)


class _Bridge(NSObject):
    """Receives window.webkit.messageHandlers.quill.postMessage(...) calls."""

    def initWithHub_(self, hub):
        self = objc.super(_Bridge, self).init()
        self.hub = hub
        return self

    def userContentController_didReceiveScriptMessage_(self, _ucc, message):
        try:
            body = dict(message.body())
            self.hub.handle_action(body.get("action"), body)
        except Exception:
            log.exception("Bridge message failed")


class HubWindow:
    def __init__(self) -> None:
        rect = NSMakeRect(0, 0, 1060, 700)
        style = (
            NSWindowStyleMaskTitled
            | NSWindowStyleMaskClosable
            | NSWindowStyleMaskMiniaturizable
            | NSWindowStyleMaskResizable
        )
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style, NSBackingStoreBuffered, False
        )
        self.window.setTitle_("Quill")
        self.window.setReleasedWhenClosed_(False)
        self.window.center()

        cfg = WKWebViewConfiguration.alloc().init()
        ucc = WKUserContentController.alloc().init()
        self._bridge = _Bridge.alloc().initWithHub_(self)
        ucc.addScriptMessageHandler_name_(self._bridge, "quill")
        cfg.setUserContentController_(ucc)
        self.webview = WKWebView.alloc().initWithFrame_configuration_(rect, cfg)
        self.window.setContentView_(self.webview)

    def show(self) -> None:
        self.reload()
        self.window.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)

    def reload(self, page: str = "home") -> None:
        payload = stats.hub_payload()
        payload["ai_available"] = bool(_brain and _brain.available)
        payload["initial"] = page
        html = _render(payload)
        self.webview.loadHTMLString_baseURL_(html, NSURL.fileURLWithPath_(str(config.CONFIG_DIR)))

    # --- bridge actions ---------------------------------------------------------

    def handle_action(self, action: str, body: dict) -> None:
        if action == "copy":
            subprocess.run(["pbcopy"], input=str(body.get("text", "")).encode("utf-8"))
        elif action == "addTerm":
            term = str(body.get("term", "")).strip()
            if term:
                config.CONFIG_DIR.mkdir(exist_ok=True)
                with open(config.DICTIONARY_FILE, "a") as f:
                    f.write(term + "\n")
                self.reload(page="dictionary")
        elif action == "removeTerm":
            term = str(body.get("term", "")).strip()
            lines = config.DICTIONARY_FILE.read_text().splitlines()
            kept = [
                ln for ln in lines
                if ln.strip() != term and not ln.strip().startswith(term + " ->")
            ]
            config.DICTIONARY_FILE.write_text("\n".join(kept) + "\n")
            self.reload(page="dictionary")
        elif action == "refreshVoice":
            threading.Thread(target=self._regen_voice, daemon=True).start()
        elif action == "clearHistory":
            stats.clear_history_data()
            self._refresh_app_menu()
            self.reload(page="storage")
        elif action == "resetVoice":
            stats.reset_voice_profile()
            self.reload(page="storage")
        elif action == "removeModels":
            keep = bool(body.get("keepCurrent", True))
            freed = stats.remove_models(keep_current=keep)
            log.info("Removed model downloads, freed %.2f GB", freed / 1e9)
            self.reload(page="storage")

    @staticmethod
    def _refresh_app_menu() -> None:
        """Keep the menu bar's Recent list and stats in sync after a clear."""
        try:
            import rumps

            app = getattr(rumps.App, "*app_instance", None)
            if app is not None:
                app.stats_item.title = f"This week: {history.words_this_week():,} words"
                app._refresh_recent_menu()
        except Exception:
            log.exception("Menu refresh after clear failed")

    def _regen_voice(self) -> None:
        if _brain is None:
            return
        profile = _brain.voice_profile(history.all_text())
        if profile:
            stats.save_voice_profile(profile)
        _on_main(lambda: self.reload(page="insights"))


def maybe_refresh_voice_profile() -> None:
    """Regenerate the voice profile in the background when it's due."""
    if _brain is None or not _brain.available or not stats.voice_profile_due():
        return

    def work():
        profile = _brain.voice_profile(history.all_text())
        if profile:
            stats.save_voice_profile(profile)

    threading.Thread(target=work, daemon=True).start()


# --- template -------------------------------------------------------------------

def _render(payload: dict) -> str:
    data = json.dumps(payload).replace("</", "<\\/")
    return _PAGE.replace("__PAYLOAD__", data)


_PAGE = r"""<!doctype html>
<html><head><meta charset="utf-8"><style>
:root {
  --paper:#f7f2ea; --card:#fffdf9; --line:#e8e1d4; --ink:#17171c;
  --muted:#8a8378; --accent:#ff7340; --teal:#2e6e64;
}
* { box-sizing:border-box; margin:0; font-family:-apple-system,"SF Pro Text",Helvetica,sans-serif; }
body { background:var(--paper); color:var(--ink); font-size:14px; line-height:1.5;
       display:flex; height:100vh; overflow:hidden; -webkit-user-select:none; }
button { font:inherit; cursor:pointer; }

/* sidebar */
#side { width:212px; padding:22px 14px; border-right:1px solid var(--line);
        display:flex; flex-direction:column; gap:4px; }
#logo { font-size:19px; font-weight:700; letter-spacing:-0.02em; padding:0 10px 16px; }
#logo .badge { font-size:10px; font-weight:600; background:var(--ink); color:var(--paper);
               border-radius:99px; padding:2px 8px; vertical-align:2px; margin-left:6px; }
.nav { text-align:left; background:none; border:none; border-radius:10px;
       padding:9px 12px; font-size:14px; color:var(--ink); }
.nav.on { background:#ece5d8; font-weight:600; }
.nav:hover { background:#f0e9dd; }
#side .spacer { flex:1; }
#local-card { background:#f3ecdf; border:1px solid var(--line); border-radius:12px;
              padding:12px; font-size:12px; color:var(--muted); }
#local-card b { color:var(--ink); display:block; margin-bottom:2px; }

/* main */
#main { flex:1; overflow-y:auto; padding:34px 40px; }
h1 { font-size:24px; letter-spacing:-0.02em; margin-bottom:22px; }
.grid { display:grid; gap:14px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:18px; }
.big { font-size:30px; font-weight:700; letter-spacing:-0.02em; }
.label { font-size:11px; letter-spacing:0.06em; text-transform:uppercase; color:var(--muted); margin-top:2px; }
.sub { color:var(--muted); font-size:12px; }

/* home layout */
#home-wrap { display:flex; gap:22px; align-items:flex-start; }
#feed { flex:1; min-width:0; }
#rail { width:230px; flex-shrink:0; position:sticky; top:0; }
.daylabel { font-size:11px; letter-spacing:0.08em; color:var(--muted); margin:20px 0 8px; }
.entry { background:var(--card); border:1px solid var(--line); border-radius:12px;
         padding:13px 15px; margin-bottom:9px; display:flex; gap:12px; }
.entry .time { color:var(--muted); font-size:12px; white-space:nowrap; padding-top:2px; }
.entry .txt { flex:1; white-space:pre-wrap; -webkit-user-select:text; }
.entry .meta { display:flex; flex-direction:column; align-items:flex-end; gap:6px; }
.chip { font-size:10px; background:#ece5d8; border-radius:99px; padding:2px 8px; color:var(--muted); white-space:nowrap; }
.chip.cmd { background:var(--accent); color:#fff; }
.copy { border:1px solid var(--line); background:none; border-radius:8px;
        padding:2px 9px; font-size:11px; color:var(--muted); }
.copy:hover { color:var(--ink); border-color:var(--ink); }
.railstat { padding:10px 0; border-bottom:1px solid var(--line); }
.railstat:last-child { border-bottom:none; }

/* insights */
#insights .grid { grid-template-columns:repeat(3,1fr); margin-bottom:14px; }
.bar { display:flex; align-items:center; gap:8px; margin:7px 0; font-size:12px; }
.bar .track { flex:1; height:14px; background:#efe8db; border-radius:7px; overflow:hidden; }
.bar .fill { height:100%; background:var(--teal); border-radius:7px; }
.bar .pct { width:34px; color:var(--muted); text-align:right; }
#heat { display:grid; grid-auto-flow:column; grid-template-rows:repeat(7,11px); gap:3px; margin-top:12px; }
#heat div { width:11px; height:11px; border-radius:3px; background:#eee7da; }
.voicecard { border-left:3px solid var(--accent); }
.voicecard h3 { font-family:Georgia,serif; font-size:22px; font-weight:600; margin-bottom:4px; }
.quote { font-family:Georgia,serif; font-style:italic; font-size:18px; }
.progress { height:6px; background:#efe8db; border-radius:3px; overflow:hidden; margin-top:8px; }
.progress div { height:100%; background:#8f7ae5; }
.btn { background:var(--ink); color:var(--paper); border:none; border-radius:10px; padding:8px 14px; font-size:13px; }
.btn:disabled { opacity:0.4; }

/* dictionary */
#dictadd { display:flex; gap:8px; margin-bottom:18px; }
#dictadd input { flex:1; padding:10px 14px; border:1px solid var(--line); border-radius:10px;
                 background:var(--card); font-size:14px; outline:none; }
#dictadd input:focus { border-color:var(--accent); }
.term { display:flex; align-items:center; gap:10px; background:var(--card);
        border:1px solid var(--line); border-radius:12px; padding:11px 15px; margin-bottom:8px; }
.term .t { flex:1; }
.term .arrow { color:var(--muted); }
.term .hits { font-size:11px; color:var(--muted); }
.term .rm { border:none; background:none; color:var(--muted); font-size:15px; }
.term .rm:hover { color:var(--accent); }
input, .txt { -webkit-user-select:text; }
</style></head><body>
<div id="side">
  <div id="logo">🪶 Quill<span class="badge">LOCAL</span></div>
  <button class="nav on" data-page="home">Home</button>
  <button class="nav" data-page="insights">Insights</button>
  <button class="nav" data-page="dictionary">Dictionary</button>
  <button class="nav" data-page="storage">Storage</button>
  <div class="spacer"></div>
  <div id="local-card"><b>∞ Unlimited</b>Runs entirely on your Mac. No word limits, no subscription.</div>
</div>
<div id="main"></div>
<script>
const P = __PAYLOAD__;
const send = (m) => window.webkit.messageHandlers.quill.postMessage(m);
const esc = (s) => s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
const fmtN = (n) => n >= 10000 ? (n/1000).toFixed(1) + "K" : n.toLocaleString();

function copyBtn(text) {
  return `<button class="copy" onclick='send({action:"copy",text:${JSON.stringify(text)}});this.textContent="Copied ✓";setTimeout(()=>this.textContent="Copy",1200)'>Copy</button>`;
}

function dayName(ts) {
  const d = new Date(ts*1000), today = new Date();
  const diff = Math.floor((new Date(today.toDateString()) - new Date(d.toDateString()))/86400000);
  if (diff === 0) return "TODAY";
  if (diff === 1) return "YESTERDAY";
  return d.toLocaleDateString(undefined,{month:"long",day:"numeric"}).toUpperCase();
}

function homePage() {
  let feed = "";
  if (!P.entries.length) feed = `<div class="card sub">Nothing yet — hold Right ⌥ and say something.</div>`;
  let lastDay = "";
  for (const e of P.entries) {
    const day = dayName(e.ts);
    if (day !== lastDay) { feed += `<div class="daylabel">${day}</div>`; lastDay = day; }
    const time = new Date(e.ts*1000).toLocaleTimeString(undefined,{hour:"numeric",minute:"2-digit"});
    feed += `<div class="entry"><div class="time">${time}</div>
      <div class="txt">${esc(e.text)}</div>
      <div class="meta"><span class="chip${e.mode==="command"?" cmd":""}">${e.mode==="command"?"command":esc(e.app||"")}</span>${copyBtn(e.text)}</div></div>`;
  }
  const v = P.voice;
  const voiceRail = v.profile
    ? `<b>${esc(v.profile.title)}</b><div class="sub">Next update in ${fmtN(v.words_until_update)} words</div>`
    : `<b>Your Voice Profile</b><div class="sub">${P.ai_available ? "Keep dictating for insights" : "Add an API key to unlock"}</div>`;
  return `<h1>Welcome back, ${P.user}</h1><div id="home-wrap">
    <div id="feed">${feed}</div>
    <div id="rail"><div class="card">
      <div class="railstat"><span class="big">${fmtN(P.totals.words)}</span> <span class="sub">total words</span></div>
      <div class="railstat"><span class="big">${P.totals.wpm ?? "—"}</span> <span class="sub">wpm</span></div>
      <div class="railstat"><span class="big">${P.streak.current}</span> <span class="sub">day streak</span></div>
      <div class="railstat">${voiceRail}
        <div class="progress"><div style="width:${Math.round(P.voice.progress*100)}%"></div></div></div>
    </div></div></div>`;
}

function heatmap() {
  const cells = [];
  const today = new Date(P.today);
  const start = new Date(today); start.setDate(start.getDate() - 181);
  while (start.getDay() !== 0) start.setDate(start.getDate() - 1);
  const max = Math.max(1, ...Object.values(P.by_day));
  const shades = ["#eee7da","#cfe3d8","#8ec4ae","#4d9579","#2e6e64"];
  for (let d = new Date(start); d <= today; d.setDate(d.getDate()+1)) {
    const key = d.toISOString().slice(0,10);
    const w = P.by_day[key] || 0;
    const lvl = w === 0 ? 0 : Math.min(4, 1 + Math.floor(3 * w / max));
    cells.push(`<div style="background:${shades[lvl]}" title="${key}: ${w} words"></div>`);
  }
  return cells.join("");
}

function insightsPage() {
  const appMax = Math.max(1, ...P.apps.map(a=>a[1]));
  const totalAppWords = Math.max(1, P.apps.reduce((s,a)=>s+a[1],0));
  const bars = P.apps.map(([app,w]) =>
    `<div class="bar"><span style="width:110px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(app)}</span>
     <div class="track"><div class="fill" style="width:${Math.round(100*w/appMax)}%"></div></div>
     <span class="pct">${Math.round(100*w/totalAppWords)}%</span></div>`).join("") || `<div class="sub">No data yet</div>`;
  const v = P.voice;
  const mu = P.most_used[0];
  const voice = v.profile ? `
    <div class="card voicecard" style="grid-column:span 2"><h3>${esc(v.profile.title)}</h3>
      <div class="label">Voice profile</div><p style="margin-top:8px">${esc(v.profile.description)}</p>
      <p class="sub" style="margin-top:6px">${esc(v.profile.style_note)}</p></div>
    <div class="card"><div class="quote">“${esc(v.profile.catchphrase)}”</div><div class="label">Catchphrase</div></div>`
    : `<div class="card" style="grid-column:span 3"><b>Your voice profile is brewing.</b>
       <p class="sub" style="margin:6px 0 10px">${P.ai_available
         ? "Quill asks Claude to study your dictations every " + fmtN(1000) + " words. " + fmtN(v.words_until_update) + " words to go — or generate one now."
         : "Add your Anthropic API key to ~/.quill/env to unlock AI voice insights."}</p>
       ${P.ai_available ? `<button class="btn" onclick='send({action:"refreshVoice"});this.disabled=true;this.textContent="Analyzing…"'>Generate now</button>` : ""}</div>`;
  return `<h1>Insights</h1><div id="insights">
    <div class="grid">
      <div class="card"><span class="big">${P.totals.wpm ?? "—"}</span><div class="label">Words per minute</div>
        <div class="sub" style="margin-top:8px">${P.minutes_spoken} min spoken all-time</div></div>
      <div class="card"><span class="big">${fmtN(P.fixes.total)}</span><div class="label">Fixes made by Quill</div>
        <div class="sub" style="margin-top:8px">${fmtN(P.fixes.words_corrected)} words corrected<br>${fmtN(P.fixes.dictionary_fixes)} dictionary hits</div></div>
      <div class="card"><span class="big">${fmtN(P.totals.words)}</span><div class="label">Total words dictated</div>
        <div class="sub" style="margin-top:8px">${fmtN(P.totals.week_words)} this week · ${P.essays >= 0.5 ? "you've written " + P.essays + " college essays!" : "just getting started"}</div></div>
    </div>
    <div class="grid" style="grid-template-columns:1fr 1fr">
      <div class="card"><b>App usage</b><div style="margin-top:10px">${bars}</div></div>
      <div class="card"><b>${P.streak.current} day streak</b>
        <span class="sub" style="float:right">LONGEST | ${P.streak.longest} DAYS</span>
        <div id="heat">${heatmap()}</div></div>
    </div>
    <h1 style="margin-top:28px;font-size:19px">Your Voice</h1>
    <div class="grid" style="grid-template-columns:repeat(3,1fr)">
      ${voice}
      ${mu ? `<div class="card"><div class="quote">“${esc(mu[0])}”</div><div class="label">Most used word</div><div class="sub" style="margin-top:6px">${mu[1]} times</div></div>` : ""}
      ${P.peak ? `<div class="card"><div class="quote">${esc(P.peak)}</div><div class="label">Your peak time</div></div>` : ""}
    </div></div>`;
}

function dictionaryPage() {
  const rows = P.dictionary.map(t => `<div class="term">
      <span class="t">${esc(t.term)}${t.replacement ? ` <span class="arrow">→</span> ${esc(t.replacement)}` : ""}</span>
      <span class="hits">${(P.dict_hits[t.term]||0)} uses</span>
      <button class="rm" title="Remove" onclick='send({action:"removeTerm",term:${JSON.stringify(t.term)}})'>✕</button>
    </div>`).join("") || `<div class="card sub">No terms yet.</div>`;
  return `<h1>Dictionary</h1>
    <p class="sub" style="margin:-14px 0 18px">Quill spells the way you do — names, jargon, shortcuts. Use "btw -> by the way" for replacements.</p>
    <div id="dictadd"><input id="newterm" placeholder="Add a word, name, or replacement…">
      <button class="btn" onclick='const i=document.getElementById("newterm"); if(i.value.trim()) send({action:"addTerm",term:i.value.trim()})'>Add</button></div>
    ${rows}`;
}

const fmtB = (b) => b >= 1e9 ? (b/1e9).toFixed(2)+" GB" : b >= 1e6 ? (b/1e6).toFixed(1)+" MB" : Math.max(1, Math.round(b/1e3))+" KB";

// WKWebView has no native confirm() without a UI delegate — two-click confirm.
function confirmThen(btn, action) {
  if (btn.dataset.armed) {
    btn.disabled = true; btn.textContent = "Working…";
    send(action);
  } else {
    btn.dataset.armed = "1"; btn.dataset.orig = btn.textContent;
    btn.textContent = "Click again to confirm";
    setTimeout(() => { if (!btn.disabled) { delete btn.dataset.armed; btn.textContent = btn.dataset.orig; } }, 3500);
  }
}

function storagePage() {
  const s = P.storage;
  const modelRows = s.models.map(m => `<div class="term">
      <span class="t">${esc(m.name)}${m.current ? ' <span class="chip">in use</span>' : ""}</span>
      <span class="hits">${fmtB(m.bytes)}</span>
    </div>`).join("") || `<div class="sub">No models downloaded.</div>`;
  const unused = s.models.filter(m => !m.current).reduce((a,m) => a+m.bytes, 0);
  return `<h1>Storage</h1>
    <p class="sub" style="margin:-14px 0 18px">Everything here is safe to clear — Quill regenerates or re-downloads what it needs. Reclaimable right now: <b>${fmtB(s.reclaimable)}</b>.</p>

    <div class="card" style="margin-bottom:14px"><b>Speech models</b>
      <p class="sub" style="margin:4px 0 12px">Downloaded Whisper models. Only the one marked "in use" is needed; removed models re-download automatically if ever needed again.</p>
      ${modelRows}
      <div style="display:flex; gap:8px; margin-top:12px">
        ${unused > 0 ? `<button class="btn" onclick='confirmThen(this, {action:"removeModels", keepCurrent:true})'>Remove unused models (${fmtB(unused)})</button>` : ""}
        <button class="copy" onclick='confirmThen(this, {action:"removeModels", keepCurrent:false})'>Remove all (re-downloads on next launch)</button>
      </div>
    </div>

    <div class="card" style="margin-bottom:14px"><b>Transcription history</b>
      <p class="sub" style="margin:4px 0 12px">${P.storage.history_count.toLocaleString()} transcripts · ${fmtB(s.history_bytes + s.legacy_bytes)}. Clearing also resets your stats, streak, and word counts.</p>
      <button class="btn" onclick='confirmThen(this, {action:"clearHistory"})'>Clear history</button>
    </div>

    <div class="card"><b>Voice profile</b>
      <p class="sub" style="margin:4px 0 12px">${fmtB(s.voice_bytes)}. Regenerates from your future dictations.</p>
      <button class="copy" onclick='confirmThen(this, {action:"resetVoice"})'>Reset voice profile</button>
    </div>`;
}

const pages = { home: homePage, insights: insightsPage, dictionary: dictionaryPage, storage: storagePage };
function nav(page) {
  document.querySelectorAll(".nav").forEach(b => b.classList.toggle("on", b.dataset.page === page));
  document.getElementById("main").innerHTML = pages[page]();
  const inp = document.getElementById("newterm");
  if (inp) inp.addEventListener("keydown", e => { if (e.key === "Enter" && inp.value.trim()) send({action:"addTerm",term:inp.value.trim()}); });
}
document.querySelectorAll(".nav").forEach(b => b.onclick = () => nav(b.dataset.page));
nav(P.initial || "home");
</script></body></html>
"""
