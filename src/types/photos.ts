export type DateSource =
  | "exif_offset"
  | "io_nhq"
  | "io_exif"
  | "io_corrected"
  | "io_onboard"
  | "flickr"
  | "nasa_images";

export type ExportSource = "eol" | "nasa_images" | "flickr" | "ia_stills";

export type BracketDetectionSource = "shooting_mode" | "makernote_shotnumber" | "ev_heuristic";

export interface Photo {
  id: string;
  title: string;
  description?: string;
  date: string;
  dateSource: DateSource;
  thumbUrl: string;
  imgUrl: string;
  hiResUrl: string;
  exifUrl: string;
  hasExif: boolean;
  exportedIn: ExportSource[];
  bracketSetId?: string;
}

export interface BracketSet {
  setId: string;
  hero: string;
  members: string[];
  evs: number[];
  detectionSource: BracketDetectionSource;
}

export type BracketsIndex = Record<string, BracketSet>;

export interface ExifMeta {
  nasaId: string;
  exifSource: string;
  date: string;
  dateSource: DateSource;
  exported: boolean;
  exportedIn: ExportSource[];
}

/**
 * EXIF JSON has two shapes:
 *  - raw_crew (NEF): grouped under EXIF / MakerNotes / XMP / Composite
 *  - JPEG sources (Flickr, NASA Images): flattened at top level
 * We accept both by treating most fields as optional and looking in both places at render time.
 */
export interface PhotoExif {
  DateTimeOriginal?: string;
  DateTimeOriginalUTC?: string;
  OffsetTimeOriginal?: string;
  ExposureBiasValue?: number;
  EXIF?: Record<string, unknown>;
  MakerNotes?: Record<string, unknown>;
  XMP?: Record<string, unknown>;
  Composite?: Record<string, unknown>;
  _meta?: ExifMeta;
  // Allow flattened fields (Flickr/NASA images JPEGs)
  [key: string]: unknown;
}
