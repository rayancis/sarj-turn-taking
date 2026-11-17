"""Prototype kept for the negative result D3 (see experiments/P3-phase3-report.md): an echo detector based on the
correlation between the caller level and the delayed agent level. It finds the echo path in every echo scenario, but
it also "declares" an echo during genuine interruptions (dev OV-D01, GI-D01, GI-D03), so it was not integrated.

    python experiments/phase3/echo_correlation_prototype.py
"""
import sys,json,wave,glob,numpy as np
sys.path[:0]=['.','tools']
import my_system as m
LAGS=26
def track(A,C,W=50,dyn=25.0,thr=0.75,persist=8):
    """per frame: (declared lag or -1, loss, corr)"""
    out=[]; peak=-120.0; run=0; last=-9
    for n in range(len(A)):
        peak=max(peak,A[n])
        if n<W+LAGS: out.append((-1,0,0)); continue
        c=C[n-W+1:n+1]
        a=np.stack([A[n-d-W+1:n-d+1] for d in range(LAGS)])
        a=np.maximum(a,peak-dyn)
        ca=c-c.mean(); aa=a-a.mean(axis=1,keepdims=True)
        den=np.sqrt((aa**2).sum(axis=1)*(ca**2).sum())
        ok=(aa.std(axis=1)>2.0)&(den>0)
        r=np.where(ok,(aa@ca)/np.where(den>0,den,1),0)
        b=int(np.argmax(r))
        if r[b]>=thr and abs(b-last)<=1: run+=1
        elif r[b]>=thr: run=1
        else: run=0
        last=b
        if run>=persist:
            sel=a[b]>=peak-12
            loss=float(np.median((c-a[b])[sel])) if sel.sum()>=5 else 0.0
            out.append((b,loss,r[b]))
        else: out.append((-1,0,r[b]))
    return out
if __name__=='__main__':
    for thr in (0.7,0.8):
      for W in (50,75):
        print('thr',thr,'W',W)
        for split in ('data/dev','data/extra/xdev'):
            bad=[]
            for f in sorted(glob.glob(split+'/labels/*.json')):
                l=json.load(open(f)); sid=l['scenario_id']
                w=wave.open(f'{split}/audio/{sid}.wav'); x=np.frombuffer(w.readframes(w.getnframes()),dtype=np.int16).reshape(-1,2)
                n=len(x)//320
                A=np.array([m.band_level_db(x[i*320:(i+1)*320,0]) for i in range(n)]); C=np.array([m.band_level_db(x[i*320:(i+1)*320,1]) for i in range(n)])
                o=track(A,C,W=W,thr=thr); dec=[i for i,s in enumerate(o) if s[0]>=0]
                if l['acoustic'].get('echo'):
                    print(f"   ECHO {sid} {l['acoustic']['echo']}: declared {len(dec)/n:.2f} of frames, first at {dec[0]*20 if dec else None} ms, lags {sorted(set(o[i][0] for i in dec))}, loss median {np.median([o[i][1] for i in dec]).round(1) if dec else None}")
                elif dec: bad.append((sid,len(dec),sorted(set(o[i][0] for i in dec))[:6],dec[0]*20))
            print('   non-echo scenarios with any declaration:',bad)
