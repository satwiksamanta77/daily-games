TQ = chr(34) * 3

q = open('quordle_parity.py').read()
old = """        if (host === location.host) continue;
        f.style.setProperty('display', 'none', 'important');"""
new = """        if (host === location.host) continue;
        // display:none iframes KEEP document focus - every physical
        // keystroke would die inside them (betweenle lost guesses 3+
        // exactly this way). Blur and then remove them from the DOM.
        try { f.contentWindow && f.contentWindow.blur && f.contentWindow.blur(); } catch (e) {}
        f.style.setProperty('display', 'none', 'important');
        f.setAttribute('data-qpx', '1');"""
assert old in q; q = q.replace(old, new, 1)
old = """  setInterval(kill3p, 1200);
  setInterval(killShellBanners, 1500);"""
new = """  const reap = () => {
    try {
      for (const f of Array.from(document.querySelectorAll('iframe[data-qpx]')))
        f.remove();
    } catch (e) {}
  };
  setInterval(kill3p, 1200);
  setInterval(reap, 2600);
  setInterval(killShellBanners, 1500);"""
if old in q:
    q = q.replace(old, new, 1)
open('quordle_parity.py', 'w').write(q)
print("adhide iframe blur+reap ok")

JS = TQ + """(skip) => {
    const btns = Array.from(document.querySelectorAll('button, a'))
        .filter(e => e && e.offsetParent !== null);
    for (const b of btns) {
        const t = (b.innerText || '').trim().toUpperCase();
        if (/^(NEXT|SKIP|PLAY|START|CONTINUE|GO|GOT IT)$/.test(t)
            && t !== skip) {
            const r = b.getBoundingClientRect();
            if (r.width > 4 && r.height > 4)
                return {t: t, x: r.x + r.width / 2, y: r.y + r.height / 2};
        }
    }
    return null;
}""" + TQ

s = open('runner.py').read()
old = "\"if (a && a !== document.body) a.blur(); window.focus(); }\")"
new = ("\"if (a && a !== document.body) a.blur(); \" "
       "\'if (a && a.tagName === \"IFRAME\") { try { a.contentWindow.blur(); }\' "
       "\"catch (e) {} } try { window.top.focus(); } catch (e) {} \" "
       "\"window.focus(); }\")")
assert old in s
s = s.replace(old, new)   # all three focus-reclaim sites

assert old in s; s = s.replace(old, new, 1)

old = """            if not box:
                break
            try:
                _before = (fr.evaluate(
                    "() => document.body.innerText.slice(0, 200)") or "")
            except Exception:
                _before = ""
            page.mouse.click(box["x"], box["y"])   # iframe is full-page: 0,0"""
new = """            if not box:
                break
            try:
                _before = (fr.evaluate(
                    "() => document.body.innerText.slice(0, 200)") or "")
            except Exception:
                _before = ""
            if box["t"] == _last_tap and getattr(page, "_qp_unch", 0) >= 1:
                _alt = fr.evaluate(""" + JS + """, box["t"])
                if _alt:
                    box = _alt
            page.mouse.click(box["x"], box["y"])   # iframe is full-page: 0,0"""
assert old in s; s = s.replace(old, new, 1)

old = """                if _after == _before:
                    _dbg(gid, "slide unchanged after trusted tap")
            except Exception:
                pass"""
new = """                if _after == _before:
                    _u = getattr(page, "_qp_unch", 0) + 1
                    page._qp_unch = _u
                    _last_tap = box["t"]
                    _dbg(gid, f"slide unchanged after trusted tap "
                              f"({_u}x, last={_last_tap})")
                else:
                    page._qp_unch = 0
                    _last_tap = None
            except Exception:
                pass"""
assert old in s; s = s.replace(old, new, 1)

old = """    _dbg(gid, f"app frame: {fr.url[:60]}")"""
new = """    _dbg(gid, f"app frame: {fr.url[:60]}")
    _last_tap = None
    try:
        page._qp_unch = 0
    except Exception:
        pass"""
assert old in s; s = s.replace(old, new, 1)
open('runner.py', 'w').write(s)
print("countryle rotation ok")
