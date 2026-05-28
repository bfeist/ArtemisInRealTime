import { JSX, useEffect, useState } from "react";
import styles from "./PhotoDetail.module.css";
import type { BracketSet, BracketsIndex, DateSource, Photo, PhotoExif } from "../types/photos.ts";

interface Props {
  assetsBase: string;
  photo: Photo;
  photosById: Record<string, Photo>;
  brackets: BracketsIndex | null;
  onClose: () => void;
}

function fmtShutter(v: unknown): string | null {
  if (typeof v === "string") return v; // e.g. "1/400"
  if (typeof v === "number") {
    if (v >= 1) return `${v}s`;
    const denom = Math.round(1 / v);
    return `1/${denom}s`;
  }
  return null;
}

function pick<T>(exif: PhotoExif | null, ...keys: string[]): T | null {
  if (!exif) return null;
  for (const key of keys) {
    if (key.includes(".")) {
      const [group, field] = key.split(".");
      const g = exif[group] as Record<string, unknown> | undefined;
      if (g && g[field] !== undefined && g[field] !== null) return g[field] as T;
    } else if (exif[key] !== undefined && exif[key] !== null) {
      return exif[key] as T;
    }
  }
  return null;
}

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

/** Keys to skip in the "all fields" detail view (already shown in summary or not useful). */
const SKIP_KEYS = new Set(["_meta", "SourceFile"]);

/** Render a value as a human-readable string. */
function fmtValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") return String(v);
  if (Array.isArray(v)) return v.map(fmtValue).join(", ");
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

/** Collapsible section that renders a flat key-value table for one EXIF group. */
function ExifSection({ label, data }: { label: string; data: Record<string, unknown> }) {
  const [open, setOpen] = useState(true);
  const entries = Object.entries(data).filter(([, v]) => v != null && typeof v !== "object");
  const nested = Object.entries(data).filter(
    ([, v]) => v != null && typeof v === "object" && !Array.isArray(v)
  );
  if (entries.length === 0 && nested.length === 0) return null;
  return (
    <div className={styles.exifSection}>
      <button type="button" className={styles.exifSectionToggle} onClick={() => setOpen((v) => !v)}>
        <span className={styles.exifSectionArrow}>{open ? "▾" : "▸"}</span> {label}
        <span className={styles.exifSectionCount}>{entries.length + nested.length}</span>
      </button>
      {open && (
        <table className={styles.exifTable}>
          <tbody>
            {entries.map(([k, v]) => (
              <tr key={k}>
                <td>{k}</td>
                <td>{fmtValue(v)}</td>
              </tr>
            ))}
            {nested.map(([k, v]) => (
              <tr key={k}>
                <td>{k}</td>
                <td className={styles.nestedValue}>{fmtValue(v)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

/** Full EXIF detail view: groups nested objects as collapsible sections, top-level scalars in a "General" group. */
function ExifGroups({ exif }: { exif: PhotoExif }) {
  const topLevel: Record<string, unknown> = {};
  const groups: [string, Record<string, unknown>][] = [];
  for (const [key, val] of Object.entries(exif)) {
    if (SKIP_KEYS.has(key)) continue;
    if (val && typeof val === "object" && !Array.isArray(val)) {
      groups.push([key, val as Record<string, unknown>]);
    } else {
      topLevel[key] = val;
    }
  }
  return (
    <div className={styles.exifDetails}>
      {Object.keys(topLevel).length > 0 && <ExifSection label="General" data={topLevel} />}
      {groups.map(([label, data]) => (
        <ExifSection key={label} label={label} data={data} />
      ))}
    </div>
  );
}

function PhotoDetail({
  assetsBase,
  photo: initialPhoto,
  photosById,
  brackets,
  onClose,
}: Props): JSX.Element {
  const [activeId, setActiveId] = useState(initialPhoto.id);
  const [exif, setExif] = useState<PhotoExif | null>(null);
  const [exifLoading, setExifLoading] = useState(false);
  const [exifError, setExifError] = useState<string | null>(null);
  const [showRawExif, setShowRawExif] = useState(false);

  // The active id may be a non-hero bracket member that is NOT in photos.json
  // (which only contains heroes). Fall back to URLs built by convention and to
  // the hero's metadata for fields like title / description / dateSource.
  const heroPhoto = initialPhoto;
  const display: Photo = photosById[activeId] ?? heroPhoto;
  const bracket: BracketSet | null =
    heroPhoto.bracketSetId && brackets ? (brackets[heroPhoto.bracketSetId] ?? null) : null;
  const evIndex = bracket ? bracket.members.indexOf(activeId) : -1;
  const activeEv = evIndex >= 0 && bracket ? bracket.evs[evIndex] : undefined;
  const isViewingHero = activeId === heroPhoto.id;
  const encodedActiveId = encodeURIComponent(activeId);
  const hiResUrl = `/photos/hires/${encodedActiveId}.jpg`;
  const exifUrlPath = `/photos/exif/${encodedActiveId}.json`;

  // For non-hero bracket members `display` falls back to the hero's
  // metadata, so `display.hasExif` reflects the hero. We only skip the
  // fetch when we're looking at the hero itself and it has no EXIF.
  const shouldFetchExif = isViewingHero ? display.hasExif !== false : true;

  useEffect(() => {
    setExif(null);
    setExifError(null);
    setShowRawExif(false);

    if (!shouldFetchExif) {
      setExifLoading(false);
      return;
    }

    setExifLoading(true);
    const url = `${assetsBase}/artemis-ii/web${exifUrlPath}`;
    let cancelled = false;
    fetch(url)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.text();
      })
      .then((text) => {
        if (cancelled) return;
        try {
          const data = JSON.parse(text) as PhotoExif;
          setExif(data);
        } catch {
          setExifError(`JSON.parse: ${text.slice(0, 80)}…`);
        }
        setExifLoading(false);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setExifError(err instanceof Error ? err.message : String(err));
          setExifLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [exifUrlPath, assetsBase, shouldFetchExif]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const make = pick<string>(exif, "EXIF.Make", "Make");
  const model = pick<string>(exif, "EXIF.Model", "Model");
  const lens = pick<string>(exif, "EXIF.LensModel", "LensModel", "Composite.LensID");
  const shutter = fmtShutter(pick(exif, "EXIF.ExposureTime", "ExposureTime"));
  const fnumber = pick<number>(exif, "EXIF.FNumber", "FNumber");
  const iso = pick<number>(exif, "EXIF.ISO", "ISO", "ISOSpeedRatings");
  const focal = pick<number | string>(exif, "EXIF.FocalLength", "FocalLength");
  const evBias = pick<number>(exif, "ExposureBiasValue", "EXIF.ExposureCompensation");
  const gps = pick<Record<string, unknown>>(exif, "EXIF.GPSInfo", "GPSInfo");
  const shootingMode = pick<string>(exif, "MakerNotes.ShootingMode");
  const autoBracketOrder = pick<string>(exif, "MakerNotes.AutoBracketOrder");
  const bracketValue = pick<number>(exif, "MakerNotes.ExposureBracketValue");

  // The per-photo EXIF JSON's _meta carries the actual frame's resolved UTC
  // and provenance — important for non-hero bracket members, since `display`
  // (sourced from photos.json) falls back to the hero's date for those.
  const exifMeta = (exif?._meta ?? null) as { date?: string; dateSource?: DateSource } | null;
  const effectiveDate = exifMeta?.date ?? display.date;
  const effectiveDateSource: DateSource = exifMeta?.dateSource ?? display.dateSource;
  const toneClass = DATE_SOURCE_TONE[effectiveDateSource] ?? styles.toneGray;

  function clean(s: string | null): string | null {
    return s ? s.replace(/ +/g, "").trim() : null;
  }

  return (
    // eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions
    <div
      className={styles.backdrop}
      role="dialog"
      aria-modal="true"
      onClick={onClose}
      onKeyDown={(e) => {
        if (e.key === "Escape") onClose();
      }}
    >
      {/* eslint-disable-next-line jsx-a11y/no-static-element-interactions, jsx-a11y/click-events-have-key-events */}
      <div className={styles.panel} onClick={(e) => e.stopPropagation()}>
        <button type="button" className={styles.close} onClick={onClose} aria-label="Close">
          ×
        </button>

        <div className={styles.imageWrap}>
          <img
            className={styles.hero}
            src={`${assetsBase}/artemis-ii/web${hiResUrl}`}
            alt={display.title || activeId}
          />
        </div>

        <div className={styles.meta}>
          <div className={styles.metaRow}>
            <strong className={styles.id}>{activeId}</strong>
            {!isViewingHero && activeEv != null && (
              <span className={`${styles.chip} ${styles.toneBlue}`}>
                {activeEv === 0 ? "0 EV" : `${activeEv > 0 ? "+" : ""}${activeEv} EV`}
              </span>
            )}
            <span className={`${styles.chip} ${toneClass}`} title="Date provenance">
              {effectiveDateSource}
            </span>
            {display.exportedIn.map((src) => (
              <span key={src} className={`${styles.chip} ${styles.toneGray}`}>
                {src}
              </span>
            ))}
            {bracket && (
              <span className={`${styles.chip} ${styles.toneBlue}`}>
                ⧉ {bracket.members.length}-frame AEB
              </span>
            )}
          </div>
          <div className={styles.date}>
            {new Date(effectiveDate).toISOString().replace("T", " ").replace("Z", " UTC")}
          </div>
          {display.title && <div className={styles.title}>{display.title}</div>}
          {display.description && <div className={styles.description}>{display.description}</div>}
        </div>

        {bracket && (
          <div className={styles.brackets}>
            <div className={styles.bracketsHeader}>
              Bracket set ({bracket.detectionSource}) — click a frame to view
            </div>
            <div className={styles.bracketRow}>
              {bracket.members.map((memberId, i) => {
                const ev = bracket.evs[i];
                const isHero = memberId === bracket.hero;
                const isActive = memberId === activeId;
                const evLabel = ev === 0 ? "0 EV (metered)" : `${ev > 0 ? "+" : ""}${ev} EV`;
                return (
                  <button
                    key={memberId}
                    type="button"
                    className={`${styles.bracketBtn} ${isActive ? styles.bracketActive : ""}`}
                    onClick={() => setActiveId(memberId)}
                    title={`${memberId} • ${evLabel}${isHero ? " • hero" : ""}`}
                  >
                    <img
                      className={styles.bracketThumb}
                      src={`${assetsBase}/artemis-ii/web/photos/thumb/${encodeURIComponent(memberId)}.jpg`}
                      alt={memberId}
                      loading="lazy"
                    />
                    <div className={styles.bracketCaption}>
                      {evLabel}
                      {isHero && <span className={styles.heroMark}> ★</span>}
                    </div>
                  </button>
                );
              })}
            </div>
          </div>
        )}

        <div className={styles.exif}>
          <div className={styles.exifHeader}>EXIF</div>
          {exifLoading && <div className={styles.exifStatus}>Loading EXIF…</div>}
          {exifError && <div className={styles.exifStatus}>EXIF unavailable: {exifError}</div>}
          {exif && (
            <>
              <table className={styles.exifTable}>
                <tbody>
                  {(make || model) && (
                    <tr>
                      <td>Camera</td>
                      <td>
                        {clean(make)} {clean(model)}
                      </td>
                    </tr>
                  )}
                  {lens && (
                    <tr>
                      <td>Lens</td>
                      <td>{clean(lens)}</td>
                    </tr>
                  )}
                  {(shutter || fnumber || iso || focal) && (
                    <tr>
                      <td>Exposure</td>
                      <td>
                        {shutter ?? "—"}
                        {fnumber != null && ` • f/${fnumber}`}
                        {iso != null && ` • ISO ${iso}`}
                        {focal != null && ` • ${focal}${typeof focal === "number" ? "mm" : ""}`}
                      </td>
                    </tr>
                  )}
                  {evBias != null && (
                    <tr>
                      <td>EV bias</td>
                      <td>
                        {evBias > 0 ? "+" : ""}
                        {evBias}
                      </td>
                    </tr>
                  )}
                  {gps && (gps.GPSLatitude != null || gps.GPSLongitude != null) && (
                    <tr>
                      <td>GPS</td>
                      <td>
                        {String(gps.GPSLatitude ?? "?")}, {String(gps.GPSLongitude ?? "?")}
                      </td>
                    </tr>
                  )}
                  {(shootingMode || autoBracketOrder || bracketValue != null) && (
                    <tr>
                      <td>Bracket info</td>
                      <td>
                        {shootingMode && <div>{shootingMode}</div>}
                        {autoBracketOrder && <div>Order: {autoBracketOrder}</div>}
                        {bracketValue != null && <div>Bracket value: {bracketValue}</div>}
                      </td>
                    </tr>
                  )}
                  {exif._meta && (
                    <tr>
                      <td>EXIF source</td>
                      <td>{exif._meta.exifSource}</td>
                    </tr>
                  )}
                </tbody>
              </table>
              <button
                type="button"
                className={styles.rawToggle}
                onClick={() => setShowRawExif((v) => !v)}
              >
                {showRawExif ? "Hide full EXIF" : "Show full EXIF"}
              </button>
              {showRawExif && <ExifGroups exif={exif} />}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export default PhotoDetail;
