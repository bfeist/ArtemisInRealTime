import wave, os

root = r'F:\_repos\ArtemisInRealTime_assets\artemis-ii\raw\comm'
wavs = []
for dirpath, dirs, files in os.walk(root):
    for f in files:
        if f.lower().endswith('.wav'):
            wavs.append(os.path.join(dirpath, f))

print(f'Found {len(wavs)} WAV files')
if wavs:
    fp = sorted(wavs)[0]
    print(f'Checking: {os.path.basename(fp)}')
    with wave.open(fp, 'r') as w:
        print(f'  Sample rate: {w.getframerate()}Hz')
        print(f'  Channels: {w.getnchannels()}')
        print(f'  Sample width: {w.getsampwidth()} bytes')
        dur = w.getnframes() / w.getframerate()
        print(f'  Duration: {dur:.1f}s ({dur/60:.1f}min)')

    # Total duration
    total = 0
    for fp2 in wavs:
        with wave.open(fp2, 'r') as w2:
            total += w2.getnframes() / w2.getframerate()
    print(f'\nTotal duration: {total/3600:.1f} hours across {len(wavs)} files')

    # Check Orion-to-Earth_1 vs _2 breakdown
    ch1 = [w for w in wavs if 'Orion-to-Earth_1' in w]
    ch2 = [w for w in wavs if 'Orion-to-Earth_2' in w]
    print(f'Orion-to-Earth_1: {len(ch1)} files')
    print(f'Orion-to-Earth_2: {len(ch2)} files')
