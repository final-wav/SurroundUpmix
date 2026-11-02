
import os
import sys
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import struct
import xml.etree.ElementTree as ET
import numpy as np
import sys
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

def parse_time_str(t_str):
    if not t_str:
        return 0.0
    parts = t_str.split(':')
    if len(parts) == 3:
        h = float(parts[0])
        m = float(parts[1])
        s = float(parts[2])
        return h * 3600.0 + m * 60.0 + s
    return 0.0

def analyze_track_motion(wav_path):
    print("\n" + "="*80)
    print(f"ANALYZING DYNAMIC MOTION & AUTOMATION: {wav_path.name}")
    print("="*80)

    with open(wav_path, 'rb') as f:
        f.seek(12)
        xml_data = None
        data_offset = 0
        data_size = 0
        while True:
            hdr = f.read(8)
            if len(hdr) < 8: break
            cid, csize = struct.unpack('<4sI', hdr)
            if cid == b'axml':
                xml_data = f.read(csize).decode('utf-8', errors='ignore')
            elif cid == b'data':
                data_offset = f.tell()
                data_size = csize
                f.seek(f.tell() + csize)
            elif cid == b'ds64':
                ds = f.read(csize)
                _, data_size = struct.unpack('<QQ', ds[:16])
            else:
                f.seek(f.tell() + csize)
            if csize % 2 == 1: f.seek(f.tell() + 1)

    if not xml_data:
        print("No axml chunk found!")
        return

    root = ET.fromstring(xml_data)
    for elem in root.iter():
        if '}' in elem.tag:
            elem.tag = elem.tag.split('}', 1)[1]

    channel_blocks = {}
    for ch in root.findall('.//audioChannelFormat'):
        cid = ch.attrib.get('audioChannelFormatID')
        cname = ch.attrib.get('audioChannelFormatName')
        ctype = ch.attrib.get('typeDefinition')
        if ctype != 'Objects':
            continue
        blocks = ch.findall('.//audioBlockFormat')
        key_list = []
        for b in blocks:
            rtime = parse_time_str(b.attrib.get('rtime', ''))
            dur = parse_time_str(b.attrib.get('duration', ''))
            x_el = b.find('position[@coordinate="X"]')
            y_el = b.find('position[@coordinate="Y"]')
            z_el = b.find('position[@coordinate="Z"]')
            gain_el = b.find('gain')
            
            x = float(x_el.text) if x_el is not None and x_el.text else 0.0
            y = float(y_el.text) if y_el is not None and y_el.text else 0.0
            z = float(z_el.text) if z_el is not None and z_el.text else 0.0
            g = float(gain_el.text) if gain_el is not None and gain_el.text else 1.0
            key_list.append((rtime, dur, x, y, z, g))
        channel_blocks[cid] = (cname, key_list)

    dynamic_objects = []
    static_objects = []
    for cid, (cname, klist) in channel_blocks.items():
        if len(klist) <= 1:
            static_objects.append((cid, cname, klist[0] if klist else None))
        else:
            xs = [k[2] for k in klist]
            ys = [k[3] for k in klist]
            zs = [k[4] for k in klist]
            if (max(xs) - min(xs) > 0.05) or (max(ys) - min(ys) > 0.05) or (max(zs) - min(zs) > 0.05):
                dynamic_objects.append((cid, cname, klist))
            else:
                static_objects.append((cid, cname, klist[0]))

    print(f"\nStatic / Locked Positions ({len(static_objects)}):")
    for cid, cname, k in static_objects:
        if k:
            _, _, x, y, z, g = k
            print(f"  [{cid}] {cname:<20} -> Locked at X={x:+.2f}, Y={y:+.2f}, Z={z:+.2f} (Gain={g:.2f})")

    print(f"\nHighly Dynamic / Moving Objects ({len(dynamic_objects)}):")
    for cid, cname, klist in dynamic_objects:
        xs = [k[2] for k in klist]
        ys = [k[3] for k in klist]
        zs = [k[4] for k in klist]
        times = [k[0] for k in klist]
        print(f"\n  * [{cid}] {cname} ({len(klist)} keyframes across {times[-1]:.1f}s):")
        print(f"    Bounding Box: X=[{min(xs):+.2f}..{max(xs):+.2f}], Y=[{min(ys):+.2f}..{max(ys):+.2f}], Z=[{min(zs):+.2f}..{max(zs):+.2f}]")
        
        rear_events = [(t, x, y, z) for t, d, x, y, z, g in klist if y < -0.2]
        height_events = [(t, x, y, z) for t, d, x, y, z, g in klist if z > 0.2]
        
        print(f"    Rear Movements (Y < -0.2): {len(rear_events)} keyframes")
        if rear_events:
            print("    Key Rear Event Timestamps & Coordinates:")
            for ev in rear_events[:8]:
                print(f"      @ {ev[0]:6.1f}s: Pos=(X={ev[1]:+.2f}, Y={ev[2]:+.2f}, Z={ev[3]:+.2f})")
        
        print(f"    Ceiling/Height Movements (Z > 0.2): {len(height_events)} keyframes")
        if height_events:
            print("    Key Height Event Timestamps & Coordinates:")
            for ev in height_events[:8]:
                print(f"      @ {ev[0]:6.1f}s: Pos=(X={ev[1]:+.2f}, Y={ev[2]:+.2f}, Z={ev[3]:+.2f})")

for song in ["02 COME CLOSER.wav", "03 A BOY LIKE YOU.wav", "04 RING THE ALARM.wav"]:
    p = Path(r"M:\audio\library\music_flac\AURORA\TOMORA PROJECT") / song
    if p.is_file():
        analyze_track_motion(p)
