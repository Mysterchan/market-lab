(() => {
  const root = document.documentElement;
  const theme = document.querySelector('[data-theme-toggle]');
  const setTheme = (value) => {
    root.dataset.theme = value;
    theme.textContent = value === 'dark' ? 'Light mode' : 'Dark mode';
    theme.setAttribute('aria-label', `Switch to ${value === 'dark' ? 'light' : 'dark'} appearance`);
  };
  try { setTheme(localStorage.getItem('market-lab-theme') === 'dark' ? 'dark' : 'light'); } catch { setTheme('light'); }
  theme.addEventListener('click', () => {
    const value = root.dataset.theme === 'dark' ? 'light' : 'dark';
    setTheme(value);
    try { localStorage.setItem('market-lab-theme', value); } catch {}
  });
  const menu = document.querySelector('[data-menu-toggle]');
  const sidebar = document.querySelector('.sidebar');
  menu.addEventListener('click', () => {
    const open = sidebar.dataset.open !== 'true';
    sidebar.dataset.open = String(open);
    menu.setAttribute('aria-expanded', String(open));
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') { sidebar.dataset.open = 'false'; menu.setAttribute('aria-expanded', 'false'); }
  });
  document.querySelectorAll('[data-seek]').forEach((button) => button.addEventListener('click', () => {
    const video = document.querySelector('#demo-video');
    video.currentTime = Number(button.dataset.seek);
    video.play().catch(() => {});
  }));
  document.querySelectorAll('[data-watch]').forEach((link) => link.addEventListener('click', () => {
    const video = document.querySelector('#demo-video');
    video.focus({ preventScroll: true });
    video.play().catch(() => {});
  }));
  const stage = document.querySelector('.product-stage');
  const stageMotion = matchMedia('(hover: hover) and (pointer: fine) and (prefers-reduced-motion: no-preference)');
  if (stage) {
    let lightFrame = 0;
    let pointerX = 0;
    stage.addEventListener('pointermove', (event) => {
      if (!stageMotion.matches) return;
      pointerX = event.clientX;
      if (lightFrame) return;
      lightFrame = requestAnimationFrame(() => {
        const bounds = stage.getBoundingClientRect();
        const position = Math.max(0, Math.min(100, (pointerX - bounds.left) / bounds.width * 100));
        stage.style.setProperty('--stage-light-x', `${position}%`);
        lightFrame = 0;
      });
    });
    const resetLight = () => {
      cancelAnimationFrame(lightFrame);
      lightFrame = 0;
      stage.style.removeProperty('--stage-light-x');
    };
    stage.addEventListener('pointerleave', resetLight);
    stageMotion.addEventListener('change', resetLight);
  }
  // Light syntax colouring for Python and shell examples; the text itself is unchanged.
  const escapeHtml = (text) => text.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[c]);
  const grammars = {
    python: [
      ['c', /#[^\n]*/y],
      ['s', /("""[\s\S]*?"""|'''[\s\S]*?'''|[rbf]?"(?:\\.|[^"\\\n])*"|[rbf]?'(?:\\.|[^'\\\n])*')/y],
      ['k', /\b(?:def|class|return|import|from|as|if|elif|else|for|while|in|not|and|or|with|try|except|finally|raise|None|True|False|lambda|yield|pass|break|continue|async|await|is)\b/y],
      ['n', /\b\d[\d_]*(?:\.\d+)?(?:e[+-]?\d+)?\b/y],
      ['f', /\b[A-Za-z_]\w*(?=\()/y],
    ],
    bash: [
      ['c', /#[^\n]*/y],
      ['s', /("(?:\\.|[^"\\])*"|'[^']*')/y],
      ['k', /(?<=^|\n|&&\s|\|\s)\s*[a-z][\w.-]*/y],
      ['n', /(?<=\s)--?[A-Za-z][\w-]*/y],
    ],
  };
  grammars.py = grammars.python;
  grammars.sh = grammars.shell = grammars.zsh = grammars.bash;
  document.querySelectorAll('.article pre > code[class*="language-"]').forEach((code) => {
    const language = code.className.match(/language-(\w+)/)?.[1];
    code.parentElement.dataset.language = language;
    const grammar = grammars[language];
    if (!grammar) return;
    const text = code.textContent;
    let html = '', plain = '', index = 0;
    while (index < text.length) {
      let matched = null;
      for (const [kind, pattern] of grammar) {
        pattern.lastIndex = index;
        const found = pattern.exec(text);
        if (found && found[0].length) { matched = [kind, found[0]]; break; }
      }
      if (matched) {
        html += escapeHtml(plain) + `<span class="tok-${matched[0]}">${escapeHtml(matched[1])}</span>`;
        plain = ''; index += matched[1].length;
      } else {
        // Copy a whole identifier at once so keywords are only found at word starts.
        const word = /[A-Za-z_]\w*|./ys; word.lastIndex = index;
        const piece = word.exec(text)[0];
        plain += piece; index += piece.length;
      }
    }
    code.innerHTML = html + escapeHtml(plain);
  });
  // Mark the section being read in the "On this page" list.
  const tocLinks = new Map([...document.querySelectorAll('.toc a[href^="#"]')].map((a) => [a.hash.slice(1), a]));
  const headings = [...document.querySelectorAll('.article h2[id], .article h3[id]')].filter((h) => tocLinks.has(h.id));
  if (headings.length && 'IntersectionObserver' in window) {
    const visible = new Set();
    const mark = () => {
      const current = headings.find((h) => visible.has(h)) ||
        [...headings].reverse().find((h) => h.getBoundingClientRect().top < 120) || headings[0];
      tocLinks.forEach((a) => a.removeAttribute('aria-current'));
      tocLinks.get(current.id)?.setAttribute('aria-current', 'true');
    };
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((e) => (e.isIntersecting ? visible.add(e.target) : visible.delete(e.target)));
      mark();
    }, { rootMargin: '-90px 0px -55% 0px' });
    headings.forEach((h) => observer.observe(h));
  }
  document.querySelectorAll('.article pre').forEach((pre) => {
    const button = document.createElement('button');
    button.className = 'copy-code'; button.textContent = 'Copy'; button.type = 'button';
    button.setAttribute('aria-label', 'Copy code example');
    const code = pre.querySelector('code')?.textContent || pre.textContent;
    button.addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(code); button.textContent = 'Copied'; }
      catch { button.textContent = 'Copy unavailable'; }
      setTimeout(() => { button.textContent = 'Copy'; }, 2000);
    });
    pre.prepend(button);
  });
  const dialog = document.querySelector('.search-dialog');
  const input = dialog.querySelector('input');
  const results = dialog.querySelector('.search-results');
  const status = dialog.querySelector('.search-status');
  let previousFocus;
  const base = document.body.dataset.base;
  let index;
  const render = async () => {
    results.replaceChildren();
    status.textContent = '';
    const query = input.value.trim().toLowerCase();
    if (!query) { const p = document.createElement('p'); p.textContent = 'Search topics such as “replay”, “Python” or “account”.'; results.append(p); return; }
    status.textContent = 'Searching documentation…';
    try { index ||= await fetch(`${base}search-index.json`).then((response) => { if (!response.ok) throw Error(); return response.json(); }); }
    catch { if (!dialog.open) return; status.textContent = 'Search unavailable.'; const p = document.createElement('p'); p.textContent = 'Search could not load. Use the guide links in the navigation.'; results.append(p); return; }
    if (!dialog.open || input.value.trim().toLowerCase() !== query) return;
    const terms = query.split(/\s+/);
    const matches = index.filter((page) => terms.every((term) => `${page.title} ${page.text}`.toLowerCase().includes(term)))
      .sort((a, b) => Number(b.title.toLowerCase().includes(query)) - Number(a.title.toLowerCase().includes(query)));
    status.textContent = matches.length > 14 ? `Showing 14 of ${matches.length} matching guides` : `${matches.length} matching guides`;
    if (!matches.length) { const p = document.createElement('p'); p.textContent = 'No matching guides. Try a different term.'; results.append(p); }
    matches.slice(0, 14).forEach((page) => {
      const a = document.createElement('a'); a.href = base + page.url; a.textContent = page.title;
      const small = document.createElement('small'); small.textContent = page.section + ' · ' + page.summary;
      a.append(small); results.append(a);
    });
  };
  const open = () => { previousFocus = document.activeElement; dialog.showModal(); input.focus(); render(); };
  document.querySelector('[data-search-open]').addEventListener('click', open);
  dialog.querySelector('[data-search-close]').addEventListener('click', () => dialog.close());
  // A backdrop press must also end outside; dragging from a result must not cancel.
  const outsideDialog = (event) => {
    const bounds = dialog.getBoundingClientRect();
    return event.clientX < bounds.left || event.clientX > bounds.right ||
      event.clientY < bounds.top || event.clientY > bounds.bottom;
  };
  let backdropPress = false;
  dialog.addEventListener('pointerdown', (event) => {
    backdropPress = event.target === dialog && outsideDialog(event);
  });
  dialog.addEventListener('pointercancel', () => { backdropPress = false; });
  dialog.addEventListener('click', (event) => {
    if (backdropPress && event.target === dialog && outsideDialog(event)) dialog.close();
    backdropPress = false;
  });
  dialog.addEventListener('close', () => {
    input.value = '';
    results.replaceChildren();
    status.textContent = '';
    backdropPress = false;
    previousFocus?.focus();
  });
  input.addEventListener('input', render);
  document.addEventListener('keydown', (event) => {
    if (event.key === '/' && !['INPUT', 'TEXTAREA'].includes(document.activeElement.tagName) && !dialog.open) { event.preventDefault(); open(); }
  });
})();
