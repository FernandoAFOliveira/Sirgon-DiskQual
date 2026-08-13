# physical.py
from pathlib import Path


def drive_location(dev):
    name = Path(str(dev or '')).name
    if not name:
        return {'available': False, 'label': 'unavailable'}

    enclosure_root = Path('/sys/class/enclosure')
    if enclosure_root.exists():
        for slot in enclosure_root.glob('*/*'):
            block_dir = slot / 'device' / 'block'
            if (block_dir / name).exists():
                enclosure = slot.parent.name
                slot_name = slot.name
                locate = slot / 'locate'
                return {
                    'available': True,
                    'enclosure': enclosure,
                    'slot': slot_name,
                    'label': f'{enclosure}/{slot_name}',
                    'locate_supported': locate.exists(),
                    'locate_path': str(locate) if locate.exists() else None,
                }

    block = Path('/sys/class/block') / name / 'device'
    try:
        resolved = block.resolve()
    except OSError:
        resolved = block
    for parent in [resolved, *resolved.parents]:
        for pattern in ('enclosure_device:*', 'enclosure_device*'):
            for entry in parent.glob(pattern):
                locate = entry / 'locate'
                return {
                    'available': True,
                    'enclosure': entry.parent.name,
                    'slot': entry.name,
                    'label': f'{entry.parent.name}/{entry.name}',
                    'locate_supported': locate.exists(),
                    'locate_path': str(locate) if locate.exists() else None,
                }

    return {'available': False, 'label': 'unavailable', 'locate_supported': False, 'locate_path': None}
