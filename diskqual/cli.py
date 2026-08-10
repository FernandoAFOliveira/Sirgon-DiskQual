#!/usr/bin/env python3
import argparse, csv, json, os, re, shutil, subprocess, sys, time
from datetime import datetime
from pathlib import Path

BASE = Path(os.environ.get('DISKQUAL_HOME', '/opt/diskqual'))
DRIVES = BASE / 'drives.json'
REPORTS = BASE / 'reports'
LOGS = BASE / 'logs'
STATE = BASE / 'state.json'

TEST_DEV_GLOB = re.compile(r'^sd[b-z]+$')


def run(cmd, check=False):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check)


def sudo_needed():
    return os.geteuid() != 0


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
                return line.split(':',1)[1].strip()
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
    if not m: return ''
    return m.group(2).strip()


def list_block_disks():
    p = run(['lsblk','-dnpo','NAME,TYPE,SIZE,MODEL,SERIAL'])
    disks = []
    for line in p.stdout.splitlines():
        parts = line.split(None, 4)
        if len(parts) >= 2 and parts[1] == 'disk':
            disks.append(parts[0])
    return disks


def mounted_or_os(dev):
    name = Path(dev).name
    if name == 'sda': return True
    p = run(['lsblk','-nr','-o','MOUNTPOINT', dev])
    return any(x.strip().startswith('/') for x in p.stdout.splitlines() if x.strip())


def discover():
    ensure_dirs()
    drives = []
    for dev in list_block_disks():
        if mounted_or_os(dev):
            continue
        info = smart_text(dev, ['-i'])
        alltxt = smart_text(dev, ['-a'])
        attrs = parse_attrs(alltxt)
        health = parse_field(alltxt, ['SMART overall-health self-assessment test result','SMART Health Status']) or 'UNKNOWN'
        serial = parse_field(info, ['Serial Number']) or Path(dev).name
        model = parse_field(info, ['Device Model','Product']) or 'UNKNOWN'
        size = run(['blockdev','--getsize64',dev]).stdout.strip()
        drives.append({'dev':dev,'serial':serial,'model':model,'size_bytes':size,'health':health,**attrs})
    DRIVES.write_text(json.dumps(drives, indent=2))
    return drives


def load_drives():
    if DRIVES.exists():
        return json.loads(DRIVES.read_text())
    return discover()


def inventory(args):
    drives = discover()
    batch = REPORTS / ('inventory_' + datetime.utcnow().strftime('%Y-%m-%d_%H-%M-%S'))
    batch.mkdir(parents=True, exist_ok=True)
    with open(batch/'inventory.csv','w',newline='') as f:
        w = csv.DictWriter(f, fieldnames=['dev','serial','model','size_bytes','health','power_on_hours','reallocated','pending','uncorrectable','temperature'])
        w.writeheader(); w.writerows(drives)
    for d in drives:
        (batch/(d['serial']+'.smart.txt')).write_text(smart_text(d['dev'], ['-x']))
    print(f'Inventory saved: {batch}')
    print_table(drives)


def print_table(drives):
    print('DEV   SERIAL          HEALTH  HOURS  REALLOC PENDING UNCORR TEMP MODEL')
    for d in drives:
        print(f"{Path(d['dev']).name:<5} {d['serial']:<15} {d['health']:<7} {d.get('power_on_hours',''):<6} {d.get('reallocated',''):<7} {d.get('pending',''):<7} {d.get('uncorrectable',''):<6} {d.get('temperature',''):<4} {d['model']}")


def quick(args):
    drives = load_drives()
    print('Starting SMART short tests...')
    for d in drives:
        print(f"{d['dev']} {d['serial']}")
        print(smart_text(d['dev'], ['-t','short']).splitlines()[-3:])
    print('Waiting 90 seconds...')
    time.sleep(90)
    report('quick')


def smart_long(args):
    drives = load_drives()
    batch = datetime.utcnow().strftime('%Y-%m-%d_%H-%M-%S')
    STATE.write_text(json.dumps({'stage':'smart-long','started_utc':datetime.utcnow().isoformat(), 'batch':batch}, indent=2))
    for d in drives:
        print(f"Starting SMART long on {d['dev']} {d['serial']}")
        out = smart_text(d['dev'], ['-t','long'])
        print('\n'.join(out.splitlines()[-6:]))
    print('SMART long tests launched. Use: diskqual monitor')


def report(label='report'):
    drives = load_drives()
    batch = REPORTS / (label + '_' + datetime.utcnow().strftime('%Y-%m-%d_%H-%M-%S'))
    batch.mkdir(parents=True, exist_ok=True)
    rows=[]
    for d in drives:
        txt = smart_text(d['dev'], ['-a'])
        attrs = parse_attrs(txt)
        health = parse_field(txt, ['SMART overall-health self-assessment test result','SMART Health Status']) or 'UNKNOWN'
        line = selftest_line(txt)
        status = 'GOOD'
        try:
            if int(attrs['pending']) > 0 or int(attrs['uncorrectable']) > 0: status='BAD'
            elif int(attrs['reallocated']) > 0: status='REVIEW'
        except Exception: status='REVIEW'
        if line and any(x in line.lower() for x in ['failure','aborted','interrupted']): status='BAD'
        row = {**d, **attrs, 'health':health, 'selftest':line, 'result':status}
        rows.append(row)
        (batch/(d['serial']+'.smart.txt')).write_text(smart_text(d['dev'], ['-x']))
    with open(batch/'summary.csv','w',newline='') as f:
        fields=['dev','serial','model','size_bytes','health','power_on_hours','reallocated','pending','uncorrectable','temperature','selftest','result']
        w=csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)
    with open(batch/'summary.txt','w') as f:
        for r in rows:
            f.write(f"{r['dev']} {r['serial']} {r['result']} {r['health']} {r['selftest']}\n")
    print(f'Report saved: {batch}')
    print_table(rows)


def cmd_report(args):
    report(args.label)


def wipe_prepare(args):
    drives = load_drives()
    print('DESTRUCTIVE PREP: will clear signatures on test drives only.')
    if not args.yes:
        print('Add --yes to execute.'); return
    for d in drives:
        dev=d['dev']
        print(f'Preparing {dev} {d["serial"]}')
        run(['wipefs','-a',dev])
        run(['sgdisk','--zap-all',dev])
        run(['dd','if=/dev/zero',f'of={dev}','bs=1M','count=32','conv=fsync'], check=False)
        sectors=int(run(['blockdev','--getsz',dev]).stdout.strip())
        start=sectors-(32*1024*1024//512)
        run(['dd','if=/dev/zero',f'of={dev}','bs=512',f'seek={start}',f'count={32*1024*1024//512}','conv=fsync'], check=False)
        run(['partprobe',dev])
    print('Prepare complete.')


def monitor(args):
    while True:
        os.system('clear')
        print('Dell R510 Disk Qualification Station')
        print('='*72)
        if STATE.exists():
            print(STATE.read_text())
        print()
        rows=[]
        for d in load_drives():
            txt=smart_text(d['dev'], ['-a'])
            attrs=parse_attrs(txt)
            rows.append({**d, **attrs, 'health':parse_field(txt,['SMART overall-health self-assessment test result','SMART Health Status']) or 'UNKNOWN','exec':selftest_status(txt),'selftest':selftest_line(txt)})
        print('DEV   SERIAL          HEALTH  TEMP HOURS  STATUS')
        for r in rows:
            status = r['exec'] or r['selftest'] or 'UNKNOWN'
            print(f"{Path(r['dev']).name:<5} {r['serial']:<15} {r['health']:<7} {r.get('temperature',''):<4} {r.get('power_on_hours',''):<6} {status[:55]}")
        print('\nCtrl-C to exit. Refresh: 30 sec')
        time.sleep(args.interval)


def main():
    if shutil.which('smartctl') is None:
        print('smartctl not found. Install smartmontools.'); sys.exit(1)
    ensure_dirs()
    p=argparse.ArgumentParser(prog='diskqual')
    sub=p.add_subparsers(dest='cmd', required=True)
    sub.add_parser('inventory').set_defaults(func=inventory)
    sub.add_parser('quick').set_defaults(func=quick)
    sub.add_parser('smart-long').set_defaults(func=smart_long)
    r=sub.add_parser('report'); r.add_argument('--label',default='report'); r.set_defaults(func=cmd_report)
    m=sub.add_parser('monitor'); m.add_argument('--interval',type=int,default=30); m.set_defaults(func=monitor)
    w=sub.add_parser('prepare'); w.add_argument('--yes',action='store_true'); w.set_defaults(func=wipe_prepare)
    args=p.parse_args(); args.func(args)
