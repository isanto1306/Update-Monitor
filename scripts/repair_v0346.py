#!/usr/bin/env python3
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
INDEX=ROOT/'static'/'index.html'
REPORT=ROOT/'repair-v0346-report.txt'


def main():
    lines=INDEX.read_text(encoding='utf-8',errors='replace').splitlines()
    out=['UPDATE MONITOR v0.3.346 UI ERROR DETAIL LOCATIONS\n']
    needles=['detail','dockerInfo','docker-info','error_count','errorStatus','headerWarning','matchingVerificationScan','verificationBaselines','policyVersionSelect','fixedNotice']
    hits=[]
    for i,line in enumerate(lines,1):
        if i<18500: continue
        low=line.lower()
        if any(n.lower() in low for n in needles):
            lo=max(1,i-3); hi=min(len(lines),i+3)
            block='\n'.join(f'{n:06d}: {lines[n-1]}' for n in range(lo,hi+1))
            if block not in hits: hits.append(block)
    out.append('\n\n'.join(hits[:220]))
    REPORT.write_text(''.join(out),encoding='utf-8')

if __name__=='__main__': main()
