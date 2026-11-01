/* ══════════════════════════════════════════════════════════════
   Animation Engine v2.0 — Particles · Parallax · Scroll
   ══════════════════════════════════════════════════════════════ */

(function() {
  'use strict';

  /* i18n：文案由模板 app_js_i18n.html 注入 window.__I18N__（source-string-as-key），
     词典缺失时回退英文源串。 */
  function T(k) {
    var d = window.__I18N__;
    return (d && Object.prototype.hasOwnProperty.call(d, k)) ? d[k] : k;
  }

  // ── tsParticles Initialization ──
  function initParticles() {
    if (typeof tsParticles === 'undefined') return;
    tsParticles.load('particles-bg', {
      fullScreen: { enable: false, zIndex: 0 },
      fpsLimit: 60,
      particles: {
        number: {
          value: 80,
          density: { enable: true, area: 800 }
        },
        color: {
          value: ['#00f5ff', '#a020f0', '#6366f1', '#00ff9f', '#22d3ee']
        },
        shape: { type: 'circle' },
        opacity: {
          value: 0.3,
          random: true,
          anim: { enable: true, speed: 0.3, opacity_min: 0.05 }
        },
        size: {
          value: { min: 1, max: 3 },
          random: true,
          anim: { enable: true, speed: 0.5, size_min: 0.1 }
        },
        links: {
          enable: true,
          distance: 150,
          color: '#6366f1',
          opacity: 0.1,
          width: 1,
          triangles: { enable: false }
        },
        move: {
          enable: true,
          speed: 0.6,
          direction: 'none',
          random: true,
          straight: false,
          outModes: { default: 'bounce' },
          attract: { enable: true, rotateX: 600, rotateY: 1200 }
        }
      },
      interactivity: {
        detectsOn: 'canvas',
        events: {
          onHover: {
            enable: true,
            mode: 'grab',
            parallax: { enable: true, force: 40, smooth: 10 }
          },
          resize: true
        },
        modes: {
          grab: {
            distance: 180,
            links: { opacity: 0.3, color: '#00f5ff' }
          }
        }
      },
      retina_detect: true,
      background: { color: 'transparent' }
    });
  }

  // ── Shooting Stars (extra particles) ──
  function createShootingStar() {
    var star = document.createElement('div');
    star.style.cssText =
      'position:fixed;width:80px;height:1px;' +
      'background:linear-gradient(90deg,transparent,rgba(0,245,255,0.6),transparent);' +
      'pointer-events:none;z-index:0;' +
      'animation:shoot 0.8s ease-out forwards;';
    star.style.left = Math.random() * 100 + '%';
    star.style.top = Math.random() * 60 + '%';
    star.style.transform = 'rotate(' + (20 + Math.random() * 30) + 'deg)';
    document.body.appendChild(star);
    setTimeout(function() { star.remove(); }, 1200);
  }

  // Inject shooting star keyframe
  var styleSheet = document.createElement('style');
  styleSheet.textContent =
    '@keyframes shoot{0%{transform:translateX(0) rotate(var(--r,25deg));opacity:1}' +
    '100%{transform:translateX(-200px) rotate(var(--r,25deg)) translateY(40px);opacity:0}}';
  document.head.appendChild(styleSheet);

  function scheduleShootingStars() {
    setInterval(function() {
      if (Math.random() < 0.3) createShootingStar();
    }, 4000);
  }

  // ── Scroll-triggered Animation (Intersection Observer) ──
  function initScrollAnimations() {
    var els = document.querySelectorAll('.animate-on-scroll');
    if (!els.length) return;

    var observer = new IntersectionObserver(function(entries) {
      entries.forEach(function(entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add('visible');
          observer.unobserve(entry.target);
        }
      });
    }, {
      threshold: 0.08,
      rootMargin: '0px 0px -40px 0px'
    });

    els.forEach(function(el) { observer.observe(el); });
  }

  // Add .animate-on-scroll to blocks that don't have it
  function autoAnimateSections() {
    document.querySelectorAll('.block, .section, .hero-block').forEach(function(el, i) {
      if (!el.classList.contains('no-animate')) {
        el.querySelectorAll('.block-inner > *, .section-inner > *, .hero-block .block-inner > *').forEach(function(child) {
          if (!child.classList.contains('animate-on-scroll') &&
              !child.classList.contains('hero-stats') &&
              !child.classList.contains('hero-actions')) {
            child.classList.add('animate-on-scroll');
          }
        });
      }
    });
  }

  // ── Navbar Scroll Effect ──
  function initNavScroll() {
    var nav = document.querySelector('.nav');
    if (!nav) return;
    window.addEventListener('scroll', function() {
      nav.classList.toggle('scrolled', window.scrollY > 60);
    }, { passive: true });
  }

  // ── Data Ticker ──
  function initTicker() {
    var ticker = document.querySelector('.ticker-wrap');
    if (!ticker) return;
    ticker.style.display = 'block';

    // 演示数据：数值为本地随机样本，非实时行情。真实行情须由 VeroRun 后端
    // 行情 API 提供（前端不自行取数、不自行编造业务数据），故此处固定标注「演示数据」。
    if (!ticker.querySelector('.ticker-demo')) {
      var demoBadge = document.createElement('span');
      demoBadge.className = 'ticker-demo';
      demoBadge.textContent = T('Demo data');
      ticker.appendChild(demoBadge);
    }

    // Mock stock data
    var stocks = [
      {sym:T('A-shares / SSE'), price:'3128.45', chg:'+0.68%', dir:'up'},
      {sym:T('A-shares / SZSE'), price:'10234.56', chg:'+1.23%', dir:'up'},
      {sym:T('HKEX / HSI'), price:'18945.67', chg:'-0.32%', dir:'down'},
      {sym:T('US / S&P 500'), price:'5213.89', chg:'+0.89%', dir:'up'},
      {sym:T('US / Nasdaq'), price:'16234.56', chg:'+1.45%', dir:'up'},
      {sym:'Crypto/BTC', price:'67,432', chg:'+2.34%', dir:'up'},
      {sym:'Crypto/ETH', price:'3,245', chg:'-1.21%', dir:'down'},
      {sym:T('Gold / XAU'), price:'2,345.6', chg:'+0.56%', dir:'up'},
    ];

    function buildTickerItems() {
      var h = '';
      // Duplicate for seamless scroll
      var items = stocks.concat(stocks);
      items.forEach(function(s) {
        h += '<span class="ticker-item">' +
          '<span class="sym">' + s.sym + '</span>' +
          '<span>' + s.price + '</span>' +
          '<span class="' + s.dir + '">' + s.chg + '</span>' +
          '</span>';
      });
      ticker.querySelector('.ticker-inner').innerHTML = h;
    }

    buildTickerItems();

    // Refresh price periodically
    setInterval(function() {
      stocks.forEach(function(s) {
        var change = (Math.random() - 0.5) * 2;
        var price = parseFloat(s.price.replace(/,/g,'')) * (1 + change/100);
        s.price = price.toFixed(price > 1000 ? 2 : 2).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
        s.chg = (change > 0 ? '+' : '') + change.toFixed(2) + '%';
        s.dir = change >= 0 ? 'up' : 'down';
      });
      // Re-render every 30 seconds (after animation cycle)
      setTimeout(buildTickerItems, 29500);
    }, 30000);
  }

  // ── Parallax Effect on hero ──
  function initParallax() {
    var hero = document.querySelector('.hero-block');
    if (!hero) return;
    window.addEventListener('scroll', function() {
      var scrolled = window.scrollY;
      if (scrolled < window.innerHeight) {
        hero.style.transform = 'translateY(' + (scrolled * 0.08) + 'px)';
        hero.style.opacity = 1 - (scrolled / (window.innerHeight * 0.8));
      }
    }, { passive: true });
  }

  // ── Typewriter Effect ──
  function initTypewriter() {
    var els = document.querySelectorAll('[data-typewriter]');
    els.forEach(function(el) {
      var text = el.getAttribute('data-typewriter') || el.textContent;
      el.textContent = '';
      el.style.visibility = 'visible';
      el.classList.add('typing-text');

      var observer = new IntersectionObserver(function(entries) {
        if (entries[0].isIntersecting) {
          var i = 0;
          function type() {
            if (i < text.length) {
              el.textContent = text.substring(0, i + 1);
              i++;
              setTimeout(type, 30 + Math.random() * 40);
            } else {
              el.classList.remove('typing-text');
            }
          }
          type();
          observer.unobserve(el);
        }
      }, { threshold: 0.5 });
      observer.observe(el);
    });
  }

  // ── Counter Animation for Stats ──
  function initCounters() {
    document.querySelectorAll('[data-count-to]').forEach(function(el) {
      var target = parseInt(el.getAttribute('data-count-to'));
      var suffix = el.getAttribute('data-count-suffix') || '';

      var observer = new IntersectionObserver(function(entries) {
        if (entries[0].isIntersecting) {
          var current = 0;
          var step = Math.ceil(target / 60);
          var timer = setInterval(function() {
            current += step;
            if (current >= target) {
              current = target;
              clearInterval(timer);
            }
            el.textContent = current + suffix;
          }, 25);
          observer.unobserve(el);
        }
      }, { threshold: 0.5 });
      observer.observe(el);
    });
  }

  // ── Lazy Load Images ──
  function initLazyImages() {
    var imgs = document.querySelectorAll('img[data-src]');
    if (!imgs.length) return;
    var observer = new IntersectionObserver(function(entries) {
      entries.forEach(function(entry) {
        if (entry.isIntersecting) {
          var img = entry.target;
          img.src = img.getAttribute('data-src');
          img.removeAttribute('data-src');
          img.classList.add('loaded');
          observer.unobserve(img);
        }
      });
    }, { threshold: 0.1 });
    imgs.forEach(function(img) { observer.observe(img); });
  }

  // ── Initialize Everything ──
  function init() {
    initParticles();
    scheduleShootingStars();
    autoAnimateSections();
    initScrollAnimations();
    initNavScroll();
    initTypewriter();
    initCounters();
    initLazyImages();
    initParallax();

    // Ticker — delayed to not slow down initial render
    setTimeout(initTicker, 1500);

    // Re-run scroll animations after dynamic content loads
    document.addEventListener('DOMContentLoaded', function() {
      setTimeout(initScrollAnimations, 500);
    });
  }

  // Run on ready
  if (document.readyState === 'complete' || document.readyState === 'interactive') {
    init();
  } else {
    document.addEventListener('DOMContentLoaded', init);
  }
})();
