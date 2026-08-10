#!/usr/bin/env python3
# cli.py
import argparse, csv, json, os, re, shutil, subprocess, sys, threading, time
from datetime import datetime, timezone
from pathlib import Path

from .progress import atomic_write_json, begin_stage, complete_stage, create_batch_state, fail_drive, finish_drive, load_state, render_dashboard, update_drive

BASE = Path(os.environ.get('DISKQUAL_HOME', '/opt/diskqual'))
DRIVES = BASE / 'drives.json'
REPORTS = BASE / 'reports'
LOGS = BASE / 'logs'
STATE = BASE / 'state.json'


def run(cmd, check=False):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check)


def ensure_dirs():
    for p in [BASE, REPORTS, LOGS]:
        p.mkdir(parents=True, exist_ok=True)


def smart_text(dev, args=None):
    args = args or ['-a']
    p = run(['smartctl', *args, dev])
    return p.stdout + p.stderr


def parse_field(text, names):
    for line in text.splitlines():
        for name in names:
            if line.strip().startswith(name + ':'):
                return line.split(':', 1)[1].strip()
    return ''


def parse_attrs(text):
    vals = {'reallocated':'0','pending':'0','uncorrectable':'0','temperature':'','power_on_hours':''}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 10:
            if parts[0] == '5': vals['reallocated'] = parts[-1]
            elif parts[0] == '197': vals['pending'] = parts[-1]
            elif parts[0] == '198': vals['uncorrectable'] = parts[-1]
            elif parts[0] == '194': vals['temperature'] = parts[-1]
            elif parts[0] == '9': vals['power_on_hours'] = parts[-1]
    return vals


def selftest_line(text):
    for line in text.splitlines():
        if re.match(r'\s*#\s*1\s+', line):
            return ' '.join(line.split())
    return ''


def selftest_status(text):
    m = re.search(r'Self-test execution status:\s+\(\s*(\d+)\)\s*(.*)', text)
    return m.group(2).strip() if m else ''


def list_block_disks():
    p = run(['lsblk','-dnpo','NAME,TYPE,SIZE,MODEL,SERIAL'])
    return [line.split(None, 4)[0] for line in p.stdout.splitlines() if len(line.split(None, 4)) >= 2 and line.split(None, 4)[1] == 'disk']


def mounted_or_os(dev):
    if Path(dev).name == 'sda': return True
    p = run(['lsblk','-nr','-o','MOUNTPOINT', dev])
    return any(x.strip().startswith('/') for x in p.stdout.splitlines() if x.strip())


def discover():
    ensure_dirs()
    drives = []
    for dev in list_block_disks():
        if mounted_or_os(dev): continue
        info = smart_text(dev, ['-i'])
        alltxt = smart_text(dev, ['-a'])
        attrs = parse_attrs(alltxt)
        health = parse_field(alltxt, ['SMART overall-health self-assessment test result','SMART Health Status']) or 'UNKNOWN'
        serial = parse_field(info, ['Serial Number', 'Serial number']) or Path(dev).name
        model = parse_field(info, ['Device Model','Product']) or 'UNKNOWN'
        protocol = parse_field(info, ['Transport protocol']) or ('SAS' if 'SAS' in info else 'SATA' if 'SATA' in info else 'UNKNOWN')
        size = int(run(['blockdev','--getsize64',dev]).stdout.strip() or 0)
        drives.append({'id':serial,'dev':dev,'serial':serial,'model':model,'protocol':protocol,'size_bytes':size,'health':health,**attrs})
    DRIVES.write_text(json.dumps(drives, indent=2))
    return drives


def load_drives():
    return json.loads(DRIVES.read_text()) if DRIVES.exists() else discover()


def inventory(args):
    drives = discover()
    batch = REPORTS / ('inventory_' + datetime.now(timezone.utc).strftime('%Y-%m-%d_%H-%M-%S'))
    batch.mkdir(parents=True, exist_ok=True)
    fields=['dev','serial','model','protocol','size_bytes','health','power_on_hours','reallocated','pending','uncorrectable','temperature']
    with open(batch/'inventory.csv','w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(drives)
    for d in drives:
        (batch/(d['serial']+'.smart.txt')).write_text(smart_text(d['dev'], ['-x']))
    print(f'Inventory saved: {batch}')
    print_table(drives)


def print_table(drives):
    print('DEV   SERIAL          HEALTH  HOURS  REALLOC PENDING UNCORR TEMP MODEL')
    for d in drives:
        print(f"{Path(d['dev']).name:<5} {d['serial']:<15} {d['health']:<7} {d.get('power_on_hours',''):<6} {d.get('reallocated',''):<7} {d.get('pending',''):<7} {d.get('uncorrectable',''):<6} {d.get('temperature',''):<4} {d['model']}")


def quick(args):
    drives=load_drives(); print('Starting SMART short tests...')
    for d in drives: print(f"{d['dev']} {d['serial']}\n{smart_text(d['dev'], ['-t','short'])}")
    print('Waiting 90 seconds...'); time.sleep(90); report('quick')


def smart_long(args):
    drives=load_drives()
    for d in drives:
        print(f"Starting SMART long on {d['dev']} {d['serial']}")
        print(smart_text(d['dev'], ['-t','long']))
    print('SMART long tests launched.')


def report(label='report'):
    drives=load_drives(); batch=REPORTS/(label+'_'+datetime.now(timezone.utc).strftime('%Y-%m-%d_%H-%M-%S')); batch.mkdir(parents=True,exist_ok=True)
    rows=[]
    for d in drives:
        txt=smart_text(d['dev'],['-a']); attrs=parse_attrs(txt); health=parse_field(txt,['SMART overall-health self-assessment test result','SMART Health Status']) or 'UNKNOWN'; line=selftest_line(txt); status='GOOD'
        try:
            if int(attrs['pending'])>0 or int(attrs['uncorrectable'])>0: status='BAD'
            elif int(attrs['reallocated'])>0: status='REVIEW'
        except Exception: status='REVIEW'
        if line and any(x in line.lower() for x in ['failure','aborted','interrupted']): status='BAD'
        row={**d,**attrs,'health':health,'selftest':line,'result':status}; rows.append(row); (batch/(d['serial']+'.smart.txt')).write_text(smart_text(d['dev'],['-x']))
    fields=['dev','serial','model','protocol','size_bytes','health','power_on_hours','reallocated','pending','uncorrectable','temperature','selftest','result']
    with open(batch/'summary.csv','w',newline='') as f: w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)
    with open(batch/'summary.txt','w') as f:
        for r in rows: f.write(f"{r['dev']} {r['serial']} {r['result']} {r['health']} {r['selftest']}\n")
    print(f'Report saved: {batch}'); print_table(rows)


def cmd_report(args): report(args.label)


def wipe_prepare(args):
    drives=load_drives(); print('DESTRUCTIVE PREP: will clear signatures on test drives only.')
    if not args.yes: print('Add --yes to execute.'); return
    for d in drives:
        dev=d['dev']; print(f'Preparing {dev} {d["serial"]}'); run(['wipefs','-a',dev]); run(['sgdisk','--zap-all',dev]); run(['dd','if=/dev/zero',f'of={dev}','bs=1M','count=32','conv=fsync']); sectors=int(run(['blockdev','--getsz',dev]).stdout.strip()); start=sectors-(32*1024*1024//512); run(['dd','if=/dev/zero',f'of={dev}','bs=512',f'seek={start}',f'count={32*1024*1024//512}','conv=fsync']); run(['partprobe',dev])
    print('Prepare complete.')


def _save(state, lock):
    with lock: atomic_write_json(STATE,state)


def _wait_smart_long(d,state,lock,poll):
    begin_stage(state,d['id'],'smart-long','SMART extended self-test'); _save(state,lock)
    out=smart_text(d['dev'],['-t','long'])
    estimate=None
    m=re.search(r'Please wait\s+(\d+)\s+minutes',out,re.I)
    if m: estimate=int(m.group(1))*60
    start=time.monotonic()
    while True:
        time.sleep(poll); txt=smart_text(d['dev'],['-a']); status=selftest_status(txt); elapsed=time.monotonic()-start
        if estimate:
            progress=min(0.99,elapsed/estimate); eta=max(0,int(estimate-elapsed))
        else:
            progress=0.0; eta=None
        update_drive(state,d['id'],stage_progress=progress,stage_eta_seconds=eta,message=status or 'SMART extended self-test running'); _save(state,lock)
        lower=(status or '').lower()
        if status and not any(x in lower for x in ['remaining','progress','self-test routine in progress']): break
        if selftest_line(txt) and elapsed>30 and not status: break
    complete_stage(state,d['id'],'smart-long','SMART extended self-test complete'); _save(state,lock)


def _run_badblocks_stage(d,state,lock,stage,mode,log_path,poll):
    begin_stage(state,d['id'],stage,'Writing 0x00 pattern' if mode=='write' else 'Reading and verifying'); _save(state,lock)
    total=max(1,int(d['size_bytes'])); start=time.monotonic(); last_done=0
    if mode=='write': cmd=['badblocks','-wsv','-b','4096','-c','16384','-t','0x00',d['dev']]
    else: cmd=['badblocks','-sv','-b','4096','-c','16384',d['dev']]
    with open(log_path,'w') as log:
        proc=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT,text=True)
        while proc.poll() is None:
            time.sleep(poll)
            try: text=Path(log_path).read_text(errors='replace').replace('\r','\n')
            except OSError: text=''
            matches=re.findall(r'(\d+(?:\.\d+)?)% done',text)
            if matches: progress=min(0.999,float(matches[-1])/100.0)
            else: progress=0.0
            elapsed=max(1,time.monotonic()-start); processed=total*progress; rate=processed/elapsed/(1024*1024) if progress>0 else None; eta=int(elapsed*(1-progress)/progress) if progress>0 else None
            last_done=progress; update_drive(state,d['id'],stage_progress=progress,stage_eta_seconds=eta,throughput_mib_s=rate,message='Writing 0x00 pattern' if mode=='write' else 'Reading and verifying'); _save(state,lock)
        rc=proc.returncode
    if rc!=0: raise RuntimeError(f'badblocks {mode} exited with status {rc}; see {log_path}')
    complete_stage(state,d['id'],stage,'Write pass complete' if mode=='write' else 'Read/verify pass complete'); _save(state,lock)


def _qualify_drive(d,state,lock,batch_dir,poll):
    try:
        begin_stage(state,d['id'],'baseline-smart','Capturing baseline SMART'); _save(state,lock); (batch_dir/(d['serial']+'.before.smart.txt')).write_text(smart_text(d['dev'],['-x'])); complete_stage(state,d['id'],'baseline-smart'); _save(state,lock)
        begin_stage(state,d['id'],'smart-short','SMART short self-test'); _save(state,lock); smart_text(d['dev'],['-t','short']); time.sleep(90); complete_stage(state,d['id'],'smart-short'); _save(state,lock)
        _wait_smart_long(d,state,lock,poll)
        _run_badblocks_stage(d,state,lock,'surface-write','write',batch_dir/(Path(d['dev']).name+'.write.log'),poll)
        _run_badblocks_stage(d,state,lock,'surface-verify','read',batch_dir/(Path(d['dev']).name+'.verify.log'),poll)
        begin_stage(state,d['id'],'final-smart','Capturing final SMART'); _save(state,lock); final=smart_text(d['dev'],['-x']); (batch_dir/(d['serial']+'.after.smart.txt')).write_text(final); complete_stage(state,d['id'],'final-smart'); _save(state,lock)
        begin_stage(state,d['id'],'classify','Classifying result'); attrs=parse_attrs(final); health=parse_field(final,['SMART overall-health self-assessment test result','SMART Health Status']) or 'UNKNOWN'; result='PASS'
        try:
            if int(attrs['pending'])>0 or int(attrs['uncorrectable'])>0 or health.upper() not in ('OK','PASSED'): result='BAD'
            elif int(attrs['reallocated'])>0: result='REVIEW'
        except Exception: result='REVIEW'
        complete_stage(state,d['id'],'classify'); finish_drive(state,d['id'],result); _save(state,lock)
    except Exception as exc:
        fail_drive(state,d['id'],str(exc)); _save(state,lock)


def qualify(args):
    if not args.yes:
        print('Qualification is DESTRUCTIVE: the surface write test overwrites every selected test drive.'); print('Run: diskqual qualify --yes'); return
    drives=discover()
    if not drives: print('No eligible non-OS, unmounted disks found.'); return
    batch_id='qualify_'+datetime.now(timezone.utc).strftime('%Y-%m-%d_%H-%M-%S'); batch_dir=REPORTS/batch_id; batch_dir.mkdir(parents=True,exist_ok=True); state=create_batch_state(batch_id,drives); lock=threading.Lock(); _save(state,lock)
    threads=[threading.Thread(target=_qualify_drive,args=(d,state,lock,batch_dir,args.poll),daemon=False) for d in drives]
    for t in threads: t.start()
    try:
        while any(t.is_alive() for t in threads): os.system('clear'); print(render_dashboard(state)); time.sleep(args.interval)
    except KeyboardInterrupt:
        print('\nMonitor detached. Qualification threads remain attached to this process; do not terminate this command if tests must continue.'); raise
    for t in threads: t.join()
    state['status']='COMPLETE'; state['ended_utc']=datetime.now(timezone.utc).isoformat(); _save(state,lock); print(render_dashboard(state)); print(f'Qualification data saved: {batch_dir}')


def monitor(args):
    try:
        while True:
            os.system('clear'); print(render_dashboard(load_state(STATE))); time.sleep(args.interval)
    except KeyboardInterrupt: print('\nMonitor closed. Test processes are not signaled.')


def main():
    if shutil.which('smartctl') is None: print('smartctl not found. Install smartmontools.'); sys.exit(1)
    ensure_dirs(); p=argparse.ArgumentParser(prog='diskqual'); sub=p.add_subparsers(dest='cmd',required=True)
    sub.add_parser('inventory').set_defaults(func=inventory); sub.add_parser('quick').set_defaults(func=quick); sub.add_parser('smart-long').set_defaults(func=smart_long)
    r=sub.add_parser('report'); r.add_argument('--label',default='report'); r.set_defaults(func=cmd_report)
    m=sub.add_parser('monitor'); m.add_argument('--interval',type=int,default=5); m.set_defaults(func=monitor)
    w=sub.add_parser('prepare'); w.add_argument('--yes',action='store_true'); w.set_defaults(func=wipe_prepare)
    q=sub.add_parser('qualify'); q.add_argument('--yes',action='store_true'); q.add_argument('--interval',type=int,default=5); q.add_argument('--poll',type=int,default=10); q.set_defaults(func=qualify)
    args=p.parse_args(); args.func(args)
