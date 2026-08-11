#!/usr/bin/env python3
# cli.py
import argparse, csv, json, os, re, shutil, subprocess, sys, threading, time
from datetime import datetime, timezone
from pathlib import Path

from .devices import list_candidate_disks
from .precheck import classify_precheck
from .progress import atomic_write_json, begin_stage, complete_stage, create_batch_state, fail_drive, finish_drive, load_state, reject_drive, render_dashboard, update_drive

BASE = Path(os.environ.get('DISKQUAL_HOME', '/opt/diskqual'))
DRIVES = BASE / 'drives.json'
REPORTS = BASE / 'reports'
LOGS = BASE / 'logs'
STATE = BASE / 'state.json'

def run(cmd, check=False): return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check)
def ensure_dirs():
    for p in [BASE, REPORTS, LOGS]: p.mkdir(parents=True, exist_ok=True)
def smart_text(dev, args=None):
    p=run(['smartctl', *(args or ['-a']), dev]); return p.stdout+p.stderr
def parse_field(text,names):
    for line in text.splitlines():
        for name in names:
            if line.strip().startswith(name+':'): return line.split(':',1)[1].strip()
    return ''
def parse_attrs(text):
    vals={'reallocated':'0','pending':'0','uncorrectable':'0','temperature':'','power_on_hours':''}
    for line in text.splitlines():
        parts=line.split()
        if len(parts)>=10:
            if parts[0]=='5': vals['reallocated']=parts[-1]
            elif parts[0]=='197': vals['pending']=parts[-1]
            elif parts[0]=='198': vals['uncorrectable']=parts[-1]
            elif parts[0]=='194': vals['temperature']=parts[-1]
            elif parts[0]=='9': vals['power_on_hours']=parts[-1]
    return vals
def selftest_line(text):
    for line in text.splitlines():
        if re.match(r'\s*#\s*1\s+',line): return ' '.join(line.split())
    return ''
def selftest_status(text):
    m=re.search(r'Self-test execution status:\s+\(\s*(\d+)\)\s*(.*)',text); return m.group(2).strip() if m else ''
def list_block_disks():
    return list_candidate_disks()
def mounted_or_os(dev):
    # Eligibility is already enforced by list_candidate_disks().  Keep this
    # compatibility helper because older call sites still use it.
    return False
def discover():
    ensure_dirs(); drives=[]
    for dev in list_block_disks():
        if mounted_or_os(dev): continue
        info=smart_text(dev,['-i']); alltxt=smart_text(dev,['-a']); attrs=parse_attrs(alltxt)
        health=parse_field(alltxt,['SMART overall-health self-assessment test result','SMART Health Status']) or 'UNKNOWN'
        serial=parse_field(info,['Serial Number','Serial number']) or Path(dev).name; model=parse_field(info,['Device Model','Product']) or 'UNKNOWN'
        protocol=parse_field(info,['Transport protocol']) or ('SAS' if 'SAS' in info else 'SATA' if 'SATA' in info else 'UNKNOWN'); size=int(run(['blockdev','--getsize64',dev]).stdout.strip() or 0)
        d={'id':serial,'dev':dev,'serial':serial,'model':model,'protocol':protocol,'size_bytes':size,'health':health,**attrs}; d['precheck'],d['precheck_reason']=classify_precheck(d); drives.append(d)
    DRIVES.write_text(json.dumps(drives,indent=2)); return drives
def load_drives(): return json.loads(DRIVES.read_text()) if DRIVES.exists() else discover()
def inventory(args):
    drives=discover(); batch=REPORTS/('inventory_'+datetime.now(timezone.utc).strftime('%Y-%m-%d_%H-%M-%S')); batch.mkdir(parents=True,exist_ok=True)
    fields=['id','dev','serial','model','protocol','size_bytes','health','power_on_hours','reallocated','pending','uncorrectable','temperature','precheck','precheck_reason']
    with open(batch/'inventory.csv','w',newline='') as f: w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(drives)
    for d in drives: (batch/(d['serial']+'.smart.txt')).write_text(smart_text(d['dev'],['-x']))
    print(f'Inventory saved: {batch}'); print_table(drives)
def print_table(drives):
    print('DEV   SERIAL          PRECHECK HEALTH  HOURS  REALLOC PENDING UNCORR TEMP MODEL')
    for d in drives: print(f"{Path(d['dev']).name:<5} {d['serial']:<15} {d.get('precheck','?'):<8} {d['health']:<7} {d.get('power_on_hours',''):<6} {d.get('reallocated',''):<7} {d.get('pending',''):<7} {d.get('uncorrectable',''):<6} {d.get('temperature',''):<4} {d['model']}")
    for d in drives:
        if d.get('precheck')=='REJECT': print(f"REJECT {d['dev']} {d['serial']}: {d.get('precheck_reason','')}")
def quick(args):
    drives=load_drives(); print('Starting SMART short tests...')
    for d in drives: print(f"{d['dev']} {d['serial']}\n{smart_text(d['dev'],['-t','short'])}")
    print('Waiting 90 seconds...'); time.sleep(90); report('quick')
def smart_long(args):
    for d in load_drives(): print(f"Starting SMART long on {d['dev']} {d['serial']}\n{smart_text(d['dev'],['-t','long'])}")
def report(label='report'):
    drives=load_drives(); batch=REPORTS/(label+'_'+datetime.now(timezone.utc).strftime('%Y-%m-%d_%H-%M-%S')); batch.mkdir(parents=True,exist_ok=True); rows=[]
    for d in drives:
        txt=smart_text(d['dev'],['-a']); attrs=parse_attrs(txt); health=parse_field(txt,['SMART overall-health self-assessment test result','SMART Health Status']) or 'UNKNOWN'; line=selftest_line(txt); decision,reason=classify_precheck({**d,**attrs,'health':health}); status='BAD' if decision=='REJECT' else decision
        row={**d,**attrs,'health':health,'precheck':decision,'precheck_reason':reason,'selftest':line,'result':status}; rows.append(row); (batch/(d['serial']+'.smart.txt')).write_text(smart_text(d['dev'],['-x']))
    fields=['id','dev','serial','model','protocol','size_bytes','health','power_on_hours','reallocated','pending','uncorrectable','temperature','precheck','precheck_reason','selftest','result']
    with open(batch/'summary.csv','w',newline='') as f: w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)
    print(f'Report saved: {batch}'); print_table(rows)
def cmd_report(args): report(args.label)
def wipe_prepare(args):
    drives=discover(); print('DESTRUCTIVE PREP: rejected drives are automatically skipped.')
    if not args.yes: print('Add --yes to execute.'); return
    for d in drives:
        if d['precheck']=='REJECT': print(f"Skipping {d['dev']} {d['serial']}: {d['precheck_reason']}"); continue
        dev=d['dev']; print(f"Preparing {dev} {d['serial']}"); run(['wipefs','-a',dev]); run(['sgdisk','--zap-all',dev]); run(['dd','if=/dev/zero',f'of={dev}','bs=1M','count=32','conv=fsync']); sectors=int(run(['blockdev','--getsz',dev]).stdout.strip()); start=sectors-(32*1024*1024//512); run(['dd','if=/dev/zero',f'of={dev}','bs=512',f'seek={start}',f'count={32*1024*1024//512}','conv=fsync']); run(['partprobe',dev])
def _save(state,lock):
    with lock: atomic_write_json(STATE,state)
def _wait_smart_long(d,state,lock,poll):
    begin_stage(state,d['id'],'smart-long','SMART extended self-test'); _save(state,lock); out=smart_text(d['dev'],['-t','long']); m=re.search(r'Please wait\s+(\d+)\s+minutes',out,re.I); estimate=int(m.group(1))*60 if m else None; start=time.monotonic()
    while True:
        time.sleep(poll); txt=smart_text(d['dev'],['-a']); status=selftest_status(txt); elapsed=time.monotonic()-start; progress=min(.99,elapsed/estimate) if estimate else 0; eta=max(0,int(estimate-elapsed)) if estimate else None; update_drive(state,d['id'],stage_progress=progress,stage_eta_seconds=eta,message=status or 'SMART extended self-test running'); _save(state,lock); lower=(status or '').lower()
        if status and not any(x in lower for x in ['remaining','progress','self-test routine in progress']): break
        if selftest_line(txt) and elapsed>30 and not status: break
    complete_stage(state,d['id'],'smart-long','SMART extended self-test complete'); _save(state,lock)
def _run_badblocks_stage(d,state,lock,stage,mode,log_path,poll):
    begin_stage(state,d['id'],stage,'Writing 0x00 pattern' if mode=='write' else 'Reading and verifying'); _save(state,lock); total=max(1,int(d['size_bytes'])); start=time.monotonic(); cmd=['badblocks','-wsv','-b','4096','-c','16384','-t','0x00',d['dev']] if mode=='write' else ['badblocks','-sv','-b','4096','-c','16384',d['dev']]
    with open(log_path,'w') as log:
        proc=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT,text=True)
        while proc.poll() is None:
            time.sleep(poll)
            try: text=Path(log_path).read_text(errors='replace').replace('\r','\n')
            except OSError: text=''
            matches=re.findall(r'(\d+(?:\.\d+)?)% done',text); progress=min(.999,float(matches[-1])/100) if matches else 0; elapsed=max(1,time.monotonic()-start); rate=total*progress/elapsed/(1024*1024) if progress else None; eta=int(elapsed*(1-progress)/progress) if progress else None; update_drive(state,d['id'],stage_progress=progress,stage_eta_seconds=eta,throughput_mib_s=rate,message='Writing 0x00 pattern' if mode=='write' else 'Reading and verifying'); _save(state,lock)
        rc=proc.returncode
    if rc: raise RuntimeError(f'badblocks {mode} exited with status {rc}; see {log_path}')
    complete_stage(state,d['id'],stage); _save(state,lock)
def _qualify_drive(d,state,lock,batch_dir,poll):
    try:
        begin_stage(state,d['id'],'baseline-smart','Capturing baseline SMART'); _save(state,lock); (batch_dir/(d['serial']+'.before.smart.txt')).write_text(smart_text(d['dev'],['-x'])); complete_stage(state,d['id'],'baseline-smart'); _save(state,lock)
        begin_stage(state,d['id'],'smart-short','SMART short self-test'); _save(state,lock); smart_text(d['dev'],['-t','short']); time.sleep(90); complete_stage(state,d['id'],'smart-short'); _save(state,lock); _wait_smart_long(d,state,lock,poll); _run_badblocks_stage(d,state,lock,'surface-write','write',batch_dir/(Path(d['dev']).name+'.write.log'),poll); _run_badblocks_stage(d,state,lock,'surface-verify','read',batch_dir/(Path(d['dev']).name+'.verify.log'),poll)
        begin_stage(state,d['id'],'final-smart','Capturing final SMART'); _save(state,lock); final=smart_text(d['dev'],['-x']); (batch_dir/(d['serial']+'.after.smart.txt')).write_text(final); complete_stage(state,d['id'],'final-smart'); _save(state,lock); begin_stage(state,d['id'],'classify','Classifying result'); attrs=parse_attrs(final); health=parse_field(final,['SMART overall-health self-assessment test result','SMART Health Status']) or 'UNKNOWN'; decision,reason=classify_precheck({**d,**attrs,'health':health}); result='BAD' if decision=='REJECT' else decision; complete_stage(state,d['id'],'classify'); finish_drive(state,d['id'],result,reason); _save(state,lock)
    except Exception as exc: fail_drive(state,d['id'],str(exc)); _save(state,lock)
def qualify(args):
    if not args.yes: print('Qualification is DESTRUCTIVE: only drives that pass precheck will be overwritten.\nRun: diskqual qualify --yes'); return
    drives=discover()
    if not drives: print('No eligible non-OS, unmounted disks found.'); return
    batch_id='qualify_'+datetime.now(timezone.utc).strftime('%Y-%m-%d_%H-%M-%S'); batch_dir=REPORTS/batch_id; batch_dir.mkdir(parents=True,exist_ok=True); state=create_batch_state(batch_id,drives); lock=threading.Lock(); accepted=[]
    for d in drives:
        if d['precheck']=='REJECT': reject_drive(state,d['id'],d['precheck_reason'])
        else: accepted.append(d)
    _save(state,lock); fields=['dev','serial','model','health','reallocated','pending','uncorrectable','precheck','precheck_reason']
    with open(batch_dir/'precheck.csv','w',newline='') as f: w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows([{k:d.get(k,'') for k in fields} for d in drives])
    print(f'Precheck: {len(accepted)} accepted, {len(drives)-len(accepted)} rejected. Rejected drives will NOT receive destructive tests.')
    for d in drives:
        if d['precheck']=='REJECT': print(f"  REJECT {d['dev']} {d['serial']}: {d['precheck_reason']}")
    if not accepted: state['status']='COMPLETE'; state['ended_utc']=datetime.now(timezone.utc).isoformat(); _save(state,lock); print(render_dashboard(state)); return
    threads=[threading.Thread(target=_qualify_drive,args=(d,state,lock,batch_dir,args.poll)) for d in accepted]
    for t in threads: t.start()
    try:
        while any(t.is_alive() for t in threads): os.system('clear'); print(render_dashboard(state)); time.sleep(args.interval)
    except KeyboardInterrupt: print('\nQualification command interrupted.'); raise
    for t in threads: t.join()
    state['status']='COMPLETE'; state['ended_utc']=datetime.now(timezone.utc).isoformat(); _save(state,lock); print(render_dashboard(state)); print(f'Qualification data saved: {batch_dir}')
def monitor(args):
    try:
        while True: os.system('clear'); print(render_dashboard(load_state(STATE))); time.sleep(args.interval)
    except KeyboardInterrupt: print('\nMonitor closed. Test processes are not signaled.')
def main():
    if shutil.which('smartctl') is None: print('smartctl not found. Install smartmontools.'); sys.exit(1)
    ensure_dirs(); p=argparse.ArgumentParser(prog='diskqual'); sub=p.add_subparsers(dest='cmd',required=True); sub.add_parser('inventory').set_defaults(func=inventory); sub.add_parser('quick').set_defaults(func=quick); sub.add_parser('smart-long').set_defaults(func=smart_long); r=sub.add_parser('report'); r.add_argument('--label',default='report'); r.set_defaults(func=cmd_report); m=sub.add_parser('monitor'); m.add_argument('--interval',type=int,default=5); m.set_defaults(func=monitor); w=sub.add_parser('prepare'); w.add_argument('--yes',action='store_true'); w.set_defaults(func=wipe_prepare); q=sub.add_parser('qualify'); q.add_argument('--yes',action='store_true'); q.add_argument('--interval',type=int,default=5); q.add_argument('--poll',type=int,default=10); q.set_defaults(func=qualify); args=p.parse_args(); args.func(args)
