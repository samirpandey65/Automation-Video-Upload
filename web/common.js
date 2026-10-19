async function api(url, options={}) {
  const res = await fetch(url, {
    headers: {"Content-Type":"application/json", ...(options.headers||{})},
    ...options
  });
  const data = await res.json().catch(()=>({}));
  if (!res.ok) {
    let msg = data.detail || data.message || "Request failed";
    if (Array.isArray(msg)) {
      msg = msg.map(e => `${e.loc ? e.loc.filter(x => x !== 'body').join('.') + ': ' : ''}${e.msg}`).join('; ');
    } else if (typeof msg === 'object') {
      msg = JSON.stringify(msg);
    }
    throw new Error(msg);
  }
  return data;
}
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
  }[c]));
}
async function loadChannels(selectId=null) {
  const data = await api("/api/channels");
  if (selectId) {
    const el = document.getElementById(selectId);
    if (el) el.innerHTML = data.channels.map(c =>
      `<option value="${esc(c.channel_id)}">${esc(c.channel_name)}</option>`
    ).join("") || `<option value="">No channel connected</option>`;
  }
  return data.channels;
}
function setMessage(id, text, type="notice") {
  const el=document.getElementById(id);
  if(!el)return;
  el.className="notice "+type;
  el.textContent=text;
}


// Phase 19 — shared creator-control-center shell: dynamic time-aware greeting & live dual clock (IST & UTC)
(function initCreatorShell(){
  const path=window.location.pathname.replace(/\/$/,'')||'/';
  window.logout = async function() {
    try { await fetch('/api/auth/logout', { method: 'POST' }); } catch(e) {}
    window.location.href = '/login';
  };

  fetch('/api/auth/me').then(r => r.ok ? r.json() : null).then(d => {
    if (d && d.user) {
      if (d.user.role === 'admin') {
        document.body.classList.add('is-admin');
      } else {
        document.body.classList.remove('is-admin');
        document.querySelectorAll('.admin-only, [data-role="admin"]').forEach(el => el.remove());
      }
      const p = document.querySelector('.profile-copy');
      if (p) p.innerHTML = `<strong>${esc(d.user.username)}</strong><span>${esc(d.user.role === 'admin' ? 'Admin Workspace' : 'Creator Workspace')}</span>`;
      const av = document.querySelector('.profile-avatar');
      if (av) av.textContent = (d.user.username || 'S')[0].toUpperCase();
    } else {
      document.body.classList.remove('is-admin');
      document.querySelectorAll('.admin-only, [data-role="admin"]').forEach(el => el.remove());
    }
  }).catch(() => {
    document.body.classList.remove('is-admin');
    document.querySelectorAll('.admin-only, [data-role="admin"]').forEach(el => el.remove());
  });

  // Channels & Accounts is superseded by Connections
  document.querySelectorAll('.nav[href="/channels"], .nav[data-route="/channels"]').forEach(el => el.remove());

  document.querySelectorAll('.nav[data-route]').forEach(a=>{
    const route=a.dataset.route;
    if((route==='/'&&path==='/') || (route!=='/'&&path.startsWith(route))) a.classList.add('active');
    if ((path.startsWith('/facebook-dashboard') || path.startsWith('/instagram-dashboard') || path.startsWith('/meta-dashboard')) && (route === '/facebook-dashboard' || route === '/instagram-dashboard' || route === '/meta-dashboard')) {
      a.classList.add('active');
    }
  });
  const input=document.querySelector('.global-search input');
  if(input){ input.addEventListener('keydown',e=>{ if(e.key==='Enter' && input.value.trim()){ window.location.href='/library?search='+encodeURIComponent(input.value.trim()); } }); }

  const topbarRight = document.querySelector('.topbar-right');
  if (topbarRight && !document.querySelector('.btn-logout')) {
    const logoutBtn = document.createElement('button');
    logoutBtn.className = 'btn btn-secondary btn-sm btn-logout';
    logoutBtn.style.marginLeft = '8px';
    logoutBtn.style.padding = '6px 12px';
    logoutBtn.title = 'Sign out of local creator workspace';
    logoutBtn.textContent = 'Logout';
    logoutBtn.onclick = window.logout;
    topbarRight.appendChild(logoutBtn);
  }

  // Comprehensive YouTube growth tips, storytelling wisdom & creator quotes (56 curated deck)
  const BUILTIN_CREATOR_QUOTES = [
    // --- Core Workspace & Automation Mission ---
    "Manage your YouTube channels and automate your uploads",
    "Consistency transforms good channels into great channels 🚀",
    "Automation handles the routine so you can focus on storytelling 💡",
    "Every viral video started with a single upload — keep shipping 🌟",
    "Small daily uploads compound into massive long-term growth 📈",
    "AI-assisted metadata + scheduled uploads = creator superpower 🤖",
    "Your channels are synced, scheduled, and ready to publish ⏰",
    "Dream big, automate the routine, and create unforgettable content 💫",

    // --- YouTube CTR & Thumbnail Psychology ---
    "Thumbnails sell the click, titles sell the curiosity — never repeat the exact same text in both 🎯",
    "Follow the 3-element rule in thumbnails: Subject, Expression, Context. Eliminate visual noise 🖼️",
    "Test your thumbnail at 50x50 pixels — if the focal emotion isn't clear on mobile, simplify it 📱",
    "High CTR begins with contrast: make your subject pop against a darkened or blurred background ⚡",
    "Human eyes follow eyes: point your subject's gaze directly toward the focal point or action 👀",
    "Limit thumbnail color palettes to 2 dominant tones and 1 high-contrast punchy accent 🎨",
    "Show the moment right BEFORE the climax in your thumbnail to create an irresistible open loop 🎭",
    "The best thumbnails raise an urgent question that only clicking the video can answer 🔍",
    "Never mislead with clickbait — promise a thrilling payoff and validate it in the first 30 seconds 💡",
    "A 2% bump in CTR can 10x your impressions once YouTube's browse algorithm kicks in 🚀",

    // --- Title SEO & Search/Browse Discovery ---
    "Place your highest-value search keyword in the first 40 characters so mobile viewers never miss it 📌",
    "Search gets you found, but curiosity gets you clicked — balance search terms with emotional punch 🔍",
    "Use dynamic power verbs: 'Revealed', 'Exposed', 'Transformed', 'Mastered' spark instant action 💥",
    "Open a curiosity gap: reveal what happened without giving away the final punchline ❓",
    "Match title intent: Search titles solve problems; Browse titles trigger suspense and curiosity 🎯",
    "Analyze YouTube search autocomplete to discover the exact phrasing your audience searches for 📊",
    "Keep tags focused and relevant — 8 to 15 targeted phrases outperform 30 broad keywords every time 🏷️",
    "Specificity builds intrigue: 'How I Fixed It' vs 'The 3-Minute Secret That Fixed Everything' ✨",
    "Your video title is a promise — make sure the opening scene immediately confirms viewers are in the right place 🔑",
    "Combine emotional curiosity with search terms to dominate both YouTube Home and Search 📈",

    // --- Audience Retention & First 30-Second Hooks ---
    "Hook viewers in the first 30 seconds: skip lengthy intros and dive straight into the core conflict ⏱️",
    "Introduce a pattern interrupt every 7–10 seconds with sound effects, zoom cuts, or visual shifts 🔄",
    "Retention drops when pacing stalls — cut unnecessary pauses, trim breaths, and keep the story moving ⚡",
    "Start in media res: open with the most shocking or funny moment, then rewind to reveal how it happened 🎬",
    "High retention signals YouTube to push your video to broader audiences — hold attention to earn reach 🏆",
    "Audio quality makes or breaks retention: viewers forgive 720p video, but never tolerate bad audio 👂",
    "The first 5 seconds filter casual scrollers; the next 25 seconds turn them into hooked viewers 🪝",
    "Study your YouTube Studio retention graph: every steep dip is a lesson for your next edit 📉",
    "Give viewers a reason to watch until the final second — promise the best revelation at the very end 🎯",
    "Respect your viewer's time — deliver twice the value in half the runtime for exponential watch time ⏳",

    // --- Storytelling & Narrative Suspense (Chalaak Kahaniya & Fiction) ---
    "कहानी में दम हो तो दर्शक खुद खिंचे चले आते हैं 📖",
    "Every great story needs three pillars: high stakes, relatable conflict, and an unexpected twist 🎭",
    "Suspense is keeping an open question in the viewer's mind: 'आगे क्या होगा?' 🌪️",
    "Introduce character flaws early: flawless characters are boring, flawed characters build deep empathy 🧩",
    "Raise the stakes halfway through: make the obstacle harder and the consequences greater 🔥",
    "Show, don't tell: let actions, body language, and expressions convey emotion instead of plain narration 💡",
    "A satisfying story ending closes the main loop while leaving a lingering emotional resonance 🕊️",
    "Relatable villains make stronger stories: give antagonists a clear motive, not just evil intentions 👑",
    "Pacing is emotion: slow down for emotional depth, speed up for action and suspense ⏳",
    "Great storytelling turns one-time viewers into lifelong fans who wait eagerly for every upload 🌟",

    // --- Hindi & Hinglish Creator Wisdom ---
    "कंसिस्टेंसी ही सफलता की चाबी है — हर अपलोड आपकी पहचान मजबूत करता है! 🔥",
    "व्यूज की चिंता छोड़ो, कंटेंट की क्वालिटी पर ध्यान दो — रीच अपने आप आएगी 🌟",
    "Thumbnail par emotion dikhao, aur title mein curiosity chhodo! 🎯",
    "Storytelling ka asli fun emotions mein hai — dukh, hasi aur suspense! 🎭",
    "Har viral video ke peeche ek simple upload aur consistent practice hoti hai 🚀",
    "अपने दर्शकों की नब्ज पहचानो — वही बनाओ जो उनके दिल को छू जाए ❤️",
    "Intro लंबा मत खींचो — पहले 10 सेकंड में कहानी का हुक डाल दो! ⚡",
    "Shorts ho ya Long video, pehle frame se curiosity shuru honi chahiye! 💡",
    "मेहनत और सही तकनीक का मेल ही चैनल को ऊंचाइयों पर ले जाता है 🏔️",
    "रोजाना का एक छोटा कदम भी यूट्यूब पर बड़ा ब्रांड बना देता है 📈",
    "AI handles the routine metadata, but your heart and creativity power the channel 💫",
    "The best time to upload was yesterday; the next best time is right now — keep shipping! 🚀"
  ];

  const SESSION_DECK_KEY = 'yla_quote_deck';
  const SESSION_LAST_KEY = 'yla_last_quote';
  const LOCAL_DYNAMIC_KEY = 'yla_dynamic_quotes';

  function getDynamicQuotes() {
    try {
      const raw = localStorage.getItem(LOCAL_DYNAMIC_KEY);
      return raw ? JSON.parse(raw) : [];
    } catch(e) {
      return [];
    }
  }

  function getAllQuotesPool() {
    const dynamic = getDynamicQuotes();
    const set = new Set(BUILTIN_CREATOR_QUOTES);
    if (Array.isArray(dynamic)) {
      dynamic.forEach(q => { if (typeof q === 'string' && q.trim()) set.add(q.trim()); });
    }
    return Array.from(set);
  }

  function shuffleArray(arr) {
    const a = [...arr];
    for (let i = a.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      const tmp = a[i]; a[i] = a[j]; a[j] = tmp;
    }
    return a;
  }

  function getNextQuote() {
    let deck = [];
    try {
      const raw = sessionStorage.getItem(SESSION_DECK_KEY);
      if (raw) deck = JSON.parse(raw);
    } catch(e) {
      deck = [];
    }

    const lastQuote = sessionStorage.getItem(SESSION_LAST_KEY) || '';

    if (!Array.isArray(deck) || deck.length === 0) {
      deck = shuffleArray(getAllQuotesPool());
      if (deck.length > 1 && deck[0] === lastQuote) {
        const swapIdx = deck.length - 1;
        const tmp = deck[0]; deck[0] = deck[swapIdx]; deck[swapIdx] = tmp;
      }
    }

    const quote = deck.shift() || BUILTIN_CREATOR_QUOTES[0];
    try {
      sessionStorage.setItem(SESSION_DECK_KEY, JSON.stringify(deck));
      sessionStorage.setItem(SESSION_LAST_KEY, quote);
    } catch(e) {}
    return quote;
  }

  let quoteTimer = null;

  function slideNextQuote(isManual = false){
    const el = document.querySelector('.top-subtitle');
    if(!el) return;
    el.style.opacity = '0';
    el.style.transform = 'translateY(-6px)';
    setTimeout(()=>{
      const nextQ = getNextQuote();
      el.textContent = nextQ;
      el.style.transform = 'translateY(6px)';
      void el.offsetWidth;
      el.style.opacity = '1';
      el.style.transform = 'translateY(0)';
    }, 320);

    if (isManual && quoteTimer) {
      clearInterval(quoteTimer);
      quoteTimer = setInterval(() => slideNextQuote(false), 60000);
    }
  }

  async function fetchFreshGeminiQuotes() {
    try {
      const lastFetch = Number(sessionStorage.getItem('yla_gemini_quotes_fetch') || 0);
      const now = Date.now();
      if (now - lastFetch < 15 * 60 * 1000) return;
      sessionStorage.setItem('yla_gemini_quotes_fetch', String(now));

      const res = await fetch('/api/quotes/fresh');
      if (!res.ok) return;
      const data = await res.json();
      if (data && Array.isArray(data.quotes) && data.quotes.length > 0) {
        const stored = getDynamicQuotes();
        const existingSet = new Set([...BUILTIN_CREATOR_QUOTES, ...stored]);
        const newOnes = data.quotes.filter(q => typeof q === 'string' && q.trim().length > 10 && !existingSet.has(q.trim()));
        if (newOnes.length > 0) {
          const updated = [...stored, ...newOnes];
          localStorage.setItem(LOCAL_DYNAMIC_KEY, JSON.stringify(updated));
          try {
            const rawDeck = sessionStorage.getItem(SESSION_DECK_KEY);
            const activeDeck = rawDeck ? JSON.parse(rawDeck) : [];
            sessionStorage.setItem(SESSION_DECK_KEY, JSON.stringify([...activeDeck, ...newOnes]));
          } catch(e) {}
        }
      }
    } catch(e) {}
  }

  const subEl = document.querySelector('.top-subtitle');
  if(subEl){
    let currentQuote = sessionStorage.getItem(SESSION_LAST_KEY);
    if (!currentQuote) {
      currentQuote = getNextQuote();
    }
    subEl.textContent = currentQuote;
    subEl.title = 'Click to advance tip · Auto-rotates every 1 min · Non-repeating deck of 50+ tips';
    subEl.style.cursor = 'pointer';
    subEl.addEventListener('click', () => slideNextQuote(true));
  }
  quoteTimer = setInterval(() => slideNextQuote(false), 60000);
  setTimeout(fetchFreshGeminiQuotes, 2500);

  function updateGreeting(){
    const greetingEl=document.querySelector('.top-greeting');
    if(!greetingEl) return;

    const now=new Date();
    let hour;
    try {
      hour=Number(new Intl.DateTimeFormat('en-IN',{timeZone:'Asia/Kolkata',hour:'numeric',hour12:false}).format(now));
    } catch(e) {
      hour=now.getHours();
    }

    let salutation='Good Morning', icon='👋';
    if(hour >= 5 && hour < 12) {
      salutation='Good Morning'; icon='👋';
    } else if(hour >= 12 && hour < 17) {
      salutation='Good Afternoon'; icon='👋';
    } else if(hour >= 17 && hour < 21) {
      salutation='Good Evening'; icon='👋';
    } else {
      salutation='Good Night'; icon='🌙';
    }

    greetingEl.innerHTML=`${salutation}, Samir! <span>${icon}</span>`;
  }

  function renderClock(){
    let clockEl=document.querySelector('.topbar-clock');
    if(!clockEl){
      const topbarRight=document.querySelector('.topbar-right');
      if(!topbarRight) return;
      clockEl=document.createElement('div');
      clockEl.className='topbar-clock';
      clockEl.title='Live System & Upload Clocks · Indian Standard Time (IST) & Universal Coordinated Time (UTC)';
      clockEl.innerHTML=`
        <div class="clock-item ist">
          <div class="clock-line"><span class="clock-dot live"></span><strong id="tbIstTime">--:--:--</strong><span class="clock-zone">IST</span></div>
          <div class="clock-sub" id="tbIstDate">-- --- ----</div>
        </div>
        <div class="clock-divider"></div>
        <div class="clock-item utc">
          <div class="clock-line"><strong id="tbUtcTime">--:--:--</strong><span class="clock-zone">UTC</span></div>
          <div class="clock-sub" id="tbUtcDate">-- --- ----</div>
        </div>
      `;
      topbarRight.insertBefore(clockEl, topbarRight.firstChild);
    }

    const now=new Date();
    try {
      const istTimeStr=new Intl.DateTimeFormat('en-IN',{timeZone:'Asia/Kolkata',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(now);
      const istDateStr=new Intl.DateTimeFormat('en-IN',{timeZone:'Asia/Kolkata',weekday:'short',day:'2-digit',month:'short',year:'numeric'}).format(now);
      const tEl=document.getElementById('tbIstTime'), dEl=document.getElementById('tbIstDate');
      if(tEl) tEl.textContent=istTimeStr;
      if(dEl) dEl.textContent=istDateStr;
    } catch(e) {
      const istOffset=5.5*60*60*1000;
      const istNow=new Date(now.getTime()+(now.getTimezoneOffset()*60000)+istOffset);
      const tEl=document.getElementById('tbIstTime'), dEl=document.getElementById('tbIstDate');
      if(tEl) tEl.textContent=istNow.toTimeString().slice(0,8);
      if(dEl) dEl.textContent=istNow.toDateString();
    }

    try {
      const utcTimeStr=new Intl.DateTimeFormat('en-GB',{timeZone:'UTC',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(now);
      const utcDateStr=new Intl.DateTimeFormat('en-GB',{timeZone:'UTC',day:'2-digit',month:'short',year:'numeric'}).format(now);
      const tEl=document.getElementById('tbUtcTime'), dEl=document.getElementById('tbUtcDate');
      if(tEl) tEl.textContent=utcTimeStr;
      if(dEl) dEl.textContent=utcDateStr;
    } catch(e) {
      const tEl=document.getElementById('tbUtcTime'), dEl=document.getElementById('tbUtcDate');
      if(tEl) tEl.textContent=now.toISOString().slice(11,19);
      if(dEl) dEl.textContent=now.toISOString().slice(0,10);
    }
  }

  updateGreeting();
  renderClock();
  setInterval(()=>{
    renderClock();
    updateGreeting();
  }, 1000);
})();

/* ================================================================
   PLATFORM LOGOS, SMART ACCOUNT NAMES & VIDEO LINKS SYSTEM
   ================================================================ */

// High quality crisp platform SVG logos
const PLATFORM_SVGS = {
  youtube: `<svg class="plat-svg" width="16" height="16" viewBox="0 0 24 24" fill="#ff0000" style="vertical-align:middle;flex-shrink:0"><path d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z"/></svg>`,
  facebook: `<svg class="plat-svg" width="16" height="16" viewBox="0 0 24 24" fill="#1877f2" style="vertical-align:middle;flex-shrink:0"><path d="M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z"/></svg>`,
  instagram: `<svg class="plat-svg" width="16" height="16" viewBox="0 0 24 24" style="vertical-align:middle;flex-shrink:0"><radialGradient id="ig-rg-global" cx="20%" cy="110%" r="130%"><stop offset="0%" stop-color="#ffd521"/><stop offset="20%" stop-color="#f50000"/><stop offset="50%" stop-color="#b900b4"/><stop offset="100%" stop-color="#c13584"/></radialGradient><path fill="url(#ig-rg-global)" d="M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 4.919.058 1.265.069 1.645.069 4.849 0 3.205-.012 3.584-.069 4.849-.149 3.225-1.664 4.771-4.919 4.919-1.266.058-1.644.07-4.85.07-3.204 0-3.584-.012-4.849-.07-3.26-.149-4.771-1.699-4.919-4.92-.058-1.265-.07-1.644-.07-4.849 0-3.204.013-3.583.07-4.849.149-3.227 1.664-4.771 4.919-4.919 1.266-.057 1.645-.069 4.849-.069zm0-2.163c-3.259 0-3.667.014-4.947.072-4.358.2-6.78 2.618-6.98 6.98-.059 1.281-.073 1.689-.073 4.948 0 3.259.014 3.668.072 4.948.2 4.358 2.618 6.78 6.98 6.98 1.281.058 1.689.072 4.948.072 3.259 0 3.668-.014 4.948-.072 4.354-.2 6.782-2.618 6.979-6.98.059-1.28.073-1.689.073-4.948 0-3.259-.014-3.667-.072-4.947-.196-4.354-2.617-6.78-6.979-6.98-1.281-.059-1.69-.073-4.949-.073zm0 5.838a6.162 6.162 0 1 0 0 12.324 6.162 6.162 0 0 0 0-12.324zM12 16a4 4 0 1 1 0-8 4 4 0 0 1 0 8zm6.406-11.845a1.44 1.44 0 1 0 0 2.881 1.44 1.44 0 0 0 0-2.881z"/></svg>`
};

function getPlatformSvg(platform) {
  const p = String(platform || '').toLowerCase();
  if (p.includes('youtube') || p === 'yt') return PLATFORM_SVGS.youtube;
  if (p.includes('facebook') || p === 'fb') return PLATFORM_SVGS.facebook;
  if (p.includes('instagram') || p === 'ig') return PLATFORM_SVGS.instagram;
  return '🌐';
}

function normalizePlatformAccountName(n) {
  return String(n || '')
    .toLowerCase()
    .replace(/[@_.\s\-\/]/g, '')
    .trim();
}

/**
 * Compare all account names in an array.
 * Returns true if all non-empty names match each other (e.g. all "Samir Pandey").
 */
function checkAllAccountNamesSame(nameList) {
  const list = (nameList || []).map(normalizePlatformAccountName).filter(Boolean);
  if (list.length <= 1) return true;
  const first = list[0];
  return list.every(n => n === first);
}

/**
 * Renders a platform badge / link WITHOUT video context (Dashboard channel card, Connections, Channels).
 * Clicking it opens the account profile URL.
 * If all account names are same: logo only!
 * If different: logo + platform account name!
 */
function renderAccountPlatformBadge({ platform, accountName, accountUrl, allSame = false, extraClasses = '' }) {
  const svg = getPlatformSvg(platform);
  const platKey = platform.toLowerCase().includes('youtube') ? 'yt' : (platform.toLowerCase().includes('facebook') ? 'fb' : 'ig');
  const safeName = esc(accountName || '');
  const url = accountUrl || '#';
  const showText = !allSame && safeName;
  const labelHtml = showText ? `<span>${safeName}</span>` : '';
  const iconOnlyClass = showText ? '' : 'plat-badge-icon-only';
  const tooltip = `${platform.toUpperCase()}: ${safeName}`;

  return `<a href="${esc(url)}" target="_blank" rel="noopener" class="plat-badge-link ${platKey} ${iconOnlyClass} ${extraClasses}" title="${esc(tooltip)}" onclick="event.stopPropagation();">
    ${svg}
    ${labelHtml}
    <span class="plat-badge-arrow">↗</span>
  </a>`;
}

/**
 * Renders a group of platform badges for an account or channel card (WITHOUT video).
 */
function renderAccountPlatformBadgesGroup(badges) {
  if (!badges || !badges.length) return '';
  const names = badges.map(b => b.name);
  const allSame = checkAllAccountNamesSame(names);
  return badges.map(b => renderAccountPlatformBadge({
    platform: b.platform,
    accountName: b.name,
    accountUrl: b.url,
    allSame: allSame
  })).join(' ');
}

/**
 * Renders platform badges WITH video context (Video Library, Dashboard video row, Analytics matrix).
 * Clicking it opens that published video URL on that platform!
 * If all platform account names are same: logo only (linking to the video).
 * If a platform account name is different: logo + account name (linking to the video).
 */
function renderVideoPlatformLinks(v, channelMeta = {}) {
  const cName = channelMeta.channelName || v.channel_name || '';
  const fbName = channelMeta.fbPageName || '';
  const igName = channelMeta.igUsername || '';
  const allSame = channelMeta.allSame != null ? channelMeta.allSame : checkAllAccountNamesSame([cName, fbName, igName]);

  const yId = v.youtube_video_id || '';
  const fbId = v.facebook_video_id || '';
  const fbrId = v.facebook_reel_id || '';
  const igId = v.instagram_media_id || '';

  let pubList = [];
  try {
    pubList = typeof v.platforms_published === 'string' ? JSON.parse(v.platforms_published || '[]') : (v.platforms_published || []);
    if (!Array.isArray(pubList)) pubList = [];
  } catch(e) { pubList = []; }

  const badges = [];

  // YouTube
  if (yId || pubList.includes('youtube')) {
    const yUrl = v.youtube_url || (yId ? `https://www.youtube.com/watch?v=${encodeURIComponent(yId)}` : '');
    const ytLabel = (!allSame && cName) ? `<span>${esc(cName)}</span>` : '';
    const iconClass = ytLabel ? '' : 'plat-badge-icon-only';
    if (yUrl) {
      badges.push(`<a href="${yUrl}" target="_blank" rel="noopener" class="plat-badge-link yt ${iconClass}" title="Watch video on YouTube (${esc(cName || 'YouTube')})">${PLATFORM_SVGS.youtube} ${ytLabel} <span class="plat-badge-arrow">↗</span></a>`);
    } else {
      badges.push(`<span class="plat-badge-link yt ${iconClass}" title="Published on YouTube">${PLATFORM_SVGS.youtube} ${ytLabel}</span>`);
    }
  }

  // Facebook
  if (fbId || fbrId || pubList.includes('facebook_video') || pubList.includes('facebook_reel')) {
    const fbUrl = v.facebook_url || (fbId ? `https://www.facebook.com/watch/?v=${encodeURIComponent(fbId)}` : `https://www.facebook.com/reel/${encodeURIComponent(fbrId)}`);
    const fbLabel = (!allSame && fbName) ? `<span>${esc(fbName)}</span>` : '';
    const iconClass = fbLabel ? '' : 'plat-badge-icon-only';
    badges.push(`<a href="${fbUrl}" target="_blank" rel="noopener" class="plat-badge-link fb ${iconClass}" title="Watch video on Facebook (${esc(fbName || 'Facebook')})">${PLATFORM_SVGS.facebook} ${fbLabel} <span class="plat-badge-arrow">↗</span></a>`);
  } else if (yId) {
    const fbShareUrl = `https://www.facebook.com/sharer/sharer.php?u=${encodeURIComponent('https://www.youtube.com/watch?v=' + yId)}`;
    const fbLabel = (!allSame && fbName) ? `<span>${esc(fbName)}</span>` : '';
    badges.push(`<a href="${fbShareUrl}" target="_blank" rel="noopener" class="plat-badge-link fb plat-badge-icon-only" title="Share video to Facebook (${esc(fbName || 'Facebook')})">${PLATFORM_SVGS.facebook} ${fbLabel} <span class="plat-badge-arrow">↗</span></a>`);
  }

  // Instagram
  if (igId || pubList.includes('instagram_reel')) {
    const igUrl = v.instagram_url || (igId ? `https://www.instagram.com/reel/${encodeURIComponent(igId)}/` : `https://www.instagram.com/`);
    const igLabel = (!allSame && igName) ? `<span>@${esc(igName.replace(/^@/, ''))}</span>` : '';
    const iconClass = igLabel ? '' : 'plat-badge-icon-only';
    badges.push(`<a href="${igUrl}" target="_blank" rel="noopener" class="plat-badge-link ig ${iconClass}" title="Watch reel on Instagram (${esc(igName || 'Instagram')})">${PLATFORM_SVGS.instagram} ${igLabel} <span class="plat-badge-arrow">↗</span></a>`);
  } else if (yId && v.id) {
    const igLabel = (!allSame && igName) ? `<span>@${esc(igName.replace(/^@/, ''))}</span>` : '';
    badges.push(`<a href="javascript:void(0)" onclick="openMetaPublishModal(${v.id})" class="plat-badge-link ig plat-badge-icon-only" title="Publish Reel to Instagram (${esc(igName || 'Instagram')})">${PLATFORM_SVGS.instagram} ${igLabel} <span class="plat-badge-arrow">+</span></a>`);
  }

  return badges.length ? `<div style="display:flex;gap:5px;flex-wrap:wrap;align-items:center">${badges.join('')}</div>` : `<span class="muted tiny">Pending all</span>`;
}

// Inject styling for platform badges
(function injectPlatformBadgeStyles() {
  if (document.getElementById('platformBadgeStyles')) return;
  const style = document.createElement('style');
  style.id = 'platformBadgeStyles';
  style.textContent = `
    .plat-badge-link {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      padding: 3px 8px;
      border-radius: 6px;
      font-size: 11px;
      font-weight: 700;
      text-decoration: none !important;
      transition: all 0.16s ease;
      white-space: nowrap;
      cursor: pointer;
      line-height: 1.3;
    }
    .plat-badge-link:hover {
      transform: translateY(-1px);
      box-shadow: 0 3px 8px rgba(0,0,0,0.12);
      text-decoration: none !important;
      filter: brightness(0.96);
    }
    .plat-badge-link.yt {
      background: #fee2e2;
      color: #b91c1c;
      border: 1px solid #fca5a5;
    }
    .plat-badge-link.fb {
      background: #dbeafe;
      color: #1d4ed8;
      border: 1px solid #bfdbfe;
    }
    .plat-badge-link.ig {
      background: #fce7f3;
      color: #be185d;
      border: 1px solid #fbcfe8;
    }
    .plat-badge-link.plat-badge-icon-only {
      padding: 3px 6px;
    }
    .plat-badge-arrow {
      font-size: 9px;
      opacity: 0.65;
      margin-left: 1px;
    }
    .plat-svg {
      width: 15px;
      height: 15px;
      flex-shrink: 0;
      vertical-align: middle;
      display: inline-block;
    }
  `;
  document.head.appendChild(style);
})();
