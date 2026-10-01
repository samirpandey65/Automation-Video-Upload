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
      const p = document.querySelector('.profile-copy');
      if (p) p.innerHTML = `<strong>${esc(d.user.username)}</strong><span>${esc(d.user.role === 'admin' ? 'Admin Workspace' : 'Creator Workspace')}</span>`;
      const av = document.querySelector('.profile-avatar');
      if (av) av.textContent = (d.user.username || 'S')[0].toUpperCase();
    }
  }).catch(() => {});

  document.querySelectorAll('.nav[data-route]').forEach(a=>{
    const route=a.dataset.route;
    if((route==='/'&&path==='/') || (route!=='/'&&path.startsWith(route))) a.classList.add('active');
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
