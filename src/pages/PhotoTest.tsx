import { JSX, useEffect, useState } from "react";
import styles from "./PhotoTest.module.css";

const ASSETS_BASE = import.meta.env.DEV ? "/artemis-assets" : "https://media.artemisinrealtime.org";

interface Photo {
  id: string;
  title: string;
  date: string;
  source: string;
  thumbUrl: string;
  imgUrl: string;
  hiResUrl: string;
}

function PhotoTest(): JSX.Element {
  const [photos, setPhotos] = useState<Photo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(`${ASSETS_BASE}/artemis-ii/web/photos.json`)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json() as Promise<Photo[]>;
      })
      .then((data) => {
        setPhotos(data);
        setLoading(false);
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : String(err));
        setLoading(false);
      });
  }, []);

  return (
    <div className={styles.page}>
      <h1 className={styles.heading}>Photo Test — Artemis II</h1>
      {loading && <p className={styles.status}>Loading…</p>}
      {error && <p className={styles.status}>Error: {error}</p>}
      {!loading && !error && <p className={styles.status}>{photos.length} photos</p>}
      <div className={styles.grid}>
        {photos.map((photo) => (
          <a
            key={photo.id}
            className={styles.thumbLink}
            href={photo.imgUrl}
            target="_blank"
            rel="noreferrer"
            title={photo.title}
          >
            <img className={styles.thumb} src={photo.thumbUrl} alt={photo.title} loading="lazy" />
          </a>
        ))}
      </div>
    </div>
  );
}

export default PhotoTest;
