"""Shared human-eye config, tuned jointly across all five 21-Sep-2026 stocks.
No stock names anywhere: every threshold is relative to the stock's own bars."""
CFG=dict(SLOTS=2,ANGMODE='abs',ANGP=60,ANG=10,SANG=15,FLIPW=3,STOPR=2,DIPF=8,DIPM=1.5,
         SHORTS=True,SHORTCUM=6,USEHA=False,USECLOUD=False,OPENB=4,OPENBODY=0.3,
         OPENCUM=0.5,OPENVOL=1.0,RW=3,COOL=4,BODYX=0,NHB=0,VOLQ=0,RNGQ=0,KEEP=0.12,KEEPT='1015',
         LASTT='1430',EXITT='1515',MAXTR=20)
