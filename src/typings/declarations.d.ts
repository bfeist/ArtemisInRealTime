declare module "*.module.css" {
  const classes: { [key: string]: string };
  export default classes;
}

declare module "*.css" {
  const content: string;
  export default content;
}

declare module "*.webp" {
  const src: string;
  export default src;
}

declare module "*.png" {
  const src: string;
  export default src;
}

declare module "*.jpg" {
  const src: string;
  export default src;
}

declare module "*.svg" {
  const src: string;
  export default src;
}

declare module "solar-calculator" {
  export function century(date: Date | number): number;
  export function apparentLongitude(t: number): number;
  export function declination(t: number): number;
  export function equationOfCenter(t: number): number;
  export function equationOfTime(t: number): number;
  export function meanAnomaly(t: number): number;
  export function meanLongitude(t: number): number;
  export function obliquityOfEcliptic(t: number): number;
  export function orbitEccentricity(t: number): number;
  export function trueLongitude(t: number): number;
}
