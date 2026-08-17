#!/usr/bin/env python3
# split_vocals.py <input_vocals> <output_dir> [model_dir]
# Splits a vocal stem into LEAD + BACKING using a Mel-Band Roformer karaoke model.
# Renames the outputs to lead.flac / backing.flac. Prints "LEAD <path>" and "BACKING <path>".
import sys, os, glob, shutil
from audio_separator.separator import Separator

MODEL = 'mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt'

def main():
    inp = sys.argv[1]
    outdir = sys.argv[2]
    model_dir = sys.argv[3] if len(sys.argv) > 3 else os.path.join(outdir, '_models')
    os.makedirs(outdir, exist_ok=True)
    os.makedirs(model_dir, exist_ok=True)

    sep = Separator(output_dir=outdir, output_format='FLAC', model_file_dir=model_dir)
    sep.load_model(model_filename=MODEL)
    files = sep.separate(inp)                      # returns list of output file paths (relative to outdir)
    paths = [f if os.path.isabs(f) else os.path.join(outdir, f) for f in files]

    # karaoke model: the "Vocals" output is the LEAD, the "Instrumental" output is the BACKING
    lead = next((p for p in paths if 'Vocals' in os.path.basename(p) or '(Vocals)' in os.path.basename(p)), None)
    backing = next((p for p in paths if 'Instrumental' in os.path.basename(p)), None)
    if lead is None or backing is None:
        # fall back to order if names differ
        if len(paths) >= 2:
            lead, backing = paths[0], paths[1]

    lead_out = os.path.join(outdir, 'lead.flac')
    back_out = os.path.join(outdir, 'backing.flac')
    if lead and os.path.exists(lead):    shutil.copyfile(lead, lead_out)
    if backing and os.path.exists(backing): shutil.copyfile(backing, back_out)
    print("LEAD", lead_out)
    print("BACKING", back_out)

main()
