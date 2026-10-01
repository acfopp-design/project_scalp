"""tv_ports.py -- faithful Python ports of Sri's TradingView favourites.

Ported from the ACTUAL Pine source pulled from pine-facade.tradingview.com,
not from the description. That distinction matters: AlphaTrend and Pullback ALT
were both written from descriptions and both turned out wrong.
"""

def _atr(b, n=10):
    tr = []
    for i, x in enumerate(b):
        if i == 0:
            tr.append(x['h'] - x['l'])
        else:
            pc = b[i-1]['c']
            tr.append(max(x['h']-x['l'], abs(x['h']-pc), abs(x['l']-pc)))
    out, a = [], None
    for i, t in enumerate(tr):            # Pine ta.atr = RMA of TR
        a = t if a is None else (a*(n-1)+t)/n
        out.append(a)
    return out


def ml_adaptive_supertrend(b, atr_len=10, fact=3.0, train=100,
                           highvol=0.75, midvol=0.5, lowvol=0.25):
    """AlgoAlpha. k-means (3 clusters) over the ATR series picks which
    volatility regime we are in; that centroid becomes the ATR fed to a normal
    SuperTrend. Returns +1 uptrend / -1 downtrend (Pine's _direction is
    inverted: -1 there means bullish, so it is flipped here)."""
    n = len(b)
    atr = _atr(b, atr_len)
    out = [0]*n
    st = None; direction = 1; prev_up = None; prev_lo = None
    for i in range(n):
        if i < train-1 or not atr[i]:
            out[i] = 0
            continue
        w = atr[i-train+1:i+1]
        up, lo = max(w), min(w)
        a = lo + (up-lo)*highvol
        bb = lo + (up-lo)*midvol
        c = lo + (up-lo)*lowvol
        for _ in range(30):                      # Pine loops till centroids settle
            hv, mv, lv = [], [], []
            for v in w:
                d1, d2, d3 = abs(v-a), abs(v-bb), abs(v-c)
                if d1 < d2 and d1 < d3: hv.append(v)
                elif d2 < d1 and d2 < d3: mv.append(v)
                elif d3 < d1 and d3 < d2: lv.append(v)
            na = sum(hv)/len(hv) if hv else a
            nb = sum(mv)/len(mv) if mv else bb
            nc = sum(lv)/len(lv) if lv else c
            if (na, nb, nc) == (a, bb, c): break
            a, bb, c = na, nb, nc
        cents = [a, bb, c]
        dists = [abs(atr[i]-x) for x in cents]
        assigned = cents[dists.index(min(dists))]
        # --- pine_supertrend(factor, atr) verbatim
        src = (b[i]['h']+b[i]['l'])/2.0
        ub = src + fact*assigned
        lb = src - fact*assigned
        plb = prev_lo if prev_lo is not None else lb
        pub = prev_up if prev_up is not None else ub
        pc = b[i-1]['c'] if i else b[i]['c']
        lb = lb if (lb > plb or pc < plb) else plb
        ub = ub if (ub < pub or pc > pub) else pub
        if st is None:
            direction = 1
        elif st == pub:
            direction = -1 if b[i]['c'] > ub else 1
        else:
            direction = 1 if b[i]['c'] < lb else -1
        st = lb if direction == -1 else ub
        prev_up, prev_lo = ub, lb
        out[i] = 1 if direction == -1 else -1     # flip to our convention
    return out


def consolidation_zones(b, prd=10, conslen=5):
    """LonesomeTheBlue. Sri: 'I use consolidation zones only to identify if the
    stock is entering into passive state... once stock comes out from zone, it
    will bump up or bump down.'
    Returns (in_zone, break_up, break_dn, zone_hi, zone_lo) per bar."""
    n = len(b)
    hi = [x['h'] for x in b]; lo = [x['l'] for x in b]
    dirv = [0]*n; zz = [None]*n
    for i in range(n):
        w0 = max(0, i-prd+1)
        is_h = hi[i] == max(hi[w0:i+1])
        is_l = lo[i] == min(lo[w0:i+1])
        dirv[i] = 1 if (is_h and not is_l) else (-1 if (is_l and not is_h) else (dirv[i-1] if i else 0))
        if is_h and is_l: zz[i] = hi[i] if dirv[i] == 1 else lo[i]
        elif is_h: zz[i] = hi[i]
        elif is_l: zz[i] = lo[i]
    pp = [None]*n
    for i in range(n):
        p = pp[i-1] if i else None
        if zz[i] is not None:
            if p is None: p = zz[i]
            elif dirv[i] == 1 and zz[i] > p: p = zz[i]
            elif dirv[i] == -1 and zz[i] < p: p = zz[i]
        pp[i] = p
    conscnt = 0; ch = cl = None
    inz = [False]*n; bu = [False]*n; bd = [False]*n
    zh = [None]*n; zl = [None]*n
    for i in range(n):
        w0 = max(0, i-conslen+1)
        H_, L_ = max(hi[w0:i+1]), min(lo[w0:i+1])
        changed = i > 0 and pp[i] != pp[i-1]
        if changed:
            if conscnt > conslen and ch is not None:
                if pp[i] is not None and pp[i] > ch: bu[i] = True
                if pp[i] is not None and pp[i] < cl: bd[i] = True
            if conscnt > 0 and ch is not None and cl is not None \
               and pp[i] is not None and cl <= pp[i] <= ch:
                conscnt += 1
            else:
                conscnt = 0
        else:
            conscnt += 1
        if conscnt >= conslen:
            if conscnt == conslen: ch, cl = H_, L_
            else:
                ch = max(ch, hi[i]); cl = min(cl, lo[i])
            inz[i] = conscnt > conslen
        zh[i], zl[i] = ch, cl
    return inz, bu, bd, zh, zl
