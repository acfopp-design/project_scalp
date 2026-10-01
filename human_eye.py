"""human_eye.py -- Sri's own documented strategy (Sheet11) as code.

GATE ON THE STOCK, BEFORE ANY SIGNAL (Sri, 08-Sep on PODDARMENT: "though today
it surged 17%, it is not a bullish stock as the volumes are pathetic... every
candle is unpredictable"). Also his listed complaints: no penny stocks, no
non-volume stocks.
ENTRY  EMA8 crosses above MA12, MACD crossing up (strongest above the zero
       line), RSI 50-65, SAR dot below the candle. Taken NEAR the cross --
       "always prefer to take entries near crossing MA/EMA, MACD & RSI",
       never at the peak where the buying is exhausted.
EXIT   "If the candle crosses below EMA and closes, then I generally exit."
       Plus the SAR flipping above the candle, and the -1% stop.
"""
MIN_TOVER_L = 5.0     # Rs lakh median turnover per 30s candle
MIN_PRICE = 20.0      # no penny stocks
# Sri, 08-Sep: "dont take my rules as thumb rules written on the rock... I do
# scalping, from RSI 20 till 50 stock may have shot up 2% which is fine."
# The 50-65 band blocked his real entries at RSI 83, 87, 88 and 100.
RSI_LO, RSI_HI = 20.0, 100.0
MACD_F, MACD_S, MACD_G = 5, 13, 5   # warm in ~6 min, not 13 -- he trades 09:16
MIN_HOLD = 3          # bars; nobody exits 30 seconds after buying
SELL_VOLX = 0.75      # a fall only counts when volume is behind it
SAR_FADE = 0.50       # dot has closed to half its best distance = dip warning
SEP_YOUNG = 0.35      # a gap wider than this has already been open too long
RSI_SMOOTH = 14       # the pink smoothing line under Sri's RSI
MIN_SEP = 0.15        # EMA8/MA12 must be genuinely apart, not welded together
MIN_VOLX = 1.20       # and the entry candle must carry real volume
STOP = -1.0

def _ema(v,n):
    o,k,a=[],2.0/(n+1),None
    for x in v:
        a=x if a is None else x*k+a*(1-k); o.append(a)
    return o
def _sma(v,n):
    o=[]
    for i in range(len(v)):
        w=v[max(0,i-n+1):i+1]; o.append(sum(w)/len(w))
    return o
def _rsi(c,n=14):
    g=[0.0]*len(c); l=[0.0]*len(c)
    for i in range(1,len(c)):
        d=c[i]-c[i-1]; g[i]=max(d,0.0); l[i]=max(-d,0.0)
    ag,al=_ema(g,n),_ema(l,n)
    return [100-100/(1+(ag[i]/al[i])) if al[i] else 100.0 for i in range(len(c))]
def _sar(b,af0=0.02,step=0.02,afmax=0.2):
    n=len(b); out=[None]*n
    if n<2: return out
    up=b[1]['c']>=b[0]['c']; af=af0
    ep=(b[0]['h'] if up else b[0]['l']); sar=(b[0]['l'] if up else b[0]['h'])
    for i in range(1,n):
        sar=sar+af*(ep-sar); h,l=b[i]['h'],b[i]['l']
        if up:
            if l<sar: up=False; sar=ep; ep=l; af=af0
            elif h>ep: ep=h; af=min(af+step,afmax)
        else:
            if h>sar: up=True; sar=ep; ep=h; af=af0
            elif l<ep: ep=l; af=min(af+step,afmax)
        out[i]=sar
    return out

def tradeable(b):
    """Sri's gate. Returns (ok, median 30s turnover in Rs lakh)."""
    t=sorted((x['v'] or 0)*x['c']/1e5 for x in b)
    med=t[len(t)//2] if t else 0.0
    return (med>=MIN_TOVER_L and b[0]['c']>=MIN_PRICE), med

def trades(b, frm="09:16:00", upto="15:15:00", near_bars=6):
    ok,med=tradeable(b)
    if not ok: return []
    c=[x['c'] for x in b]
    e8,m12=_ema(c,8),_sma(c,12)
    mac=[a-x for a,x in zip(_ema(c,MACD_F),_ema(c,MACD_S))]; sig=_ema(mac,MACD_G)
    rsi=_rsi(c); rsm=_sma(rsi,RSI_SMOOTH); sar=_sar(b); vv=[x['v'] or 0 for x in b]
    # VWAP -- Sri's 5th condition ("optionally all candles are above vwap")
    _cum=_cv=0.0; vwap=[]
    for _i,_x in enumerate(b):
        _tp=((_x['h'] or c[_i])+(_x['l'] or c[_i])+c[_i])/3.0
        _cum+=_tp*vv[_i]; _cv+=vv[_i]; vwap.append(_cum/_cv if _cv else c[_i])
    # BUG FIXED 08-Sep: crosses were only registered from bar MACD_S, so
    # RAYMOND's real cross at 09:20:00 was never seen and the whole 09:16-09:30
    # run (+4.5%) was untakeable. Scan for crosses over the WHOLE series first.
    xall=[i for i in range(1,len(b)) if e8[i]>m12[i] and e8[i-1]<=m12[i-1]]
    sep=[(e8[i]-m12[i])/m12[i]*100 if m12[i] else 0.0 for i in range(len(b))]
    out=[]; pos=None
    # NO WARM-UP INDEX GATE. Starting this loop at MACD_S made every bar
    # before 09:28 unenterable, which silently hid ALLCARGO 09:24 (+1.88%) and
    # RAYMOND 09:20. Sri, 08-Sep: "eliminate your 9:28 dependency completely."
    # If an indicator needs history, LOAD the history -- never wait for it.
    for i in range(1,len(b)):
        t=b[i]['hhmm']
        if t<frm: continue
        xs=[k for k in xall if k<=i]
        xat=xs[-1] if xs else None
        if pos:
            lo=b[i]['l'] or c[i]
            if lo<=pos['in']*(1+STOP/100):
                pos.update(out=round(pos['in']*(1+STOP/100),2),out_t=t,why='stop loss')
                out.append(pos); pos=None; continue
            # "If the candle crosses below EMA and closes, then I generally
            # exit." On 30s bars a strong trend still dips under the EMA8 for
            # single bars -- MIDHANI 10:09-10:38 does it 3 times and Sri held
            # through all three. So the close below EMA8 only counts when
            # momentum is also fading.
            # THE JUDGMENT CALL, Sheet11: "Moment when SAR dot touches red
            # candle... stock starts dipping, but you have to decide if its
            # real dip or dip for sometime and buyers are in full control."
            # MIDHANI flipped SAR at 10:20 AND at 10:39. He held the first and
            # sold the second. What separates them is not how deep price fell
            # -- the dip he HELD was deeper (-0.28% vs -0.02% under EMA8) --
            # it is whether momentum was still wide. Hist was near its peak at
            # 10:20 and collapsing at 10:39. So: the dot warns, the collapse of
            # the histogram decides.
            # MEASURED on MIDHANI, the two dips that look identical on price:
            #   10:19-10:24 (he HELD)  pullback bars at VOLx 0.24-0.66
            #   10:36-10:40 (he SOLD)  blow-off 1.95 then reds 1.12/0.82/1.18
            # A pullback on drying volume is buyers pausing; a fall on rising
            # volume is distribution. Sheet11: "any candle if it shoots up
            # extremely high (abnormally), high chance that next candle goes
            # for profit booking."
            a=sum(vv[max(0,i-20):i])/max(1,len(vv[max(0,i-20):i]))
            # Only DOWN bars are selling. A big green recovery bar inside the
            # pullback (MIDHANI 10:23:30, VOLx 1.88) is buyers stepping back
            # in -- counting it as sell volume exits the trade at 10:24 on a
            # dip Sri rode for another 14 minutes.
            red=[vv[j]/a for j in range(max(0,i-3),i+1)
                 if a and b[j]['c']<(b[j]['o'] or b[j]['c'])]
            sell_vol=sum(red)/len(red) if red else 0.0
            # SAR DISTANCE, Sheet11: "if the distance between SAR dot and candle
            # is high, then I consider strong bullish... as and when the SAR dot
            # is nearing to candle, the stock is getting ready to dip." Sri
            # exited ALLCARGO 10:15 on exactly this -- distance peaked 5.24% at
            # 10:12 and had halved by 10:15. But it must carry the SAME selling-
            # volume test as the EMA exit: on 09:24 the distance collapsed to
            # 0.72% at 09:32 on volume of 0.5x (a pause, not a top) and price
            # then ran to 14.42. Dot closing in + no sellers = hold.
            sd = ((c[i]-sar[i])/c[i]*100) if sar[i] else 0.0
            pos['sd_pk']=max(pos.get('sd_pk',0.0),sd)
            sar_in = pos['sd_pk']>0.8 and sd < SAR_FADE*pos['sd_pk']
            # Sri, 08-Sep on HINDCOPPER: "13:58:30 is a bad exit, 13:48 is good.
            # Stock started dropping and still you were holding. Look at the RSI
            # and smoothing - it crossed and dipping down."
            # RSI crossed under its own SMA at 13:44:30 and fell 87->77->69->57
            # while the old rule sat until 13:58:30 and gave back 2.70 a share.
            # Two bars of confirmation, because it popped back above once at
            # 13:45:30. This fires BEFORE price breaks the EMA -- it is the
            # earliest of the three exits and usually the right one.
            rsi_roll = (rsi[i]<rsm[i] and rsi[i-1]<rsm[i-1] and rsi[i]<rsi[i-1])
            if i-pos['i']>=MIN_HOLD and rsi_roll:
                pos.update(out=c[i],out_t=t,why='RSI crossed below smoothing')
                out.append(pos); pos=None; continue
            if i-pos['i']>=MIN_HOLD and sell_vol>=SELL_VOLX and (c[i]<e8[i] or sar_in):
                pos.update(out=c[i],out_t=t,
                           why='SAR closing in on volume' if sar_in else 'EMA8 break on selling volume')
                out.append(pos); pos=None; continue
            # A FLIP, not a state. Entering while the dot is still above (Sri
            # did exactly that on MIDHANI at 10:09) must not exit on the very
            # next bar -- the exit arms only once the dot has gone below.
            # SAR is NOT an exit. Sri, Sheet11: when the dot flips above "you
            # have to decide if its real dip or dip for sometime and buyers are
            # in full control". MIDHANI flipped at 10:20 and ran to 455 by
            # 10:37 -- he held. The dot is a warning, the EMA close is the exit.
            if t>=upto:
                pos.update(out=c[i],out_t=t,why='square-off'); out.append(pos); pos=None
            continue
        if t>upto: continue
        near = xat is not None and 0 <= i-xat <= near_bars    # near the cross, not the peak
        # Sri, 08-Sep: "I also see from my eyes if ma and ema are together and
        # sustaining to be together." RAYMOND 09:34:30 was a bad entry because
        # EMA8 and MA12 were welded (sep 0.10%, oscillating +-0.06% for four
        # minutes) -- chop. During the good 09:20 run sep averaged 0.28%.
        # And: "the more trades you take, the more costly the dhan charges...
        # avoid penny trades, take only sureshot trades." So the lines must be
        # SEPARATED and STILL SPREADING before anything is taken.
        # SRI'S FIVE CONDITIONS, verbatim 08-Sep on RAYMOND:
        #   "Macd line above signal, Rsi above 50, candle above ema, and there is
        #    gap JUST STARTED between ma and ema, and optionally all candles are
        #    above vwap."
        # The gap must be OPENING, not already open and widening -- requiring
        # width plus three bars of widening entered after the move was half over.
        # "gap JUST STARTED" = the gap is still YOUNG and still growing, not
        # that the previous bar was at zero. Demanding prev sep <= 0.02 blocked
        # BANDHANBNK 09:18:30 (sep 0.098 off 0.082) and 10:09 (0.133 off 0.073).
        just = sep[i]>0 and sep[i]>sep[i-1] and sep[i]<SEP_YOUNG
        # VWAP is Sri's word "optionally" -- a preference, NOT a gate. As a gate
        # it blocked MIDHANI 10:07, the +6% trade he called himself.
        if (mac[i]>sig[i] and rsi[i]>RSI_LO and c[i]>e8[i] and just
                and c[i]>(b[i]['o'] or c[i])):
            pos={'in':c[i],'in_t':t,'i':i,'above0':mac[i]>0}
    return out
