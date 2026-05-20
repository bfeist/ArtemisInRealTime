import { JSX, useEffect, useMemo, useState } from "react";
import styles from "./PhotoTest.module.css";
import PhotoDetail from "./PhotoDetail.tsx";
import type { BracketsIndex, DateSource, ExportSource, Photo } from "../types/photos.ts";

const ASSETS_BASE = import.meta.env.DEV ? "/artemis-assets" : "https://media.artemisinrealtime.org";

const DATE_SOURCE_TONE: Record<DateSource, string> = {
  exif_offset: styles.toneGreen,
  io_nhq: styles.toneGreen,
  io_exif: styles.toneGreen,
  io_corrected: styles.toneYellow,
  io_onboard: styles.toneYellow,
  exif_notz: styles.toneOrange,
  flickr: styles.toneOrange,
  nasa_images: styles.toneOrange,
};

// Photos whose date is at midnight UTC have only day-level precision —
// they're placed at 00:00:00 and look wrong on any time-based timeline.
function isMidnightUtc(iso: string): boolean {
  const d = new Date(iso);
  return d.getUTCHours() === 0 && d.getUTCMinutes() === 0 && d.getUTCSeconds() === 0;
}

const DATE_SOURCE_LABEL: Record<DateSource, string> = {
  exif_offset: "EXIF OffsetTimeOriginal (camera-set timezone)",
  io_nhq: "Image Online — NASA HQ archive",
  io_exif: "Image Online — scraped EXIF (DateCreated / DigitalCreationTime)",
  io_corrected: "Image Online — corrected timestamp",
  io_onboard: "Image Online — onboard timestamp",
  exif_notz: "EXIF DateTimeOriginal (camera-local, no UTC offset)",
  flickr: "Flickr metadata",
  nasa_images: "NASA Images metadata",
};

const EXPORT_SOURCE_LABEL: Record<ExportSource, string> = {
  eol: "EOL (Earth Observations Lab — raw NEF)",
  nasa_images: "NASA Images (images.nasa.gov)",
  flickr: "Flickr",
  ia_stills: "Internet Archive — stills",
};

function calendarDate(iso: string): string {
  // Returns the UTC calendar date (YYYY-MM-DD) used as the group key.
  return iso.slice(0, 10);
}

function formatDateHeading(ymd: string): string {
  return new Date(ymd + "T12:00:00Z").toLocaleDateString("en-US", {
    month: "long",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  });
}

interface DateGroup {
  date: string;
  photos: Photo[];
}

function PhotoTest(): JSX.Element {
  const [photos, setPhotos] = useState<Photo[]>([]);
  const [brackets, setBrackets] = useState<BracketsIndex | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [hideQuestionable, setHideQuestionable] = useState(true);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      fetch(`${ASSETS_BASE}/artemis-ii/web/photos.json`).then((r) => {
        if (!r.ok) throw new Error(`photos.json HTTP ${r.status}`);
        return r.json() as Promise<Photo[]>;
      }),
      fetch(`${ASSETS_BASE}/artemis-ii/web/photos/brackets.json`).then((r) => {
        // brackets.json may be absent on early missions — treat 404 as empty.
        if (r.status === 404) return {} as BracketsIndex;
        if (!r.ok) throw new Error(`brackets.json HTTP ${r.status}`);
        return r.json() as Promise<BracketsIndex>;
      }),
    ])
      .then(([rawPhotos, br]) => {
        if (cancelled) return;
        const sorted = [...rawPhotos].sort((a, b) => a.date.localeCompare(b.date));
        setPhotos(sorted);
        setBrackets(br);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const photosById = useMemo(() => {
    const m: Record<string, Photo> = {};
    for (const p of photos) m[p.id] = p;
    return m;
  }, [photos]);

  const visiblePhotos = useMemo(
    () => (hideQuestionable ? photos.filter((p) => !isMidnightUtc(p.date)) : photos),
    [photos, hideQuestionable]
  );

  const groups: DateGroup[] = useMemo(() => {
    const out: DateGroup[] = [];
    let cur: DateGroup | null = null;
    for (const p of visiblePhotos) {
      const date = calendarDate(p.date);
      if (!cur || cur.date !== date) {
        cur = { date, photos: [] };
        out.push(cur);
      }
      cur.photos.push(p);
    }
    return out;
  }, [visiblePhotos]);

  const midnightCount = useMemo(() => photos.filter((p) => isMidnightUtc(p.date)).length, [photos]);

  const stats = useMemo(() => {
    const dateBreakdown: Record<string, number> = {};
    const sourceBreakdown: Record<string, number> = {};
    let bracketHeroCount = 0;
    for (const p of visiblePhotos) {
      dateBreakdown[p.dateSource] = (dateBreakdown[p.dateSource] ?? 0) + 1;
      for (const s of p.exportedIn) sourceBreakdown[s] = (sourceBreakdown[s] ?? 0) + 1;
      if (p.bracketSetId) bracketHeroCount++;
    }
    return {
      total: visiblePhotos.length,
      bracketSets: brackets ? Object.keys(brackets).length : 0,
      bracketHeroCount,
      dateBreakdown,
      sourceBreakdown,
    };
  }, [visiblePhotos, brackets]);

  const active = activeId ? photosById[activeId] : null;

  return (
    <div className={styles.page}>
      <h1 className={styles.heading}>Photo Test — Artemis II</h1>

      {loading && <p className={styles.status}>Loading…</p>}
      {error && <p className={styles.status}>Error: {error}</p>}

      {!loading && !error && (
        <label className={styles.toggleRow}>
          <input
            type="checkbox"
            checked={hideQuestionable}
            onChange={(e) => setHideQuestionable(e.target.checked)}
          />
          Hide photos with no intraday time ({midnightCount} photos — day-precision only)
        </label>
      )}

      {!loading && !error && (
        <div className={styles.statsStrip}>
          <div className={styles.statCard}>
            <div className={styles.statBig}>{stats.total.toLocaleString()}</div>
            <div className={styles.statLabel}>photos</div>
          </div>
          <div className={styles.statCard}>
            <div className={styles.statBig}>{stats.bracketSets.toLocaleString()}</div>
            <div className={styles.statLabel}>bracket sets ({stats.bracketHeroCount} heroes)</div>
          </div>
          <div className={styles.statCard}>
            <div className={styles.statLabel}>date provenance</div>
            <div className={styles.statChips}>
              {Object.entries(stats.dateBreakdown)
                .sort((a, b) => b[1] - a[1])
                .map(([src, n]) => (
                  <span
                    key={src}
                    className={`${styles.chip} ${DATE_SOURCE_TONE[src as DateSource] ?? styles.toneGray}`}
                  >
                    {src}: {n}
                  </span>
                ))}
            </div>
          </div>
          <div className={styles.statCard}>
            <div className={styles.statLabel}>exported in</div>
            <div className={styles.statChips}>
              {Object.entries(stats.sourceBreakdown)
                .sort((a, b) => b[1] - a[1])
                .map(([src, n]) => (
                  <span key={src} className={`${styles.chip} ${styles.toneGray}`}>
                    {src}: {n}
                  </span>
                ))}
            </div>
          </div>
        </div>
      )}

      {groups.map((group) => (
        <section key={group.date} className={styles.fdSection}>
          <h2 className={styles.fdHeading}>
            {formatDateHeading(group.date)}{" "}
            <span className={styles.fdCount}>({group.photos.length})</span>
          </h2>
          <div className={styles.grid}>
            {group.photos.map((photo) => (
              <PhotoCard key={photo.id} photo={photo} onClick={() => setActiveId(photo.id)} />
            ))}
          </div>
        </section>
      ))}

      {active && (
        <PhotoDetail
          assetsBase={ASSETS_BASE}
          photo={active}
          photosById={photosById}
          brackets={brackets}
          onClose={() => setActiveId(null)}
        />
      )}
    </div>
  );
}

interface CardProps {
  photo: Photo;
  onClick: () => void;
}

function PhotoCard({ photo, onClick }: CardProps): JSX.Element {
  const dateLabel = new Date(photo.date).toISOString().slice(11, 23) + " UTC";
  const toneClass = DATE_SOURCE_TONE[photo.dateSource] ?? styles.toneGray;
  return (
    <button type="button" className={styles.thumbBtn} onClick={onClick}>
      <div className={styles.thumbWrap}>
        <img
          className={styles.thumb}
          src={`${ASSETS_BASE}/artemis-ii/web${photo.thumbUrl}`.replaceAll(" ", "%20")}
          alt={photo.title || photo.id}
          loading="lazy"
        />
        {photo.bracketSetId && (
          <span className={styles.bracketBadge} title="AEB bracket hero">
            ⧉
          </span>
        )}
        <span className={styles.exportBadges}>
          {photo.exportedIn.map((s: ExportSource) => (
            <span key={s} className={styles.exportBadge} title={EXPORT_SOURCE_LABEL[s] ?? s}>
              {s[0].toUpperCase()}
            </span>
          ))}
        </span>
      </div>
      <div className={styles.thumbCaption}>
        <span
          className={`${styles.chipDot} ${toneClass}`}
          title={DATE_SOURCE_LABEL[photo.dateSource] ?? photo.dateSource}
        />
        <span className={styles.thumbTime}>{dateLabel}</span>
      </div>
    </button>
  );
}

export default PhotoTest;
