"""Conservative text candidates and bounded acoustic correspondence, without ASR."""
import re


def tokens(text):
    return re.findall(r"[a-z0-9]+", text.lower())


def text_anchor_candidates(ia_rows, yt_rows, width=10):
    """Exact unique phrases propose correspondences; repeated phrases stay ambiguous.

    IA rows have id/recordingId/start/end/text. YT rows additionally have part.
    Contiguous YT tokens may span utterances but never a part boundary.
    """
    queries = {}
    for row in ia_rows:
        words = tokens(row["text"])
        if len(words) < width:
            continue
        begin = (len(words) - width) // 2
        phrase = tuple(words[begin:begin + width])
        queries.setdefault(phrase, []).append(row)
    hits = {key: [] for key in queries}
    parts = {}
    for row in yt_rows:
        parts.setdefault(row["part"], []).append(row)
    for part, rows in sorted(parts.items()):
        words, owners = [], []
        for row in sorted(rows, key=lambda row: (row["start"], row["id"])):
            word_list = tokens(row["text"])
            words.extend(word_list)
            owners.extend([row] * len(word_list))
        for i in range(len(words) - width + 1):
            phrase = tuple(words[i:i + width])
            if phrase in queries:
                first, last = owners[i], owners[i + width - 1]
                hits[phrase].append({"part": part, "start": first["start"], "end": last["end"],
                                    "segmentIds": list(dict.fromkeys(o["id"] for o in owners[i:i + width]))})
    anchors, ambiguous, missing = [], [], []
    for phrase, rows in queries.items():
        matches = hits[phrase]
        for row in rows:
            identity = {"iaId": row["id"], "recordingId": row["recordingId"], "iaStart": row["start"], "iaEnd": row["end"], "phrase": " ".join(phrase)}
            if len(matches) == 1 and len(rows) == 1:
                anchors.append(identity | {"youtube": matches[0], "verification": "text-candidate"})
            elif matches:
                ambiguous.append(identity | {"iaOccurrences": len(rows), "ytOccurrences": len(matches)})
            else:
                missing.append(identity)
    # An anchor list must be ordered within each IA recording before it can be
    # used to propose a continuous comparison interval.
    by_recording = {}
    for anchor in anchors:
        by_recording.setdefault(anchor["recordingId"], []).append(anchor)
    for group in by_recording.values():
        group.sort(key=lambda a: a["iaStart"])
        ordered = all((a["youtube"]["part"], a["youtube"]["start"]) <= (b["youtube"]["part"], b["youtube"]["start"]) for a, b in zip(group, group[1:]))
        for anchor in group:
            anchor["orderedWithinRecording"] = ordered
    matched_yt = {sid for a in anchors for sid in a["youtube"]["segmentIds"]}
    yt_summary = []
    for part, rows in sorted(parts.items()):
        speech = sorted((r for r in rows if len(tokens(r["text"])) >= 3), key=lambda r: r["start"])
        runs, current = [], []
        for row in speech:
            if row["id"] not in matched_yt:
                current.append(row)
            elif current:
                runs.append(current)
                current = []
        if current:
            runs.append(current)
        yt_summary.append({"part": part, "firstSpeechSeconds": speech[0]["start"] if speech else None,
                           "lastSpeechSeconds": speech[-1]["end"] if speech else None,
                           "unanchoredRuns": [{"start": r[0]["start"], "end": r[-1]["end"],
                                                "segmentIds": [s["id"] for s in r],
                                                "firstText": r[0]["text"], "lastText": r[-1]["text"]} for r in runs]})
    return {"anchors": anchors, "ambiguous": ambiguous, "missingIaPhrases": missing,
            "youtubeTextCoverage": yt_summary,
            "unanchoredYoutubeSegmentIds": [r["id"] for r in yt_rows if r["id"] not in matched_yt],
            "counts": {"iaRows": len(ia_rows), "youtubeRows": len(yt_rows), "uniqueAnchors": len(anchors),
                       "ambiguousIaPhrases": len(ambiguous), "missingIaPhrases": len(missing),
                       "anchoredYoutubeSegments": len(matched_yt)}}


def normalized_audio_match(reference, search, sample_rate, threshold=0.75, separation_s=0.5, lowpass_hz=None):
    """NCC against a larger search window; reject silence and ambiguous peaks.

    The returned interval is only the duration of the supplied reference.
    Mean subtraction and normalization tolerate independent recording gain.
    """
    import numpy as np
    from scipy.signal import correlate, butter, sosfilt
    ref = np.asarray(reference, dtype=np.float64)
    target = np.asarray(search, dtype=np.float64)
    if len(ref) < sample_rate or len(target) < len(ref):
        return {"verification": "unknown", "reason": "short-audio"}
    if lowpass_hz:
        sos = butter(6, lowpass_hz, fs=sample_rate, output="sos")
        ref, target = sosfilt(sos, ref), sosfilt(sos, target)
    ref = ref - ref.mean()
    energy = float(np.dot(ref, ref))
    if energy < 1e-8 or float(np.std(target)) < 1e-6:
        return {"verification": "unknown", "reason": "silent-audio"}
    sums = np.concatenate(([0.0], np.cumsum(target)))
    squares = np.concatenate(([0.0], np.cumsum(target * target)))
    n = len(ref)
    local_energy = squares[n:] - squares[:-n] - (sums[n:] - sums[:-n]) ** 2 / n
    scores = correlate(target, ref, mode="valid", method="fft") / np.sqrt(np.maximum(local_energy * energy, 1e-20))
    index = int(np.argmax(scores))
    peak = float(scores[index])
    excluded = max(1, int(sample_rate * separation_s))
    alternatives = np.concatenate((scores[:max(0, index-excluded)], scores[min(len(scores), index+excluded):]))
    second = float(np.max(alternatives)) if len(alternatives) else 0.0
    verified = peak >= threshold and peak - second >= 0.1
    return {"verification": "verified-bounded-acoustic" if verified else "unknown",
            "reason": "unique-normalized-correlation" if verified else "weak-or-ambiguous-correlation",
            "correlation": peak, "secondPeak": second, "offsetSeconds": index / sample_rate,
            "durationSeconds": n / sample_rate}
